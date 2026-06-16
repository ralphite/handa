from __future__ import annotations

import re
import os
import subprocess
import time

import pytest

from src.runtime import append_task_event
from src.runtime import build_agent_run_report
from src.runtime import cancel_stale_live_tasks
from src.runtime import create_task_notification
from src.runtime import get_task_status
from src.runtime import list_files
from src.runtime import list_task_events
from src.runtime import list_task_notifications
from src.runtime import save_task
from src.runtime import read_file
from src.runtime import read_task_log
from src.runtime import read_task_result
from src.runtime import replace_in_file
from src.runtime import run_command
from src.runtime import search_code
from src.runtime import start_agent_run_task
from src.runtime import start_run_agent_task
from src.runtime import start_system_agent_run_task
from src.runtime import start_background_task
from src.runtime import task_result_file
from src.runtime import task_status_view
from src.runtime import validate_command
from src.runtime import write_file
from src.storage import HandaSessionService


def test_build_agent_run_report_no_stray_indentation():
  # A multi-line prompt/result must not leave template lines indented: a 4+ space
  # indent renders as a Markdown code block in the viewer.
  task = {
      "id": "task_1",
      "child_session_id": "child_1",
      "status": "succeeded",
      "kind": "run_agent",
      "agent_runtime": "native",
      "agent_id": "browser",
      "prompt": "Line one.\nLine two has no indent.",
  }
  report = build_agent_run_report(task, "Result line one.\nResult line two.")

  assert "## Prompt" in report
  assert "## Result" in report
  assert not any(line.startswith("    ") for line in report.splitlines())


def test_build_agent_run_report_fences_escape_inner_backticks():
  # Prompt/result are quoted verbatim and may contain ```code fences```; the
  # wrapping fence must be longer so they cannot break out of the block.
  task = {
      "id": "task_1",
      "child_session_id": "child_1",
      "status": "succeeded",
      "kind": "run_agent",
      "agent_id": "browser",
      "prompt": "plain prompt",
  }
  result = "Here is code:\n```python\nprint(1)\n```\nDone."
  report = build_agent_run_report(task, result)

  # The result block is fenced with at least four backticks so the inner
  # triple-backtick fence renders literally instead of closing the block.
  assert "````" in report
  assert result in report


def test_file_tools(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))

  write_file("demo.txt", "one\ntwo\nthree\n")
  snippet = read_file("demo.txt", 2, 3)
  replaced = replace_in_file("demo.txt", "two", "TWO")

  assert "2: two" in snippet["content"]
  assert replaced["success"] is True


def test_file_tools_report_line_change_counts(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))

  # A brand-new file reports every line as added, none removed.
  created = write_file("demo.txt", "one\ntwo\nthree\n")
  assert (created["lines_added"], created["lines_removed"]) == (3, 0)

  # Overwriting an existing file counts only the changed lines.
  rewritten = write_file("demo.txt", "one\nTWO\nthree\nfour\n")
  assert (rewritten["lines_added"], rewritten["lines_removed"]) == (2, 1)

  # A replace swaps one line for one.
  replaced = replace_in_file("demo.txt", "four", "FOUR")
  assert (replaced["lines_added"], replaced["lines_removed"]) == (1, 1)

  # A failed replace changed nothing, so it carries no line counts.
  missing = replace_in_file("demo.txt", "absent", "x")
  assert missing["success"] is False
  assert "lines_added" not in missing


