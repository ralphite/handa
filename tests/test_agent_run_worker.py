from __future__ import annotations

import asyncio
import io
import json

from google.genai.errors import APIError
from google.genai import types

from src.agent_run_worker import _is_retryable_run_error
from src.agent_run_worker import _has_child_tasks
from src.agent_run_worker import _load_agent_config
from src.agent_run_worker import _load_task_agent
from src.agent_run_worker import _run_langgraph_task
from src.agent_run_worker import _run_child_agent_with_retries
from src.agent_run_worker import _task_prompt
from src.runner import APP_NAME
from src.runner import DEFAULT_USER_ID
from src.runtime import save_task
from src.storage import HandaArtifactService
from src.storage import HandaSessionService
from src.storage.runtime_event_store import RuntimeEventStore


class FakeRunner:
  def __init__(self):
    self.calls = 0

  async def run_async(self, **kwargs):
    self.calls += 1
    if self.calls == 1:
      raise APIError(
          503,
          {
              "error": {
                  "code": 503,
                  "message": "temporary overload",
                  "status": "UNAVAILABLE",
              }
          },
      )
    yield FakeEvent("done")


class FakeRateLimitRunner:
  def __init__(self):
    self.calls = 0

  async def run_async(self, **kwargs):
    self.calls += 1
    if self.calls == 1:
      raise FakeResourceExhaustedError()
    yield FakeEvent("done after quota retry")


class FakeResourceExhaustedError(Exception):
  code = 429

  def __str__(self):
    return "429 RESOURCE_EXHAUSTED. quota exceeded"


class FakeEvent:
  author = "qa_runner"

  def __init__(self, text: str):
    self.content = type("Content", (), {"parts": [type("Part", (), {"text": text})()]})()

  def is_final_response(self) -> bool:
    return True

  def get_function_calls(self) -> list:
    return []

  def get_function_responses(self) -> list:
    return []


def test_retryable_agent_run_error_detects_429_and_5xx():
  assert _is_retryable_run_error(APIError(429, {"error": {"code": 429}})) is True
  assert _is_retryable_run_error(APIError(503, {"error": {"code": 503}})) is True
  assert _is_retryable_run_error(FakeResourceExhaustedError()) is True
  assert _is_retryable_run_error(APIError(400, {"error": {"code": 400}})) is False
  assert _is_retryable_run_error(RuntimeError("boom")) is False


def test_run_child_agent_retries_transient_error(monkeypatch):
  async def run():
    runner = FakeRunner()
    log = io.StringIO()
    sleep_delays = []

    async def fake_sleep(delay):
      sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = await _run_child_agent_with_retries(
        runner=runner,
        user_id="user",
        child_session_id="child",
        prompt="run qa",
        log_handle=log,
        base_delay_sec=0,
    )

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert result == "done"
    assert runner.calls == 2
    assert records[0]["retry"] is True
    assert records[0]["error"]["code"] == 503
    assert records[0]["delay_sec"] == 0
    assert sleep_delays == [0]
    assert records[-1]["final"] is True
    assert records[-1]["text"] == "done"

  asyncio.run(run())


def test_run_child_agent_retries_resource_exhausted_error(monkeypatch):
  async def run():
    runner = FakeRateLimitRunner()
    log = io.StringIO()
    sleep_delays = []

    async def fake_sleep(delay):
      sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = await _run_child_agent_with_retries(
        runner=runner,
        user_id="user",
        child_session_id="child",
        prompt="run qa",
        log_handle=log,
        base_delay_sec=0,
    )

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert result == "done after quota retry"
    assert runner.calls == 2
    assert records[0]["retry"] is True
    assert records[0]["error"]["code"] == 429
    assert records[0]["delay_sec"] == 60.0
    assert sleep_delays == [60.0]

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

    agent = await _load_task_agent(
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

    assert agent.model == "gemini-3.5-flash"

  asyncio.run(run())


def test_system_agent_run_uses_predefined_model_config_id(tmp_path):
  async def run():
    service = HandaArtifactService(root=str(tmp_path / ".handa"))

    agent = await _load_task_agent(
        task={
            "kind": "system_agent_run",
            "config": {
                "name": "research_agent",
                "model_config_id": "gemini-3.1-pro-low",
            },
        },
        artifact_service=service,
        parent_session_id="session-1",
        user_id=DEFAULT_USER_ID,
    )

    assert agent.model == "gemini-3.1-pro-preview"
    assert (
        agent.generate_content_config.thinking_config.thinking_level
        == types.ThinkingLevel.LOW
    )

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


def test_load_run_agent_includes_project_agents_in_instruction(tmp_path):
  async def run():
    (tmp_path / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")

    agent = await _load_task_agent(
        task={
            "kind": "run_agent",
            "agent_id": "orca_adk",
            "project_root": str(tmp_path),
        },
        artifact_service=HandaArtifactService(root=str(tmp_path / ".handa")),
        parent_session_id="session-1",
        user_id=DEFAULT_USER_ID,
    )

    assert "Project Instructions (project_root/AGENTS.md)" in agent.instruction
    assert "Answer in Chinese." in agent.instruction

  asyncio.run(run())


def test_run_langgraph_task_returns_final_text_and_logs_events(tmp_path, monkeypatch):
  async def run():
    from types import SimpleNamespace

    from google.genai import types
    from src.agents.handa_langgraph import orca as lg_main

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    log = io.StringIO()

    scripted = [
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="files_read", args={"path": "README.md"}
                    )
                )
            ],
        ),
        types.Content(role="model", parts=[types.Part(text="LLM inspected README.md.")]),
    ]
    calls: list[int] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append(1)
      content = scripted[len(calls) - 1]
      return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    monkeypatch.setattr(lg_main, "_generate_model_response", fake_generate)

    final_text = await _run_langgraph_task(
        task={
            "kind": "run_agent",
            "agent_runtime": "langgraph",
            "agent_id": "orca",
            "prompt": "Inspect the repo.",
            "context": "Focus on top-level files.",
            "project_root": str(tmp_path),
            "session_id": "parent-session",
            "child_session_id": "child-session",
            "user_id": "user",
        },
        log_handle=log,
        session_service=HandaSessionService(root=str(tmp_path / ".handa")),
        storage_root=tmp_path / ".handa",
    )

    records = [json.loads(line) for line in log.getvalue().splitlines()]
    assert final_text == "LLM inspected README.md."
    kinds = [record["kind"] for record in records]
    assert kinds[0] == "langgraph.started"
    assert kinds[-1] == "agent_text"
    assert "langgraph.tool_call" in kinds
    assert "langgraph.tool_result" in kinds
    tool_calls = [
        record["payload"]["name"]
        for record in records
        if record["kind"] == "langgraph.tool_call"
    ]
    assert tool_calls == ["files_read"]
    assert records[-1]["payload"]["final"] is True
    stored = RuntimeEventStore(tmp_path / ".handa").list_events(
        session_id="child-session",
        runtime="langgraph",
    )
    assert [item["event"]["kind"] for item in stored] == kinds
    assert stored[0]["turn_id"] == "session:child-session"

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
