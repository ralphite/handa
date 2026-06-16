from __future__ import annotations

import asyncio

from src.agents.handa_langgraph.tools import MAX_TOOL_RESULT_CHARS
from src.agents.handa_langgraph.tools import SessionContext
from src.agents.handa_langgraph.tools import _truncate_value
from src.agents.handa_langgraph.tools import build_session_context
from src.agents.handa_langgraph.tools import build_toolset
from src.progress import PROGRESS_STATE_KEY
from src.runner import APP_NAME
from src.storage import HandaSessionService


def test_truncate_value_keeps_small_fields_when_one_overflows():
  payload = {
      "ok": True,
      "result": {
          "success": False,
          "command": "pytest",
          "returncode": 1,
          "stdout": "x" * 13000,
          "stderr": "ImportError: cannot import name foo",
      },
  }

  out = _truncate_value(payload, MAX_TOOL_RESULT_CHARS)

  # Every key survives: a failing command must keep stderr and returncode even
  # when stdout alone exceeds the whole budget.
  inner = out["result"]
  assert set(inner) == {"success", "command", "returncode", "stdout", "stderr"}
  assert inner["stderr"] == "ImportError: cannot import name foo"
  assert inner["returncode"] == 1
  assert inner["stdout"].endswith("... truncated ...")
  assert len(inner["stdout"]) < 13000


def _ctx(session_id: str, *, depth: int = 0) -> SessionContext:
  return SessionContext(
      session_id=session_id,
      user_id="user",
      model_config_id="gemini-3.5-flash",
      agent_run_depth=depth,
  )


