from __future__ import annotations

import asyncio
import io
import json
import sys
from urllib.parse import parse_qs

import httpx

from src import handacli
from src.handacli import HandaCliResult


class FakeWebApi:
  """Scripted stand-in for the Handa web API, served via httpx.MockTransport."""

  def __init__(
      self,
      *,
      projects: list[dict] | None = None,
      poll_statuses: list[str] | None = None,
      final_text: str = "done",
      error_type: str | None = None,
      error_message: str | None = None,
      submit_status_code: int | None = None,
      submit_detail: str | None = None,
      poll_transport_errors: int = 0,
      input_token_count: int = 0,
      output_token_count: int = 0,
      total_token_count: int = 0,
      tool_call_count: int = 0,
      tool_success_count: int = 0,
      tool_fail_count: int = 0,
      tool_duration_ms: int = 0,
      file_lines_added: int = 0,
      file_lines_removed: int = 0,
      active_seconds: float = 0.0,
  ):
    self.projects = list(projects or [])
    self.poll_statuses = list(poll_statuses or ["queued", "running", "completed"])
    self.final_text = final_text
    self.error_type = error_type
    self.error_message = error_message
    self.submit_status_code = submit_status_code
    self.submit_detail = submit_detail
    self.poll_transport_errors = poll_transport_errors
    self.input_token_count = input_token_count
    self.output_token_count = output_token_count
    self.total_token_count = total_token_count
    self.tool_call_count = tool_call_count
    self.tool_success_count = tool_success_count
    self.tool_fail_count = tool_fail_count
    self.tool_duration_ms = tool_duration_ms
    self.file_lines_added = file_lines_added
    self.file_lines_removed = file_lines_removed
    self.active_seconds = active_seconds
    self.submitted_form: dict[str, list[str]] | None = None
    self.created_project: dict | None = None
    self.poll_count = 0

  def transport(self) -> httpx.MockTransport:
    return httpx.MockTransport(self.handle)

  def _turn(self, status: str) -> dict:
    return {
        "id": "turn-1",
        "session_id": "sess-1",
        "status": status,
        "input_text": "hello",
        "created_at": "2026-06-12T00:00:00Z",
        "updated_at": "2026-06-12T00:00:00Z",
        "final_text": self.final_text if status == "completed" else None,
        "error_type": self.error_type,
        "error_message": self.error_message,
        "input_token_count": self.input_token_count,
        "output_token_count": self.output_token_count,
        "total_token_count": self.total_token_count,
        "tool_call_count": self.tool_call_count,
        "tool_success_count": self.tool_success_count,
        "tool_fail_count": self.tool_fail_count,
        "tool_duration_ms": self.tool_duration_ms,
        "file_lines_added": self.file_lines_added,
        "file_lines_removed": self.file_lines_removed,
        "active_seconds": self.active_seconds,
    }

  def handle(self, request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "GET" and path == "/api/projects":
      return httpx.Response(200, json=self.projects)
    if request.method == "POST" and path == "/api/projects":
      body = json.loads(request.content)
      self.created_project = {
          "id": "proj-new",
          "name": "project",
          "root_path": body["root_path"],
      }
      self.projects.append(self.created_project)
      return httpx.Response(200, json=self.created_project)
    if request.method == "POST" and path == "/api/turns":
      if self.submit_status_code is not None:
        return httpx.Response(
            self.submit_status_code,
            json={"detail": self.submit_detail or "error"},
        )
      self.submitted_form = parse_qs(request.content.decode())
      return httpx.Response(200, json=self._turn(self.poll_statuses[0]))
    if request.method == "GET" and path.startswith("/api/turns/"):
      if self.poll_transport_errors > 0:
        self.poll_transport_errors -= 1
        raise httpx.ConnectError("connection refused", request=request)
      self.poll_count += 1
      index = min(self.poll_count - 1, len(self.poll_statuses) - 1)
      return httpx.Response(200, json=self._turn(self.poll_statuses[index]))
    return httpx.Response(404, json={"detail": f"Unexpected: {path}"})


def _project(name: str = "p", *, root_path: str = "/tmp/p", project_id: str = "proj-1") -> dict:
  return {"id": project_id, "name": name, "root_path": root_path}


def _run(api: FakeWebApi, **kwargs) -> HandaCliResult:
  kwargs.setdefault("project_name", "p")
  return asyncio.run(
      handacli.run_handa_cli(
          transport=api.transport(),
          poll_interval_sec=0,
          **kwargs,
      )
  )


def test_handa_cli_result_serializes_stable_json_shape():
  result = HandaCliResult(
      ok=True,
      status="completed",
      session_id="20260509-143012-k7p4xq",
      turn_id="turn-1",
      response="done",
  )

  payload = json.loads(result.model_dump_json())

  assert payload == {
      "ok": True,
      "status": "completed",
      "session_id": "20260509-143012-k7p4xq",
      "turn_id": "turn-1",
      "response": "done",
      "stats": {
          "tokens": {"input": 0, "output": 0, "total": 0},
          "tools": {
              "total_calls": 0,
              "total_success": 0,
              "total_fail": 0,
              "total_duration_ms": 0,
          },
          "files": {"lines_added": 0, "lines_removed": 0},
          "active_seconds": 0.0,
      },
      "error": None,
  }


def test_run_handa_cli_completes_turn_via_web_api():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      poll_statuses=["queued", "running", "completed"],
      final_text="world",
  )

  result = _run(api, project_name="p", prompt="hello")

  assert result.ok is True
  assert result.status == "completed"
  assert result.session_id == "sess-1"
  assert result.turn_id == "turn-1"
  assert result.response == "world"
  assert result.error is None
  assert api.submitted_form == {
      "input_text": ["hello"],
      "project_id": ["proj-1"],
      "agent_id": ["orca_adk"],
  }
  # Name resolution never registers a project.
  assert api.created_project is None
  assert api.poll_count == 3


