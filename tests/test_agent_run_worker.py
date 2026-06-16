from __future__ import annotations

import asyncio
import io
import json

from google.genai import types
from google.genai.errors import APIError

from src.agent_run_worker import _has_child_tasks
from src.agent_run_worker import _load_agent_config
from src.agent_run_worker import _run_config_task
from src.agent_run_worker import _run_registered_agent_task
from src.agent_run_worker import _run_with_child_event_log
from src.agent_run_worker import _task_config
from src.agent_run_worker import _task_prompt
from src.run_outcome import RunOutcome
from src.runner import APP_NAME
from src.runner import DEFAULT_USER_ID
from src.runtime import save_task
from src.storage import HandaArtifactService
from src.storage import HandaSessionService
from src.storage.runtime_event_store import RuntimeEventStore


def test_run_with_child_event_log_retries_transient_error_before_output(
    tmp_path,
    monkeypatch,
):
  async def run():
    calls = 0
    log = io.StringIO()
    sleeps = []

    async def fake_sleep(delay):
      sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def runner(emit_event):
      nonlocal calls
      calls += 1
      if calls == 1:
        raise APIError(503, {"error": {"code": 503}})
      await emit_event({"id": "evt-final", "kind": "agent_text"})
      return RunOutcome(final_text="done")

    final_text = await _run_with_child_event_log(
        task={"child_session_id": "child-session"},
        log_handle=log,
        session_service=HandaSessionService(root=str(tmp_path / ".handa")),
        storage_root=tmp_path / ".handa",
        runner=runner,
    )

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert final_text == "done"
    assert calls == 2
    assert sleeps == [2.0]
    assert records[0]["kind"] == "native.retry"
    assert records[-1]["kind"] == "agent_text"

  asyncio.run(run())


def test_run_with_child_event_log_does_not_retry_after_output(tmp_path, monkeypatch):
  async def run():
    calls = 0
    log = io.StringIO()
    sleeps = []

    async def fake_sleep(delay):
      sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def runner(emit_event):
      nonlocal calls
      calls += 1
      await emit_event({"id": "evt-partial", "kind": "agent_text"})
      raise APIError(503, {"error": {"code": 503}})

    try:
      await _run_with_child_event_log(
          task={"child_session_id": "child-session"},
          log_handle=log,
          session_service=HandaSessionService(root=str(tmp_path / ".handa")),
          storage_root=tmp_path / ".handa",
          runner=runner,
      )
    except APIError:
      pass
    else:
      raise AssertionError("Expected APIError")

    assert calls == 1
    assert sleeps == []
    assert [json.loads(line)["kind"] for line in log.getvalue().splitlines()] == [
        "agent_text"
    ]

  asyncio.run(run())


def test_load_agent_config_accepts_name_with_agent_json_suffix(tmp_path):
  async def run():
    service = HandaArtifactService(root=str(tmp_path / ".handa"))
    await service.save_artifact(
        app_name=APP_NAME,
        user_id=DEFAULT_USER_ID,
        session_id="session-1",
        filename="test_worker.agent.json",
        artifact=types.Part.from_text(
            text='{"name":"test_worker","model":"gemini-test"}'
        ),
    )

    config = await _load_agent_config(
        artifact_service=service,
        parent_session_id="session-1",
        user_id=DEFAULT_USER_ID,
        config_name="test_worker.agent.json",
        config_version=None,
    )

    assert config.name == "test_worker"
    assert config.description == ""

  asyncio.run(run())


def test_generated_agent_run_ignores_legacy_model_field(tmp_path):
  async def run():
    service = HandaArtifactService(root=str(tmp_path / ".handa"))
    await service.save_artifact(
        app_name=APP_NAME,
        user_id=DEFAULT_USER_ID,
        session_id="session-1",
        filename="legacy_worker.agent.json",
        artifact=types.Part.from_text(
            text='{"name":"legacy_worker","model":"gpt-4o"}'
        ),
    )

    config = await _task_config(
        task={
            "kind": "agent_run",
            "config_name": "legacy_worker",
            "config_version": None,
            "model_config_id": "gemini-3.5-flash-high",
        },
        artifact_service=service,
        parent_session_id="session-1",
        user_id=DEFAULT_USER_ID,
    )

    assert config.model_config_id == "gemini-3.5-flash-high"

  asyncio.run(run())


def test_system_agent_run_uses_predefined_model_config_id(tmp_path):
  async def run():
    config = await _task_config(
        task={
            "kind": "system_agent_run",
            "config": {
                "name": "research_agent",
                "model_config_id": "gemini-3.1-pro-low",
            },
        },
        artifact_service=HandaArtifactService(root=str(tmp_path / ".handa")),
        parent_session_id="session-1",
        user_id=DEFAULT_USER_ID,
    )

    assert config.model_config_id == "gemini-3.1-pro-low"

  asyncio.run(run())


