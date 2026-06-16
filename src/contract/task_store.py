"""Run-record and task storage: the control half of the web↔runtime contract.

Owns the task file layout under sessions/<sid>/tasks/<task_id>/ (record, log,
result, events, notifications), liveness/cancel semantics (killpg + zombie
reaping), and web-turn worker spawn/resume. Extracted from src/runtime.py so
the Web API depends on this module instead of the runtime grab-bag.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import uuid

from ..storage.file_io import atomic_write_text
from ..storage.file_io import file_lock
from ..storage.paths import resolve_storage_root
from ..storage.paths import session_dir
from ..storage.paths import sessions_dir

LIVE_TASK_STATUSES = {"queued", "running", "waiting"}
# Task kinds that represent a spawned child agent run (as opposed to background
# commands). Mirrors web.api.session_detail.AGENT_TASK_KINDS, kept here so this
# lower-level store can walk the parent->child run tree without importing the
# web layer.
AGENT_RUN_TASK_KINDS = {"agent_run", "run_agent", "system_agent_run"}

def get_product_root() -> Path:
  # repo root: src/contract/task_store.py -> src/contract -> src -> root
  return Path(__file__).resolve().parent.parent.parent

def get_storage_root() -> Path:
  return resolve_storage_root()

def get_session_storage_dir(
    session_id: str,
) -> Path:
  return session_dir(get_storage_root(), session_id)

def get_tasks_dir(
    session_id: str,
) -> Path:
  return get_session_storage_dir(session_id) / "tasks"

def get_task_events_path(
    session_id: str,
) -> Path:
  return get_tasks_dir(session_id) / "task_events.jsonl"

def get_task_notifications_path(
    session_id: str,
) -> Path:
  return get_tasks_dir(session_id) / "task_notifications.json"

def ensure_task_dirs(
    session_id: str,
) -> None:
  tasks_dir = get_tasks_dir(session_id)
  tasks_dir.mkdir(parents=True, exist_ok=True)
  events_path = get_task_events_path(session_id)
  with file_lock(_task_events_lock_path(session_id)):
    if not events_path.exists():
      atomic_write_text(events_path, "")
  notifications_path = get_task_notifications_path(session_id)
  with file_lock(_task_notifications_lock_path(session_id)):
    if not notifications_path.exists():
      atomic_write_text(notifications_path, "[]\n")

def now_ts() -> float:
  return time.time()

def now_iso() -> str:
  return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts()))

def append_task_event(
    kind: str,
    summary: str,
    *,
    session_id: str,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
  ensure_task_dirs(session_id)
  event = {
      "id": f"evt_{uuid.uuid4().hex[:12]}",
      "created_ts": now_ts(),
      "created_at": now_iso(),
      "kind": kind,
      "session_id": session_id,
      "summary": summary,
      "task_id": task_id,
      "payload": payload or {},
  }
  with file_lock(_task_events_lock_path(session_id)):
    with get_task_events_path(session_id).open(
        "a",
        encoding="utf-8",
    ) as handle:
      handle.write(json.dumps(event, ensure_ascii=True) + "\n")
  return event

def list_task_events(
    *,
    session_id: str,
    after_ts: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
  ensure_task_dirs(session_id)
  events: list[dict[str, Any]] = []
  events_path = get_task_events_path(session_id)
  for line in events_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    event = json.loads(line)
    if after_ts is not None and event["created_ts"] <= after_ts:
      continue
    events.append(event)
  return events[-max(1, min(limit, 200)) :]

def list_task_notifications(
    *,
    session_id: str,
) -> list[dict[str, Any]]:
  ensure_task_dirs(session_id)
  return _read_task_notifications_unlocked(session_id)

def save_task_notifications(
    notifications: list[dict[str, Any]],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
  ensure_task_dirs(session_id)
  with file_lock(_task_notifications_lock_path(session_id)):
    _write_task_notifications_unlocked(notifications, session_id=session_id)
  return notifications

def create_task_notification(
    *,
    session_id: str,
    task_id: str,
    source_event_id: str,
    source_event_kind: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
  ensure_task_dirs(session_id)
  with file_lock(_task_notifications_lock_path(session_id)):
    notifications = _read_task_notifications_unlocked(session_id)
    for notification in notifications:
      if notification.get("source_event_id") == source_event_id:
        return notification
    notification = {
        "id": f"ntf_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "task_id": task_id,
        "source_event_id": source_event_id,
        "source_event_kind": source_event_kind,
        "status": "pending",
        "created_at": now_iso(),
        "created_ts": now_ts(),
        "delivered_at": None,
        "delivered_turn_id": None,
        "payload": payload or {},
    }
    notifications.append(notification)
    _write_task_notifications_unlocked(notifications, session_id=session_id)
    return notification

def update_task_notification(
    notification_id: str,
    *,
    session_id: str,
    **fields: Any,
) -> dict[str, Any]:
  ensure_task_dirs(session_id)
  with file_lock(_task_notifications_lock_path(session_id)):
    notifications = _read_task_notifications_unlocked(session_id)
    for index, notification in enumerate(notifications):
      if notification.get("id") == notification_id:
        updated = {**notification, **fields}
        notifications[index] = updated
        _write_task_notifications_unlocked(notifications, session_id=session_id)
        return updated
  raise KeyError(f"Task notification not found: {notification_id}")

def task_dir(
    task_id: str,
    *,
    session_id: str,
) -> Path:
  return get_tasks_dir(session_id) / task_id

def task_file(
    task_id: str,
    *,
    session_id: str,
) -> Path:
  return task_dir(task_id, session_id=session_id) / "task.json"

def task_log_file(
    task_id: str,
    *,
    session_id: str,
) -> Path:
  return task_dir(task_id, session_id=session_id) / "stdout.log"

def load_task(
    task_id: str,
    *,
    session_id: str,
) -> dict[str, Any]:
  return json.loads(
      task_file(
          task_id,
          session_id=session_id,
      ).read_text(encoding="utf-8")
  )

def save_task(
    task: dict[str, Any],
) -> dict[str, Any]:
  path = task_file(
      task["id"],
      session_id=task["session_id"],
  )
  path.parent.mkdir(parents=True, exist_ok=True)
  with file_lock(_task_lock_path(task["id"], session_id=task["session_id"])):
    atomic_write_text(path, json.dumps(task, indent=2, ensure_ascii=True) + "\n")
  return task

def list_tasks(
    *,
    session_id: str,
) -> list[dict[str, Any]]:
  ensure_task_dirs(session_id)
  tasks: list[dict[str, Any]] = []
  for entry in sorted(get_tasks_dir(session_id).glob("*/task.json")):
    tasks.append(json.loads(entry.read_text(encoding="utf-8")))
  tasks.sort(key=lambda item: item["created_ts"], reverse=True)
  return tasks

def cancel_stale_live_tasks() -> int:
  """Cancel live task records whose worker process is no longer running."""
  changed = 0
  finished_at = now_iso()
  task_files = sorted(sessions_dir(get_storage_root()).glob("*/tasks/*/task.json"))
  for path in task_files:
    try:
      task = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
      continue
    if task.get("status") not in {"queued", "running"}:
      continue
    if _is_process_alive(task.get("worker_pid")):
      continue
    task["status"] = "cancelled"
    task["returncode"] = 1
    task["finished_at"] = task.get("finished_at") or finished_at
    task["cancel_requested_at"] = task.get("cancel_requested_at") or finished_at
    save_task(task)
    # Startup cleanup should not enqueue a parent-agent notification.
    append_task_event(
        "task.stale_cancelled",
        f"Task {task['id']} cancelled after Web API restart",
        session_id=task["session_id"],
        task_id=task["id"],
        payload={"reason": "stale_worker"},
    )
    changed += 1
  return changed

def start_web_turn_task(
    *,
    session_id: str,
    turn_id: str,
    project_root: str,
    agent_id: str,
    agent_runtime: str,
    input_text: str,
    user_id: str,
    model_config_id: str | None = None,
    streaming_mode_enabled: bool = True,
    attachments: list[dict[str, Any]] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
  """Persist a web turn run record and spawn its worker process."""
  task = create_web_turn_task(
      session_id=session_id,
      turn_id=turn_id,
      project_root=project_root,
      agent_id=agent_id,
      agent_runtime=agent_runtime,
      input_text=input_text,
      user_id=user_id,
      model_config_id=model_config_id,
      streaming_mode_enabled=streaming_mode_enabled,
      attachments=attachments,
  )
  return spawn_web_turn_worker(task, extra_env=extra_env)

def create_web_turn_task(
    *,
    session_id: str,
    turn_id: str,
    project_root: str,
    agent_id: str,
    agent_runtime: str,
    input_text: str,
    user_id: str,
    model_config_id: str | None = None,
    streaming_mode_enabled: bool = True,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  """Persist a web turn run record without spawning the worker.

  The record carries everything the worker needs so it never reads the Web
  API's sqlite; the Web layer mirrors worker-owned status back from this file.
  """
  ensure_task_dirs(session_id)
  task = {
      "id": turn_id,
      "session_id": session_id,
      "kind": "web_turn",
      "project_root": project_root,
      "agent_id": agent_id,
      "agent_runtime": agent_runtime,
      "model_config_id": model_config_id,
      "input_text": input_text,
      "attachments": attachments or [],
      "streaming_mode_enabled": streaming_mode_enabled,
      "resume_user_input": None,
      "user_id": user_id,
      "summary": f"Web turn {turn_id}",
      "status": "queued",
      "created_at": now_iso(),
      "created_ts": now_ts(),
      "started_at": None,
      "finished_at": None,
      "returncode": None,
      "worker_pid": None,
      "command_pid": None,
      "cancel_requested_at": None,
      "final_text": None,
      "error_type": None,
      "error_message": None,
      "pending_user_input": None,
      "log_path": str(task_log_file(turn_id, session_id=session_id)),
  }
  save_task(task)
  append_task_event(
      "web_turn.created",
      f"Web turn {turn_id} created",
      session_id=session_id,
      task_id=turn_id,
  )
  return task

def resume_web_turn_task(
    *,
    session_id: str,
    turn_id: str,
    resume_user_input: dict[str, Any],
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
  """Re-spawn the worker for a turn that paused on user input."""
  task = load_task(turn_id, session_id=session_id)
  if task.get("status") not in {"waiting", "waiting_input"}:
    raise ValueError(f"Turn run is not waiting for user input: {task.get('status')}")
  task["resume_user_input"] = resume_user_input
  task["pending_user_input"] = None
  task["status"] = "queued"
  task["finished_at"] = None
  task["returncode"] = None
  save_task(task)
  return spawn_web_turn_worker(task, extra_env=extra_env)

def spawn_web_turn_worker(
    task: dict[str, Any],
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
  env = os.environ.copy()
  env["HANDA_STORAGE_ROOT"] = str(get_storage_root())
  env.update(extra_env or {})
  process = subprocess.Popen(
      [sys.executable, "-m", "src.turn_worker", task["session_id"], task["id"]],
      cwd=str(get_product_root()),
      env=env,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
  )
  task["worker_pid"] = process.pid
  save_task(task)
  return task

def is_process_alive(pid: Any) -> bool:
  return _is_process_alive(pid)

def task_result_file(
    task_id: str,
    *,
    session_id: str,
) -> Path:
  return task_dir(task_id, session_id=session_id) / "result.json"

def read_task_result(
    task_id: str,
    *,
    session_id: str,
) -> dict[str, Any]:
  task = load_task(task_id, session_id=session_id)
  result_path = task_result_file(task_id, session_id=session_id)
  if not result_path.exists():
    return {"found": False, "task_id": task_id, "task": task}
  return {
      "found": True,
      "task_id": task_id,
      "task": task,
      "result": json.loads(result_path.read_text(encoding="utf-8")),
  }

def agent_run_report_label(task: dict[str, Any]) -> str:
  return task.get("agent_id") or task.get("config_name") or task["id"]

def _fenced_block(text: str) -> str:
  """Wrap arbitrary text in a code fence longer than any backtick run inside it.

  The prompt and result are quoted verbatim and may themselves contain ```code
  fences```; a fixed 3-backtick fence would let them break out of the block.
  Using `max(longest_run + 1, 3)` backticks guarantees no inner line can close
  the fence early.
  """
  longest = 0
  current = 0
  for char in text:
    if char == "`":
      current += 1
      longest = max(longest, current)
    else:
      current = 0
  fence = "`" * max(longest + 1, 3)
  return f"{fence}\n{text}\n{fence}"


def build_agent_run_report(task: dict[str, Any], final_text: str) -> str:
  """Render the parent-facing Agent Run summary as Markdown.

  Built line-by-line (not via an indented f-string + ``textwrap.dedent``):
  interpolating a multi-line prompt/result into an indented template leaves the
  template lines indented because ``dedent`` finds no common leading whitespace,
  and Markdown then renders those 4+ space lines as stray code blocks.
  """
  label = agent_run_report_label(task)
  lines = [
      f"# Agent Run: {label}",
      "",
      f"- parent_task_id: `{task['id']}`",
      f"- child_session_id: `{task['child_session_id']}`",
      f"- status: `{task['status']}`",
      f"- kind: `{task['kind']}`",
      f"- runtime: `{task.get('agent_runtime', 'native')}`",
      "",
      "## Prompt",
      "",
      _fenced_block(str(task["prompt"])),
      "",
      "## Result",
      "",
      _fenced_block(final_text or "(no final text)"),
      "",
  ]
  return "\n".join(lines)

def cancel_task(
    task_id: str,
    *,
  session_id: str,
) -> dict[str, Any]:
  task = load_task(task_id, session_id=session_id)
  if task["status"] not in {"queued", "running", "waiting"}:
    return {"success": False, "task_id": task_id, "error": f"task is {task['status']}"}
  pid = task.get("command_pid") or task.get("worker_pid")
  if task["status"] == "waiting" and not pid:
    task["cancel_requested_at"] = now_iso()
    task["finished_at"] = task["cancel_requested_at"]
    task["status"] = "cancelled"
    task["returncode"] = 1
    save_task(task)
    append_task_event(
        "task.cancelled",
        f"Task {task_id} cancelled",
        session_id=session_id,
        task_id=task_id,
    )
    return {"success": True, "task_id": task_id}
  if not pid:
    return {"success": False, "task_id": task_id, "error": "no pid recorded"}
  try:
    os.killpg(pid, 15)
  except ProcessLookupError:
    pass
  except PermissionError as exc:
    return {"success": False, "task_id": task_id, "error": str(exc)}
  task["cancel_requested_at"] = now_iso()
  task["finished_at"] = task["cancel_requested_at"]
  task["status"] = "cancelled"
  save_task(task)
  append_task_event(
      "task.cancel_requested",
      f"Task {task_id} cancel requested",
      session_id=session_id,
      task_id=task_id,
  )
  return {"success": True, "task_id": task_id}

def cancel_descendant_runs(
    session_id: str,
    *,
    _visited: set[str] | None = None,
) -> int:
  """Recursively cancel live agent-run child tasks spawned under a session.

  Terminating a parent turn/run otherwise leaves its in-flight child agent runs
  as orphan worker process groups: they run to completion and post a result
  notification to a parent that no longer exists. Each worker is spawned with
  start_new_session=True (its own process group), so killpg on the parent never
  reaches them. Walk the parent->child task tree -- a child task's own sub-runs
  live under its child_session_id -- and cancel every live agent-run task.

  Returns the count of descendant tasks cancelled. Best-effort: a missing record
  or dead process for one child never blocks cancelling the rest.
  """
  visited = _visited if _visited is not None else set()
  if session_id in visited:
    return 0
  visited.add(session_id)
  # Guard on existence so we never call list_tasks (which would create empty
  # task dirs) for leaf child sessions that spawned nothing.
  if not get_tasks_dir(session_id).exists():
    return 0
  try:
    tasks = list_tasks(session_id=session_id)
  except (OSError, ValueError):
    return 0
  cancelled = 0
  for task in tasks:
    if task.get("kind") not in AGENT_RUN_TASK_KINDS:
      continue
    if task.get("status") in LIVE_TASK_STATUSES:
      try:
        if cancel_task(task["id"], session_id=session_id).get("success"):
          cancelled += 1
      except (FileNotFoundError, KeyError, ValueError, OSError):
        pass
    child_session_id = task.get("child_session_id")
    if child_session_id:
      cancelled += cancel_descendant_runs(str(child_session_id), _visited=visited)
  return cancelled

def _task_events_lock_path(session_id: str) -> Path:
  return get_tasks_dir(session_id) / ".task_events.jsonl.lock"

def _task_notifications_lock_path(session_id: str) -> Path:
  return get_tasks_dir(session_id) / ".task_notifications.json.lock"

def _task_lock_path(task_id: str, *, session_id: str) -> Path:
  return task_dir(task_id, session_id=session_id) / ".task.json.lock"

def _read_task_notifications_unlocked(session_id: str) -> list[dict[str, Any]]:
  path = get_task_notifications_path(session_id)
  if not path.exists():
    return []
  return json.loads(path.read_text(encoding="utf-8") or "[]")

def _write_task_notifications_unlocked(
    notifications: list[dict[str, Any]],
    *,
    session_id: str,
) -> None:
  atomic_write_text(
      get_task_notifications_path(session_id),
      json.dumps(notifications, indent=2, ensure_ascii=True) + "\n",
  )

def _is_process_alive(pid: Any) -> bool:
  try:
    value = int(pid)
  except (TypeError, ValueError):
    return False
  if value <= 0:
    return False
  # A worker we spawned and never waited on lingers as a zombie that still
  # answers kill(pid, 0); reap it so liveness reflects reality.
  try:
    reaped, _ = os.waitpid(value, os.WNOHANG)
    if reaped == value:
      return False
  except (ChildProcessError, OSError):
    pass
  try:
    os.kill(value, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  return True
