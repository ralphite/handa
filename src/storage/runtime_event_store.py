from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from datetime import timezone
from typing import Any
import uuid

from .file_io import file_lock
from .paths import resolve_storage_root
from .paths import runtime_events_path


class RuntimeEventStore:
  """Append-only JSONL trace store for framework runtime events."""

  def __init__(self, root: Path | str | None = None):
    self.root = resolve_storage_root(root)

  def append(
      self,
      *,
      session_id: str,
      runtime: str,
      event: dict[str, Any],
      turn_id: str | None = None,
      event_id: str | None = None,
      created_at: str | None = None,
  ) -> dict[str, Any]:
    path = self._path(session_id, runtime)
    lock_path = self._lock_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_event = dict(event)
    resolved_event_id = (
        event_id
        or _optional_str(resolved_event.get("id"))
        or _optional_str(resolved_event.get("event_id"))
        or _optional_str(resolved_event.get("eventId"))
        or f"evt_{uuid.uuid4().hex[:12]}"
    )
    resolved_event["id"] = resolved_event_id
    with file_lock(lock_path):
      envelope = {
          "id": resolved_event_id,
          "session_id": session_id,
          "turn_id": turn_id,
          "created_at": created_at or _now_iso(),
          "event": resolved_event,
      }
      line = json.dumps(envelope, ensure_ascii=True, default=str).encode("utf-8") + b"\n"
      with path.open("ab") as handle:
        handle.write(line)
        handle.flush()
    return envelope

  def list_events(
      self,
      *,
      session_id: str,
      runtime: str,
  ) -> list[dict[str, Any]]:
    path = self._path(session_id, runtime)
    if not path.exists():
      return []
    events: list[dict[str, Any]] = []
    with path.open("rb") as handle:
      for raw_line in handle:
        line = raw_line.strip()
        if not line:
          continue
        try:
          item = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
          continue
        if isinstance(item, dict):
            events.append(item)
    return events

  def replace_events(
      self,
      *,
      session_id: str,
      runtime: str,
      events: list[dict[str, Any]],
  ) -> None:
    path = self._path(session_id, runtime)
    lock_path = self._lock_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with file_lock(lock_path):
      with tmp_path.open("wb") as handle:
        for event in events:
          line = json.dumps(event, ensure_ascii=True, default=str).encode("utf-8") + b"\n"
          handle.write(line)
      tmp_path.replace(path)

  def identity_index(
      self,
      *,
      session_id: str,
      runtime: str,
  ) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in self.list_events(session_id=session_id, runtime=runtime):
      key = _optional_str(event.get("id") or event.get("runtime_event_id"))
      if key:
        result.setdefault(key, event)
    return result

  def _path(self, session_id: str, runtime: str) -> Path:
    return runtime_events_path(self.root, session_id, runtime)

  @staticmethod
  def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")

def _optional_str(value: Any) -> str | None:
  return None if value is None else str(value)


def _now_iso() -> str:
  return datetime.now(tz=timezone.utc).isoformat()
