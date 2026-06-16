from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from google.genai import types

from src.agents.browser import runner as browser
from src.agents.browser.loader import MAIN_CONFIG_PATH
from src.agents.browser.runner import run
from src.agents.orca import tools as native_tools
from src.runner import APP_NAME
from src.storage import HandaSessionService


def _model_response(content: types.Content) -> SimpleNamespace:
  return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _function_call(name: str, args: dict) -> types.Content:
  return types.Content(
      role="model",
      parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
  )


def _text(text: str) -> types.Content:
  return types.Content(role="model", parts=[types.Part(text=text)])


def test_browser_runs_native_tool_loop(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    session_service = HandaSessionService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-browser-tool-loop",
        state={},
    )

    captured_tool_args = {}

    async def fake_open(*, session_id, url, wait_until="domcontentloaded", project_root=None):
      captured_tool_args.update(
          {
              "session_id": session_id,
              "url": url,
              "wait_until": wait_until,
              "project_root": project_root,
          }
      )
      return {"opened": True, "url": url}

    monkeypatch.setattr(native_tools.browser_tools, "open", fake_open)

    scripted = [
        _function_call(
            "browser_open",
            {"url": "http://example.test", "wait_until": "load"},
        ),
        _text("Opened the page."),
    ]
    calls: list[dict] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append({"contents": list(contents), "config": config})
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(browser, "_generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Open the page.",
        project_root=str(project),
        session_id="session-browser-tool-loop",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.final_text == "Opened the page."
    assert captured_tool_args == {
        "session_id": "session-browser-tool-loop",
        "url": "http://example.test",
        "wait_until": "load",
        "project_root": str(project.resolve()),
    }
    declared = [
        declaration.name
        for tool in calls[0]["config"].tools
        for declaration in tool.function_declarations
    ]
    assert "browser_open" in declared
    assert "commands_run" not in declared

    kinds = [event["kind"] for event in events]
    assert kinds[0] == "browser.started"
    assert kinds[-2] == "agent_text"
    assert kinds[-1] == "browser.history_boundary"
    tool_calls = [
        event["payload"] for event in events if event["kind"] == "browser.tool_call"
    ]
    tool_results = [
        event["payload"] for event in events if event["kind"] == "browser.tool_result"
    ]
    assert tool_calls[0]["name"] == "browser_open"
    assert tool_results[0]["ok"] is True
    assert tool_results[0]["call_id"].startswith("browser_call_")
    state = session_service.read_state_sync("session-browser-tool-loop")
    assert len(state["handa:browser_history"]) == 4

  asyncio.run(run_test())


def test_browser_config_is_native_only():
  native_config = json.loads(MAIN_CONFIG_PATH.read_text(encoding="utf-8"))

  assert native_config["name"] == "browser"
  assert native_config["tools"]


def test_browser_agent_package_has_no_framework_dependency():
  for path in MAIN_CONFIG_PATH.parent.glob("*.py"):
    text = path.read_text(encoding="utf-8")
    assert "google.adk" not in text
    assert "langgraph" not in text
  assert "google.adk" not in MAIN_CONFIG_PATH.read_text(encoding="utf-8")
