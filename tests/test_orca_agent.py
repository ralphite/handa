from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import types

from src.agents import native_runner
from src.agents.native_runner import CODE_AGENT_MAX_OUTPUT_TOKENS
from src.agents.orca.runner import run


def _model_response(
    content: types.Content,
    *,
    finish_reason: str | None = None,
) -> SimpleNamespace:
  candidate = SimpleNamespace(content=content)
  if finish_reason is not None:
    candidate.finish_reason = finish_reason
  return SimpleNamespace(candidates=[candidate])


def _function_call(name: str, args: dict) -> types.Content:
  return types.Content(
      role="model",
      parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
  )


def _text(text: str) -> types.Content:
  return types.Content(role="model", parts=[types.Part(text=text)])


def test_orca_runs_react_tool_loop(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    scripted = [
        _function_call("files_write", {"path": "notes.md", "content": "hello world"}),
        _function_call("files_read", {"path": "notes.md"}),
        _text("Created notes.md with the greeting."),
    ]
    calls: list[dict] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append({"contents": list(contents), "config": config})
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Create notes.md with a greeting.",
        project_root=str(project),
        session_id="session-orca-react",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert (project / "notes.md").read_text(encoding="utf-8") == "hello world"
    assert outcome.final_text == "Created notes.md with the greeting."
    assert len(calls) == 3
    assert calls[0]["contents"][0].parts[0].text == "Create notes.md with a greeting."
    assert "Project Instructions (project_root/AGENTS.md)" in calls[0]["config"].system_instruction
    assert "Answer in Chinese." in calls[0]["config"].system_instruction
    assert calls[0]["config"].tools

    kinds = [event["kind"] for event in events]
    assert kinds[0] == "orca.started"
    assert kinds[-2] == "agent_text"
    assert kinds[-1] == "orca.history_boundary"
    assert events[-2]["payload"]["text"] == outcome.final_text
    tool_calls = [
        event["payload"]["name"] for event in events if event["kind"] == "orca.tool_call"
    ]
    assert tool_calls == ["files_write", "files_read"]
    tool_results = [
        event["payload"] for event in events if event["kind"] == "orca.tool_result"
    ]
    assert all(result["ok"] for result in tool_results)
    assert all(result["call_id"].startswith("orca_call_") for result in tool_results)
    assert tool_results[0]["result"]["path"] == "notes.md"

  asyncio.run(run_test())


def test_orca_can_return_candidate_without_final_agent_text(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    async def fake_generate(*, client, model, contents, config):
      return _model_response(_text("Candidate answer."))

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)
    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Try to finish.",
        project_root=str(project),
        session_id="session-orca-candidate",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
        emit_final_agent_text=False,
    )

    assert outcome.final_text == "Candidate answer."
    assert "agent_text" not in [event["kind"] for event in events]
    assert events[-1]["kind"] == "orca.history_boundary"

  asyncio.run(run_test())


