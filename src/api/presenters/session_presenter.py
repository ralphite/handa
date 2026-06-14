from __future__ import annotations

from typing import Any

from ...contract.product import DEFAULT_AGENT_ID
from ..schemas import SessionSummary


def present_session(
    meta: dict[str, Any],
    *,
    status: str,
    updated_at: str,
    automated_task_id: str | None = None,
) -> SessionSummary:
  agent_id = str(meta.get("agent_id") or DEFAULT_AGENT_ID)
  starred_at = meta.get("starred_at")
  unread_at = meta.get("unread_at")
  return SessionSummary(
      id=str(meta["id"]),
      session_id=str(meta["id"]),
      title=session_display_title(meta),
      agent_id=agent_id,
      agent_runtime=str(meta.get("agent_runtime") or "adk"),
      automated_task_id=automated_task_id,
      project_id=meta.get("project_id"),
      status=status,
      created_at=str(meta["created_at"]),
      updated_at=updated_at,
      parent_session_id=meta.get("parent_session_id"),
      forked_from_session_id=meta.get("forked_from_session_id"),
      forked_from_turn_id=meta.get("forked_from_turn_id"),
      forked_at=meta.get("forked_at"),
      starred=bool(starred_at),
      starred_at=starred_at,
      archived_at=meta.get("archived_at"),
      unread=bool(unread_at),
      unread_at=unread_at,
  )


def session_display_title(meta: dict[str, Any]) -> str:
  title = str(meta.get("title") or "").strip()
  return title or "New session"
