from __future__ import annotations

from datetime import datetime
from datetime import timezone
import re
from typing import Any


PROGRESS_STATE_KEY = "handa:progress"
PROGRESS_STATUSES = {"pending", "running", "done", "failed"}

_STATUS_ALIASES = {
    "active": "running",
    "cancelled": "failed",
    "canceled": "failed",
    "complete": "done",
    "completed": "done",
    "error": "failed",
    "errored": "failed",
    "in-progress": "running",
    "in_progress": "running",
    "not_started": "pending",
    "not-started": "pending",
    "open": "pending",
    "pass": "done",
    "passed": "done",
    "queued": "pending",
    "started": "running",
    "success": "done",
    "succeeded": "done",
    "todo": "pending",
    "waiting": "pending",
}


def progress_timestamp() -> str:
  return datetime.now(tz=timezone.utc).isoformat()


def replace_progress_items(
    items: Any,
    *,
    existing_items: Any = None,
    source_turn_id: str | None = None,
) -> list[dict[str, Any]]:
  if not isinstance(items, list):
    raise ValueError("progress items must be a list")
  return normalize_progress_items(
      items,
      existing_items=existing_items,
      timestamp=progress_timestamp(),
      source_turn_id=source_turn_id,
  )


def normalize_progress_items(
    items: Any,
    *,
    existing_items: Any = None,
    timestamp: str | None = None,
    source_turn_id: str | None = None,
) -> list[dict[str, Any]]:
  if not isinstance(items, list):
    return []

  existing_by_id = {
      str(item.get("id")): item
      for item in existing_items
      if isinstance(item, dict) and item.get("id")
  } if isinstance(existing_items, list) else {}

  result: list[dict[str, Any]] = []
  seen: dict[str, int] = {}
  for index, item in enumerate(items):
    if not isinstance(item, dict):
      continue
    normalized = _normalize_item(
        item,
        index=index,
        existing_by_id=existing_by_id,
        timestamp=timestamp,
        source_turn_id=source_turn_id,
    )
    if normalized is None:
      continue
    item_id = normalized["id"]
    duplicate_count = seen.get(item_id, 0)
    seen[item_id] = duplicate_count + 1
    if duplicate_count:
      normalized["id"] = f"{item_id}-{duplicate_count + 1}"
    result.append(normalized)
  return result


def _normalize_item(
    item: dict[str, Any],
    *,
    index: int,
    existing_by_id: dict[str, dict[str, Any]],
    timestamp: str | None,
    source_turn_id: str | None,
) -> dict[str, Any] | None:
  title = _clean_str(item.get("title") or item.get("summary") or item.get("label"))
  if not title:
    return None
  item_id = _clean_str(item.get("id")) or _slug(title) or f"item-{index + 1}"
  status = normalize_progress_status(item.get("status"))
  detail = _clean_str(item.get("detail") or item.get("description"))
  previous = existing_by_id.get(item_id)
  changed = (
      previous is None
      or _clean_str(previous.get("title")) != title
      or normalize_progress_status(previous.get("status")) != status
      or _clean_str(previous.get("detail")) != detail
  )
  updated_at = (
      timestamp
      if timestamp and changed
      else _clean_str((previous or {}).get("updated_at"))
      or _clean_str(item.get("updated_at"))
      or timestamp
  )
  stored_source_turn_id = (
      source_turn_id
      if source_turn_id and (changed or not previous)
      else _clean_str((previous or {}).get("source_turn_id"))
      or _clean_str(item.get("source_turn_id"))
      or source_turn_id
  )

  return {
      "id": item_id,
      "title": title,
      "status": status,
      "detail": detail or None,
      "updated_at": updated_at or None,
      "source_turn_id": stored_source_turn_id or None,
  }


def normalize_progress_status(value: Any) -> str:
  text = _clean_str(value).lower()
  if text in PROGRESS_STATUSES:
    return text
  return _STATUS_ALIASES.get(text, "pending")


def _clean_str(value: Any) -> str:
  if value is None:
    return ""
  return str(value).strip()


def _slug(value: str) -> str:
  slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower())
  return slug.strip("-")[:48]