def test_agents_config_roundtrip(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    toolset = build_toolset(
        ["agents_save_config", "agents_read_config", "agents_list_configs"],
        _ctx("sess-config"),
    )
    saved = await toolset.dispatch(
        "agents_save_config",
        {
            "name": "tester",
            "description": "Verification agent",
            "tools": ["files_read", "commands_run"],
            "skills": [],
            "instruction_sections": ["identity", "testing"],
        },
    )
    assert saved["ok"] is True
    assert saved["filename"] == "tester.agent.json"
    assert saved["model_config_id"] == "gemini-3.5-flash"

    # list_artifact_keys returns versioned stored names, matching ADK semantics.
    listed = await toolset.dispatch("agents_list_configs", {})
    assert any(
        name.startswith("tester") and name.endswith(".agent.json")
        for name in listed["configs"]
    )

    # Reads use the logical name; the store resolves the latest version.
    read = await toolset.dispatch("agents_read_config", {"name": "tester"})
    assert read["found"] is True
    assert read["config"]["name"] == "tester"
    assert read["config"]["tools"] == ["files_read", "commands_run"]
    # Generated configs must not carry a model field.
    assert "model" not in read["config"]
    assert "model_config_id" not in read["config"]

  asyncio.run(go())


def test_agents_save_config_validates_tools_and_warns(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    toolset = build_toolset(["agents_save_config"], _ctx("sess-validate"))

    # Unknown tool name is rejected (surfaced as a tool error via dispatch).
    rejected = await toolset.dispatch(
        "agents_save_config",
        {
            "name": "typo_agent",
            "description": "x",
            "tools": ["files_read", "file_write"],
            "skills": [],
            "instruction_sections": ["identity"],
        },
    )
    assert rejected["ok"] is False
    assert "Unknown agent tools: file_write" in rejected["error"]["message"]

    # Write-intent without a write tool warns but still saves.
    warned = await toolset.dispatch(
        "agents_save_config",
        {
            "name": "reader_agent",
            "description": "Analyze the project.",
            "tools": ["files_read"],
            "skills": [],
            "instruction_sections": ["identity"],
            "custom_instruction": "Write a summary report to a file.",
        },
    )
    assert warned["ok"] is True
    assert any("write-capable tool" in w for w in warned["warnings"])

  asyncio.run(go())


def test_artifacts_read_windowing_survives_dispatch(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  from src.tools.text_window import DEFAULT_READ_MAX_CHARS

  async def go():
    await HandaSessionService().create_session(
        app_name=APP_NAME, user_id="user", session_id="sess-win"
    )
    toolset = build_toolset(["artifacts_save_text", "artifacts_read"], _ctx("sess-win"))
    big = "Q" * (DEFAULT_READ_MAX_CHARS + 2000)
    await toolset.dispatch(
        "artifacts_save_text", {"filename": "big.report.md", "content": big}
    )

    loaded = await toolset.dispatch("artifacts_read", {"filename": "big.report.md"})
    assert loaded["ok"] is True
    # The window stays intact through the dispatch-level payload budget: the
    # default read is bounded and next_offset points at the true cut.
    assert len(loaded["content"]) == DEFAULT_READ_MAX_CHARS
    assert loaded["truncated"] is True
    assert loaded["next_offset"] == DEFAULT_READ_MAX_CHARS

    tail = await toolset.dispatch(
        "artifacts_read",
        {"filename": "big.report.md", "offset": DEFAULT_READ_MAX_CHARS},
    )
    assert tail["content"] == "Q" * 2000

  asyncio.run(go())


def test_run_agent_depth_guard_blocks_recursion(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    toolset = build_toolset(["run_agent"], _ctx("sess-depth", depth=3))
    result = await toolset.dispatch(
        "run_agent",
        {"agent_id": "orca", "prompt": "go", "max_depth": 3},
    )
    assert result["ok"] is False
    assert "max depth" in result["error"]["message"]

  asyncio.run(go())


def test_run_agent_starts_child_task(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  captured: dict = {}

  def fake_start(**kwargs):
    captured.update(kwargs)
    return {
        "id": "task_x",
        "status": "queued",
        "agent_id": kwargs["agent_id"],
        "child_session_id": "sess-run-child",
    }

  monkeypatch.setattr(
      "src.agents.orca.tools.start_run_agent_task", fake_start
  )

  async def go():
    toolset = build_toolset(["run_agent"], _ctx("sess-run", depth=0))
    result = await toolset.dispatch(
        "run_agent",
        {"agent_id": "orca_adk", "prompt": "do it", "summary": "child"},
    )
    assert result["ok"] is True
    assert result["task_id"] == "task_x"
    assert result["child_session_id"] == "sess-run-child"
    assert captured["agent_id"] == "orca_adk"
    assert captured["session_id"] == "sess-run"
    assert captured["depth"] == 0
    # The success payload stays lean: no available_agents dump on every call.
    assert "available_agents" not in result

  asyncio.run(go())


def test_notes_and_notifications_persist_session_state(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    service = HandaSessionService()
    await service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="sess-notes",
        state={},
    )
    toolset = build_toolset(["notes_add", "notifications_get"], _ctx("sess-notes"))

    added = await toolset.dispatch("notes_add", {"summary": "remember the plan"})
    assert added["ok"] is True
    assert added["note"]["summary"] == "remember the plan"

    state = service.read_state_sync("sess-notes")
    assert state["handa:notes"][0]["summary"] == "remember the plan"

    notifications = await toolset.dispatch("notifications_get", {})
    assert notifications["ok"] is True
    assert notifications["count"] == 0
    assert notifications["unread_only"] is True

  asyncio.run(go())


def test_progress_update_persists_session_state(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    service = HandaSessionService()
    await service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="sess-progress",
        state={"handa:active_turn_id": "turn-progress"},
    )
    toolset = build_toolset(["progress_update"], _ctx("sess-progress"))

    updated = await toolset.dispatch(
        "progress_update",
        {
            "items": [
                {
                    "id": "plan",
                    "title": "Create plan",
                    "status": "done",
                },
                {
                    "id": "verify",
                    "title": "Run verification",
                    "status": "running",
                    "detail": "pytest",
                },
            ]
        },
    )

    assert updated["ok"] is True
    assert updated["count"] == 2
    state = service.read_state_sync("sess-progress")
    assert state[PROGRESS_STATE_KEY][0]["status"] == "done"
    assert state[PROGRESS_STATE_KEY][0]["source_turn_id"] == "turn-progress"
    assert state[PROGRESS_STATE_KEY][1]["detail"] == "pytest"

  asyncio.run(go())


def test_tasks_list_passthrough_is_empty(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    toolset = build_toolset(["tasks_list"], _ctx("sess-tasks"))
    result = await toolset.dispatch("tasks_list", {})
    assert result["ok"] is True
    assert result["tasks"] == []

  asyncio.run(go())


def test_build_session_context_inherits_depth(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  async def go():
    service = HandaSessionService()
    await service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="sess-child",
        state={"handa:agent_run_depth": 2},
    )
    ctx = build_session_context(
        session_id="sess-child",
        user_id="user",
        model_config_id="gemini-3.5-flash",
    )
    assert ctx.agent_run_depth == 2
    assert ctx.model_config_id == "gemini-3.5-flash"

  asyncio.run(go())


def test_unknown_tool_rejected():
  try:
    build_toolset(["files_read", "not_a_tool"], _ctx("sess-x"))
  except ValueError as exc:
    assert "not_a_tool" in str(exc)
  else:
    raise AssertionError("expected ValueError for unknown tool")


def test_agents_get_run_status_separates_tool_and_task_status(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  from src.contract.task_store import save_task

  async def go():
    save_task(
        {
            "id": "task_failed_1",
            "session_id": "sess-status",
            "kind": "agent_run",
            "status": "failed",
            "child_session_id": "sess-status-child",
            "config_name": "analysis",
            "returncode": 1,
        }
    )
    toolset = build_toolset(["agents_get_run_status"], _ctx("sess-status"))
    result = await toolset.dispatch(
        "agents_get_run_status", {"task_id": "task_failed_1"}
    )

    # Reading a *failed* run is itself a successful tool call: the envelope is
    # ok and tool_status is "ok"; the child outcome lives in task_status.
    assert result["ok"] is True
    assert result["tool_status"] == "ok"
    assert result["task_status"] == "failed"
    assert result["task"]["status"] == "failed"

  asyncio.run(go())