def test_run_handa_cli_errors_on_unknown_project_name():
  api = FakeWebApi(projects=[_project("known", project_id="proj-1")])

  result = _run(api, project_name="missing", prompt="hello")

  assert result.ok is False
  assert result.status == "failed"
  assert result.error is not None
  assert result.error.type == "ProjectNotFound"
  assert "missing" in result.error.message
  assert "known" in result.error.message
  assert api.submitted_form is None


def test_run_handa_cli_errors_on_ambiguous_project_name():
  api = FakeWebApi(
      projects=[
          _project("dup", root_path="/a", project_id="proj-a"),
          _project("dup", root_path="/b", project_id="proj-b"),
      ],
  )

  result = _run(api, project_name="dup", prompt="hello")

  assert result.ok is False
  assert result.status == "failed"
  assert result.error is not None
  assert result.error.type == "AmbiguousProject"
  assert "/a" in result.error.message and "/b" in result.error.message
  assert api.submitted_form is None


def test_run_handa_cli_passes_session_agent_and_model_config():
  api = FakeWebApi(projects=[_project("p", project_id="proj-1")], poll_statuses=["completed"])

  result = _run(
      api,
      project_name="p",
      prompt="hello",
      session_id="sess-1",
      agent_id="ralph",
      model_config_id="gemini-fast",
  )

  assert result.ok is True
  assert api.submitted_form == {
      "input_text": ["hello"],
      "project_id": ["proj-1"],
      "agent_id": ["ralph"],
      "session_id": ["sess-1"],
      "model_config_id": ["gemini-fast"],
  }


def test_run_handa_cli_maps_session_not_found():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      submit_status_code=404,
      submit_detail="Session not found",
  )

  result = _run(api, project_name="p", prompt="hello", session_id="missing-session")

  assert result.ok is False
  assert result.status == "failed"
  assert result.session_id == "missing-session"
  assert result.error is not None
  assert result.error.type == "SessionNotFound"
  assert result.error.message == "Session not found: missing-session"


def test_run_handa_cli_returns_waiting_input():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      poll_statuses=["running", "waiting_input"],
  )

  result = _run(api, project_name="p", prompt="hello")

  assert result.ok is True
  assert result.status == "waiting_input"
  assert result.session_id == "sess-1"
  assert result.turn_id == "turn-1"
  assert result.response == ""
  assert result.error is None


def test_run_handa_cli_reports_failed_turn():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      poll_statuses=["running", "failed"],
      error_type="RuntimeError",
      error_message="boom",
  )

  result = _run(api, project_name="p", prompt="hello")

  assert result.ok is False
  assert result.status == "failed"
  assert result.session_id == "sess-1"
  assert result.error is not None
  assert result.error.type == "RuntimeError"
  assert result.error.message == "boom"


def test_run_handa_cli_reports_cancelled_turn():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      poll_statuses=["running", "cancelled"],
      error_message="User terminated the turn.",
  )

  result = _run(api, project_name="p", prompt="hello")

  assert result.ok is False
  assert result.status == "cancelled"
  assert result.error is not None
  assert result.error.type == "Cancelled"
  assert result.error.message == "User terminated the turn."


