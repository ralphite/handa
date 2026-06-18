from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from google.genai.errors import APIError

from src.contract.goals import GOAL_STATUS_ACHIEVED
from src.contract.goals import GOAL_STATUS_BLOCKED
from src.contract.goals import GOAL_STATUS_MAX_ATTEMPTS
from src.goal_judge import GoalJudgeVerdict
from src.run_manager import run_agent_invocation
from src.run_outcome import RunOutcome
from src.runner import APP_NAME
from src.storage import HandaSessionService


def test_run_agent_invocation_calls_native_runner(tmp_path, monkeypatch):
  captured = {}

  async def fake_runner(**kwargs):
    captured.update(kwargs)
    await kwargs["emit_event"]({"kind": "agent_text", "payload": {"text": "done"}})
    return RunOutcome(final_text="done")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-1",
          user_id="user",
          agent_id="orca",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
          model_config_id="gemini-3.5-flash",
      )
  )

  assert result.final_text == "done"
  assert captured["prompt"] == "hello"
  assert captured["project_root"] == str(tmp_path.resolve())
  assert captured["model_config_id"] == "gemini-3.5-flash"
  assert captured["session_id"] == "session-1"
  assert captured["user_id"] == "user"
  assert events == [{"kind": "agent_text", "payload": {"text": "done"}}]


def test_run_agent_invocation_creates_phoenix_span(tmp_path, monkeypatch):
  spans = []

  @contextmanager
  def fake_trace_span(name, attributes=None):
    spans.append((name, attributes))
    yield

  async def fake_runner(**kwargs):
    return RunOutcome(final_text="done")

  monkeypatch.setattr("src.run_manager.trace_span", fake_trace_span)
  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-1",
          user_id="user",
          agent_id="orca",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
          model_config_id="gemini-3.5-flash",
      )
  )

  assert result.final_text == "done"
  assert spans == [
      (
          "handa.agent_invocation",
          {
              "session_id": "session-1",
              "user_id": "user",
              "agent_id": "orca",
              "project_root": str(tmp_path.resolve()),
              "model_config_id": "gemini-3.5-flash",
          },
      )
  ]


def test_run_agent_invocation_keeps_project_agents_out_of_user_prompt(
    tmp_path,
    monkeypatch,
):
  (tmp_path / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
  captured = {}

  async def fake_runner(**kwargs):
    captured.update(kwargs)
    return RunOutcome(final_text="done")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-1",
          user_id="user",
          agent_id="orca",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
      )
  )

  assert result.final_text == "done"
  assert captured["prompt"] == "hello"
  assert captured["project_root"] == str(tmp_path.resolve())


def test_run_agent_invocation_forwards_image_attachment(tmp_path, monkeypatch):
  image_path = tmp_path / "image.png"
  image_path.write_bytes(b"\x89PNG\r\n\x1a\nPIX")
  attachments = [
      {
          "storage_path": str(image_path),
          "mime_type": "image/png",
          "kind": "image",
          "filename": "image.png",
      }
  ]
  captured = {}

  async def fake_runner(**kwargs):
    captured.update(kwargs)
    return RunOutcome(final_text="done")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-1",
          user_id="user",
          agent_id="orca",
          input_text="look at this",
          attachments=attachments,
          on_event=on_event,
          project_root=str(tmp_path),
      )
  )

  assert result.final_text == "done"
  assert captured["attachments"] == attachments


def test_run_agent_invocation_injects_active_goal(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  session_service = HandaSessionService(root=str(storage_root))
  asyncio.run(
      session_service.create_session(
          app_name=APP_NAME,
          user_id="user",
          session_id="session-goal",
          state={
              "handa:goal": {
                  "text": "Ship the login fix.",
                  "status": "active",
                  "created_at": "2026-01-01T00:00:00Z",
                  "updated_at": "2026-01-01T00:00:00Z",
              }
          },
      )
  )
  captured = {}

  async def fake_runner(**kwargs):
    captured.update(kwargs)
    return RunOutcome(final_text="done")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)

  async def fake_judge(**kwargs):
    return GoalJudgeVerdict(status="achieved", reason="Prompt injection test.")

  monkeypatch.setattr("src.run_manager.judge_goal_completion", fake_judge)

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-goal",
          user_id="user",
          agent_id="orca",
          input_text="What next?",
          on_event=on_event,
          project_root=str(project),
      )
  )

  assert result.final_text == "done"
  assert "# Goal\nShip the login fix." in captured["prompt"]
  assert "This message is a Goal." in captured["prompt"]
  assert "A promise, plan, or status update is not a final answer." in captured["prompt"]
  assert "Do not claim completion without proof." in captured["prompt"]
  assert captured["prompt"].endswith("# User Message\nWhat next?")


