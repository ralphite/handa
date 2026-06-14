from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contract.services import DEFAULT_USER_ID


@dataclass(frozen=True)
class WebSettings:
  """Resolved product data paths for this Web API process."""

  storage_root: Path
  sqlite_path: Path
  user_id: str = DEFAULT_USER_ID


def create_web_settings(
    *,
    storage_root: Path,
) -> WebSettings:
  return WebSettings(
      storage_root=storage_root,
      sqlite_path=storage_root / "handa.sqlite3",
  )
