from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.agents.run_config import StreamingMode

from google.genai.errors import APIError

from src.run_manager import run_agent_invocation
from src.run_outcome import RunOutcome


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(True, StreamingMode.SSE), (False, StreamingMode.NONE)],
)
def test_run_agent_invocation_sets_streaming_mode(enabled, expected, monkeypatch):
  captured = {}

  class FakeRunner:
    async def run_async(self, **kwargs):
      captured["run_config"] = kwargs["run_config"]
      yield SimpleNamespace(
          content=SimpleNamespace(parts=[SimpleNamespace(text="done")]),
          is_final_response=lambda: True,
      )

  monkeypatch.setattr(
      "src.run_manager.load_agent",
      lambda agent_id, *, project_root=None: SimpleNamespace(),
  )
  monkeypatch.setattr(
      "src.run_manager.create_runner",
      lambda services, agent: FakeRunner(),
  )

  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          services=SimpleNamespace(),
          session_id="session-1",
          user_id="user",
          agent_id="orca_adk",
          input_text="hello",
          on_event=on_event,
          streaming_mode_enabled=enabled,
      )
  )

  assert result.final_text == "done"
  assert len(events) == 1
  assert captured["run_config"].streaming_mode == expected


def test_run_agent_invocation_includes_project_agents_md_for_adk(
    tmp_path,
    monkeypatch,
):
  (tmp_path / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
  captured = {}

  class FakeRunner:
    async def run_async(self, **kwargs):
      captured["message"] = kwargs["new_message"]
      yield SimpleNamespace(
          content=SimpleNamespace(parts=[SimpleNamespace(text="done")]),
          is_final_response=lambda: True,
      )

  def fake_load_agent(agent_id, *, project_root=None):
    captured["agent_id"] = agent_id
    captured["project_root"] = project_root
    return SimpleNamespace()

  monkeypatch.setattr("src.run_manager.load_agent", fake_load_agent)
  monkeypatch.setattr(
      "src.run_manager.create_runner",
      lambda services, agent: FakeRunner(),
  )

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          services=SimpleNamespace(),
          session_id="session-1",
          user_id="user",
          agent_id="orca_adk",
          input_text="hello",
          on_event=on_event,
          project_root=str(tmp_path),
      )
  )

  message_text = captured["message"].parts[0].text
  assert result.final_text == "done"
  assert captured["agent_id"] == "orca_adk"
  assert captured["project_root"] == str(tmp_path.resolve())
  assert message_text == "hello"


def test_run_agent_invocation_forwards_image_attachment_to_adk(tmp_path, monkeypatch):
  image_path = tmp_path / "image.png"
  image_path.write_bytes(b"\x89PNG\r\n\x1a\nPIX")
  captured = {}

  class FakeRunner:
    async def run_async(self, **kwargs):
      captured["message"] = kwargs["new_message"]
      yield SimpleNamespace(
          content=SimpleNamespace(parts=[SimpleNamespace(text="done")]),
          is_final_response=lambda: True,
      )

  monkeypatch.setattr(
      "src.run_manager.load_agent",
      lambda agent_id, *, project_root=None: SimpleNamespace(),
  )
  monkeypatch.setattr(
      "src.run_manager.create_runner",
      lambda services, agent: FakeRunner(),
  )

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          services=SimpleNamespace(),
          session_id="session-1",
          user_id="user",
          agent_id="orca_adk",
          input_text="look at this",
          attachments=[
              {
                  "storage_path": str(image_path),
                  "mime_type": "image/png",
                  "kind": "image",
                  "filename": "image.png",
              }
          ],
          on_event=on_event,
      )
  )

  parts = captured["message"].parts
  assert result.final_text == "done"
  assert parts[0].text == "look at this"
  # The image must reach the model as binary inline_data, not a text placeholder.
  assert parts[1].inline_data is not None
  assert parts[1].inline_data.mime_type == "image/png"
  assert parts[1].inline_data.data == b"\x89PNG\r\n\x1a\nPIX"


def test_run_agent_invocation_forwards_image_attachment_to_langgraph(
    tmp_path,
    monkeypatch,
):
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

  monkeypatch.setattr(
      "src.run_manager.load_langgraph_agent",
      lambda agent_id: fake_runner,
  )

  async def on_event(event):
    return None

  result = asyncio.run(
      run_agent_invocation(
          services=SimpleNamespace(),
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
  # Raw attachments (incl. image bytes) must be forwarded to the langgraph
  # runner so it can build inline_data parts -- not flattened to a placeholder.
  assert captured["attachments"] == attachments
  assert "attached file:" not in captured.get("context", "")


def test_run_agent_invocation_retries_transient_error_before_output(monkeypatch):
  class FlakyRunner:
    def __init__(self):
      self.calls = 0

    async def run_async(self, **kwargs):
      self.calls += 1
      if self.calls == 1:
        raise APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
      yield SimpleNamespace(
          content=SimpleNamespace(parts=[SimpleNamespace(text="recovered")]),
          is_final_response=lambda: True,
      )

  runner = FlakyRunner()
  monkeypatch.setattr(
      "src.run_manager.load_agent",
      lambda agent_id, *, project_root=None: SimpleNamespace(),
  )
  monkeypatch.setattr("src.run_manager.create_runner", lambda services, agent: runner)
  sleeps = []

  async def fake_sleep(delay):
    sleeps.append(delay)

  monkeypatch.setattr(asyncio, "sleep", fake_sleep)

  events = []

  async def on_event(event):
    events.append(event)

  result = asyncio.run(
      run_agent_invocation(
          services=SimpleNamespace(),
          session_id="session-1",
          user_id="user",
          agent_id="orca_adk",
          input_text="hello",
          on_event=on_event,
      )
  )

  # The 429 fired before any event streamed, so the whole turn is re-run and
  # only the successful attempt's event reaches on_event.
  assert result.final_text == "recovered"
  assert runner.calls == 2
  assert sleeps == [60.0]
  assert len(events) == 1


def test_run_agent_invocation_does_not_retry_after_output(monkeypatch):
  class MidStreamFailRunner:
    def __init__(self):
      self.calls = 0

    async def run_async(self, **kwargs):
      self.calls += 1
      yield SimpleNamespace(
          content=SimpleNamespace(parts=[SimpleNamespace(text="partial")]),
          is_final_response=lambda: False,
      )
      raise APIError(503, {"error": {"code": 503}})

  runner = MidStreamFailRunner()
  monkeypatch.setattr(
      "src.run_manager.load_agent",
      lambda agent_id, *, project_root=None: SimpleNamespace(),
  )
  monkeypatch.setattr("src.run_manager.create_runner", lambda services, agent: runner)
  sleeps = []

  async def fake_sleep(delay):
    sleeps.append(delay)

  monkeypatch.setattr(asyncio, "sleep", fake_sleep)

  events = []

  async def on_event(event):
    events.append(event)

  # Output already streamed, so the transient error must surface instead of
  # re-running (which would duplicate the visible partial output).
  with pytest.raises(APIError):
    asyncio.run(
        run_agent_invocation(
            services=SimpleNamespace(),
            session_id="session-1",
            user_id="user",
            agent_id="orca_adk",
            input_text="hello",
            on_event=on_event,
        )
    )

  assert runner.calls == 1
  assert sleeps == []
  assert len(events) == 1
