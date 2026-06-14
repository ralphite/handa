from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.routes import dictate as dictate_module


def _make_client(tmp_path, monkeypatch) -> tuple[TestClient, dict]:
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  app = create_app()
  return TestClient(app), {"app": app}


def test_dictate_returns_503_when_api_key_missing(tmp_path, monkeypatch):
  client, _ = _make_client(tmp_path, monkeypatch)
  # create_app() calls load_dotenv() which may repopulate GEMINI_API_KEY, so
  # patch the helper directly to simulate the "no key" environment.
  monkeypatch.setattr(dictate_module, "_api_key", lambda: None)

  response = client.post(
      "/api/dictate",
      files={"audio": ("clip.webm", b"\x00\x00\x00", "audio/webm")},
  )

  assert response.status_code == 503
  assert "GEMINI_API_KEY" in response.json()["detail"]


def test_dictate_rejects_empty_audio(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
  client, _ = _make_client(tmp_path, monkeypatch)

  response = client.post(
      "/api/dictate",
      files={"audio": ("clip.webm", b"", "audio/webm")},
  )

  assert response.status_code == 400
  assert response.json()["detail"] == "Empty audio upload"


def test_dictate_calls_gemini_with_context_and_returns_transcript(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  project = tmp_path / "project"
  project.mkdir()
  (project / "AGENTS.md").write_text(
      "# Project agents\n\nProject uses the Ralph planner.\n",
      encoding="utf-8",
  )

  client, _ = _make_client(tmp_path, monkeypatch)
  project = client.post(
      "/api/projects",
      json={"name": "wk", "root_path": str(project)},
  ).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()

  # Seed a prior turn so the history context is non-empty.
  ctx = client.app.state.web_context  # type: ignore[attr-defined]
  turn = ctx.db.create_turn(
      session_id=session["id"],
      title="refactor planner",
      input_text="please refactor the planner",
  )
  ctx.db.update_turn(
      turn["id"],
      status="completed",
      final_text="Done, here's the new module.",
  )

  captured: dict = {}

  async def fake_transcribe(api_key, audio_bytes, mime_type, context_block):
    captured["api_key"] = api_key
    captured["mime_type"] = mime_type
    captured["audio_len"] = len(audio_bytes)
    captured["context_block"] = context_block
    return "  add a unit test for the Ralph planner  "

  monkeypatch.setattr(dictate_module, "_transcribe", fake_transcribe)

  response = client.post(
      "/api/dictate",
      data={"session_id": session["id"], "project_id": project["id"]},
      files={"audio": ("clip.webm", b"\x01\x02\x03\x04\x05", "audio/webm")},
  )

  assert response.status_code == 200, response.text
  assert response.json() == {"transcript": "add a unit test for the Ralph planner"}
  assert captured["api_key"] == "fake-key"
  assert captured["mime_type"] == "audio/webm"
  assert captured["audio_len"] == 5

  ctx_block = captured["context_block"]
  assert "Project context" in ctx_block
  assert "Project root:" in ctx_block
  assert "AGENTS.md" in ctx_block
  assert "Ralph planner" in ctx_block
  assert "Recent chat history" in ctx_block
  assert "User: please refactor the planner" in ctx_block
  assert "Agent: Done, here's the new module." in ctx_block


def test_dictate_works_without_session_or_project(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  captured: dict = {}

  async def fake_transcribe(api_key, audio_bytes, mime_type, context_block):
    captured["context_block"] = context_block
    return "hello world"

  monkeypatch.setattr(dictate_module, "_transcribe", fake_transcribe)

  client, _ = _make_client(tmp_path, monkeypatch)
  response = client.post(
      "/api/dictate",
      files={"audio": ("clip.webm", b"\x10", "audio/webm")},
  )

  assert response.status_code == 200
  assert response.json() == {"transcript": "hello world"}
  assert captured["context_block"] == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("hello world", "hello world"),
        ("  spaced  ", "spaced"),
    ],
)
def test_dictate_strips_whitespace(tmp_path, monkeypatch, raw, expected):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  async def fake_transcribe(*_args, **_kwargs):
    return raw

  monkeypatch.setattr(dictate_module, "_transcribe", fake_transcribe)
  client, _ = _make_client(tmp_path, monkeypatch)
  response = client.post(
      "/api/dictate",
      files={"audio": ("clip.webm", b"x", "audio/webm")},
  )

  assert response.status_code == 200
  assert response.json()["transcript"] == expected


def test_dictate_uses_readme_excerpt_when_agents_missing(tmp_path, monkeypatch):
  project = tmp_path / "project"
  project.mkdir()
  (project / "README.md").write_text("# Cool tool", encoding="utf-8")
  Path(project / "README.md").write_text("README body content", encoding="utf-8")

  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  app = create_app()
  ctx = app.state.web_context  # type: ignore[attr-defined]
  project = ctx.db.create_project(name="wk", root_path=str(project))

  block = dictate_module._project_context(ctx, project["id"])
  assert "README.md" in block
  assert "README body content" in block
