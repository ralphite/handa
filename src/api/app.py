from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from ..contract.introspection import refresh_tool_definitions
from ..contract.product import setup_phoenix_tracing
from ..contract.services import create_handa_services
from ..contract.task_store import cancel_stale_live_tasks
from .automated_tasks.dispatcher import backfill_time_trigger_schedules
from .background_task_manager import BackgroundTaskManager
from .context import WebApiContext
from .routes import artifacts
from .routes import agents
from .routes import automated_tasks
from .routes import browser
from .routes import dictate
from .routes import health
from .routes import optimize_prompt
from .routes import turns
from .routes import projects
from .routes import settings as settings_routes
from .routes import sessions
from .settings import create_web_settings
from .sqlite import WebDatabase


DEFAULT_PORT = 5086


def create_app(
    *,
    handa_dir: Path | str | None = None,
) -> FastAPI:
  setup_phoenix_tracing()
  services = create_handa_services(handa_dir)
  settings = create_web_settings(
      storage_root=services.storage_root,
  )
  db = WebDatabase(settings.sqlite_path)
  db.init_schema()
  # Reconcile run records whose worker died (web restart does not touch live
  # turns: their workers run out of process and keep going).
  cancel_stale_live_tasks()
  # Tool-definition texts for context previews are exported by a runtime-side
  # process; refresh in the background so this process never imports tools.
  refresh_tool_definitions(services.storage_root)

  context = WebApiContext(
      settings=settings,
      services=services,
      db=db,
  )

  @asynccontextmanager
  async def lifespan(app: FastAPI):
    manager = BackgroundTaskManager(context)
    app.state.background_task_manager = manager
    # Schedule any time triggers that predate the scheduler before the loop runs.
    backfill_time_trigger_schedules(context)
    manager.start()
    try:
      yield
    finally:
      await manager.stop()

  app = FastAPI(title="Handa Web API", lifespan=lifespan)
  app.state.web_context = context
  app.add_middleware(
      CORSMiddleware,
      allow_origins=[
          "http://127.0.0.1:8086",
          "http://localhost:8086",
      ],
      allow_origin_regex=r"^http://(127\.0\.0\.1|localhost):\d+$",
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  app.include_router(health.router)
  app.include_router(agents.router)
  app.include_router(projects.router)
  app.include_router(automated_tasks.router)
  app.include_router(sessions.router)
  app.include_router(turns.router)
  app.include_router(artifacts.router)
  app.include_router(browser.router)
  app.include_router(dictate.router)
  app.include_router(optimize_prompt.router)
  app.include_router(settings_routes.router)
  _mount_frontend(app)
  return app


def _frontend_dist_dir() -> Path | None:
  configured = os.getenv("HANDA_FRONTEND_DIST")
  candidates: list[Path] = []
  if configured:
    candidates.append(Path(configured).expanduser())

  app_root = Path(__file__).resolve().parents[2]
  candidates.extend([
      app_root / "web_dist",
      app_root / "src" / "web" / "dist",
  ])

  for candidate in candidates:
    if (candidate / "index.html").is_file():
      return candidate.resolve()
  return None


def _mount_frontend(app: FastAPI) -> None:
  dist_dir = _frontend_dist_dir()
  if dist_dir is None:
    return

  assets_dir = dist_dir / "assets"
  if assets_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=assets_dir),
        name="frontend-assets",
    )

  @app.get("/", include_in_schema=False)
  async def frontend_index():
    return FileResponse(dist_dir / "index.html")

  @app.get("/{full_path:path}", include_in_schema=False)
  async def frontend_spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
      raise HTTPException(status_code=404, detail="Not Found")

    target = (dist_dir / full_path).resolve()
    if target.is_file() and _is_relative_to(target, dist_dir):
      return FileResponse(target)
    return FileResponse(dist_dir / "index.html")


def _is_relative_to(path: Path, parent: Path) -> bool:
  try:
    path.relative_to(parent)
    return True
  except ValueError:
    return False


def main() -> None:
  parser = argparse.ArgumentParser(description="Run Handa Web API.")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT)
  parser.add_argument(
      "--handa-dir",
      help="Product data directory. Defaults to ~/.handa.",
  )
  args = parser.parse_args()

  app = create_app(
      handa_dir=Path(args.handa_dir) if args.handa_dir else None,
  )
  uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
  main()
