from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import urlparse

from .runtime import now_iso
from .storage.file_io import atomic_write_text
from .storage.paths import browser_dir
from .storage.paths import browser_events_path
from .storage.paths import browser_profile_dir
from .storage.paths import browser_screenshot_path
from .storage.paths import browser_state_path
from .storage.paths import resolve_storage_root

DEFAULT_VIEWPORT = {"width": 1280, "height": 720}
# Bounds for the live viewport when the human viewer resizes the browser surface
# to fill the chat panel. Sizes are CSS pixels, as sent by the frontend.
MIN_VIEWPORT = {"width": 320, "height": 240}
MAX_VIEWPORT = {"width": 2560, "height": 1600}
# Render at 2x so screenshots and live-stream frames stay crisp on HiDPI/Retina
# displays. The frontend sends CSS-pixel viewport sizes (not multiplied by
# devicePixelRatio), so without this the page renders 1 device pixel per CSS
# pixel and gets upscaled — and blurred — when displayed at dpr=2. This only
# affects pixels, not the CSS viewport, so page layout and the fractional click
# mapping are unchanged. Live frames must be captured via Page.captureScreenshot
# with clip.scale=DEVICE_SCALE_FACTOR — Chromium's screencast output is capped
# at 1x regardless of this setting (see stream_frames).
DEVICE_SCALE_FACTOR = 2
MAX_WAIT_MS = 30000
ELEMENT_TARGET_PREFIX = "e"
MAX_UI_TYPE_CHARS = 8000

# Internal page-state fields persisted to disk for snapshot-id resolution but
# never returned to the model (they are large and would duplicate / stale-out
# the explicit response fields).
INTERNAL_STATE_KEYS = frozenset({"last_snapshot", "last_text"})


class BrowserEnvironmentError(RuntimeError):
    pass


