from __future__ import annotations

import subprocess
import sys

from .runtime import append_task_event
from .runtime import get_project_root
from .runtime import load_task
from .runtime import now_iso
from .runtime import save_task
from .runtime import task_log_file
from .runtime import project_context


def main(session_id: str, task_id: str) -> int:
  task = load_task(task_id, session_id=session_id)
  if task.get("project_root"):
    with project_context(task["project_root"]):
      return _run_task(session_id, task_id, task)
  return _run_task(session_id, task_id, task)


def _run_task(session_id: str, task_id: str, task: dict) -> int:
  task["status"] = "running"
  task["started_at"] = now_iso()
  save_task(task)
  append_task_event(
      "task.started",
      f"Task {task_id} started",
      session_id=session_id,
      task_id=task_id,
  )

  log_path = task_log_file(task_id, session_id=session_id)
  log_path.parent.mkdir(parents=True, exist_ok=True)
  with log_path.open("a", encoding="utf-8") as log_handle:
    process = subprocess.Popen(
        ["/bin/zsh", "-fc", task["command"]],
        cwd=str((get_project_root() / task["cwd"]).resolve()),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    task["command_pid"] = process.pid
    save_task(task)
    returncode = process.wait()

  task = load_task(task_id, session_id=session_id)
  task["returncode"] = returncode
  task["finished_at"] = now_iso()
  if task.get("cancel_requested_at") and returncode != 0:
    task["status"] = "cancelled"
    append_task_event(
        "task.cancelled",
        f"Task {task_id} cancelled",
        session_id=session_id,
        task_id=task_id,
    )
  elif returncode == 0:
    task["status"] = "succeeded"
    append_task_event(
        "task.completed",
        f"Task {task_id} completed",
        session_id=session_id,
        task_id=task_id,
    )
  else:
    task["status"] = "failed"
    append_task_event(
        "task.failed",
        f"Task {task_id} failed with code {returncode}",
        session_id=session_id,
        task_id=task_id,
        payload={"returncode": returncode},
    )
  save_task(task)
  return returncode


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1], sys.argv[2]))