def test_run_handa_cli_reports_api_unavailable():
  def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)

  result = asyncio.run(
      handacli.run_handa_cli(
          project_name="p",
          prompt="hello",
          transport=httpx.MockTransport(refuse),
          poll_interval_sec=0,
      )
  )

  assert result.ok is False
  assert result.status == "failed"
  assert result.error is not None
  assert result.error.type == "WebApiUnavailable"
  assert "uv run python -m src.api.app" in result.error.message


def test_run_handa_cli_polls_through_transient_api_outage():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      poll_statuses=["completed"],
      poll_transport_errors=3,
  )

  result = _run(api, project_name="p", prompt="hello")

  assert result.ok is True
  assert result.status == "completed"


def test_resolve_project_name_for_path_finds_existing(tmp_path):
  root = str(tmp_path.resolve())
  api = FakeWebApi(projects=[_project("logue-vibe-1", root_path=root, project_id="proj-1")])

  name = handacli.resolve_project_name_for_path(tmp_path, transport=api.transport())

  assert name == "logue-vibe-1"
  assert api.created_project is None


def test_resolve_project_name_for_path_registers_missing(tmp_path):
  api = FakeWebApi(projects=[])

  name = handacli.resolve_project_name_for_path(tmp_path, transport=api.transport())

  assert name == "project"
  assert api.created_project is not None
  assert api.created_project["root_path"] == str(tmp_path.resolve())


def test_handacli_main_prints_json_from_model(monkeypatch, capsys, tmp_path):
  async def fake_run_handa_cli(**kwargs):
    assert kwargs["project_name"] == "logue-vibe-1"
    assert kwargs["prompt"] == "hello"
    assert kwargs["session_id"] is None
    assert kwargs["agent_id"] == "orca_adk"
    assert kwargs["model_config_id"] is None
    assert kwargs["api_url"] is None
    return HandaCliResult(
        ok=True,
        status="completed",
        session_id="session-1",
        turn_id="turn-1",
        response="world",
    )

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)

  exit_code = handacli.main(
      ["--project", "logue-vibe-1", "--prompt", "hello", "--json"]
  )

  assert exit_code == 0
  assert json.loads(capsys.readouterr().out) == {
      "ok": True,
      "status": "completed",
      "session_id": "session-1",
      "turn_id": "turn-1",
      "response": "world",
      "stats": {
          "tokens": {"input": 0, "output": 0, "total": 0},
          "tools": {
              "total_calls": 0,
              "total_success": 0,
              "total_fail": 0,
              "total_duration_ms": 0,
          },
          "files": {"lines_added": 0, "lines_removed": 0},
          "active_seconds": 0.0,
      },
      "error": None,
  }


def test_handacli_main_returns_failed_json_on_exception(monkeypatch, capsys):
  async def fake_run_handa_cli(**kwargs):
    assert kwargs["agent_id"] == "ralph"
    raise RuntimeError("boom")

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)

  exit_code = handacli.main(
      [
          "--project",
          "logue-vibe-1",
          "--session",
          "session-1",
          "--agent",
          "ralph",
          "--prompt",
          "hello",
          "--json",
      ]
  )

  payload = json.loads(capsys.readouterr().out)
  assert exit_code == 1
  assert payload == {
      "ok": False,
      "status": "failed",
      "session_id": "session-1",
      "turn_id": None,
      "response": "",
      "stats": {
          "tokens": {"input": 0, "output": 0, "total": 0},
          "tools": {
              "total_calls": 0,
              "total_success": 0,
              "total_fail": 0,
              "total_duration_ms": 0,
          },
          "files": {"lines_added": 0, "lines_removed": 0},
          "active_seconds": 0.0,
      },
      "error": {
          "type": "RuntimeError",
          "message": "boom",
      },
  }


def test_handacli_main_passes_model_config_and_api_url(monkeypatch, capsys):
  async def fake_run_handa_cli(**kwargs):
    assert kwargs["project_name"] == "logue-vibe-1"
    assert kwargs["model_config_id"] == "gemini-fast"
    assert kwargs["api_url"] == "http://127.0.0.1:9999"
    return HandaCliResult(
        ok=True,
        status="completed",
        session_id="session-1",
        response="ok",
    )

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)

  exit_code = handacli.main(
      [
          "--project",
          "logue-vibe-1",
          "--prompt",
          "hello",
          "--model-config",
          "gemini-fast",
          "--api-url",
          "http://127.0.0.1:9999",
          "--json",
      ]
  )

  assert exit_code == 0
  assert json.loads(capsys.readouterr().out)["response"] == "ok"