def test_orca_uses_code_agent_output_budget(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    calls: list[types.GenerateContentConfig] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append(config)
      return _model_response(_text("Done."))

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    async def emit_event(event):
      pass

    outcome = await run(
        prompt="Write a large file.",
        project_root=str(project),
        session_id="session-orca-output-budget",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.final_text == "Done."
    assert calls[0].max_output_tokens == CODE_AGENT_MAX_OUTPUT_TOKENS
    assert calls[0].max_output_tokens > 8192

  asyncio.run(run_test())


def test_orca_model_text_includes_finish_reason(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    async def fake_generate(*, client, model, contents, config):
      return _model_response(_text("Partial response."), finish_reason="MAX_TOKENS")

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)
    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Write a large file.",
        project_root=str(project),
        session_id="session-orca-finish-reason",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.final_text == "Partial response."
    model_text = next(event for event in events if event["kind"] == "orca.model_text")
    assert model_text["payload"]["finish_reason"] == "MAX_TOKENS"

  asyncio.run(run_test())


def test_orca_persists_history_across_turns(tmp_path, monkeypatch):
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
        session_id="session-orca-history",
        state={},
    )

    scripted = [_text("First answer."), _text("Second answer.")]
    calls: list[list[types.Content]] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append(list(contents))
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    async def emit_event(event):
      pass

    first = await run(
        prompt="First question.",
        project_root=str(project),
        session_id="session-orca-history",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )
    second = await run(
        prompt="Second question.",
        project_root=str(project),
        session_id="session-orca-history",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert first.final_text == "First answer."
    assert second.final_text == "Second answer."
    second_contents = calls[-1]
    assert [item.role for item in second_contents] == ["user", "model", "user"]
    assert second_contents[0].parts[0].text == "First question."
    assert second_contents[1].parts[0].text == "First answer."
    assert second_contents[2].parts[0].text == "Second question."

  asyncio.run(run_test())


def test_orca_reports_failed_tool_without_crashing(tmp_path, monkeypatch):
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

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Read a file that does not exist.",
        project_root=str(project),
        session_id="session-orca-tool-error",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.final_text == "That file is missing; nothing to report."
    failed = [
        event["payload"]
        for event in events
        if event["kind"] == "orca.tool_result" and not event["payload"]["ok"]
    ]
    assert len(failed) == 1
    assert failed[0]["name"] == "files_read"
    assert failed[0]["result"]["ok"] is False

  asyncio.run(run_test())


def test_orca_continues_past_twenty_four_tool_rounds(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    scripted = [
        _function_call("files_read", {"path": f"missing-{index}.md"})
        for index in range(25)
    ]
    scripted.append(_text("Finished after many tool rounds."))
    calls: list[dict] = []

    async def fake_generate(*, client, model, contents, config):
      calls.append({"contents": list(contents), "config": config})
      return _model_response(scripted[len(calls) - 1])

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Keep reading files until you can answer.",
        project_root=str(project),
        session_id="session-orca-many-tool-rounds",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.final_text == "Finished after many tool rounds."
    assert len(calls) == 26
    assert all(call["config"].tools for call in calls)
    assert not any(event["kind"] == "orca.tool_round_limit" for event in events)
    assert events[-2]["kind"] == "agent_text"
    assert events[-1]["kind"] == "orca.history_boundary"

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


def test_orca_pauses_on_request_user_input_and_resumes(tmp_path, monkeypatch):
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
        session_id="session-orca-user-input",
        state={},
    )

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

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    events = []

    async def emit_event(event):
      events.append(event)

    outcome = await run(
        prompt="Do the thing.",
        project_root=str(project),
        session_id="session-orca-user-input",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    pending = outcome.pending_user_input
    assert pending is not None
    assert pending["runtime"] == "native"
    assert pending["questions"][0]["id"] == "approach"
    assert not (project / "x.md").exists()
    state = session_service.read_state_sync("session-orca-user-input")
    assert state["handa:pending_user_input"]["request_id"] == pending["request_id"]
    assert any(event["kind"] == "orca.user_input_requested" for event in events)

    outcome2 = await run(
        prompt="",
        project_root=str(project),
        session_id="session-orca-user-input",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
        resume_user_input={
            "request_id": pending["request_id"],
            "response": {
                "answers": [{"id": "approach", "selected": ["A (recommended)"]}]
            },
        },
    )

    assert outcome2.final_text == "Done with approach A."
    assert (project / "x.md").read_text(encoding="utf-8") == "v1"
    response_parts = [
        part for part in calls[-1][-1].parts if part.function_response
    ]
    by_name = {
        part.function_response.name: part.function_response.response
        for part in response_parts
    }
    assert by_name["files_write"]["ok"] is True
    assert by_name["request_user_input"]["answers"] == [
        {"id": "approach", "selected": ["A (recommended)"]}
    ]
    assert any(event["kind"] == "orca.user_input_result" for event in events)

  asyncio.run(run_test())


def test_orca_returns_validation_error_without_pausing(tmp_path, monkeypatch):
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

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    async def emit_event(event):
      pass

    outcome = await run(
        prompt="Ask me something.",
        project_root=str(project),
        session_id="session-orca-user-input-invalid",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert outcome.pending_user_input is None
    assert outcome.final_text == "Asked nothing; proceeding with defaults."
    response = calls[-1][-1].parts[0].function_response.response
    assert response["ok"] is False
    assert "non-empty" in response["error"]["message"]

  asyncio.run(run_test())


def test_orca_excludes_user_input_tool_for_child_runs(tmp_path, monkeypatch):
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
        session_id="session-orca-child-depth",
        state={"handa:agent_run_depth": 1},
    )

    captured_config = {}

    async def fake_generate(*, client, model, contents, config):
      captured_config["config"] = config
      return _model_response(_text("Child finished."))

    monkeypatch.setattr(native_runner, "generate_model_response", fake_generate)

    async def emit_event(event):
      pass

    outcome = await run(
        prompt="Child task.",
        project_root=str(project),
        session_id="session-orca-child-depth",
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
