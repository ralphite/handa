"""Storage service construction: the stateful half of the contract surface.

HandaServices bundles the file-backed session and artifact services bound to a
resolved storage root. Pure storage wiring — no agent or model code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ..storage import HandaArtifactService
from ..storage import HandaSessionService
from ..storage.paths import resolve_storage_root


APP_NAME = "handa"
DEFAULT_USER_ID = "user"


@dataclass(frozen=True)
class HandaServices:
  """Process-wide Handa storage services bound to one resolved handa_dir."""

  storage_root: Path
  session_service: HandaSessionService
  artifact_service: HandaArtifactService


def configure_base_environment() -> None:
  load_dotenv()
  if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def create_handa_services(handa_dir: Path | str | None = None) -> HandaServices:
  configure_base_environment()
  resolved_storage_root = resolve_storage_root(handa_dir)
  os.environ["HANDA_STORAGE_ROOT"] = str(resolved_storage_root)
  return HandaServices(
      storage_root=resolved_storage_root,
      session_service=HandaSessionService(root=str(resolved_storage_root)),
      artifact_service=HandaArtifactService(root=str(resolved_storage_root)),
  )