class BrowserEnvironmentManager:
    """Process-local Playwright manager backed by per-session profile storage."""

    def __init__(self, root: Path | str | None = None):
        self.root = resolve_storage_root(root)
        self._playwright: Any | None = None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def open(
        self,
        *,
        session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        project_root: Path | str | None = None,
    ) -> dict[str, Any]:
        normalized_url = _normalize_url(url, project_root=project_root)
        wait_until = _normalize_wait_until(wait_until)
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            await self._update_state(
                session_id,
                status="running",
                last_action=f"Open {normalized_url}",
                last_error=None,
            )
            await page.goto(normalized_url, wait_until=wait_until, timeout=MAX_WAIT_MS)
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Opened {normalized_url}",
            )

    async def snapshot(
        self,
        *,
        session_id: str,
        max_elements: int = 80,
    ) -> dict[str, Any]:
        max_elements = max(1, min(int(max_elements), 200))
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            snapshot = await page.evaluate(SNAPSHOT_SCRIPT, max_elements)
            if isinstance(snapshot, dict):
                elements = (
                    snapshot.get("elements")
                    if isinstance(snapshot.get("elements"), list)
                    else []
                )
                page_text = str(snapshot.get("text") or "")
            elif isinstance(snapshot, list):
                elements = snapshot
                page_text = ""
            else:
                elements = []
                page_text = ""
            state = await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Captured snapshot ({len(elements)} elements)",
                extra={"last_snapshot": elements, "last_text": page_text},
            )
            return {
                **state,
                "elements": elements,
                "text": page_text,
                "count": len(elements),
            }

    async def click(self, *, session_id: str, target: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            selector = self._selector_for_target(session_id, target)
            await page.locator(selector).first.click(timeout=MAX_WAIT_MS)
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Clicked {target}",
            )

    async def click_at(
        self,
        *,
        session_id: str,
        x: float,
        y: float,
        button: str = "left",
        capture_screenshot: bool = True,
    ) -> dict[str, Any]:
        x = _clamp_float(x, 0, 1)
        y = _clamp_float(y, 0, 1)
        button = _normalize_mouse_button(button)
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            viewport = _page_viewport(page)
            pixel_x = round(viewport["width"] * x)
            pixel_y = round(viewport["height"] * y)
            await self._update_state(
                session_id,
                status="running",
                last_action=f"Clicking at {pixel_x},{pixel_y}",
                last_error=None,
            )
            await page.mouse.click(pixel_x, pixel_y, button=button)
            if not capture_screenshot:
                return await self._record_page_metadata(
                    session_id,
                    page,
                    status="open",
                    last_action=f"Clicked at {pixel_x},{pixel_y}",
                )
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Clicked at {pixel_x},{pixel_y}",
            )

    async def type(
        self,
        *,
        session_id: str,
        target: str,
        text: str,
        clear: bool = True,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            selector = self._selector_for_target(session_id, target)
            locator = page.locator(selector).first
            if clear:
                await locator.fill(text, timeout=MAX_WAIT_MS)
            else:
                await locator.click(timeout=MAX_WAIT_MS)
                await locator.type(text, timeout=MAX_WAIT_MS)
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Typed into {target}",
            )

    async def keys(self, *, session_id: str, keys: str) -> dict[str, Any]:
        return await self.press_keys(session_id=session_id, keys=keys)

    async def press_keys(
        self,
        *,
        session_id: str,
        keys: str,
        capture_screenshot: bool = True,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            await page.keyboard.press(keys)
            if not capture_screenshot:
                return await self._record_page_metadata(
                    session_id,
                    page,
                    status="open",
                    last_action=f"Pressed {keys}",
                )
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Pressed {keys}",
            )

    async def type_text(
        self,
        *,
        session_id: str,
        text: str,
        capture_screenshot: bool = True,
    ) -> dict[str, Any]:
        text = str(text or "")
        if not text:
            raise ValueError("text must not be empty.")
        if len(text) > MAX_UI_TYPE_CHARS:
            raise ValueError(f"text must be at most {MAX_UI_TYPE_CHARS} characters.")
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            await self._update_state(
                session_id,
                status="running",
                last_action="Typing in browser",
                last_error=None,
            )
            await page.keyboard.type(text)
            if not capture_screenshot:
                return await self._record_page_metadata(
                    session_id,
                    page,
                    status="open",
                    last_action="Typed in browser",
                )
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action="Typed in browser",
            )

    async def wheel(
        self,
        *,
        session_id: str,
        delta_x: int = 0,
        delta_y: int = 0,
        capture_screenshot: bool = True,
    ) -> dict[str, Any]:
        delta_x = _clamp_int(delta_x, -5000, 5000)
        delta_y = _clamp_int(delta_y, -5000, 5000)
        if delta_x == 0 and delta_y == 0:
            raise ValueError("scroll delta must not be zero.")
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            await self._update_state(
                session_id,
                status="running",
                last_action="Scrolling browser",
                last_error=None,
            )
            await page.mouse.wheel(delta_x, delta_y)
            if not capture_screenshot:
                return await self._record_page_metadata(
                    session_id,
                    page,
                    status="open",
                    last_action="Scrolled browser",
                )
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action="Scrolled browser",
            )

    async def scroll(
        self,
        *,
        session_id: str,
        direction: str = "down",
        amount: int = 600,
    ) -> dict[str, Any]:
        amount = max(1, min(int(amount), 5000))
        delta = amount if str(direction).lower() != "up" else -amount
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            await page.mouse.wheel(0, delta)
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=f"Scrolled {direction}",
            )

    async def set_viewport(
        self,
        *,
        session_id: str,
        width: int,
        height: int,
        capture_screenshot: bool = False,
    ) -> dict[str, Any]:
        width = _clamp_int(width, MIN_VIEWPORT["width"], MAX_VIEWPORT["width"])
        height = _clamp_int(height, MIN_VIEWPORT["height"], MAX_VIEWPORT["height"])
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            current = _page_viewport(page)
            if current["width"] == width and current["height"] == height:
                # No change — avoid churning state on the frequent resize pings the live
                # viewer sends while the panel is being dragged.
                summary = read_browser_summary(self.root, session_id)
                if summary is not None:
                    return summary
            await page.set_viewport_size({"width": width, "height": height})
            action = f"Resized browser to {width}x{height}"
            if capture_screenshot:
                return await self._record_page_state(
                    session_id,
                    page,
                    status="open",
                    last_action=action,
                    append_event=False,
                )
            return await self._record_page_metadata(
                session_id,
                page,
                status="open",
                last_action=action,
                append_event=False,
            )

    def has_live_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    async def wait(
        self,
        *,
        session_id: str,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        timeout_ms = max(1, min(int(timeout_ms), MAX_WAIT_MS))
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            if selector and selector.strip():
                await page.locator(selector.strip()).first.wait_for(timeout=timeout_ms)
                action = f"Waited for selector {selector.strip()}"
            elif text and text.strip():
                await page.get_by_text(text.strip()).first.wait_for(timeout=timeout_ms)
                action = f"Waited for text {text.strip()}"
            else:
                await page.wait_for_timeout(timeout_ms)
                action = f"Waited {timeout_ms}ms"
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action=action,
            )

    async def screenshot(
        self,
        *,
        session_id: str,
        full_page: bool = False,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            return await self._record_page_state(
                session_id,
                page,
                status="open",
                last_action="Captured screenshot",
                full_page=full_page,
            )

    async def refresh(self, *, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            existing = _read_state(self.root, session_id)
            if not existing:
                raise BrowserEnvironmentError("Browser environment not found.")
            status = (
                existing.get("status")
                if isinstance(existing.get("status"), str)
                else ""
            )
            if status == "closed" and session_id not in self._sessions:
                summary = read_browser_summary(self.root, session_id)
                if summary is not None:
                    return summary
            page = await self._page(session_id)
            return await self._record_page_state(
                session_id,
                page,
                status=status or "open",
                last_action=(
                    existing.get("last_action")
                    if isinstance(existing.get("last_action"), str)
                    else "Refreshed browser"
                ),
                append_event=False,
            )

    async def ensure_live(self, *, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            return await self._record_page_metadata(
                session_id,
                page,
                status="open",
                last_action="Opened live browser stream",
                append_event=False,
            )

    async def stream_frames(
        self,
        *,
        session_id: str,
        quality: int = 85,
    ) -> AsyncIterator[bytes]:
        async with self._session_lock(session_id):
            page = await self._page(session_id)
            await self._record_page_metadata(
                session_id,
                page,
                status="open",
                last_action="Started live browser stream",
                append_event=False,
            )

        page = await self._page(session_id)
        client = await page.context.new_cdp_session(page)
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
        loop = asyncio.get_running_loop()
        quality = max(1, min(int(quality), 100))

        # Chromium's screencast emits frames in device-independent pixels (its
        # internal scale factor is capped at 1), so its frames are always 1x and
        # look blurry on HiDPI displays no matter what device_scale_factor the
        # context uses. Stream hi-res frames instead: use the screencast purely as
        # a cheap "page repainted" signal and grab the displayed frame with
        # Page.captureScreenshot, whose clip.scale exposes the full 2x surface.
        dirty = asyncio.Event()

        async def ack_frame(params: dict[str, Any]) -> None:
            session_token = params.get("sessionId")
            if session_token is not None:
                try:
                    await client.send(
                        "Page.screencastFrameAck", {"sessionId": session_token}
                    )
                except Exception:
                    return
            dirty.set()

        def on_frame(params: dict[str, Any]) -> None:
            loop.create_task(ack_frame(params))

        async def capture_frames() -> None:
            while True:
                await dirty.wait()
                dirty.clear()
                viewport = _page_viewport(page)
                try:
                    result = await client.send(
                        "Page.captureScreenshot",
                        {
                            "format": "jpeg",
                            "quality": quality,
                            "clip": {
                                "x": 0,
                                "y": 0,
                                "width": viewport["width"],
                                "height": viewport["height"],
                                "scale": DEVICE_SCALE_FACTOR,
                            },
                        },
                    )
                except Exception:
                    # Capture can fail transiently (e.g. mid-navigation). Retry on
                    # the next repaint signal instead of tearing down the stream.
                    await asyncio.sleep(0.25)
                    dirty.set()
                    continue
                data = result.get("data")
                if not isinstance(data, str):
                    continue
                frame = base64.b64decode(data)
                while queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                queue.put_nowait(frame)

        client.on("Page.screencastFrame", on_frame)
        capture_task = loop.create_task(capture_frames())
        await client.send(
            "Page.startScreencast",
            {
                # Signal-only stream: the frames are discarded, so keep them as
                # small and cheap to encode as possible.
                "format": "jpeg",
                "quality": 10,
                "maxWidth": MIN_VIEWPORT["width"],
                "maxHeight": MIN_VIEWPORT["height"],
                "everyNthFrame": 1,
            },
        )
        try:
            while True:
                yield await queue.get()
        finally:
            capture_task.cancel()
            try:
                await client.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                await client.detach()
            except Exception:
                pass

    async def mark_error(
        self, *, session_id: str, action: str, error: str
    ) -> dict[str, Any]:
        async with self._session_lock(session_id):
            await self._update_state(
                session_id,
                status="error",
                last_action=action,
                last_error=error,
            )
            self._append_event(
                session_id, {"action": action, "status": "error", "error": error}
            )
            return read_browser_summary(self.root, session_id) or {
                "success": False,
                "status": "error",
                "last_action": action,
                "last_error": error,
                "screenshot_url": None,
            }

    async def close(self, *, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            record = self._sessions.pop(session_id, None)
            if record is not None:
                await record["context"].close()
            await self._update_state(
                session_id,
                status="closed",
                last_action="Closed browser",
                last_error=None,
            )
            self._append_event(session_id, {"action": "close", "status": "closed"})
            return read_browser_summary(self.root, session_id) or {
                "success": True,
                "status": "closed",
                "screenshot_url": None,
            }

    async def stop(self) -> None:
        for session_id in list(self._sessions):
            try:
                await self.close(session_id=session_id)
            except Exception:
                pass
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _page(self, session_id: str):
        record = self._sessions.get(session_id)
        if record is not None:
            return record["page"]

        browser_dir(self.root, session_id).mkdir(parents=True, exist_ok=True)
        browser_profile_dir(self.root, session_id).mkdir(parents=True, exist_ok=True)
        playwright = await self._ensure_playwright()
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(browser_profile_dir(self.root, session_id)),
                headless=True,
                viewport=DEFAULT_VIEWPORT,
                device_scale_factor=DEVICE_SCALE_FACTOR,
            )
        except Exception as exc:  # noqa: BLE001 - add product-specific install hint.
            raise BrowserEnvironmentError(
                "Could not launch Playwright Chromium. Run `uv run playwright install chromium`."
            ) from exc
        page = context.pages[0] if context.pages else await context.new_page()
        await self._restore_last_url_if_needed(session_id, page)
        self._sessions[session_id] = {"context": context, "page": page}
        await self._update_state(
            session_id,
            status="idle",
            last_action="Started browser",
            last_error=None,
        )
        return page

    async def _restore_last_url_if_needed(self, session_id: str, page: Any) -> None:
        if page.url and page.url != "about:blank":
            return
        state = _read_state(self.root, session_id) or {}
        url = state.get("url")
        if not isinstance(url, str) or not url.strip():
            return
        if url.startswith(("http://", "https://", "file://")):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=MAX_WAIT_MS)
            except Exception:
                # The restored page is a convenience for live preview after API restart.
                # Keep browser startup available even if the old URL is temporarily down.
                return

    async def _ensure_playwright(self):
        if self._playwright is not None:
            return self._playwright
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserEnvironmentError(
                "Playwright is not installed. Run `uv sync` and `uv run playwright install chromium`."
            ) from exc
        self._playwright = await async_playwright().start()
        return self._playwright

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def _selector_for_target(self, session_id: str, target: str) -> str:
        target = str(target or "").strip()
        if not target:
            raise ValueError("target must not be empty.")
        if target.startswith(ELEMENT_TARGET_PREFIX) and target[1:].isdigit():
            state = _read_state(self.root, session_id) or {}
            elements = state.get("last_snapshot")
            if isinstance(elements, list):
                for element in elements:
                    if isinstance(element, dict) and element.get("id") == target:
                        selector = str(element.get("selector") or "").strip()
                        if selector:
                            return selector
            raise ValueError(f"Unknown browser snapshot target: {target}")
        return target

    async def _record_page_state(
        self,
        session_id: str,
        page: Any,
        *,
        status: str,
        last_action: str,
        extra: dict[str, Any] | None = None,
        full_page: bool = False,
        append_event: bool = True,
    ) -> dict[str, Any]:
        screenshot_path = browser_screenshot_path(self.root, session_id)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=full_page)
        return await self._record_page_metadata(
            session_id,
            page,
            status=status,
            last_action=last_action,
            extra=extra,
            append_event=append_event,
        )

    async def _record_page_metadata(
        self,
        session_id: str,
        page: Any,
        *,
        status: str,
        last_action: str,
        extra: dict[str, Any] | None = None,
        append_event: bool = True,
    ) -> dict[str, Any]:
        title = await page.title()
        existing = _read_state(self.root, session_id) or {}
        state = {
            **existing,
            "success": True,
            "status": status,
            "url": page.url,
            "title": title,
            "last_action": last_action,
            "last_error": None,
            "updated_at": now_iso(),
            "viewport": _page_viewport(page),
            **(extra or {}),
        }
        _write_state(self.root, session_id, state)
        if append_event:
            self._append_event(
                session_id, {"action": last_action, "status": status, "url": page.url}
            )
        # `last_snapshot` / `last_text` are large internal fields persisted to disk
        # so `_selector_for_target` can resolve snapshot ids like `e12`. They must
        # never be echoed back to the model: in `snapshot()` they would duplicate
        # the explicit `elements`/`text` fields, and after click/type/scroll/wait
        # they would carry a stale pre-action page tree. Strip them from the reply.
        returned = {
            key: value for key, value in state.items() if key not in INTERNAL_STATE_KEYS
        }
        return _with_screenshot_url(self.root, session_id, returned)

    async def _update_state(
        self,
        session_id: str,
        *,
        status: str,
        last_action: str,
        last_error: str | None,
    ) -> None:
        existing = _read_state(self.root, session_id) or {}
        state = {
            **existing,
            "success": last_error is None,
            "status": status,
            "last_action": last_action,
            "last_error": last_error,
            "updated_at": now_iso(),
            "viewport": existing.get("viewport") or dict(DEFAULT_VIEWPORT),
        }
        _write_state(self.root, session_id, state)

    def _append_event(self, session_id: str, payload: dict[str, Any]) -> None:
        path = browser_events_path(self.root, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"created_at": now_iso(), **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


def read_browser_summary(
    root: Path | str | None,
    session_id: str,
) -> dict[str, Any] | None:
    state = _read_state(resolve_storage_root(root), session_id)
    if not state:
        return None
    summary = {
        key: state.get(key)
        for key in (
            "success",
            "status",
            "url",
            "title",
            "last_action",
            "last_error",
            "updated_at",
            "viewport",
        )
        if key in state
    }
    return _with_screenshot_url(resolve_storage_root(root), session_id, summary)


def browser_screenshot_file(root: Path | str | None, session_id: str) -> Path:
    return browser_screenshot_path(root, session_id)


def _read_state(root: Path | str, session_id: str) -> dict[str, Any] | None:
    path = browser_state_path(root, session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(root: Path | str, session_id: str, state: dict[str, Any]) -> None:
    path = browser_state_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps(state, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    )


def _with_screenshot_url(
    root: Path | str,
    session_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    screenshot = browser_screenshot_path(root, session_id)
    screenshot_url = (
        f"/api/sessions/{session_id}/browser/screenshot"
        if screenshot.is_file()
        else None
    )
    return {
        **state,
        "session_id": session_id,
        "screenshot_url": screenshot_url,
        "stream_url": f"/api/sessions/{session_id}/browser/stream",
    }


def _normalize_wait_until(value: str) -> str:
    allowed = {"commit", "domcontentloaded", "load", "networkidle"}
    normalized = str(value or "domcontentloaded").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"Unsupported wait_until: {value}")
    return normalized


def _normalize_mouse_button(value: str) -> str:
    normalized = str(value or "left").strip().lower()
    if normalized not in {"left", "right", "middle"}:
        raise ValueError(f"Unsupported mouse button: {value}")
    return normalized


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(float(value), maximum))


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _page_viewport(page: Any) -> dict[str, int]:
    viewport = getattr(page, "viewport_size", None)
    if isinstance(viewport, dict):
        width = viewport.get("width")
        height = viewport.get("height")
        if isinstance(width, int) and isinstance(height, int):
            return {"width": width, "height": height}
    return dict(DEFAULT_VIEWPORT)


def _normalize_url(url: str, *, project_root: Path | str | None) -> str:
    text = str(url or "").strip()
    if not text:
        raise ValueError("url must not be empty.")
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        return text
    if parsed.scheme == "file":
        if project_root is None:
            raise ValueError("file:// URLs require a project root.")
        target = Path(unquote(parsed.path)).expanduser().resolve()
        root = Path(project_root).expanduser().resolve()
        if target != root and root not in target.parents:
            raise ValueError("file:// URL escapes project root.")
        return target.as_uri()
    raise ValueError(
        "Browser Environment supports http, https, and project-root file URLs."
    )


SNAPSHOT_SCRIPT = r"""
(maxElements) => {
  const selectorFor = (el) => {
    const escape = (value) => {
      if (window.CSS && CSS.escape) return CSS.escape(value)
      return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&')
    }
    if (el.id) return `#${escape(el.id)}`
    const parts = []
    let current = el
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {
      const tag = current.tagName.toLowerCase()
      const parent = current.parentElement
      if (!parent) {
        parts.unshift(tag)
        break
      }
      const sameTag = Array.from(parent.children).filter((child) => child.tagName === current.tagName)
      if (sameTag.length === 1) {
        parts.unshift(tag)
      } else {
        parts.unshift(`${tag}:nth-of-type(${sameTag.indexOf(current) + 1})`)
      }
      current = parent
    }
    return `body > ${parts.join(' > ')}`
  }
  const textFor = (el) => {
    const direct = el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder')
    if (direct) return direct.trim()
    if ('value' in el && el.value) return String(el.value).trim()
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()
  }
  const isVisible = (el) => {
    const style = window.getComputedStyle(el)
    const rect = el.getBoundingClientRect()
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0
  }
  const collectShadowText = (root) => {
    const chunks = []
    const visit = (node, visible) => {
      if (!node) return
      if (node.nodeType === Node.TEXT_NODE) {
        if (visible) {
          const text = String(node.textContent || '').replace(/\s+/g, ' ').trim()
          if (text) chunks.push(text)
        }
        return
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        const tag = node.tagName.toLowerCase()
        if (tag === 'script' || tag === 'style' || tag === 'template') return
        const nextVisible = visible && isVisible(node)
        if (node.shadowRoot) visit(node.shadowRoot, nextVisible)
        for (const child of Array.from(node.childNodes)) visit(child, nextVisible)
        return
      }
      for (const child of Array.from(node.childNodes || [])) visit(child, visible)
    }
    for (const el of Array.from(document.querySelectorAll('*'))) {
      if (el.shadowRoot) visit(el.shadowRoot, isVisible(el))
    }
    return chunks.join(' ')
  }
  const bodyText = document.body ? document.body.innerText || document.body.textContent || '' : ''
  const pageText = `${bodyText} ${collectShadowText(document)}`
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 12000)
  const candidates = Array.from(document.querySelectorAll(
    'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"],[tabindex]'
  )).filter(isVisible)
  const elements = candidates.slice(0, maxElements).map((el, index) => {
    const rect = el.getBoundingClientRect()
    return {
      id: `e${index + 1}`,
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      text: textFor(el).slice(0, 160),
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      bbox: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      }
    }
  })
  return { text: pageText, elements }
}
"""
