from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext


def add(summary: str, tool_context: ToolContext) -> dict[str, Any]:
  """Create a lightweight note in the current session state."""
  notes = list(tool_context.state.get("handa:notes", []))
  note = {
      "summary": summary,
      "session_id": tool_context.session.id,
  }
  notes.append(note)
  tool_context.state["handa:notes"] = notes
  return {"success": True, "note": note}
