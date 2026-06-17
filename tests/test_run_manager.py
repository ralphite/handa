from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.genai.errors import APIError

from src.run_manager import run_agent_invocation
from src.run_outcome import RunOutcome


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