def test_task_prompt_keeps_project_agents_out_of_user_prompt(tmp_path):
  (tmp_path / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
  nested = tmp_path / "src"
  nested.mkdir()
  (nested / "AGENTS.md").write_text("Nested rule.\n", encoding="utf-8")

  prompt = _task_prompt(
      {
          "prompt": "Inspect the repo.",
          "context": "Focus on changed files.",
          "project_root": str(tmp_path),
      }
  )

  assert prompt.startswith("Inspect the repo.")
  assert "Project Instructions (project_root/AGENTS.md)" not in prompt
  assert "Answer in Chinese." not in prompt
  assert "Context:\nFocus on changed files." in prompt


def test_run_config_task_returns_final_text_and_logs_events(tmp_path, monkeypatch):
  async def run():
    artifact_service = HandaArtifactService(root=str(tmp_path / ".handa"))
    await artifact_service.save_artifact(
        app_name=APP_NAME,
        user_id=DEFAULT_USER_ID,
        session_id="parent-session",
        filename="worker.agent.json",
        artifact=types.Part.from_text(
            text='{"name":"worker","model_config_id":"gemini-3.5-flash"}'
        ),
    )
    log = io.StringIO()

    async def fake_config_agent(**kwargs):
      await kwargs["emit_event"]({"id": "evt-start", "kind": "worker.started"})
      await kwargs["emit_event"](
          {
              "id": "evt-final",
              "kind": "agent_text",
              "payload": {"text": "config done", "final": True},
          }
      )
      return RunOutcome(final_text="config done")

    monkeypatch.setattr("src.agent_run_worker.run_config_agent", fake_config_agent)

    final_text = await _run_config_task(
        task={
            "kind": "agent_run",
            "config_name": "worker",
            "config_version": None,
            "prompt": "Inspect the repo.",
            "context": "Focus on top-level files.",
            "project_root": str(tmp_path),
            "session_id": "parent-session",
            "child_session_id": "child-config-session",
            "user_id": "user",
        },
        artifact_service=artifact_service,
        parent_session_id="parent-session",
        user_id=DEFAULT_USER_ID,
        log_handle=log,
        session_service=HandaSessionService(root=str(tmp_path / ".handa")),
        storage_root=tmp_path / ".handa",
    )

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert final_text == "config done"
    assert [record["kind"] for record in records] == [
        "worker.started",
        "agent_text",
    ]
    stored = RuntimeEventStore(tmp_path / ".handa").list_events(
        session_id="child-config-session",
        runtime="native",
    )
    assert [item["event"]["kind"] for item in stored] == [
        "worker.started",
        "agent_text",
    ]

  asyncio.run(run())


def test_run_registered_agent_task_returns_final_text_and_logs_events(
    tmp_path,
    monkeypatch,
):
  async def run():
    log = io.StringIO()

    async def fake_runner(**kwargs):
      await kwargs["emit_event"]({"id": "evt-start", "kind": "browser.started"})
      await kwargs["emit_event"](
          {
              "id": "evt-final",
              "kind": "agent_text",
              "payload": {"text": "browser done", "final": True},
          }
      )
      return RunOutcome(final_text="browser done")

    monkeypatch.setattr(
        "src.agent_run_worker.load_native_agent",
        lambda agent_id: fake_runner,
    )

    final_text = await _run_registered_agent_task(
        task={
            "kind": "run_agent",
            "agent_runtime": "native",
            "agent_id": "browser",
            "prompt": "Open a page.",
            "context": "",
            "project_root": str(tmp_path),
            "session_id": "parent-session",
            "child_session_id": "child-browser-session",
            "user_id": "user",
        },
        log_handle=log,
        session_service=HandaSessionService(root=str(tmp_path / ".handa")),
        storage_root=tmp_path / ".handa",
    )

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert final_text == "browser done"
    assert [record["kind"] for record in records] == [
        "browser.started",
        "agent_text",
    ]
    stored = RuntimeEventStore(tmp_path / ".handa").list_events(
        session_id="child-browser-session",
        runtime="native",
    )
    assert [item["event"]["kind"] for item in stored] == [
        "browser.started",
        "agent_text",
    ]
    assert stored[0]["turn_id"] == "session:child-browser-session"

  asyncio.run(run())


def test_has_child_tasks_only_counts_live_tasks(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))

  save_task(
      {
          "id": "task_done",
          "session_id": "child-session",
          "status": "succeeded",
          "created_ts": 1,
      }
  )
  assert _has_child_tasks("child-session") is False

  save_task(
      {
          "id": "task_live",
          "session_id": "child-session",
          "status": "queued",
          "created_ts": 2,
      }
  )
  assert _has_child_tasks("child-session") is True