def test_run_handa_cli_populates_stats_from_turn():
  api = FakeWebApi(
      projects=[_project("p", project_id="proj-1")],
      poll_statuses=["completed"],
      input_token_count=120,
      output_token_count=45,
      total_token_count=165,
      tool_call_count=7,
      tool_success_count=6,
      tool_fail_count=1,
      tool_duration_ms=2400,
      file_lines_added=30,
      file_lines_removed=12,
      active_seconds=3.5,
  )

  result = _run(api, project_name="p", prompt="hello")

  assert result.stats.tokens.input == 120
  assert result.stats.tokens.output == 45
  assert result.stats.tokens.total == 165
  assert result.stats.tools.total_calls == 7
  assert result.stats.tools.total_success == 6
  assert result.stats.tools.total_fail == 1
  assert result.stats.tools.total_duration_ms == 2400
  assert result.stats.files.lines_added == 30
  assert result.stats.files.lines_removed == 12
  assert result.stats.active_seconds == 3.5


def test_handacli_text_mode_prints_response_to_stdout(monkeypatch, capsys):
  async def fake_run_handa_cli(**kwargs):
    assert kwargs["prompt"] == "hello"
    return HandaCliResult(
        ok=True,
        status="completed",
        session_id="session-1",
        turn_id="turn-1",
        response="the answer",
    )

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)

  # No --json / --output-format: text is the default, like gemini -p.
  exit_code = handacli.main(["--project", "logue-vibe-1", "--prompt", "hello"])

  captured = capsys.readouterr()
  assert exit_code == 0
  assert captured.out.strip() == "the answer"
  assert captured.err == ""


def test_handacli_text_mode_reports_error_to_stderr(monkeypatch, capsys):
  async def fake_run_handa_cli(**kwargs):
    raise RuntimeError("boom")

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)

  exit_code = handacli.main(["--project", "logue-vibe-1", "--prompt", "hello"])

  captured = capsys.readouterr()
  assert exit_code == 1
  assert captured.out == ""
  assert "RuntimeError: boom" in captured.err


def test_handacli_output_format_json_matches_json_alias(monkeypatch, capsys):
  async def fake_run_handa_cli(**kwargs):
    return HandaCliResult(
        ok=True,
        status="completed",
        session_id="session-1",
        turn_id="turn-1",
        response="hi",
    )

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)

  exit_code = handacli.main(
      ["--project", "logue-vibe-1", "--prompt", "hello", "--output-format", "json"]
  )

  payload = json.loads(capsys.readouterr().out)
  assert exit_code == 0
  assert payload["response"] == "hi"
  assert payload["stats"] == {
      "tokens": {"input": 0, "output": 0, "total": 0},
      "tools": {
          "total_calls": 0,
          "total_success": 0,
          "total_fail": 0,
          "total_duration_ms": 0,
      },
      "files": {"lines_added": 0, "lines_removed": 0},
      "active_seconds": 0.0,
  }


def test_handacli_reads_prompt_from_stdin(monkeypatch, capsys):
  captured_prompt: dict[str, str] = {}

  async def fake_run_handa_cli(**kwargs):
    captured_prompt["value"] = kwargs["prompt"]
    return HandaCliResult(
        ok=True, status="completed", session_id="s", turn_id="t", response="ok"
    )

  monkeypatch.setattr(handacli, "run_handa_cli", fake_run_handa_cli)
  monkeypatch.setattr(sys, "stdin", io.StringIO("piped prompt\n"))

  exit_code = handacli.main(["--project", "logue-vibe-1", "--json"])

  assert exit_code == 0
  assert captured_prompt["value"] == "piped prompt\n"


def test_handacli_module_imports_no_runtime_code():
  """handacli is a thin web API client; importing it must not load runtime."""
  import subprocess

  forbidden_prefixes = (
      "src.runner",
      "src.agents",
      "src.tools",
      "src.run_manager",
      "src.observability",
      "src.contract",
      "google.adk",
  )
  probe = (
      "import sys; import src.handacli; "
      f"bad = [m for m in sys.modules if m.startswith({forbidden_prefixes!r})]; "
      "print('\\n'.join(bad)); sys.exit(1 if bad else 0)"
  )
  completed = subprocess.run(
      [sys.executable, "-c", probe],
      capture_output=True,
      text=True,
  )
  assert completed.returncode == 0, completed.stdout + completed.stderr