def test_goal_judge_continue_runs_next_attempt_in_same_invocation(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  session_service = HandaSessionService(root=str(storage_root))
  asyncio.run(
      session_service.create_session(
          app_name=APP_NAME,
          user_id="user",
          session_id="session-goal-loop",
          state={
              "handa:goal": {
                  "goal_id": "goal_test",
                  "text": "Make the command return true.",
                  "status": "active",
                  "max_attempts": 3,
                  "created_at": "2026-01-01T00:00:00Z",
                  "updated_at": "2026-01-01T00:00:00Z",
              }
          },
      )
  )
  prompts = []

  async def fake_runner(**kwargs):
    prompts.append(kwargs["prompt"])
    assert kwargs["emit_final_agent_text"] is False
    text = "not yet" if len(prompts) == 1 else "done with proof"
    await kwargs["emit_event"](
        {"kind": "orca.model_text", "payload": {"text": text, "has_tool_calls": False}}
    )
    return RunOutcome(final_text=text)

  verdicts = []

  async def fake_judge(**kwargs):
    verdicts.append(kwargs)
    if len(verdicts) == 1:
      return GoalJudgeVerdict(
          status="continue",
          reason="No successful command result is visible.",
          next_request="Run the command again and show the result.",
      )
    return GoalJudgeVerdict(
        status="achieved",
        reason="The second attempt contains the required proof.",
        citations=["orca_evt_2"],
    )

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  monkeypatch.setattr("src.run_manager.judge_goal_completion", fake_judge)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-goal-loop",
          user_id="user",
          agent_id="orca",
          input_text="Make the command return true.",
          on_event=on_event,
          project_root=str(project),
      )
  )

  assert result.final_text == "done with proof"
  assert result.goal_status == GOAL_STATUS_ACHIEVED
  assert len(prompts) == 2
  assert prompts[0].endswith("# User Message\nMake the command return true.")
  assert "The goal is not achieved yet." in prompts[1]
  assert "# Required next action" in prompts[1]
  assert "Run the command again and show the result." in prompts[1]
  assert "Do not respond with a promise or plan" in prompts[1]
  assert "Continue the missing work now" in prompts[1]
  assert [event["kind"] for event in events].count("goal_judge_verdict") == 2
  assert [event["kind"] for event in events].count("goal_continue") == 1
  assert [event["kind"] for event in events].count("goal_attempt_started") == 2
  scoped_model_events = [
      event for event in events if event["kind"] == "orca.model_text"
  ]
  assert scoped_model_events[0]["payload"]["goal_id"] == "goal_test"
  assert scoped_model_events[0]["payload"]["goal_attempt_id"].startswith("goal_attempt_")
  assert scoped_model_events[0]["payload"]["goal_attempt_number"] == 1
  assert verdicts[0]["attempt_id"] == scoped_model_events[0]["payload"]["goal_attempt_id"]
  continue_event = next(event for event in events if event["kind"] == "goal_continue")
  assert continue_event["payload"]["previous_goal_attempt_id"] == verdicts[0]["attempt_id"]
  assert continue_event["payload"]["next_attempt_number"] == 2
  final_events = [event for event in events if event["kind"] == "agent_text"]
  assert len(final_events) == 1
  assert final_events[0]["payload"]["final"] is True
  stored = session_service.read_state_sync("session-goal-loop")["handa:goal"]
  assert stored["status"] == GOAL_STATUS_ACHIEVED
  assert stored["reason"] == "The second attempt contains the required proof."


def test_goal_judge_blocked_marks_goal_incomplete(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  session_service = HandaSessionService(root=str(storage_root))
  asyncio.run(
      session_service.create_session(
          app_name=APP_NAME,
          user_id="user",
          session_id="session-goal-blocked",
          state={
              "handa:goal": {
                  "goal_id": "goal_blocked",
                  "text": "Finish external deployment.",
                  "status": "active",
              }
          },
      )
  )

  async def fake_runner(**kwargs):
    return RunOutcome(final_text="I cannot deploy from here.")

  async def fake_judge(**kwargs):
    return GoalJudgeVerdict(
        status="blocked",
        reason="Deployment requires credentials that are not available.",
    )

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  monkeypatch.setattr("src.run_manager.judge_goal_completion", fake_judge)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-goal-blocked",
          user_id="user",
          agent_id="orca",
          input_text="Finish external deployment.",
          on_event=on_event,
          project_root=str(project),
      )
  )

  assert result.goal_status == GOAL_STATUS_BLOCKED
  assert "Goal not completed." in result.final_text
  assert "requires credentials" in result.final_text
  stored = session_service.read_state_sync("session-goal-blocked")["handa:goal"]
  assert stored["status"] == GOAL_STATUS_BLOCKED


