from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import types

from src.agents.handa_langgraph import orca
from src.agents.handa_langgraph.orca import run


def _model_response(content: types.Content) -> SimpleNamespace:
  return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _function_call(name: str, args: dict) -> types.Content:
  return types.Content(
      role="model",
      parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
  )


def _text(text: str) -> types.Content:
  return types.Content(role="model", parts=[types.Part(text=text)])


def test_langgraph_main_runs_react_tool_loop(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    # Scripted model turns: write a file, read it back, then answer in text.
    scripted = [
        _function_call("files_write", {"path": "notes.md", "content": "hello world"}),
        _function_call("files_read", {"path": "notes.md"}),
        _text("Created notes.md with the greeting."),
    ]
    calls: list[dict] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append({"contents": list(contents), "config": config})
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(orca, "_generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Create notes.md with a greeting.",
        project_root=str(project),
        session_id="session-langgraph-react",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    # The write tool actually ran against the project root.
    assert (project / "notes.md").read_text(encoding="utf-8") == "hello world"
    final_text = outcome.final_text
    assert final_text == "Created notes.md with the greeting."

    # Three model turns: two tool rounds plus the final text answer.
    assert len(calls) == 3
    first_user_text = calls[0]["contents"][0].parts[0].text
    assert first_user_text == "Create notes.md with a greeting."
    system_instruction = calls[0]["config"].system_instruction
    assert "Project Instructions (project_root/AGENTS.md)" in system_instruction
    assert "Answer in Chinese." in system_instruction
    # Tool declarations are exposed to the model while the loop can still run.
    assert calls[0]["config"].tools, "model should receive tool declarations"

    kinds = [event["kind"] for event in events]
    assert kinds[0] == "langgraph.started"
    assert kinds[-1] == "agent_text"
    assert events[-1]["payload"]["final"] is True
    assert events[-1]["payload"]["text"] == final_text

    tool_calls = [
        event["payload"]["name"]
        for event in events
        if event["kind"] == "langgraph.tool_call"
    ]
    assert tool_calls == ["files_write", "files_read"]
    tool_results = [
        event["payload"]
        for event in events
        if event["kind"] == "langgraph.tool_result"
    ]
    assert all(result["ok"] for result in tool_results)
    assert all(result["call_id"].startswith("lg_call_") for result in tool_results)
    assert tool_results[0]["result"]["ok"] is True
    assert tool_results[0]["result"]["path"] == "notes.md"

  asyncio.run(run_test())


def test_langgraph_main_reports_failed_tool_without_crashing(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    scripted = [
        _function_call("files_read", {"path": "does-not-exist.md"}),
        _text("That file is missing; nothing to report."),
    ]
    calls: list[int] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append(1)
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(orca, "_generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Read a file that does not exist.",
        project_root=str(project),
        session_id="session-langgraph-tool-error",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    final_text = outcome.final_text
    assert final_text == "That file is missing; nothing to report."
    failed = [
        event["payload"]
        for event in events
        if event["kind"] == "langgraph.tool_result" and not event["payload"]["ok"]
    ]
    assert len(failed) == 1
    assert failed[0]["name"] == "files_read"
    assert failed[0]["result"]["ok"] is False
    assert failed[0]["call_id"].startswith("lg_call_")

  asyncio.run(run_test())


def test_langgraph_main_stops_when_tool_round_limit_is_exhausted(
    tmp_path,
    monkeypatch,
):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(orca, "MAX_TOOL_ROUNDS", 1)

    scripted = [
        _function_call("files_read", {"path": "first.md"}),
        _function_call("files_read", {"path": "second.md"}),
    ]
    calls: list[dict] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append({"contents": list(contents), "config": config})
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(orca, "_generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Keep reading files.",
        project_root=str(project),
        session_id="session-tool-round-limit",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert "Stopped after 1 tool round" in outcome.final_text
    assert "LangGraph recursion limit" in outcome.final_text
    assert len(calls) == 2
    assert calls[0]["config"].tools
    assert not calls[1]["config"].tools

    tool_calls = [
        event["payload"]["name"]
        for event in events
        if event["kind"] == "langgraph.tool_call"
    ]
    assert tool_calls == ["files_read"]
    assert any(event["kind"] == "langgraph.tool_round_limit" for event in events)
    assert events[-1]["kind"] == "agent_text"
    assert events[-1]["payload"]["final"] is True

  asyncio.run(run_test())


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


def test_langgraph_main_pauses_on_request_user_input_and_resumes(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    from src.runner import APP_NAME
    from src.storage import HandaSessionService

    session_service = HandaSessionService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-user-input",
        state={},
    )

    # One model turn issues a side-effect tool call AND a user-input request;
    # after the user answers, the model produces the final text.
    scripted = [
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="files_write",
                        args={"path": "x.md", "content": "v1"},
                    )
                ),
                types.Part(
                    function_call=types.FunctionCall(
                        name="request_user_input",
                        args={"questions": _QUESTIONS},
                    )
                ),
            ],
        ),
        _text("Done with approach A."),
    ]
    calls: list[list] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append(list(contents))
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(orca, "_generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Do the thing.",
        project_root=str(project),
        session_id="session-user-input",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    pending = outcome.pending_user_input
    assert pending is not None
    assert pending["runtime"] == "langgraph"
    assert pending["questions"][0]["id"] == "approach"
    assert pending["request_id"].startswith("uireq_")
    # The side-effect tool must not run before the interrupt.
    assert not (project / "x.md").exists()
    # The pending request is persisted in session state for the Web layer.
    state = session_service.read_state_sync("session-user-input")
    assert state["handa:pending_user_input"]["request_id"] == pending["request_id"]
    assert any(
        event["kind"] == "langgraph.user_input_requested" for event in events
    )

    # Resume through a fresh run() call, as after a server restart.
    outcome2 = await run(
        prompt="",
        project_root=str(project),
        session_id="session-user-input",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
        resume_user_input={
            "answers": [{"id": "approach", "selected": ["A (recommended)"]}]
        },
    )

    assert outcome2.final_text == "Done with approach A."
    # The side-effect tool ran exactly once, on resume.
    assert (project / "x.md").read_text(encoding="utf-8") == "v1"
    # Both calls received paired function responses in the next model turn.
    response_parts = [
        part for part in calls[-1][-1].parts if part.function_response
    ]
    by_name = {part.function_response.name: part.function_response.response
               for part in response_parts}
    assert by_name["files_write"]["ok"] is True
    assert by_name["request_user_input"]["answers"] == [
        {"id": "approach", "selected": ["A (recommended)"]}
    ]
    assert any(
        event["kind"] == "langgraph.user_input_result" for event in events
    )

  asyncio.run(run_test())


def test_langgraph_main_returns_validation_error_without_pausing(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    scripted = [
        _function_call("request_user_input", {"questions": []}),
        _text("Asked nothing; proceeding with defaults."),
    ]
    calls: list[list] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append(list(contents))
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(orca, "_generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Ask me something.",
        project_root=str(project),
        session_id="session-user-input-invalid",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    # Invalid arguments do not pause the run; the model gets the error back.
    assert outcome.pending_user_input is None
    assert outcome.final_text == "Asked nothing; proceeding with defaults."
    response = calls[-1][-1].parts[0].function_response.response
    assert response["ok"] is False
    assert "non-empty" in response["error"]["message"]

  asyncio.run(run_test())


def test_langgraph_main_excludes_user_input_tool_for_child_runs(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    from src.runner import APP_NAME
    from src.storage import HandaSessionService

    session_service = HandaSessionService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-child-depth",
        state={"handa:agent_run_depth": 1},
    )

    captured_config = {}

    async def fake_generate(*, client, model, contents, config):
      captured_config["config"] = config
      return _model_response(_text("Child finished."))

    monkeypatch.setattr(orca, "_generate_model_response", fake_generate)

    async def emit_event(event):
      pass

    outcome = await run(
        prompt="Child task.",
        project_root=str(project),
        session_id="session-child-depth",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.final_text == "Child finished."
    declared = [
        declaration.name
        for tool in captured_config["config"].tools
        for declaration in tool.function_declarations
    ]
    assert "request_user_input" not in declared
    assert "files_read" in declared

  asyncio.run(run_test())
