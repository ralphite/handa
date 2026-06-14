from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Storage wiring moved to the contract package; re-exported for runtime-side
# callers (workers, CLI). Web code imports src.contract.services directly.
from .contract.services import APP_NAME as APP_NAME
from .contract.services import configure_base_environment as configure_base_environment
from .contract.services import create_handa_services as create_handa_services
from .contract.services import DEFAULT_USER_ID as DEFAULT_USER_ID
from .contract.services import HandaServices as HandaServices
from .storage.paths import resolve_storage_root


@dataclass(frozen=True)
class HandaApp:
  project_root: Path
  services: HandaServices


def configure_handa_environment(
    project_root: Path | str,
    handa_dir: Path | str | None = None,
) -> tuple[Path, Path]:
  import os

  configure_base_environment()
  project_path = Path(project_root).expanduser().resolve()
  storage_root = resolve_storage_root(handa_dir)
  os.environ["HANDA_PROJECT_ROOT"] = str(project_path)
  os.environ["HANDA_STORAGE_ROOT"] = str(storage_root)
  return project_path, storage_root


def create_handa_app(
    project_root: Path | str,
    handa_dir: Path | str | None = None,
) -> HandaApp:
  project_path, storage_root = configure_handa_environment(project_root, handa_dir)
  return HandaApp(
      project_root=project_path,
      services=create_handa_services(storage_root),
  )


def create_runner(services: HandaServices, agent, *, app_name: str = APP_NAME):
  from google.adk.runners import Runner

  return Runner(
      app_name=app_name,
      agent=agent,
      artifact_service=services.artifact_service,
      session_service=services.session_service,
  )
