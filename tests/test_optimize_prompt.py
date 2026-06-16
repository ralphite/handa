from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.routes import optimize_prompt as optimize_module


def _make_client(tmp_path, monkeypatch) -> TestClient:
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  app = create_app()
  return TestClient(app)


def test_optimize_returns_503_when_api_key_missing(tmp_path, monkeypatch):
  client = _make_client(tmp_path, monkeypatch)
  # create_app() calls load_dotenv() which may repopulate GEMINI_API_KEY, so
  # patch the helper directly to simulate the "no key" environment.
  monkeypatch.setattr(optimize_module, "_api_key", lambda: None)

  response = client.post("/api/optimize_prompt", json={"prompt": "fix the bug"})

  assert response.status_code == 503
  assert "GEMINI_API_KEY" in response.json()["detail"]


def test_optimize_rejects_blank_prompt(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
  client = _make_client(tmp_path, monkeypatch)

  response = client.post("/api/optimize_prompt", json={"prompt": "   "})

  assert response.status_code == 400
  assert response.json()["detail"] == "Empty prompt"


def test_optimize_rejects_oversized_prompt(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
  client = _make_client(tmp_path, monkeypatch)

  response = client.post(
      "/api/optimize_prompt",
      json={"prompt": "x" * (optimize_module.MAX_PROMPT_CHARS + 1)},
  )

  assert response.status_code == 413
  assert "Prompt too long" in response.json()["detail"]


def test_optimize_calls_gemini_with_context_and_returns_rewrite(
    tmp_path, monkeypatch
):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  project = tmp_path / "project"
  project.mkdir()
  (project / "AGENTS.md").write_text(
      "# Project agents\n\nProject uses the Ralph planner.\n",
      encoding="utf-8",
  )

  client = _make_client(tmp_path, monkeypatch)
  project = client.post(
      "/api/projects",
      json={"name": "wk", "root_path": str(project)},
  ).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca", "project_id": project["id"]},
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

  async def fake_optimize(api_key, prompt, context_block):
    captured["api_key"] = api_key
    captured["prompt"] = prompt
    captured["context_block"] = context_block
    return "  Add a unit test covering the Ralph planner refactor.  "

  monkeypatch.setattr(optimize_module, "_optimize", fake_optimize)

  response = client.post(
      "/api/optimize_prompt",
      json={
          "prompt": "  test it  ",
          "session_id": session["id"],
          "project_id": project["id"],
      },
  )

  assert response.status_code == 200, response.text
  assert response.json() == {
      "optimized": "Add a unit test covering the Ralph planner refactor."
  }
  assert captured["api_key"] == "fake-key"
  assert captured["prompt"] == "test it"

  ctx_block = captured["context_block"]
  assert "Project context" in ctx_block
  assert "Project root:" in ctx_block
  assert "AGENTS.md" in ctx_block
  assert "Ralph planner" in ctx_block
  assert "Recent chat history" in ctx_block
  assert "User: please refactor the planner" in ctx_block
  assert "Agent: Done, here's the new module." in ctx_block


def test_optimize_works_without_session_or_project(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  captured: dict = {}

  async def fake_optimize(api_key, prompt, context_block):
    captured["context_block"] = context_block
    return "Fix the login bug in auth.py."

  monkeypatch.setattr(optimize_module, "_optimize", fake_optimize)

  client = _make_client(tmp_path, monkeypatch)
  response = client.post("/api/optimize_prompt", json={"prompt": "fix login"})

  assert response.status_code == 200
  assert response.json() == {"optimized": "Fix the login bug in auth.py."}
  assert captured["context_block"] == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"quoted rewrite"', "quoted rewrite"),
        ("```\nfenced rewrite\n```", "fenced rewrite"),
    ],
)
def test_optimize_normalises_model_output(tmp_path, monkeypatch, raw, expected):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  async def fake_optimize(*_args, **_kwargs):
    return raw

  monkeypatch.setattr(optimize_module, "_optimize", fake_optimize)
  client = _make_client(tmp_path, monkeypatch)
  response = client.post("/api/optimize_prompt", json={"prompt": "do the thing"})

  assert response.status_code == 200
  assert response.json()["optimized"] == expected


def test_optimize_falls_back_to_original_on_empty_rewrite(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  async def fake_optimize(*_args, **_kwargs):
    return "   "

  monkeypatch.setattr(optimize_module, "_optimize", fake_optimize)
  client = _make_client(tmp_path, monkeypatch)
  response = client.post("/api/optimize_prompt", json={"prompt": "keep me"})

  assert response.status_code == 200
  assert response.json()["optimized"] == "keep me"


def test_optimize_returns_502_when_gemini_fails(tmp_path, monkeypatch):
  monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

  async def fake_optimize(*_args, **_kwargs):
    raise RuntimeError("model exploded")

  monkeypatch.setattr(optimize_module, "_optimize", fake_optimize)
  client = _make_client(tmp_path, monkeypatch)
  response = client.post("/api/optimize_prompt", json={"prompt": "do it"})

  assert response.status_code == 502
  assert "model exploded" in response.json()["detail"]