def test_list_files_groups_by_parent_directory(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  (tmp_path / "a" / "b" / "c").mkdir(parents=True)
  (tmp_path / "a" / "b" / "x").mkdir(parents=True)
  (tmp_path / "a" / "b" / "c" / "d").write_text("d\n", encoding="utf-8")
  (tmp_path / "a" / "b" / "c" / "e").write_text("e\n", encoding="utf-8")
  (tmp_path / "a" / "b" / "x" / "y").write_text("y\n", encoding="utf-8")
  (tmp_path / "root.txt").write_text("root\n", encoding="utf-8")

  result = list_files()

  assert result["format"] == "directory_groups"
  assert result["file_count"] == 4
  assert result["shown_count"] == 4
  assert result["listing"] == (
      "./\n"
      "  root.txt\n"
      "a/b/c/\n"
      "  d\n"
      "  e\n"
      "a/b/x/\n"
      "  y"
  )
  assert "a/b/c/d" not in result["listing"]


def test_list_files_only_truncates_when_max_files_is_explicit(
    tmp_path,
    monkeypatch,
):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  for index in range(5):
    (tmp_path / f"file-{index}.txt").write_text("x\n", encoding="utf-8")

  complete = list_files()
  limited = list_files(max_files=3)

  assert complete["file_count"] == 5
  assert complete["shown_count"] == 5
  assert "truncated" not in complete
  assert limited["file_count"] == 5
  assert limited["shown_count"] == 3
  assert limited["truncated"] is True
  assert limited["omitted_count"] == 2
  assert limited["listing"] == (
      "./\n"
      "  file-0.txt\n"
      "  file-1.txt\n"
      "  file-2.txt\n"
      "... 2 of 5 files omitted: ./ +2."
      " List a subdirectory or raise max_files for the rest."
  )


def test_list_files_truncation_keeps_shallow_files(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  (tmp_path / "a" / "b").mkdir(parents=True)
  (tmp_path / "a" / "b" / "deep-1.txt").write_text("x\n", encoding="utf-8")
  (tmp_path / "a" / "b" / "deep-2.txt").write_text("x\n", encoding="utf-8")
  (tmp_path / "z-root.txt").write_text("x\n", encoding="utf-8")

  limited = list_files(max_files=1)

  # The root file survives even though `a/b/` sorts first in walk order, so a
  # preview maps the top of the repo instead of an alphabetical prefix.
  assert limited["shown_count"] == 1
  assert limited["listing"] == (
      "./\n"
      "  z-root.txt\n"
      "... 2 of 3 files omitted: a/b/ +2."
      " List a subdirectory or raise max_files for the rest."
  )


def test_list_files_notes_depth_limit(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  (tmp_path / "a" / "b").mkdir(parents=True)
  (tmp_path / "a" / "b" / "deep.txt").write_text("x\n", encoding="utf-8")
  (tmp_path / "root.txt").write_text("x\n", encoding="utf-8")

  limited = list_files(max_depth=0)

  assert limited["depth_limited"] is True
  assert limited["listing"] == (
      "./\n"
      "  root.txt\n"
      "... subdirectories below depth 0 not listed (raise max_depth for more)"
  )


def test_list_files_honours_gitignore_in_git_repos(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
  (tmp_path / ".gitignore").write_text("tmp/\n*.secret\n", encoding="utf-8")
  (tmp_path / "tmp").mkdir()
  (tmp_path / "tmp" / "scratch.txt").write_text("x\n", encoding="utf-8")
  (tmp_path / "creds.secret").write_text("x\n", encoding="utf-8")
  (tmp_path / "src").mkdir()
  (tmp_path / "src" / "tracked.py").write_text("x\n", encoding="utf-8")
  subprocess.run(["git", "add", "src/tracked.py"], cwd=tmp_path, check=True)
  # Untracked but not ignored: must still be listed (`--others`).
  (tmp_path / "src" / "untracked.py").write_text("x\n", encoding="utf-8")
  # Not ignored by git, but EXCLUDED_DIRS still applies as a backstop.
  (tmp_path / ".claude").mkdir()
  (tmp_path / ".claude" / "notes.md").write_text("x\n", encoding="utf-8")

  result = list_files()

  assert result["file_count"] == 3
  assert result["shown_count"] == 3
  assert result["listing"] == (
      "./\n"
      "  .gitignore\n"
      "src/\n"
      "  tracked.py\n"
      "  untracked.py"
  )


def test_list_files_git_repo_keeps_depth_and_truncation_semantics(
    tmp_path,
    monkeypatch,
):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
  (tmp_path / "a" / "b").mkdir(parents=True)
  (tmp_path / "a" / "b" / "deep-1.txt").write_text("x\n", encoding="utf-8")
  (tmp_path / "a" / "b" / "deep-2.txt").write_text("x\n", encoding="utf-8")
  (tmp_path / "z-root.txt").write_text("x\n", encoding="utf-8")

  shallow = list_files(max_depth=0)
  limited = list_files(max_files=1)

  assert shallow["depth_limited"] is True
  assert shallow["listing"] == (
      "./\n"
      "  z-root.txt\n"
      "... subdirectories below depth 0 not listed (raise max_depth for more)"
  )
  # Truncation still keeps the shallowest files, exactly like the walk path.
  assert limited["truncated"] is True
  assert limited["omitted_count"] == 2
  assert limited["listing"] == (
      "./\n"
      "  z-root.txt\n"
      "... 2 of 3 files omitted: a/b/ +2."
      " List a subdirectory or raise max_files for the rest."
  )


def test_search_and_run(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))

  write_file("src/app.py", "print('hello handa')\n")
  result = search_code("hello handa")
  command = run_command("python3 -c \"print('ok')\"")

  assert result["match_count"] == 1
  assert command["success"] is True
  assert "ok" in command["stdout"]


def test_search_code_surfaces_rg_errors(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  write_file("src/app.py", "print('hello handa')\n")

  result = search_code("hello handa(")

  # An unclosed group is a regex parse error: it must come back as an error,
  # not as a clean "no matches" result the model reads as "symbol absent".
  assert result["match_count"] == 0
  assert "error" in result


def test_validate_command_blocks_command_position_only():
  # Argument position: legitimate searches and project cleanups must pass.
  validate_command("grep -rn shutdown src")
  validate_command("git log --grep reboot")
  validate_command("echo killall test")
  validate_command("rm -rf ./build")

  # Command position: direct, chained, wrapped, and substituted forms.
  with pytest.raises(ValueError):
    validate_command("shutdown -h now")
  with pytest.raises(ValueError):
    validate_command("echo hi && reboot")
  with pytest.raises(ValueError):
    validate_command("echo $(reboot)")
  with pytest.raises(ValueError):
    validate_command("sudo ls")
  with pytest.raises(ValueError):
    validate_command("nohup killall Dock")
  with pytest.raises(ValueError):
    validate_command("rm -rf /")


def test_read_file_reports_total_lines(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  write_file("demo.txt", "\n".join(f"line {i}" for i in range(1, 11)) + "\n")

  snippet = read_file("demo.txt", 1, 3)

  assert snippet["end_line"] == 3
  assert snippet["total_lines"] == 10


def test_run_command_marks_truncated_output(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))

  command = run_command("python3 -c \"print('x' * 20000)\"")

  assert command["success"] is True
  assert command["stdout_truncated"] is True
  assert command["stdout"].startswith("... first ")
  assert "stderr_truncated" not in command


def test_run_command_returns_timeout_instead_of_raising(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))

  command = run_command("sleep 5", timeout_sec=1)

  assert command["success"] is False
  assert command["timed_out"] is True
  assert command["returncode"] is None
  assert "timed out" in command["error"]


def test_replace_in_file_enforces_expected_replacements(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  write_file("demo.txt", "alpha beta alpha\n")

  mismatch = replace_in_file("demo.txt", "alpha", "gamma", expected_replacements=1)
  matched = replace_in_file("demo.txt", "alpha", "gamma", expected_replacements=2)

  assert mismatch["success"] is False
  assert mismatch["occurrences"] == 2
  assert matched["success"] is True
  assert matched["replacements"] == 2
  assert "alpha" not in (tmp_path / "demo.txt").read_text(encoding="utf-8")


def test_task_views_slim_records_and_report_unknown_ids(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  task = start_background_task("python3 -c \"print('ok')\"", session_id=session_id)
  view = task_status_view(task["id"], session_id=session_id)
  missing = task_status_view("task_missing", session_id=session_id)
  missing_log = read_task_log("task_missing", session_id=session_id)

  assert view["found"] is True
  assert view["task"]["id"] == task["id"]
  # Worker bookkeeping and absolute paths stay out of tool responses.
  assert "worker_pid" not in view["task"]
  assert "log_path" not in view["task"]
  assert "project_root" not in view["task"]
  assert missing["found"] is False
  assert "unknown task_id" in missing["error"]
  assert missing_log["found"] is False


def test_events_and_background_task(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  append_task_event("task.note", "created note", session_id=session_id)
  task = start_background_task(
      "python3 -c \"print('task ok')\"",
      session_id=session_id,
  )

  for _ in range(40):
    status = get_task_status(task["id"], session_id=session_id)
    if status["status"] in {"succeeded", "failed", "cancelled"}:
      break
    time.sleep(0.1)

  status = get_task_status(task["id"], session_id=session_id)
  log = read_task_log(task["id"], session_id=session_id)
  events = list_task_events(session_id=session_id, limit=20)

  assert status["status"] == "succeeded"
  assert status["session_id"] == session_id
  assert "task ok" in log["log"]
  assert any(event["kind"] == "task.completed" for event in events)
  assert (
      storage_root / "sessions" / session_id / "tasks" / "task_events.jsonl"
  ).exists()
  assert (
      storage_root / "sessions" / session_id / "tasks" / task["id"] / "task.json"
  ).exists()


def test_task_notification_is_idempotent_by_source_event(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  first = create_task_notification(
      session_id=session_id,
      task_id="task_1",
      source_event_id="evt_1",
      source_event_kind="task.completed",
      payload={"task_status": "succeeded"},
  )
  second = create_task_notification(
      session_id=session_id,
      task_id="task_1",
      source_event_id="evt_1",
      source_event_kind="task.completed",
      payload={"task_status": "succeeded"},
  )
  notifications = list_task_notifications(session_id=session_id)

  assert first["id"] == second["id"]
  assert len(notifications) == 1
  assert notifications[0]["status"] == "pending"


def test_cancel_stale_live_tasks_marks_dead_workers_cancelled(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr("src.runtime._is_process_alive", lambda pid: pid == 123)

  save_task(
      {
          "id": "task_stale",
          "session_id": session_id,
          "kind": "command",
          "status": "running",
          "summary": "Stale task",
          "worker_pid": 999999,
          "command_pid": None,
          "created_at": "2026-06-02T00:00:00Z",
          "created_ts": 1.0,
          "started_at": "2026-06-02T00:00:01Z",
          "finished_at": None,
          "returncode": None,
          "cancel_requested_at": None,
      }
  )
  save_task(
      {
          "id": "task_live",
          "session_id": session_id,
          "kind": "command",
          "status": "running",
          "summary": "Live task",
          "worker_pid": os.getpid(),
          "command_pid": None,
          "created_at": "2026-06-02T00:00:00Z",
          "created_ts": 2.0,
          "started_at": "2026-06-02T00:00:01Z",
          "finished_at": None,
          "returncode": None,
          "cancel_requested_at": None,
      }
  )

  changed = cancel_stale_live_tasks()
  stale = get_task_status("task_stale", session_id=session_id)
  live = get_task_status("task_live", session_id=session_id)
  events = list_task_events(session_id=session_id, limit=20)

  assert changed == 1
  assert stale["status"] == "cancelled"
  assert stale["returncode"] == 1
  assert stale["finished_at"]
  assert live["status"] == "running"
  assert [event["kind"] for event in events] == ["task.stale_cancelled"]
  assert events[0]["payload"] == {"reason": "stale_worker"}


def test_agent_run_task_creates_child_session(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.runtime.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  task = start_agent_run_task(
      config_name="testing_quality",
      prompt="Run pytest.",
      context="Focus on changed files.",
      model_config_id="gemini-3.5-flash",
      session_id=session_id,
      user_id="user",
      app_name="handa",
  )
  result_path = task_result_file(task["id"], session_id=session_id)
  result_path.write_text('{"success": true}\n', encoding="utf-8")
  result = read_task_result(task["id"], session_id=session_id)
  child = HandaSessionService(root=str(storage_root))._read_session(
      task["child_session_id"]
  )

  assert task["kind"] == "agent_run"
  assert task["status"] == "queued"
  assert task["worker_pid"] == 12345
  assert re.fullmatch(
      rf"{re.escape(session_id)}-[0-9a-z]{{6}}",
      task["child_session_id"],
  )
  assert (
      storage_root / "sessions" / task["child_session_id"] / "session.json"
  ).exists()
  assert child is not None
  assert child.state["handa:session_kind"] == "agent_run_child"
  assert child.state["handa:parent_task_id"] == task["id"]
  assert child.state["handa:agent_run_prompt"] == "Run pytest."
  assert task["model_config_id"] == "gemini-3.5-flash"
  assert child.state["handa:model_config_id"] == "gemini-3.5-flash"
  assert child.state["handa:agent_run_depth"] == 1
  assert result["found"] is True
  assert result["result"]["success"] is True


def test_run_agent_task_creates_child_session(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.runtime.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  task = start_run_agent_task(
      agent_id="ralph",
      prompt="Run ralph.",
      context="Use the real registered Ralph agent.",
      session_id=session_id,
      user_id="user",
      app_name="handa",
      depth=1,
  )
  child = HandaSessionService(root=str(storage_root))._read_session(
      task["child_session_id"]
  )

  assert task["kind"] == "run_agent"
  assert task["agent_id"] == "ralph"
  assert task["worker_pid"] == 12345
  assert child is not None
  assert child.state["handa:session_kind"] == "run_agent_child"
  assert child.state["handa:target_agent_id"] == "ralph"
  assert task["agent_runtime"] == "native"
  assert child.state["handa:agent_runtime"] == "native"
  assert child.state["handa:agent_run_depth"] == 2


def test_native_orca_run_agent_task_creates_runtime_snapshot(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.runtime.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  task = start_run_agent_task(
      agent_id="orca",
      prompt="Summarize project.",
      session_id=session_id,
      user_id="user",
      app_name="handa",
  )
  child = HandaSessionService(root=str(storage_root))._read_session(
      task["child_session_id"]
  )

  assert task["agent_runtime"] == "native"
  assert child is not None
  assert child.state["handa:agent_runtime"] == "native"


def test_system_agent_run_task_creates_immutable_config_child_session(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.runtime.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  task = start_system_agent_run_task(
      config={
          "name": "ralph_verifier",
          "model_config_id": "gemini-3.1-pro-low",
          "description": "System verifier.",
          "tools": [],
          "skills": [],
          "instruction_sections": [],
      },
      prompt="Verify.",
      session_id=session_id,
      user_id="user",
      app_name="handa",
  )
  child = HandaSessionService(root=str(storage_root))._read_session(
      task["child_session_id"]
  )

  assert task["kind"] == "system_agent_run"
  assert task["config_name"] == "ralph_verifier"
  assert task["config"]["name"] == "ralph_verifier"
  assert task["model_config_id"] == "gemini-3.1-pro-low"
  assert task["config"]["model_config_id"] == "gemini-3.1-pro-low"
  assert task["save_parent_summary"] is False
  assert task["worker_pid"] == 12345
  assert child is not None
  assert child.state["handa:session_kind"] == "system_agent_run_child"
  assert child.state["handa:system_agent_config_name"] == "ralph_verifier"
  assert child.state["handa:model_config_id"] == "gemini-3.1-pro-low"


def test_system_agent_run_task_rejects_unknown_model_config_id(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  session_id = "session-1"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  with pytest.raises(ValueError, match="Unknown model_config_id"):
    start_system_agent_run_task(
        config={
            "name": "research_agent",
            "model_config_id": "gpt-4o",
        },
        prompt="Research.",
        session_id=session_id,
        user_id="user",
        app_name="handa",
    )
