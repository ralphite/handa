from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import ValidationError

from ...contract.browser import BrowserDaemonClient
from ...contract.browser import default_browser_client
from ...contract.browser import browser_screenshot_file
from ...contract.browser import DEFAULT_VIEWPORT
from ...contract.browser import read_browser_summary
from ...contract.services import APP_NAME
from ..context import WebApiContext
from ..context import get_context
from ..schemas import BrowserInteractionRequest
from ..schemas import BrowserEnvironmentSummary


router = APIRouter(prefix="/api/sessions/{session_id}/browser")


async def _ensure_session_in_context(session_id: str, ctx: WebApiContext) -> None:
  if ctx.db.is_session_deleted(session_id):
    raise HTTPException(status_code=404, detail="Session not found")
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if session is None:
    raise HTTPException(status_code=404, detail="Session not found")


async def _ensure_session(session_id: str, request: Request) -> None:
  await _ensure_session_in_context(session_id, get_context(request))


@router.get("", response_model=BrowserEnvironmentSummary)
async def get_browser_environment(session_id: str, request: Request):
  await _ensure_session(session_id, request)
  ctx = get_context(request)
  summary = read_browser_summary(ctx.services.storage_root, session_id)
  if summary is None:
    raise HTTPException(status_code=404, detail="Browser environment not found")
  return summary


@router.get("/screenshot")
async def get_browser_screenshot(session_id: str, request: Request):
  await _ensure_session(session_id, request)
  ctx = get_context(request)
  path = browser_screenshot_file(ctx.services.storage_root, session_id)
  if not path.is_file():
    raise HTTPException(status_code=404, detail="Browser screenshot not found")
  return FileResponse(
      path,
      media_type="image/png",
      headers={"cache-control": "no-store"},
  )


@router.post("/refresh", response_model=BrowserEnvironmentSummary)
async def refresh_browser_environment(session_id: str, request: Request):
  await _ensure_session(session_id, request)
  try:
    return await default_browser_client().refresh(session_id=session_id)
  except Exception as exc:  # noqa: BLE001 - API should return product-shaped error state.
    summary = await default_browser_client().mark_error(
        session_id=session_id,
        action="Refresh browser",
        error=str(exc),
    )
    raise HTTPException(status_code=400, detail=summary["last_error"])


@router.post("/interactions", response_model=BrowserEnvironmentSummary)
async def interact_with_browser(
    session_id: str,
    payload: BrowserInteractionRequest,
    request: Request,
):
  await _ensure_session(session_id, request)
  manager = default_browser_client()
  try:
    return await _dispatch_browser_interaction(
        manager,
        session_id=session_id,
        payload=payload,
        capture_screenshot=True,
    )
  except Exception as exc:  # noqa: BLE001 - expose failure in browser summary.
    summary = await manager.mark_error(
        session_id=session_id,
        action=f"Browser {payload.action}",
        error=str(exc),
    )
    raise HTTPException(status_code=400, detail=summary["last_error"])


@router.websocket("/stream")
async def stream_browser(session_id: str, websocket: WebSocket):
  await websocket.accept()
  ctx: WebApiContext = websocket.app.state.web_context
  try:
    await _ensure_session_in_context(session_id, ctx)
  except HTTPException as exc:
    await websocket.send_json({"type": "error", "message": exc.detail})
    await websocket.close(code=1008)
    return

  manager = default_browser_client()
  send_lock = asyncio.Lock()

  async def send_json(payload: dict) -> None:
    async with send_lock:
      await websocket.send_json(payload)

  async def send_frames() -> None:
    async for frame in manager.stream_frames(session_id=session_id):
      async with send_lock:
        await websocket.send_bytes(frame)

  async def receive_interactions() -> None:
    while True:
      raw = await websocket.receive_text()
      try:
        payload = BrowserInteractionRequest.model_validate_json(raw)
        summary = await _dispatch_browser_interaction(
            manager,
            session_id=session_id,
            payload=payload,
            capture_screenshot=False,
        )
      except (ValidationError, ValueError) as exc:
        await send_json({"type": "error", "message": str(exc)})
        continue
      except Exception as exc:  # noqa: BLE001 - live browser errors should stay in-stream.
        summary = await manager.mark_error(
            session_id=session_id,
            action="Browser stream interaction",
            error=str(exc),
        )
        await send_json({"type": "error", "message": summary["last_error"], "summary": summary})
        continue
      await send_json({"type": "summary", "summary": summary})

  try:
    summary = await manager.ensure_live(session_id=session_id)
    await send_json({"type": "ready", "summary": summary})
    send_task = asyncio.create_task(send_frames())
    receive_task = asyncio.create_task(receive_interactions())
    done, pending = await asyncio.wait(
        {send_task, receive_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
      task.cancel()
    if pending:
      await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
      task.result()
  except WebSocketDisconnect:
    return
  except Exception as exc:  # noqa: BLE001 - close websocket with a product-shaped error.
    try:
      await send_json({"type": "error", "message": str(exc)})
    except Exception:
      pass
  finally:
    # The live viewer resizes the viewport to fill its panel; restore the default
    # 16:9 viewport once it disconnects so the agent's own browser view returns to
    # normal. Skip if the browser was already closed to avoid resurrecting it.
    if manager.has_live_session(session_id):
      try:
        await manager.set_viewport(
            session_id=session_id,
            width=DEFAULT_VIEWPORT["width"],
            height=DEFAULT_VIEWPORT["height"],
        )
      except Exception:
        pass
    try:
      await websocket.close()
    except Exception:
      pass


async def _dispatch_browser_interaction(
    manager: BrowserDaemonClient,
    *,
    session_id: str,
    payload: BrowserInteractionRequest,
    capture_screenshot: bool,
) -> dict:
  if payload.action == "click":
    if payload.x is None or payload.y is None:
      raise ValueError("click requires x and y.")
    return await manager.click_at(
        session_id=session_id,
        x=payload.x,
        y=payload.y,
        button=payload.button,
        capture_screenshot=capture_screenshot,
    )
  if payload.action == "drag":
    if (
        payload.x is None
        or payload.y is None
        or payload.x2 is None
        or payload.y2 is None
    ):
      raise ValueError("drag requires x, y, x2, and y2.")
    return await manager.drag(
        session_id=session_id,
        x=payload.x,
        y=payload.y,
        x2=payload.x2,
        y2=payload.y2,
        button=payload.button,
        capture_screenshot=capture_screenshot,
    )
  if payload.action == "type":
    if not payload.text:
      raise ValueError("type requires text.")
    return await manager.type_text(
        session_id=session_id,
        text=payload.text,
        capture_screenshot=capture_screenshot,
    )
  if payload.action == "key":
    if not payload.key:
      raise ValueError("key requires key.")
    return await manager.press_keys(
        session_id=session_id,
        keys=payload.key,
        capture_screenshot=capture_screenshot,
    )
  if payload.action == "scroll":
    return await manager.wheel(
        session_id=session_id,
        delta_x=payload.delta_x,
        delta_y=payload.delta_y,
        capture_screenshot=capture_screenshot,
    )
  if payload.action == "resize":
    if payload.width is None or payload.height is None:
      raise ValueError("resize requires width and height.")
    return await manager.set_viewport(
        session_id=session_id,
        width=payload.width,
        height=payload.height,
        capture_screenshot=capture_screenshot,
    )
  raise ValueError(f"Unsupported browser action: {payload.action}")
