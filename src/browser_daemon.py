from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
import uvicorn

from .browser_environment import BrowserEnvironmentManager
from .storage.file_io import atomic_write_text
from .storage.paths import browser_dir
from .storage.paths import resolve_storage_root


# Methods the client may invoke; mirrors the BrowserEnvironmentManager surface
# used by the agent tools and the Web routes.
ALLOWED_METHODS = frozenset({
    "open",
    "snapshot",
    "click",
    "click_at",
    "drag",
    "type",
    "type_text",
    "keys",
    "press_keys",
    "wheel",
    "scroll",
    "set_viewport",
    "wait",
    "screenshot",
    "refresh",
    "ensure_live",
    "mark_error",
    "close",
})

IDLE_EXIT_SECONDS = 900.0
IDLE_CHECK_INTERVAL_SECONDS = 30.0


def daemon_endpoint_path(root: Path | str | None, session_id: str) -> Path:
  return browser_dir(resolve_storage_root(root), session_id) / "daemon.json"


def read_daemon_endpoint(
    root: Path | str | None,
    session_id: str,
) -> dict[str, Any] | None:
  path = daemon_endpoint_path(root, session_id)
  if not path.is_file():
    return None
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None
  return data if isinstance(data, dict) else None


class _DaemonState:
  def __init__(self, session_id: str, manager: BrowserEnvironmentManager):
    self.session_id = session_id
    self.manager = manager
    self.last_activity = time.monotonic()
    self.stream_clients = 0
    self.shutdown = asyncio.Event()

  def touch(self) -> None:
    self.last_activity = time.monotonic()


def create_daemon_app(session_id: str, *, root: Path | str | None = None) -> FastAPI:
  manager = BrowserEnvironmentManager(root)
  state = _DaemonState(session_id, manager)
  app = FastAPI(title=f"Handa Browser Daemon {session_id}")
  app.state.daemon = state

  @app.get("/health")
  async def health() -> dict[str, Any]:
    return {"ok": True, "session_id": session_id, "pid": os.getpid()}

  @app.post("/call")
  async def call(payload: dict[str, Any]) -> dict[str, Any]:
    state.touch()
    method_name = str(payload.get("method") or "")
    if method_name not in ALLOWED_METHODS:
      raise HTTPException(status_code=400, detail=f"Unknown method: {method_name}")
    kwargs = payload.get("kwargs")
    kwargs = dict(kwargs) if isinstance(kwargs, dict) else {}
    kwargs["session_id"] = session_id
    if method_name == "open" and kwargs.get("project_root"):
      kwargs["project_root"] = Path(str(kwargs["project_root"]))
    method = getattr(manager, method_name)
    try:
      result = await method(**kwargs)
    except Exception as exc:  # noqa: BLE001 - surface as a structured RPC error.
      return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    state.touch()
    if method_name == "close":
      state.shutdown.set()
    return {"ok": True, "result": result}

  @app.get("/live")
  async def live() -> dict[str, Any]:
    return {"ok": True, "live": manager.has_live_session(session_id)}

  @app.websocket("/frames")
  async def frames(websocket: WebSocket) -> None:
    await websocket.accept()
    state.stream_clients += 1
    state.touch()
    try:
      async for frame in manager.stream_frames(session_id=session_id):
        state.touch()
        await websocket.send_bytes(frame)
    except WebSocketDisconnect:
      pass
    except Exception:  # noqa: BLE001 - stream teardown must not kill the daemon.
      pass
    finally:
      state.stream_clients -= 1
      try:
        await websocket.close()
      except Exception:
        pass

  return app


async def _serve(session_id: str) -> int:
  root = resolve_storage_root()
  app = create_daemon_app(session_id, root=root)
  state: _DaemonState = app.state.daemon

  config = uvicorn.Config(
      app,
      host="127.0.0.1",
      port=0,
      log_level="warning",
      lifespan="on",
  )
  server = uvicorn.Server(config)
  serve_task = asyncio.create_task(server.serve())
  while not server.started:
    if serve_task.done():
      serve_task.result()
      return 1
    await asyncio.sleep(0.02)

  port = server.servers[0].sockets[0].getsockname()[1]
  endpoint_path = daemon_endpoint_path(root, session_id)
  endpoint_path.parent.mkdir(parents=True, exist_ok=True)
  atomic_write_text(
      endpoint_path,
      json.dumps({"pid": os.getpid(), "port": port}, ensure_ascii=True) + "\n",
  )

  try:
    while True:
      try:
        await asyncio.wait_for(
            state.shutdown.wait(),
            timeout=IDLE_CHECK_INTERVAL_SECONDS,
        )
        break
      except TimeoutError:
        pass
      idle_for = time.monotonic() - state.last_activity
      if state.stream_clients > 0:
        continue
      if idle_for >= IDLE_EXIT_SECONDS:
        break
  finally:
    try:
      await state.manager.stop()
    except Exception:
      pass
    try:
      if read_daemon_endpoint(root, session_id) == {"pid": os.getpid(), "port": port}:
        endpoint_path.unlink(missing_ok=True)
    except OSError:
      pass
    server.should_exit = True
    await serve_task
  return 0


def main(session_id: str) -> int:
  return asyncio.run(_serve(session_id))


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1]))
