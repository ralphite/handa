from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.genai import types

from src import run_manager
from src.agents.handa_adk.tools.registry import create_agent_tools
from src.run_manager import _pending_adk_user_input
from src.run_manager import _user_input_response_message


_QUESTIONS = [
    {
        "id": "approach",
        "prompt": "Which approach?",
        "options": [
            {"label": "A (recommended)", "description": "fast"},
            {"label": "B"},
        ],
        "multi_select": False,
        "allow_free_text": True,
    }
]


def _tool_context(state: dict | None = None) -> SimpleNamespace:
  return SimpleNamespace(state=state or {})


def test_registry_builds_long_running_tool():
  tools = create_agent_tools(["request_user_input", "files_read"])
  by_name = {tool.name: tool for tool in tools}
  assert isinstance(by_name["request_user_input"], LongRunningFunctionTool)
  assert by_name["request_user_input"].is_long_running
  assert isinstance(by_name["files_read"], FunctionTool)
  assert not by_name["files_read"].is_long_running


def test_wrapper_preserves_none_for_long_running_tool():
  tool = create_agent_tools(["request_user_input"])[0]
  result = tool.func(questions=_QUESTIONS, tool_context=_tool_context())
  assert result is None


def test_wrapper_returns_error_payload_for_invalid_questions():
  tool = create_agent_tools(["request_user_input"])[0]
  result = tool.func(questions=[], tool_context=_tool_context())
  assert result["ok"] is False
  assert "non-empty" in result["error"]["message"]


def test_tool_rejects_child_agent_runs():
  tool = create_agent_tools(["request_user_input"])[0]
  result = tool.func(
      questions=_QUESTIONS,
      tool_context=_tool_context({"handa:agent_run_depth": 1}),
  )
  assert result["ok"] is False
  assert "child agent runs" in result["error"]["message"]


def _function_call_event(name: str, args: dict, call_id: str, long_running: bool):
  return SimpleNamespace(
      long_running_tool_ids={call_id} if long_running else set(),
      content=types.Content(
          role="model",
          parts=[
              types.Part(
                  function_call=types.FunctionCall(id=call_id, name=name, args=args)
              )
          ],
      ),
      is_final_response=lambda: long_running,
  )


def test_pending_adk_user_input_detects_paused_call():
  event = _function_call_event(
      "request_user_input", {"questions": _QUESTIONS}, "call-1", long_running=True
  )
  pending = _pending_adk_user_input(event)
  assert pending is not None
  assert pending["runtime"] == "adk"
  assert pending["function_call_id"] == "call-1"
  assert pending["questions"][0]["id"] == "approach"


def test_pending_adk_user_input_ignores_other_calls():
  event = _function_call_event(
      "files_read", {"path": "x"}, "call-2", long_running=False
  )
  assert _pending_adk_user_input(event) is None
  # Invalid arguments mean the tool already answered with an error response.
  invalid = _function_call_event(
      "request_user_input", {"questions": []}, "call-3", long_running=True
  )
  assert _pending_adk_user_input(invalid) is None


def test_user_input_response_message_pairs_function_call_id():
  message = _user_input_response_message(
      {
          "request_id": "uireq_x",
          "function_call_id": "call-1",
          "response": {"answers": [{"id": "approach", "selected": ["B"]}]},
      }
  )
  part = message.parts[0]
  assert message.role == "user"
  assert part.function_response.id == "call-1"
  assert part.function_response.name == "request_user_input"
  assert part.function_response.response["answers"][0]["selected"] == ["B"]


def test_user_input_response_message_requires_call_id():
  with pytest.raises(ValueError, match="function_call_id"):
    _user_input_response_message({"response": {"cancelled": True}})


def test_adk_invocation_pauses_and_resumes_on_user_input(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

  from src.runner import APP_NAME
  from src.runner import HandaServices
  from src.storage import HandaArtifactService
  from src.storage import HandaSessionService

  session_service = HandaSessionService()
  asyncio.run(
      session_service.create_session(
          app_name=APP_NAME,
          user_id="user",
          session_id="session-adk-input",
          state={},
      )
  )
  services = HandaServices(
      storage_root=tmp_path / ".handa",
      session_service=session_service,
      artifact_service=HandaArtifactService(),
  )

  captured: dict = {}

  class FakeRunner:
    def __init__(self, events):
      self._events = events

    async def run_async(self, *, user_id, session_id, new_message, run_config):
      captured["new_message"] = new_message
      for event in self._events:
        yield event

  pause_event = _function_call_event(
      "request_user_input", {"questions": _QUESTIONS}, "call-9", long_running=True
  )
  final_event = SimpleNamespace(
      long_running_tool_ids=set(),
      content=types.Content(role="model", parts=[types.Part(text="All done.")]),
      is_final_response=lambda: True,
  )
  runners = [FakeRunner([pause_event]), FakeRunner([final_event])]
  monkeypatch.setattr(
      run_manager, "create_runner", lambda services, agent: runners.pop(0)
  )
  monkeypatch.setattr(
      run_manager, "_apply_model_config", lambda agent, model_config_id: SimpleNamespace(
          model="fake-model", generate_content_config=None
      )
  )
  monkeypatch.setattr(run_manager, "load_agent", lambda agent_id, project_root=None: SimpleNamespace())

  async def on_event(event):
    pass

  outcome = asyncio.run(
      run_manager.run_agent_invocation(
          services=services,
          session_id="session-adk-input",
          user_id="user",
          agent_id="orca_adk",
          input_text="Do the thing.",
          on_event=on_event,
          project_root=str(tmp_path),
      )
  )

  pending = outcome.pending_user_input
  assert pending is not None
  assert pending["function_call_id"] == "call-9"
  state = session_service.read_state_sync("session-adk-input")
  assert state["handa:pending_user_input"]["request_id"] == pending["request_id"]

  outcome2 = asyncio.run(
      run_manager.run_agent_invocation(
          services=services,
          session_id="session-adk-input",
          user_id="user",
          agent_id="orca_adk",
          input_text="",
          on_event=on_event,
          project_root=str(tmp_path),
          resume_user_input={
              "request_id": pending["request_id"],
              "function_call_id": pending["function_call_id"],
              "response": {"answers": [{"id": "approach", "selected": ["B"]}]},
          },
      )
  )

  assert outcome2.final_text == "All done."
  resume_message = captured["new_message"]
  assert resume_message.parts[0].function_response.id == "call-9"
  assert resume_message.parts[0].function_response.response["answers"][0][
      "selected"
  ] == ["B"]
