from __future__ import annotations

import os
from pathlib import Path
import shutil


STORAGE_ROOT_ENV = "HANDA_STORAGE_ROOT"


def default_storage_root() -> Path:
  return Path.home() / ".handa"


def resolve_storage_root(root: Path | str | None = None) -> Path:
  if root is None:
    # Internal process-level bridge for runtime paths that cannot receive
    # handa_dir directly. Web startup should pass handa_dir explicitly.
    configured = os.getenv(STORAGE_ROOT_ENV)
    if configured:
      return Path(configured).expanduser().resolve()
    return default_storage_root().resolve()
  return Path(root).expanduser().resolve()


def sessions_dir(root: Path | str | None = None) -> Path:
  return resolve_storage_root(root) / "sessions"


def session_dir(root: Path | str | None, session_id: str) -> Path:
  return sessions_dir(root) / _safe_segment(session_id, "session_id")


def artifacts_dir(root: Path | str | None, session_id: str) -> Path:
  return session_dir(root, session_id) / "artifacts"


def attachments_dir(root: Path | str | None, session_id: str) -> Path:
  return session_dir(root, session_id) / "attachments"


def runtime_dir(root: Path | str | None, session_id: str, runtime: str) -> Path:
  return session_dir(root, session_id) / "runtime" / _safe_segment(runtime, "runtime")


def runtime_events_path(root: Path | str | None, session_id: str, runtime: str) -> Path:
  return runtime_dir(root, session_id, runtime) / "events.jsonl"


def browser_dir(root: Path | str | None, session_id: str) -> Path:
  return session_dir(root, session_id) / "browser"


def browser_profile_dir(root: Path | str | None, session_id: str) -> Path:
  return browser_dir(root, session_id) / "profile"


def browser_state_path(root: Path | str | None, session_id: str) -> Path:
  return browser_dir(root, session_id) / "state.json"


def browser_screenshot_path(root: Path | str | None, session_id: str) -> Path:
  return browser_dir(root, session_id) / "latest.png"


def browser_events_path(root: Path | str | None, session_id: str) -> Path:
  return browser_dir(root, session_id) / "events.jsonl"


def migrate_legacy_session_storage(root: Path | str | None = None) -> None:
  resolved = resolve_storage_root(root)
  current = resolved / "sessions"
  legacy = resolved / "threads"
  if not legacy.is_dir():
    current.mkdir(parents=True, exist_ok=True)
    return
  if not current.exists():
    legacy.rename(current)
    return
  current.mkdir(parents=True, exist_ok=True)
  for legacy_session in legacy.iterdir():
    target = current / legacy_session.name
    if target.exists():
      continue
    if legacy_session.is_dir():
      shutil.copytree(legacy_session, target)
    elif legacy_session.is_file():
      shutil.copy2(legacy_session, target)


def _safe_segment(value: str, field_name: str) -> str:
  if not value or value in {".", ".."}:
    raise ValueError(f"{field_name} must be a non-empty path segment")
  if "/" in value or "\\" in value or "\x00" in value:
    raise ValueError(f"{field_name} must not contain path separators")
  return value
