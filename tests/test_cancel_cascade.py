from __future__ import annotations

import os

from src.contract.task_store import cancel_descendant_runs
from src.contract.task_store import load_task
from src.contract.task_store import save_task


def _save(task_id, session_id, kind, status, *, child_session_id="", worker_pid=4242):
  save_task(
      {
          "id": task_id,
          "session_id": session_id,
          "kind": kind,
          "status": status,
          "worker_pid": worker_pid,
          "child_session_id": child_session_id,
          "created_ts": 1.0,
      }
  )


def test_cancel_descendant_runs_cancels_live_agent_runs_recursively(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  killed: list[tuple[int, int]] = []
  monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))

  # A live child agent run that itself spawned a live grandchild run.
  _save("child", "parent", "run_agent", "running", child_session_id="gc-sess", worker_pid=111)
  _save("grandchild", "gc-sess", "agent_run", "running", worker_pid=222)
  # Non-agent background work and already-terminal runs must be left alone.
  _save("bg", "parent", "command", "running", worker_pid=333)
  _save("done", "parent", "agent_run", "succeeded", worker_pid=444)

  cancelled = cancel_descendant_runs("parent")

  assert cancelled == 2
  assert load_task("child", session_id="parent")["status"] == "cancelled"
  assert load_task("grandchild", session_id="gc-sess")["status"] == "cancelled"
  assert load_task("bg", session_id="parent")["status"] == "running"
  assert load_task("done", session_id="parent")["status"] == "succeeded"
  assert {pid for pid, _ in killed} == {111, 222}


def test_cancel_descendant_runs_noop_without_tasks(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  monkeypatch.setattr(os, "killpg", lambda pid, sig: None)
  assert cancel_descendant_runs("session-with-no-tasks") == 0