def test_goal_judge_continue_stops_at_max_attempts(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  session_service = HandaSessionService(root=str(storage_root))
  asyncio.run(
      session_service.create_session(
          app_name=APP_NAME,
          user_id="user",
          session_id="session-goal-max",
          state={
              "handa:goal": {
                  "goal_id": "goal_max",
                  "text": "Make flaky condition true.",
                  "status": "active",
                  "max_attempts": 2,
              }
          },
      )
  )
  calls = 0

  async def fake_runner(**kwargs):
    nonlocal calls
    calls += 1
    return RunOutcome(final_text=f"attempt {calls}")

  async def fake_judge(**kwargs):
    return GoalJudgeVerdict(
        status="continue",
        reason="The condition is still false.",
        next_request="Try again.",
    )

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  monkeypatch.setattr("src.run_manager.judge_goal_completion", fake_judge)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-goal-max",
          user_id="user",
          agent_id="orca",
          input_text="Make flaky condition true.",
          on_event=on_event,
          project_root=str(project),
      )
  )

  assert calls == 2
  assert result.goal_status == GOAL_STATUS_MAX_ATTEMPTS
  assert "not judged complete after 2 attempts" in result.final_text
  assert [event["kind"] for event in events].count("goal_continue") == 1
  stored = session_service.read_state_sync("session-goal-max")["handa:goal"]
  assert stored["status"] == GOAL_STATUS_MAX_ATTEMPTS


def test_run_agent_invocation_runs_invocation_hooks(tmp_path, monkeypatch):
  calls = []

  async def fake_runner(**kwargs):
    calls.append("runner")
    await kwargs["emit_event"]({"kind": "agent_text", "payload": {"text": "done"}})
    return RunOutcome(final_text="done")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-hooks",
          user_id="user",
          agent_id="orca",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
          hooks=[
              {
                  "id": "pre",
                  "trigger": "pre_invocation",
                  "command": "python3 -c 'print(\"pre\")'",
              },
              {
                  "id": "post",
                  "trigger": "post_invocation",
                  "command": "python3 -c 'print(\"post\")'",
              },
          ],
      )
  )

  assert result.final_text == "done"
  assert calls == ["runner"]
  assert [event["kind"] for event in events] == [
      "hook.started",
      "hook.completed",
      "agent_text",
      "hook.started",
      "hook.completed",
  ]


def test_run_agent_invocation_retries_transient_error_before_output(tmp_path, monkeypatch):
  calls = 0

  async def fake_runner(**kwargs):
    nonlocal calls
    calls += 1
    if calls == 1:
      raise APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
    await kwargs["emit_event"]({"kind": "agent_text", "payload": {"text": "recovered"}})
    return RunOutcome(final_text="recovered")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  sleeps = []

  async def fake_sleep(delay):
    sleeps.append(delay)

  monkeypatch.setattr(asyncio, "sleep", fake_sleep)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-1",
          user_id="user",
          agent_id="orca",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
      )
  )

  assert result.final_text == "recovered"
  assert calls == 2
  assert sleeps == [60.0]
  assert [event["kind"] for event in events] == ["agent_text"]


def test_run_agent_invocation_does_not_retry_after_output(tmp_path, monkeypatch):
  calls = 0

  async def fake_runner(**kwargs):
    nonlocal calls
    calls += 1
    await kwargs["emit_event"]({"kind": "agent_text", "payload": {"text": "partial"}})
    raise APIError(503, {"error": {"code": 503}})

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  sleeps = []

  async def fake_sleep(delay):
    sleeps.append(delay)

  monkeypatch.setattr(asyncio, "sleep", fake_sleep)
  events = []

  async def on_event(event):
    events.append(event)

  with pytest.raises(APIError):
    asyncio.run(
        run_agent_invocation(
              session_id="session-1",
            user_id="user",
            agent_id="orca",
            input_text="hello",
            on_event=on_event,
            project_root=str(tmp_path),
        )
    )

  assert calls == 1
  assert sleeps == []
  assert [event["payload"]["text"] for event in events] == ["partial"]


def test_run_agent_invocation_retries_after_started_before_output(tmp_path, monkeypatch):
  calls = 0

  async def fake_runner(**kwargs):
    nonlocal calls
    calls += 1
    await kwargs["emit_event"]({"kind": "orca.started"})
    if calls == 1:
      raise APIError(503, {"error": {"code": 503}})
    await kwargs["emit_event"]({"kind": "agent_text", "payload": {"text": "recovered"}})
    return RunOutcome(final_text="recovered")

  monkeypatch.setattr("src.run_manager.load_native_agent", lambda agent_id: fake_runner)
  sleeps = []

  async def fake_sleep(delay):
    sleeps.append(delay)

  monkeypatch.setattr(asyncio, "sleep", fake_sleep)
  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          session_id="session-1",
          user_id="user",
          agent_id="orca",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
      )
  )

  assert result.final_text == "recovered"
  assert calls == 2
  assert sleeps == [2.0]
  assert [event["kind"] for event in events] == [
      "orca.started",
      "orca.started",
      "agent_text",
  ]
