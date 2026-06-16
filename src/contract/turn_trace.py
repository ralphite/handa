from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from .run_events import serialize_event
from ..storage.runtime_event_store import RuntimeEventStore


WEB_EVENT_KIND_PREFIX = "web."

# Web-originated trace events (lifecycle steps, terminate/error markers) live
# in their own stream. Agent events are written to the native runtime stream.
WEB_TRACE_RUNTIME = "web"


def append_web_step_event(
    storage_root: Path | str | None,
    *,
    session_id: str,
    turn_id: str,
    kind: str,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
  """Append a web lifecycle step (user input, terminate, error) to the trace stream."""
  return RuntimeEventStore(storage_root).append(
      session_id=session_id,
      turn_id=turn_id,
      runtime=WEB_TRACE_RUNTIME,
      event={
          "kind": f"{WEB_EVENT_KIND_PREFIX}{kind}",
          "summary": summary,
          "payload": payload,
      },
  )


def append_runtime_trace_event(
    storage_root: Path | str | None,
    *,
    session_id: str,
    turn_id: str,
    runtime: str,
    event: Any,
) -> dict[str, Any]:
  """Append a native runtime event to its trace stream."""
  raw_event = jsonable_event(event)
  event_id = _optional_str(raw_event.get("id")) or f"{runtime}_evt_{uuid.uuid4().hex[:12]}"
  raw_event["id"] = event_id
  return RuntimeEventStore(storage_root).append(
      session_id=session_id,
      turn_id=turn_id,
      runtime=runtime,
      event_id=event_id,
      created_at=_optional_str(raw_event.get("created_at") or raw_event.get("timestamp")),
      event=raw_event,
  )


def jsonable_event(event: Any) -> dict[str, Any]:
  if isinstance(event, dict):
    return dict(event)
  serialized = serialize_event(event)
  return serialized if isinstance(serialized, dict) else {"value": serialized}


def _optional_str(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None
