from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from .browser_environment import BrowserEnvironmentManager
from .browser_environment import BrowserEnvironmentError
from .browser_daemon import daemon_endpoint_path
from .browser_daemon import read_daemon_endpoint
from .runtime import get_product_root
from .runtime import is_process_alive
from .storage.file_io import file_lock
from .storage.paths import resolve_storage_root


DAEMON_START_TIMEOUT_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 120.0


class BrowserDaemonClient:
  """Drives a session's browser through its per-session daemon process.

  The daemon owns the Playwright instance, so the browser survives turn
  workers and Web API restarts alike. Method calls mirror
  BrowserEnvironmentManager; the daemon endpoint is discovered via
  browser/<session>/daemon.json and spawned on demand.
  """

  def __init__(self, root: Path | str | None = None):
    self.root = resolve_storage_root(root)

  async def open(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    if kwargs.get("project_root") is not None:
      kwargs["project_root"] = str(kwargs["project_root"])
    return await self._call(session_id, "open", spawn=True, **kwargs)

  async def snapshot(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "snapshot", spawn=True, **kwargs)

  async def click(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "click", spawn=True, **kwargs)

  async def click_at(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "click_at", spawn=True, **kwargs)

  async def type(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "type", spawn=True, **kwargs)

  async def type_text(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "type_text", spawn=True, **kwargs)

  async def keys(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "keys", spawn=True, **kwargs)

  async def press_keys(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "press_keys", spawn=True, **kwargs)

  async def wheel(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "wheel", spawn=True, **kwargs)

  async def scroll(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "scroll", spawn=True, **kwargs)

  async def set_viewport(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "set_viewport", spawn=True, **kwargs)

  async def wait(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "wait", spawn=True, **kwargs)

  async def screenshot(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "screenshot", spawn=True, **kwargs)

  async def refresh(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "refresh", spawn=True, **kwargs)

  async def ensure_live(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    return await self._call(session_id, "ensure_live", spawn=True, **kwargs)

  async def close(self, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
    endpoint = await self._live_endpoint(session_id)
    if endpoint is None:
      # No daemon: just persist the closed state.
      return await self._local_manager().close(session_id=session_id)
    return await self._call(session_id, "close", spawn=False, **kwargs)

  async def mark_error(
      self,
      *,
      session_id: str,
      action: str,
      error: str,
  ) -> dict[str, Any]:
    endpoint = await self._live_endpoint(session_id)
    if endpoint is None:
      # State writes don't need a live browser.
      return await self._local_manager().mark_error(
          session_id=session_id,
          action=action,
          error=error,
      )
    return await self._call(
        session_id,
        "mark_error",
        spawn=False,
        action=action,
        error=error,
    )

  def has_live_session(self, session_id: str) -> bool:
    endpoint = read_daemon_endpoint(self.root, session_id)
    if endpoint is None or not is_process_alive(endpoint.get("pid")):
      return False
    try:
      response = httpx.get(
          f"http://127.0.0.1:{endpoint['port']}/live",
          timeout=2.0,
      )
      return bool(response.json().get("live"))
    except Exception:  # noqa: BLE001 - unreachable daemon means no live browser.
      return False

  async def stream_frames(self, *, session_id: str) -> AsyncIterator[bytes]:
    import websockets

    endpoint = await self._ensure_daemon(session_id)
    url = f"ws://127.0.0.1:{endpoint['port']}/frames"
    async with websockets.connect(url, max_size=None) as connection:
      async for message in connection:
        if isinstance(message, bytes):
          yield message

  async def _call(
      self,
      session_id: str,
      method: str,
      *,
      spawn: bool,
      **kwargs: Any,
  ) -> dict[str, Any]:
    if spawn:
      endpoint = await self._ensure_daemon(session_id)
    else:
      endpoint = await self._live_endpoint(session_id)
      if endpoint is None:
        raise BrowserEnvironmentError("Browser daemon is not running.")
    async with httpx.AsyncClient(timeout=CALL_TIMEOUT_SECONDS) as client:
      response = await client.post(
          f"http://127.0.0.1:{endpoint['port']}/call",
          json={"method": method, "kwargs": kwargs},
      )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
      raise BrowserEnvironmentError(str(payload.get("error") or "Browser call failed"))
    result = payload.get("result")
    return result if isinstance(result, dict) else {"value": result}

  async def _live_endpoint(self, session_id: str) -> dict[str, Any] | None:
    endpoint = read_daemon_endpoint(self.root, session_id)
    if endpoint is None or not is_process_alive(endpoint.get("pid")):
      return None
    if await self._healthy(endpoint):
      return endpoint
    return None

  async def _ensure_daemon(self, session_id: str) -> dict[str, Any]:
    endpoint = await self._live_endpoint(session_id)
    if endpoint is not None:
      return endpoint
    spawn_lock = daemon_endpoint_path(self.root, session_id).with_suffix(".spawn.lock")
    spawn_lock.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(spawn_lock):
      endpoint = await self._live_endpoint(session_id)
      if endpoint is not None:
        return endpoint
      env = os.environ.copy()
      env["HANDA_STORAGE_ROOT"] = str(self.root)
      subprocess.Popen(
          [sys.executable, "-m", "src.browser_daemon", session_id],
          cwd=str(get_product_root()),
          env=env,
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
          start_new_session=True,
      )
    deadline = asyncio.get_event_loop().time() + DAEMON_START_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
      endpoint = await self._live_endpoint(session_id)
      if endpoint is not None:
        return endpoint
      await asyncio.sleep(0.1)
    raise BrowserEnvironmentError("Browser daemon failed to start.")

  async def _healthy(self, endpoint: dict[str, Any]) -> bool:
    try:
      async with httpx.AsyncClient(timeout=2.0) as client:
        response = await client.get(f"http://127.0.0.1:{endpoint['port']}/health")
      return bool(response.json().get("ok"))
    except Exception:  # noqa: BLE001 - any failure means the daemon is unusable.
      return False

  def _local_manager(self) -> BrowserEnvironmentManager:
    return BrowserEnvironmentManager(self.root)


def default_browser_client() -> BrowserDaemonClient:
  # Stateless: resolve the storage root per call so env overrides (tests,
  # alternate handa dirs) always apply.
  return BrowserDaemonClient()
