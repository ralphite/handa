"""Finalize a parent agent-run task when its child session's turn ends.

Agent runs delegate work to a child session; the parent task stays `waiting`
until the child's web turn reaches a terminal state. The turn worker calls
this on its way out (normal path); the Web API's background loop calls it as
the fallback when no worker survived to do it (terminate, orphan cleanup).
A dedicated finalize lock makes the two callers race-safe.
"""
from __future__ import annotations

import json
from typing import Any

from ..storage.file_io import file_lock
from .services import APP_NAME
from .services import HandaServices
from .task_store import agent_run_report_label
from .task_store import append_task_event
from .task_store import build_agent_run_report
from .task_store import LIVE_TASK_STATUSES
from .task_store import list_tasks
from .task_store import load_task
from .task_store import now_iso
from .task_store import save_task
from .task_store import task_dir
from .task_store import task_result_file


async def finalize_parent_agent_task(
    services: HandaServices,
    *,
    user_id: str,
    child_session_id: str,
    turn_status: str,
    final_text: str | None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> bool:
  """Returns True when this call resolved the parent task."""
  state = services.session_service.read_state_sync(child_session_id)
  parent_session_id = _state_str(state, "handa:parent_session_id")
  parent_task_id = _state_str(state, "handa:parent_task_id")
  if not parent_session_id or not parent_task_id:
    return False
  try:
    task = load_task(parent_task_id, session_id=parent_session_id)
  except (FileNotFoundError, KeyError, ValueError):
    return False
  if task.get("status") != "waiting":
    return False
  if _has_live_child_tasks(child_session_id):
    return False

  if turn_status == "completed":
    resolved_text = str(final_text or "")
  elif turn_status == "cancelled":
    resolved_text = "Turn terminated."
  else:
    resolved_text = str(error_message or "Turn failed.")

  finalize_lock = (
      task_dir(parent_task_id, session_id=parent_session_id) / ".finalize.lock"
  )
  with file_lock(finalize_lock):
    # Re-check under the lock: the worker and the Web fallback may both get
    # here; only the first writes the outcome.
    try:
      task = load_task(parent_task_id, session_id=parent_session_id)
    except (FileNotFoundError, KeyError, ValueError):
      return False
    if task.get("status") != "waiting":
      return False

    finished_at = now_iso()
    task["finished_at"] = finished_at
    if turn_status == "completed":
      task["status"] = "succeeded"
      task["returncode"] = 0
      event_kind = "task.completed"
      event_summary = f"{task['kind']} {parent_task_id} completed"
      result: dict[str, Any] = {
          "success": True,
          "task_id": parent_task_id,
          "kind": task["kind"],
          "agent_id": task.get("agent_id"),
          "config_name": task.get("config_name"),
          "child_session_id": child_session_id,
          "final_text": resolved_text,
      }
    else:
      task["status"] = "cancelled" if turn_status == "cancelled" else "failed"
      task["returncode"] = 1
      event_kind = f"task.{task['status']}"
      event_summary = f"{task['kind']} {parent_task_id} {task['status']}"
      result = {
          "success": False,
          "task_id": parent_task_id,
          "kind": task["kind"],
          "agent_id": task.get("agent_id"),
          "config_name": task.get("config_name"),
          "child_session_id": child_session_id,
          "final_text": resolved_text,
          "error": {
              "type": str(error_type or "TurnFailed"),
              "message": str(error_message or resolved_text),
          },
      }

    summary_artifact = None
    if task.get("save_parent_summary", True) and turn_status == "completed":
      artifact_name = f"{task['kind']}_{agent_run_report_label(task)}.report.md"
      await services.artifact_service.save_text_artifact(
          app_name=APP_NAME,
          user_id=user_id,
          session_id=parent_session_id,
          filename=artifact_name,
          text=build_agent_run_report(task, resolved_text),
      )
      summary_artifact = artifact_name
    task["summary_artifact"] = summary_artifact
    result["summary_artifact"] = summary_artifact
    task_result_file(parent_task_id, session_id=parent_session_id).write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    save_task(task)
    append_task_event(
        event_kind,
        event_summary,
        session_id=parent_session_id,
        task_id=parent_task_id,
        payload={"child_session_id": child_session_id},
    )
  return True


def _has_live_child_tasks(session_id: str) -> bool:
  return any(
      task.get("status") in LIVE_TASK_STATUSES
      for task in list_tasks(session_id=session_id)
  )


def _state_str(state: dict[str, Any], key: str) -> str | None:
  value = state.get(key)
  if value is None:
    return None
  text = str(value).strip()
  return text or None
