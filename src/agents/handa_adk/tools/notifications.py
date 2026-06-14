from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from ....runtime import list_task_events


def get(
    tool_context: ToolContext,
    unread_only: bool = True,
    limit: int = 20,
    mark_read: bool = True,
) -> dict[str, Any]:
  """Return recent structured task events for the current session."""
  state_key = "handa:last_seen_task_event_ts"
  after_ts = tool_context.state.get(state_key) if unread_only else None
  events = list_task_events(
      session_id=tool_context.session.id,
      after_ts=after_ts,
      limit=limit,
  )
  if mark_read and events:
    tool_context.state[state_key] = max(event["created_ts"] for event in events)
  return {
      "events": events,
      "count": len(events),
      "unread_only": unread_only,
  }
