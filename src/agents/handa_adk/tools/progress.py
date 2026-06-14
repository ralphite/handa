from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from ....progress import PROGRESS_STATE_KEY
from ....progress import replace_progress_items


def update(
    items: list[dict[str, Any]],
    tool_context: ToolContext,
) -> dict[str, Any]:
  """Replace the current session-level progress checklist shown in the Web UI.

  Each item should include a stable `id`, a short `title`, and a `status` of
  pending, running, done, or failed. The checklist represents the current
  session progress, so send the full list whenever it changes.
  """
  current = tool_context.state.get(PROGRESS_STATE_KEY)
  source_turn_id = str(tool_context.state.get("handa:active_turn_id") or "").strip() or None
  progress_items = replace_progress_items(
      items,
      existing_items=current,
      source_turn_id=source_turn_id,
  )
  tool_context.state[PROGRESS_STATE_KEY] = progress_items
  # Do not echo the full `progress_items` back: the model already supplied them
  # in the call args, the canonical copy is persisted to session state for the
  # Web UI, and replaying the normalized list every call bloats history.
  return {
      "success": True,
      "count": len(progress_items),
  }
