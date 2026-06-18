from __future__ import annotations

import uuid
from typing import Any

from .task_store import now_iso


GOAL_STATE_KEY = "handa:goal"
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_ACHIEVED = "achieved"
GOAL_STATUS_BLOCKED = "blocked"
GOAL_STATUS_CANCELLED = "cancelled"
GOAL_STATUS_CLEARED = "cleared"
GOAL_STATUS_MAX_ATTEMPTS = "max_attempts"
DEFAULT_GOAL_MAX_ATTEMPTS = 5
MAX_GOAL_TEXT_CHARS = 4000


def active_goal_from_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
  raw = (state or {}).get(GOAL_STATE_KEY)
  if not isinstance(raw, dict):
    return None
  text = str(raw.get("text") or "").strip()
  if not text or str(raw.get("status") or GOAL_STATUS_ACTIVE) != GOAL_STATUS_ACTIVE:
    return None
  return {
      "goal_id": _optional_str(raw.get("goal_id")) or _new_goal_id(),
      "text": text,
      "status": GOAL_STATUS_ACTIVE,
      "created_turn_id": _optional_str(raw.get("created_turn_id")),
      "created_at": _optional_str(raw.get("created_at")),
      "updated_at": _optional_str(raw.get("updated_at")),
      "max_attempts": _coerce_positive_int(
          raw.get("max_attempts"),
          DEFAULT_GOAL_MAX_ATTEMPTS,
      ),
  }


def present_goal_from_state(
    state: dict[str, Any] | None,
    *,
    default_cleared: bool = False,
) -> dict[str, Any] | None:
  goal = active_goal_from_state(state)
  if goal is not None:
    return goal
  raw = (state or {}).get(GOAL_STATE_KEY)
  if isinstance(raw, dict):
    status = str(raw.get("status") or GOAL_STATUS_CLEARED)
    return {
        "goal_id": _optional_str(raw.get("goal_id")),
        "text": "" if status == GOAL_STATUS_CLEARED else str(raw.get("text") or ""),
        "status": status,
        "created_turn_id": _optional_str(raw.get("created_turn_id")),
        "created_at": _optional_str(raw.get("created_at")),
        "updated_at": _optional_str(raw.get("updated_at")),
        "max_attempts": _coerce_optional_positive_int(raw.get("max_attempts")),
        "reason": _optional_str(raw.get("reason")),
    }
  if not default_cleared:
    return None
  return {
      "goal_id": None,
      "text": "",
      "status": GOAL_STATUS_CLEARED,
      "created_turn_id": None,
      "created_at": None,
      "updated_at": None,
      "max_attempts": None,
      "reason": None,
  }


def goal_state_for_text(
    text: str,
    *,
    previous: dict[str, Any] | None = None,
    created_turn_id: str | None = None,
    max_attempts: int = DEFAULT_GOAL_MAX_ATTEMPTS,
) -> dict[str, Any]:
  normalized = text.strip()
  if not normalized:
    raise ValueError("Goal text must not be empty.")
  if len(normalized) > MAX_GOAL_TEXT_CHARS:
    raise ValueError(f"Goal text must be at most {MAX_GOAL_TEXT_CHARS} characters.")
  timestamp = now_iso()
  previous_goal = previous if isinstance(previous, dict) else {}
  return {
      "goal_id": _new_goal_id(),
      "text": normalized,
      "status": GOAL_STATUS_ACTIVE,
      "created_turn_id": _optional_str(created_turn_id)
      or _optional_str(previous_goal.get("created_turn_id")),
      "created_at": _optional_str(previous_goal.get("created_at")) or timestamp,
      "updated_at": timestamp,
      "max_attempts": _coerce_positive_int(max_attempts, DEFAULT_GOAL_MAX_ATTEMPTS),
  }


def cleared_goal_state(*, previous: dict[str, Any] | None = None) -> dict[str, Any]:
  timestamp = now_iso()
  previous_goal = previous if isinstance(previous, dict) else {}
  return {
      "goal_id": _optional_str(previous_goal.get("goal_id")),
      "text": "",
      "status": GOAL_STATUS_CLEARED,
      "created_turn_id": _optional_str(previous_goal.get("created_turn_id")),
      "created_at": _optional_str(previous_goal.get("created_at")),
      "updated_at": timestamp,
      "max_attempts": _coerce_positive_int(
          previous_goal.get("max_attempts"),
          DEFAULT_GOAL_MAX_ATTEMPTS,
      ),
  }


def finished_goal_state(
    goal: dict[str, Any],
    *,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
  timestamp = now_iso()
  text = str(goal.get("text") or "").strip()
  if not text:
    raise ValueError("Goal text must not be empty.")
  if status not in {
      GOAL_STATUS_ACHIEVED,
      GOAL_STATUS_BLOCKED,
      GOAL_STATUS_CANCELLED,
      GOAL_STATUS_MAX_ATTEMPTS,
  }:
    raise ValueError(f"Unsupported goal status: {status}")
  return {
      "goal_id": _optional_str(goal.get("goal_id")) or _new_goal_id(),
      "text": text,
      "status": status,
      "created_turn_id": _optional_str(goal.get("created_turn_id")),
      "created_at": _optional_str(goal.get("created_at")),
      "updated_at": timestamp,
      "max_attempts": _coerce_positive_int(
          goal.get("max_attempts"),
          DEFAULT_GOAL_MAX_ATTEMPTS,
      ),
      "reason": _optional_str(reason),
  }


def goal_prompt_prefix(goal: dict[str, Any] | None) -> str:
  if not goal:
    return ""
  text = str(goal.get("text") or "").strip()
  if not text:
    return ""
  return (
      "# Goal\n"
      f"{text}\n\n"
      "# Goal Instructions\n"
      "This message is a Goal.\n"
      "Work until the goal is actually satisfied or you are blocked.\n"
      "Before finalizing, explain why the goal is complete and point to "
      "concrete proof visible in this session.\n"
      "Proof can be anything visible in the session: command results, "
      "observations, artifacts, file changes, child-agent results, or other "
      "concrete work.\n"
      "Do not claim completion without proof.\n\n"
      "# User Message\n"
  )


def apply_goal_to_prompt(prompt: str, goal: dict[str, Any] | None) -> str:
  prefix = goal_prompt_prefix(goal)
  if not prefix:
    return prompt
  return f"{prefix}{prompt.strip() or '(empty request)'}"


def _optional_str(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _new_goal_id() -> str:
  return f"goal_{uuid.uuid4().hex[:12]}"


def _coerce_positive_int(value: Any, default: int) -> int:
  try:
    parsed = int(value)
  except (TypeError, ValueError):
    return default
  return parsed if parsed > 0 else default


def _coerce_optional_positive_int(value: Any) -> int | None:
  try:
    parsed = int(value)
  except (TypeError, ValueError):
    return None
  return parsed if parsed > 0 else None
