from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.adk.events import Event
from google.genai import types

from src.model_configs import DEFAULT_MODEL_CONFIG_ID
from src.run_outcome import RunOutcome
from src.progress import PROGRESS_STATE_KEY
from src.runner import APP_NAME
from src.runtime import append_task_event
from src.runtime import list_task_events
from src.runtime import list_task_notifications
from src.runtime import load_task
from src.runtime import save_task
from src.runtime import start_background_task
from src.runtime import start_run_agent_task
from src.runtime import task_result_file
from src.storage.paths import browser_dir
from src.storage.paths import browser_screenshot_path
from src.storage.paths import browser_state_path
from src.api.background_task_manager import BackgroundTaskManager
from src.api.turn_queue import dispatch_next_queued_turn
from turn_test_helpers import execute_turn
from src.api.app import create_app
from src.api.routes.turns import generate_and_store_session_title
from src.api.sqlite import WebDatabase


def test_web_api_health_and_session_creation(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  assert client.get("/api/health").json() == {"ok": True}

  project = client.post(
      "/api/projects",
      json={"name": "project", "root_path": str(project)},
  ).json()
  created = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  listed = client.get("/api/sessions").json()

  assert created["agent_id"] == "orca_adk"
  assert created["id"] in [session["id"] for session in listed]


def test_web_api_session_creation_defaults_to_langgraph_main(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  created = client.post("/api/sessions", json={"project_id": project["id"]}).json()

  assert created["agent_id"] == "orca"
  assert created["agent_runtime"] == "langgraph"


def test_web_api_session_list_hides_missing_adk_sessions(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  valid = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  app.state.web_context.db.create_session(
      session_id="orphan-meta",
      project_id=project["id"],
      agent_id="orca_adk",
      title="orphan",
  )

  listed = client.get("/api/sessions?include_archived=true").json()

  assert valid["id"] in [session["id"] for session in listed]
  assert "orphan-meta" not in [session["id"] for session in listed]
  assert client.get("/api/sessions/orphan-meta/detail").status_code == 404


def test_web_api_forks_session_from_completed_turn(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  ctx.db.update_session_title(source["id"], "Investigate UI affordance", source="manual")
  first = ctx.db.create_turn(
      session_id=source["id"],
      title="First request",
      input_text="Explain the affordance.",
  )
  first = ctx.db.update_turn(
      first["id"],
      status="completed",
      started_at="2026-06-07T01:00:00+00:00",
      finished_at="2026-06-07T01:00:02+00:00",
      final_text="Use the branch icon.",
  )
  ctx.db.append_step(
      turn_id=first["id"],
      kind="agent_text",
      summary="Use the branch icon.",
      payload={"text": "Use the branch icon.", "final": True},
  )
  second = ctx.db.create_turn(
      session_id=source["id"],
      title="Second request",
      input_text="Continue differently.",
  )
  ctx.db.update_turn(
      second["id"],
      status="completed",
      finished_at="2026-06-07T01:00:05+00:00",
      final_text="Later answer.",
  )
  ctx.services.session_service.merge_state_sync(
      source["id"],
      {
          "handa:active_turn_id": first["id"],
          "handa:parent_session_id": "parent-session",
          "handa:parent_task_id": "task-1",
          "handa:automated_task_id": "atask_source",
          "handa:automated_task_run_id": "arun_source",
          "handa:trigger_kind": "automated_task",
      },
  )
  session = ctx.services.session_service._read_session(source["id"])
  asyncio.run(
      ctx.services.session_service.append_event(
          session,
          Event(
              invocation_id="runtime-first",
              author="agent",
              content=types.Content(parts=[types.Part(text="Use the branch icon.")]),
          ),
      )
  )
  asyncio.run(
      ctx.services.artifact_service.save_artifact(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=source["id"],
          filename="fork-plan.md",
          artifact=types.Part.from_text(text="artifact snapshot"),
      )
  )

  forked = client.post(
      f"/api/sessions/{source['id']}/fork",
      json={"source_turn_id": first["id"]},
  ).json()

  cloned_turns = ctx.db.list_turns_for_session(forked["id"])
  cloned_steps = ctx.db.list_steps_for_session(session_id=forked["id"])
  fork_session = ctx.services.session_service._read_session(forked["id"])
  source_artifacts = asyncio.run(
      ctx.services.artifact_service.list_artifact_keys(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=source["id"],
      )
  )
  fork_artifacts = asyncio.run(
      ctx.services.artifact_service.list_artifact_keys(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=forked["id"],
      )
  )

  assert forked["id"] != source["id"]
  assert forked["title"] == "Investigate UI affordance"
  assert forked["forked_from_session_id"] == source["id"]
  assert forked["forked_from_turn_id"] == first["id"]
  assert len(cloned_turns) == 1
  assert cloned_turns[0]["id"] != first["id"]
  assert cloned_turns[0]["input_text"] == "Explain the affordance."
  assert cloned_turns[0]["final_text"] == "Use the branch icon."
  assert cloned_steps[0]["turn_id"] == cloned_turns[0]["id"]
  assert cloned_steps[0]["payload"]["text"] == "Use the branch icon."
  assert fork_session is not None
  assert fork_session.state["handa:forked_from_session_id"] == source["id"]
  assert fork_session.state["handa:forked_from_turn_id"] == first["id"]
  assert "handa:active_turn_id" not in fork_session.state
  assert "handa:parent_session_id" not in fork_session.state
  assert "handa:automated_task_id" not in fork_session.state
  assert "handa:automated_task_run_id" not in fork_session.state
  assert "handa:trigger_kind" not in fork_session.state
  assert len(fork_session.events) == 1
  assert fork_session.events[0].invocation_id == "runtime-first"
  assert fork_artifacts == source_artifacts


def test_web_api_rejects_fork_from_running_turn(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  running = app.state.web_context.db.create_turn(
      session_id=source["id"],
      title="Running request",
      input_text="Still running.",
  )
  app.state.web_context.db.update_turn(running["id"], status="running")

  response = client.post(
      f"/api/sessions/{source['id']}/fork",
      json={"source_turn_id": running["id"]},
  )

  assert response.status_code == 409
  assert response.json()["detail"] == "Cannot fork a running turn"


def test_web_api_forks_session_before_source_turn(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  first = ctx.db.create_turn(
      session_id=source["id"],
      title="First request",
      input_text="Keep this.",
  )
  ctx.db.update_turn(first["id"], status="completed", final_text="Kept.")
  second = ctx.db.create_turn(
      session_id=source["id"],
      title="Second request",
      input_text="Edit this.",
  )
  ctx.db.update_turn(second["id"], status="completed", final_text="Old tail.")
  ctx.services.session_service.merge_state_sync(
      source["id"],
      {"handa:active_turn_id": first["id"]},
  )
  session = ctx.services.session_service._read_session(source["id"])
  asyncio.run(
      ctx.services.session_service.append_event(
          session,
          Event(
              invocation_id="runtime-first",
              author="agent",
              content=types.Content(parts=[types.Part(text="Kept.")]),
          ),
      )
  )

  forked = client.post(
      f"/api/sessions/{source['id']}/fork",
      json={"source_turn_id": second["id"], "include_source_turn": False},
  ).json()

  cloned_turns = ctx.db.list_turns_for_session(forked["id"])
  fork_session = ctx.services.session_service._read_session(forked["id"])

  assert [turn["input_text"] for turn in cloned_turns] == ["Keep this."]
  assert forked["forked_from_turn_id"] == second["id"]
  assert fork_session is not None
  assert len(fork_session.events) == 1
  assert fork_session.events[0].invocation_id == "runtime-first"


def test_web_api_rewrite_truncates_session_and_reuses_attachments(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  async def fake_run_agent_invocation(**kwargs):
    return RunOutcome(final_text="rewritten")

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  first = ctx.db.create_turn(
      session_id=source["id"],
      title="First request",
      input_text="Keep this.",
  )
  ctx.db.update_turn(first["id"], status="completed", final_text="Kept.")
  ctx.db.append_step(
      turn_id=first["id"],
      kind="artifact_delta",
      summary="Updated artifact first.plan.md",
      payload={"filename": "first.plan.md", "version": 0},
  )
  second = ctx.db.create_turn(
      session_id=source["id"],
      title="Second request",
      input_text="Replace this.",
  )
  ctx.db.update_turn(second["id"], status="completed", final_text="Old tail.")
  ctx.db.append_step(
      turn_id=second["id"],
      kind="artifact_delta",
      summary="Updated artifact second.plan.md",
      payload={"filename": "second.plan.md", "version": 0},
  )
  attachment_path = storage_root / "sessions" / source["id"] / "attachments" / "notes.txt"
  attachment_path.parent.mkdir(parents=True)
  attachment_path.write_text("original attachment", encoding="utf-8")
  attachment = ctx.db.create_attachment(
      turn_id=second["id"],
      ordinal=0,
      filename="notes.txt",
      mime_type="text/plain",
      kind="text",
      byte_count=19,
      storage_path=str(attachment_path),
  )
  for turn, text in ((first, "runtime-first"), (second, "runtime-second")):
    ctx.services.session_service.merge_state_sync(
        source["id"],
        {"handa:active_turn_id": turn["id"]},
    )
    session = ctx.services.session_service._read_session(source["id"])
    asyncio.run(
        ctx.services.session_service.append_event(
            session,
            Event(
                invocation_id=text,
                author="agent",
                content=types.Content(parts=[types.Part(text=text)]),
            ),
        )
    )
  asyncio.run(
      ctx.services.artifact_service.save_artifact(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=source["id"],
          filename="first.plan.md",
          artifact=types.Part.from_text(text="first artifact"),
      )
  )
  asyncio.run(
      ctx.services.artifact_service.save_artifact(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=source["id"],
          filename="second.plan.md",
          artifact=types.Part.from_text(text="second artifact"),
      )
  )

  response = client.post(
      "/api/turns/rewrite",
      data={
          "session_id": source["id"],
          "source_turn_id": second["id"],
          "input_text": "Edited message.",
          "existing_attachment_ids": json.dumps([attachment["id"]]),
      },
  )

  assert response.status_code == 200, response.json()
  rewritten = response.json()
  turns = ctx.db.list_turns_for_session(source["id"])
  cloned_attachments = ctx.db.list_attachments_for_turn(rewritten["id"])
  session = ctx.services.session_service._read_session(source["id"])
  artifacts = asyncio.run(
      ctx.services.artifact_service.list_artifact_keys(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=source["id"],
      )
  )

  assert [turn["id"] for turn in turns] == [first["id"], rewritten["id"]]
  assert turns[-1]["input_text"] == "Edited message."
  assert len(cloned_attachments) == 1
  assert cloned_attachments[0]["id"] != attachment["id"]
  assert cloned_attachments[0]["storage_path"] == attachment["storage_path"]
  assert session is not None
  assert [event.invocation_id for event in session.events] == ["runtime-first"]
  assert any(name.startswith("first.v1.plan.md") for name in artifacts)
  assert not any(name.startswith("second.v1.plan.md") for name in artifacts)


def test_web_api_serves_built_frontend_when_configured(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  dist = tmp_path / "dist"
  assets = dist / "assets"
  assets.mkdir(parents=True)
  (dist / "index.html").write_text("<div id=\"app\"></div>", encoding="utf-8")
  (assets / "app.js").write_text("console.log('handa')", encoding="utf-8")
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_FRONTEND_DIST", str(dist))

  app = create_app()
  client = TestClient(app)

  assert client.get("/api/health").json() == {"ok": True}
  assert client.get("/").text == "<div id=\"app\"></div>"
  assert client.get("/sessions/anything").text == "<div id=\"app\"></div>"
  assert client.get("/assets/app.js").text == "console.log('handa')"
  assert client.get("/api/missing").status_code == 404


def test_web_api_project_creation_lists_last_opened_first(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  first = tmp_path / "first"
  second = tmp_path / "second"
  first.mkdir()
  second.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  created_first = client.post(
      "/api/projects",
      json={"root_path": str(first)},
  ).json()
  created_second = client.post(
      "/api/projects",
      json={"root_path": str(second)},
  ).json()

  client.post(f"/api/projects/{created_first['id']}/open")
  listed = client.get("/api/projects").json()

  assert [project["id"] for project in listed] == [
      created_first["id"],
      created_second["id"],
  ]


def test_web_api_project_update_renames_display_name(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_root = tmp_path / "project"
  project_root.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post(
      "/api/projects",
      json={"root_path": str(project_root)},
  ).json()

  renamed = client.patch(
      f"/api/projects/{project['id']}",
      json={"name": "Renamed Project"},
  ).json()
  empty_name = client.patch(
      f"/api/projects/{project['id']}",
      json={"name": "   "},
  )
  missing = client.patch(
      "/api/projects/proj_missing",
      json={"name": "Missing"},
  )
  listed = client.get("/api/projects").json()

  assert renamed["id"] == project["id"]
  assert renamed["name"] == "Renamed Project"
  assert renamed["root_path"] == str(project_root.resolve())
  assert empty_name.status_code == 400
  assert empty_name.json()["detail"] == "Project name is required"
  assert missing.status_code == 404
  assert listed[0]["name"] == "Renamed Project"


def test_web_api_project_delete_removes_record_only(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_root = tmp_path / "project"
  project_root.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post(
      "/api/projects",
      json={"root_path": str(project_root)},
  ).json()

  removed = client.delete(f"/api/projects/{project['id']}").json()
  missing = client.delete(f"/api/projects/{project['id']}")
  listed = client.get("/api/projects").json()
  create_session = client.post(
      "/api/sessions",
      json={"project_id": project["id"]},
  )

  assert removed == {
      "project_id": project["id"],
      "root_path": str(project_root.resolve()),
      "removed": True,
  }
  assert project_root.is_dir()
  assert missing.status_code == 404
  assert all(item["id"] != project["id"] for item in listed)
  assert create_session.status_code == 404


def test_web_api_project_launcher_opens_project_root(tmp_path, monkeypatch):
  from src.api.routes import projects as project_routes

  storage_root = tmp_path / ".handa"
  project_root = tmp_path / "project"
  project_root.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr(project_routes.sys, "platform", "darwin")
  calls = []

  def fake_run(command, **kwargs):
    calls.append((command, kwargs))
    return SimpleNamespace(returncode=0)

  monkeypatch.setattr(project_routes.subprocess, "run", fake_run)

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_root)}).json()

  finder = client.post(
      f"/api/projects/{project['id']}/launcher",
      json={"target": "finder"},
  ).json()
  vscode = client.post(
      f"/api/projects/{project['id']}/launcher",
      json={"target": "vscode"},
  ).json()

  assert finder == {
      "project_id": project["id"],
      "target": "finder",
      "opened": True,
  }
  assert vscode["target"] == "vscode"
  assert calls[0][0] == ["open", str(project_root)]
  assert calls[0][1]["check"] is True
  assert calls[1][0] == ["open", "-a", "Visual Studio Code", str(project_root)]


def test_web_api_project_launcher_rejects_missing_project_root(tmp_path, monkeypatch):
  from src.api.routes import projects as project_routes

  storage_root = tmp_path / ".handa"
  project_root = tmp_path / "project"
  project_root.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr(project_routes.sys, "platform", "darwin")

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_root)}).json()
  project_root.rmdir()

  response = client.post(
      f"/api/projects/{project['id']}/launcher",
      json={"target": "finder"},
  )

  assert response.status_code == 400
  assert response.json()["detail"] == "Project root does not exist"


def test_web_api_project_launcher_icon_serves_png(tmp_path, monkeypatch):
  from src.api.routes import projects as project_routes

  storage_root = tmp_path / ".handa"
  icon_source = tmp_path / "Finder.icns"
  icon_source.write_bytes(b"icns")
  png_bytes = b"\x89PNG\r\n\x1a\napp-icon"
  calls = []

  def fake_icon_source(target):
    assert target == "finder"
    return icon_source

  def fake_run(command, **kwargs):
    calls.append((command, kwargs))
    output = command[command.index("--out") + 1]
    Path(output).write_bytes(png_bytes)
    return SimpleNamespace(returncode=0)

  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr(project_routes, "_launcher_icon_source", fake_icon_source)
  monkeypatch.setattr(project_routes.subprocess, "run", fake_run)

  app = create_app()
  client = TestClient(app)

  response = client.get("/api/projects/launcher-icons/finder")

  assert response.status_code == 200
  assert response.headers["content-type"] == "image/png"
  assert response.content == png_bytes
  assert calls[0][0][0] == "sips"
  assert calls[0][1]["check"] is True


def test_web_api_project_launcher_icon_missing_returns_404(tmp_path, monkeypatch):
  from src.api.routes import projects as project_routes

  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr(
      project_routes,
      "_launcher_icon_source",
      lambda target: tmp_path / "missing.icns",
  )

  app = create_app()
  client = TestClient(app)

  response = client.get("/api/projects/launcher-icons/finder")

  assert response.status_code == 404
  assert response.json()["detail"] == "Launcher icon not found"


def test_web_api_settings_persist_theme_id(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  initial = client.get("/api/settings").json()
  assert initial["theme_id"] == "dark"
  assert initial["model_config_id"] == DEFAULT_MODEL_CONFIG_ID
  assert initial["streaming_mode_enabled"] is True
  assert [item["id"] for item in initial["model_configs"]] == [
      "gemini-3.5-flash",
      "gemini-3.5-flash-high",
      "gemini-3.1-pro-low",
      "gemini-3.1-pro-high",
  ]

  updated = client.patch(
      "/api/settings",
      json={
          "theme_id": "light",
          "model_config_id": "gemini-3.5-flash",
          "streaming_mode_enabled": False,
      },
  ).json()
  fetched = client.get("/api/settings").json()

  assert updated["theme_id"] == "light"
  assert updated["model_config_id"] == "gemini-3.5-flash"
  assert updated["streaming_mode_enabled"] is False
  assert fetched["theme_id"] == "light"
  assert fetched["model_config_id"] == "gemini-3.5-flash"
  assert fetched["streaming_mode_enabled"] is False

  system_updated = client.patch(
      "/api/settings",
      json={
          "theme_id": "system",
      },
  ).json()
  system_fetched = client.get("/api/settings").json()

  assert system_updated["theme_id"] == "system"
  assert system_fetched["theme_id"] == "system"


def test_web_api_settings_persist_folded_project_ids(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  initial = client.get("/api/settings").json()
  assert initial["folded_project_ids"] == []

  updated = client.patch(
      "/api/settings",
      json={"folded_project_ids": ["proj_a", "proj_b"]},
  ).json()
  fetched = client.get("/api/settings").json()

  assert updated["folded_project_ids"] == ["proj_a", "proj_b"]
  assert fetched["folded_project_ids"] == ["proj_a", "proj_b"]

  # Blanks are dropped and duplicates collapse while first-seen order is kept.
  normalized = client.patch(
      "/api/settings",
      json={"folded_project_ids": ["proj_b", "  ", "proj_b", " proj_c "]},
  ).json()
  assert normalized["folded_project_ids"] == ["proj_b", "proj_c"]

  # An empty list unfolds everything and persists.
  cleared = client.patch(
      "/api/settings",
      json={"folded_project_ids": []},
  ).json()
  assert client.get("/api/settings").json()["folded_project_ids"] == []
  assert cleared["folded_project_ids"] == []


def test_web_api_resolves_legacy_theme_setting(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  ctx = client.app.state.web_context
  ctx.db.set_user_setting(
      user_id=ctx.settings.user_id,
      key="theme_id",
      value="github-light",
  )

  settings = client.get("/api/settings").json()

  assert settings["theme_id"] == "light"


def test_web_api_rejects_unknown_theme_setting(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  response = client.patch("/api/settings", json={"theme_id": "github-light"})

  assert response.status_code == 400
  assert response.json()["detail"] == "Unknown theme_id."


def test_web_api_rejects_unknown_model_config_setting(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  response = client.patch(
      "/api/settings",
      json={"model_config_id": "missing-model"},
  )

  assert response.status_code == 400


def test_web_api_stars_session(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-1",
          state={"handa:agent_id": "orca_adk", "handa:project_id": project["id"]},
      )
  )
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
      title="important task",
  )

  starred = client.put(
      "/api/sessions/session-1/star",
      json={"starred": True},
  ).json()
  listed = client.get("/api/sessions").json()
  unstarred = client.put(
      "/api/sessions/session-1/star",
      json={"starred": False},
  ).json()
  relisted = client.get("/api/sessions").json()

  assert "project_id" not in starred
  assert starred["session_id"] == "session-1"
  assert starred["starred"] is True
  assert starred["starred_at"] is not None
  listed_session = next(item for item in listed if item["id"] == "session-1")
  relisted_session = next(item for item in relisted if item["id"] == "session-1")

  assert listed_session["starred"] is True
  assert listed_session["starred_at"] == starred["starred_at"]
  assert unstarred["starred"] is False
  assert unstarred["starred_at"] is None
  assert relisted_session["starred"] is False
  assert relisted_session["starred_at"] is None


def test_web_api_star_missing_session_returns_404(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  app = create_app()
  client = TestClient(app)
  response = client.put("/api/sessions/ghost/star", json={"starred": True})
  assert response.status_code == 404


def test_web_api_archives_and_unarchives_session(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-archive",
          state={"handa:agent_id": "orca_adk", "handa:project_id": project["id"]},
      )
  )
  ctx.db.create_session(
      session_id="session-archive",
      project_id=project["id"],
      agent_id="orca_adk",
      title="archive me",
  )

  archived = client.put(
      "/api/sessions/session-archive/archive",
      json={"archived": True},
  ).json()
  listed = client.get("/api/sessions").json()
  archived_list = client.get("/api/sessions?archived=true").json()
  restored = client.put(
      "/api/sessions/session-archive/archive",
      json={"archived": False},
  ).json()
  relisted = client.get("/api/sessions").json()

  assert archived["archived_at"] is not None
  assert all(item["id"] != "session-archive" for item in listed)
  assert [item["id"] for item in archived_list] == ["session-archive"]
  assert restored["archived_at"] is None
  assert [item["id"] for item in relisted] == ["session-archive"]


def test_web_api_marks_session_unread_and_read(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-unread",
      project_id=project["id"],
      agent_id="orca_adk",
      title="read later",
  )

  unread = client.put(
      "/api/sessions/session-unread/unread",
      json={"unread": True},
  ).json()
  read = client.put(
      "/api/sessions/session-unread/unread",
      json={"unread": False},
  ).json()

  assert unread["unread"] is True
  assert unread["unread_at"] is not None
  assert read["unread"] is False
  assert read["unread_at"] is None


def test_web_api_soft_deletes_session(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-delete",
          state={"handa:agent_id": "orca_adk", "handa:project_id": project["id"]},
      )
  )
  ctx.db.create_session(
      session_id="session-delete",
      project_id=project["id"],
      agent_id="orca_adk",
      title="delete me",
  )

  deleted = client.delete("/api/sessions/session-delete").json()
  listed = client.get("/api/sessions?include_archived=true").json()
  detail = client.get("/api/sessions/session-delete/detail")

  assert deleted["deleted"] is True
  assert deleted["deleted_at"] is not None
  assert ctx.db.get_session_meta("session-delete") is None
  assert ctx.db.get_session_meta("session-delete", include_deleted=True)["deleted_at"] is not None
  assert all(item["id"] != "session-delete" for item in listed)
  assert detail.status_code == 404


def test_web_api_can_start_with_explicit_handa_dir(tmp_path, monkeypatch):
  handa_dir = tmp_path / "custom-handa"
  monkeypatch.delenv("HANDA_STORAGE_ROOT", raising=False)

  app = create_app(handa_dir=handa_dir)
  client = TestClient(app)

  settings = client.get("/api/settings").json()
  assert settings["theme_id"] == "dark"
  assert settings["model_config_id"] == DEFAULT_MODEL_CONFIG_ID
  assert settings["streaming_mode_enabled"] is True
  assert (handa_dir / "handa.sqlite3").exists()


def test_web_api_invocation_creation_uses_project_project(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  def fake_execute_turn(ctx, invocation_id):
    ctx.db.update_turn(invocation_id, status="completed", final_text="done")

  async def fake_generate_session_title(prompt):
    return None

  monkeypatch.setattr(
      "src.api.turn_queue.spawn_turn_worker",
      fake_execute_turn,
  )
  monkeypatch.setattr(
      "src.api.session_bootstrap.generate_session_title",
      fake_generate_session_title,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post(
      "/api/projects",
      json={"agent_id": "orca_adk", "root_path": str(project)},
  ).json()
  created = client.post(
      "/api/turns",
      data={
          "agent_id": "orca_adk",
          "input_text": "hello",
          "project_id": project["id"],
          "model_config_id": "gemini-3.5-flash",
      },
  ).json()
  fetched = client.get(f"/api/turns/{created['id']}").json()
  session = app.state.web_context.services.session_service._read_session(
      created["session_id"]
  )

  assert created["status"] == "queued"
  assert created["title"] == "hello"
  assert created["input_text"] == "hello"
  assert created["trigger_kind"] == "user_message"
  assert created["model_config_id"] == "gemini-3.5-flash"
  assert session.state["handa:model_config_id"] == "gemini-3.5-flash"
  assert fetched["session_id"] == created["session_id"]
  meta = app.state.web_context.db.get_session_meta(created["session_id"])
  assert meta["project_id"] == project["id"]

  continued = client.post(
      "/api/turns",
      data={
          "agent_id": "orca_adk",
          "input_text": "continue",
          "project_id": project["id"],
          "session_id": created["session_id"],
          "model_config_id": "gemini-3.1-pro-low",
      },
  ).json()
  updated_session = app.state.web_context.services.session_service._read_session(
      created["session_id"]
  )

  assert continued["model_config_id"] == "gemini-3.1-pro-low"
  assert updated_session.state["handa:model_config_id"] == "gemini-3.1-pro-low"


def test_web_api_lists_agent_definitions(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  agents = client.get("/api/agents").json()

  by_id = {agent["id"]: agent for agent in agents}
  assert by_id["orca_adk"]["runtime"] == "adk"
  assert by_id["orca_adk"]["label"] == "Orca ADK"
  assert by_id["orca"]["runtime"] == "langgraph"
  assert by_id["orca"]["label"] == "Orca"


def test_web_api_agent_catalog(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  definitions_path = storage_root / "introspection" / "tool_definitions.json"
  definitions_path.parent.mkdir(parents=True, exist_ok=True)
  definitions_path.write_text(
      json.dumps(
          {
              "tools": [
                  {
                      "name": "files_read",
                      "namespace": "files",
                      "text": "files_read(path)\nRead a repository file.",
                  },
                  {"name": "run_agent", "text": "run_agent(config_name)"},
              ]
          }
      ),
      encoding="utf-8",
  )

  app = create_app()
  client = TestClient(app)

  response = client.get("/api/agents/catalog")

  assert response.status_code == 200
  body = response.json()

  tools = {tool["name"]: tool for tool in body["tools"]}
  assert tools["files_read"]["namespace"] == "files"
  assert tools["files_read"]["definition"].startswith("files_read(path)")
  # Exports written before the namespace field existed degrade to "".
  assert tools["run_agent"]["namespace"] == ""

  sections = {item["name"]: item for item in body["instruction_sections"]}
  assert "identity" in sections
  assert sections["identity"]["title"]
  assert "{agent_name}" in sections["identity"]["template"]

  assert body["skills"] == []

  assert body["model_configs"]
  first_option = body["model_configs"][0]
  assert first_option["id"]
  assert first_option["label"]
  assert first_option["context_window"] > 0


def test_web_api_agent_catalog_without_introspection_export(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)

  response = client.get("/api/agents/catalog")

  assert response.status_code == 200
  body = response.json()
  assert body["tools"] == []
  assert body["instruction_sections"]
  assert body["skills"] == []


def test_web_api_invocation_creation_persists_langgraph_runtime(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  def fake_execute_turn(ctx, invocation_id):
    ctx.db.update_turn(invocation_id, status="completed", final_text="done")

  monkeypatch.setattr(
      "src.api.turn_queue.spawn_turn_worker",
      fake_execute_turn,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()

  created = client.post(
      "/api/turns",
      data={
          "agent_id": "orca",
          "input_text": "hello",
          "project_id": project["id"],
      },
  ).json()
  session = app.state.web_context.services.session_service._read_session(
      created["session_id"]
  )

  meta = app.state.web_context.db.get_session_meta(created["session_id"])
  assert meta["agent_id"] == "orca"
  assert meta["agent_runtime"] == "langgraph"
  assert "handa:agent_runtime" not in session.state


def test_web_api_turn_creation_defaults_to_langgraph_main(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  def fake_execute_turn(ctx, invocation_id):
    ctx.db.update_turn(invocation_id, status="completed", final_text="done")

  monkeypatch.setattr(
      "src.api.turn_queue.spawn_turn_worker",
      fake_execute_turn,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()

  created = client.post(
      "/api/turns",
      data={
          "input_text": "hello",
          "project_id": project["id"],
      },
  ).json()
  meta = app.state.web_context.db.get_session_meta(created["session_id"])

  assert meta["agent_id"] == "orca"
  assert meta["agent_runtime"] == "langgraph"


def test_session_detail_projects_agent_tasks_and_child_breadcrumbs(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project_path))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  client.patch("/api/settings", json={"model_config_id": "gemini-3.5-flash"})
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Research storage.",
      summary="Storage child agent",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  ctx = app.state.web_context
  asyncio.run(
      ctx.services.artifact_service.save_artifact(
          app_name="handa",
          user_id="user",
          session_id=task["child_session_id"],
          filename="storage-report.md",
          artifact=types.Part.from_text(text="done"),
      )
  )
  asyncio.run(
      ctx.services.artifact_service.save_artifact(
          app_name="handa",
          user_id="user",
          session_id=parent["id"],
          filename="agent_run_main.report.md",
          artifact=types.Part.from_text(text="summary"),
      )
  )
  task["summary_artifact"] = "agent_run_main.report.md"
  save_task(task)

  parent_detail = client.get(f"/api/sessions/{parent['id']}/detail").json()
  child_detail = client.get(f"/api/sessions/{task['child_session_id']}/detail").json()

  assert parent_detail["breadcrumbs"] == [
      {
          "id": f"project:{project['id']}",
          "label": project["name"],
          "title": str(project_path),
      },
      {
          "id": parent["id"],
          "label": parent_detail["title"],
          "title": parent["id"],
      },
  ]
  assert parent_detail["background_runs"][0]["title"] == "Storage child agent"
  assert parent_detail["background_runs"][0]["status"] == "queued"
  assert parent_detail["background_runs"][0]["child_session_id"] == task["child_session_id"]
  assert parent_detail["background_runs"][0]["artifact_count"] == 2
  assert "swarm" not in " / ".join(crumb["label"] for crumb in child_detail["breadcrumbs"])
  assert child_detail["parent_session_id"] == parent["id"]
  assert child_detail["parent_task_id"] == task["id"]
  assert child_detail["prompt"] == "Research storage."
  assert child_detail["breadcrumbs"][-1]["label"].startswith("Storage child agent: ")


def test_session_detail_projects_command_background_tasks(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_background_task(
      command="pnpm dev",
      cwd=".",
      summary="Serve dev server",
      session_id=session["id"],
  )

  detail = client.get(f"/api/sessions/{session['id']}/detail").json()

  assert detail["background_runs"][0]["id"] == task["id"]
  assert detail["background_runs"][0]["kind"] == "command"
  assert detail["background_runs"][0]["title"] == "Serve dev server"
  assert detail["background_runs"][0]["status"] == "queued"
  assert detail["background_runs"][0]["current_step"] == "pnpm dev"
  assert detail["background_runs"][0]["child_session_id"] is None
  assert detail["background_runs"][0]["artifact_count"] == 0


def test_session_detail_projects_session_progress_items(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  app.state.web_context.services.session_service.merge_state_sync(
      session["id"],
      {
          PROGRESS_STATE_KEY: [
              {
                  "id": "plan",
                  "title": "Write implementation plan",
                  "status": "completed",
                  "updated_at": "2026-06-07T12:00:00+00:00",
                  "source_turn_id": "turn-1",
              },
              {
                  "id": "verify",
                  "title": "Run verification",
                  "status": "in_progress",
                  "detail": "pytest tests/test_web_api.py",
              },
          ]
      },
  )

  detail = client.get(f"/api/sessions/{session['id']}/detail").json()

  # The session has no live invocation, so the stored `running` item is a
  # leftover from an interrupted invocation and must not keep spinning.
  assert detail["progress_items"] == [
      {
          "id": "plan",
          "title": "Write implementation plan",
          "status": "done",
          "detail": None,
          "updated_at": "2026-06-07T12:00:00+00:00",
          "source_turn_id": "turn-1",
      },
      {
          "id": "verify",
          "title": "Run verification",
          "status": "pending",
          "detail": "pytest tests/test_web_api.py",
          "updated_at": None,
          "source_turn_id": None,
      },
  ]


def test_session_detail_keeps_running_progress_while_invocation_is_live(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  turn = ctx.db.create_turn(
      session_id=session["id"],
      title="Verify",
      input_text="Run verification.",
  )
  ctx.db.update_turn(turn["id"], status="running")
  ctx.services.session_service.merge_state_sync(
      session["id"],
      {
          PROGRESS_STATE_KEY: [
              {"id": "verify", "title": "Run verification", "status": "running"},
          ]
      },
  )

  detail = client.get(f"/api/sessions/{session['id']}/detail").json()

  assert detail["status"] == "running"
  assert detail["progress_items"][0]["status"] == "running"


def test_session_detail_and_routes_project_browser_environment(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project_row = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project_row["id"]},
  ).json()
  browser_dir(storage_root, session["id"]).mkdir(parents=True)
  browser_screenshot_path(storage_root, session["id"]).write_bytes(b"\x89PNG\r\n\x1a\n")
  browser_state_path(storage_root, session["id"]).write_text(
      json.dumps(
          {
              "success": True,
              "status": "open",
              "url": "http://127.0.0.1:8086",
              "title": "Handa Web",
              "last_action": "Captured screenshot",
              "last_error": None,
              "updated_at": "2026-06-08T18:30:00Z",
              "viewport": {"width": 1280, "height": 720},
              "last_snapshot": [{"id": "e1", "selector": "#app"}],
          }
      ),
      encoding="utf-8",
  )

  detail = client.get(f"/api/sessions/{session['id']}/detail").json()
  summary = client.get(f"/api/sessions/{session['id']}/browser").json()
  screenshot = client.get(f"/api/sessions/{session['id']}/browser/screenshot")

  assert detail["browser_environment"] == summary
  assert summary == {
      "success": True,
      "status": "open",
      "session_id": session["id"],
      "url": "http://127.0.0.1:8086",
      "title": "Handa Web",
      "last_action": "Captured screenshot",
      "last_error": None,
      "updated_at": "2026-06-08T18:30:00Z",
      "screenshot_url": f"/api/sessions/{session['id']}/browser/screenshot",
      "stream_url": f"/api/sessions/{session['id']}/browser/stream",
      "viewport": {"width": 1280, "height": 720},
  }
  assert screenshot.status_code == 200
  assert screenshot.headers["content-type"] == "image/png"


def test_session_detail_surfaces_child_sub_agent_browser_environment(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  app = create_app()
  client = TestClient(app)
  project_row = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project_row["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="browser",
      prompt="Open example.com and read the title.",
      summary="Browser child agent",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  child_id = task["child_session_id"]
  browser_dir(storage_root, child_id).mkdir(parents=True)
  browser_screenshot_path(storage_root, child_id).write_bytes(b"\x89PNG\r\n\x1a\n")
  browser_state_path(storage_root, child_id).write_text(
      json.dumps(
          {
              "success": True,
              "status": "open",
              "url": "https://example.com",
              "title": "Example",
              "updated_at": "2026-06-08T19:00:00Z",
          }
      ),
      encoding="utf-8",
  )

  # The parent (main) session has no Browser Environment of its own; it surfaces
  # the one owned by the delegated `browser` sub-agent child session.
  browser = client.get(f"/api/sessions/{parent['id']}/detail").json()["browser_environment"]

  assert browser is not None
  assert browser["session_id"] == child_id
  assert browser["url"] == "https://example.com"
  assert browser["screenshot_url"] == f"/api/sessions/{child_id}/browser/screenshot"
  assert browser["stream_url"] == f"/api/sessions/{child_id}/browser/stream"


def test_session_detail_skips_closed_child_browser_environment(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  app = create_app()
  client = TestClient(app)
  project_row = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project_row["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="browser",
      prompt="Open example.com.",
      summary="Browser child agent",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  child_id = task["child_session_id"]
  browser_dir(storage_root, child_id).mkdir(parents=True)
  browser_state_path(storage_root, child_id).write_text(
      json.dumps({"success": True, "status": "closed", "updated_at": "2026-06-08T19:00:00Z"}),
      encoding="utf-8",
  )

  # A closed child browser must not linger in the parent panel after the
  # sub-agent finishes.
  detail = client.get(f"/api/sessions/{parent['id']}/detail").json()
  assert detail["browser_environment"] is None


def test_browser_environment_routes_forward_interactions(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  class FakeBrowserManager:
    def __init__(self):
      self.calls: list[tuple[str, dict[str, object]]] = []

    async def refresh(self, *, session_id: str):
      self.calls.append(("refresh", {"session_id": session_id}))
      return browser_response(session_id, "Refreshed")

    async def click_at(self, **kwargs):
      self.calls.append(("click_at", kwargs))
      return browser_response(str(kwargs["session_id"]), "Clicked")

    async def type_text(self, **kwargs):
      self.calls.append(("type_text", kwargs))
      return browser_response(str(kwargs["session_id"]), "Typed")

    async def press_keys(self, **kwargs):
      self.calls.append(("press_keys", kwargs))
      return browser_response(str(kwargs["session_id"]), "Pressed")

    async def wheel(self, **kwargs):
      self.calls.append(("wheel", kwargs))
      return browser_response(str(kwargs["session_id"]), "Scrolled")

    async def mark_error(self, **kwargs):
      return {
          "success": False,
          "status": "error",
          "last_action": kwargs["action"],
          "last_error": kwargs["error"],
          "screenshot_url": None,
          "stream_url": f"/api/sessions/{kwargs['session_id']}/browser/stream",
      }

  def browser_response(session_id: str, action: str):
    return {
        "success": True,
        "status": "open",
        "url": "https://example.com",
        "title": "Example",
        "last_action": action,
        "last_error": None,
        "updated_at": "2026-06-08T19:00:00Z",
        "screenshot_url": f"/api/sessions/{session_id}/browser/screenshot",
        "stream_url": f"/api/sessions/{session_id}/browser/stream",
        "viewport": {"width": 1280, "height": 720},
    }

  fake = FakeBrowserManager()
  monkeypatch.setattr("src.api.routes.browser.default_browser_client", lambda: fake)
  app = create_app()
  client = TestClient(app)
  project_row = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project_row["id"]},
  ).json()

  refresh = client.post(f"/api/sessions/{session['id']}/browser/refresh")
  click = client.post(
      f"/api/sessions/{session['id']}/browser/interactions",
      json={"action": "click", "x": 0.25, "y": 0.5, "button": "right"},
  )
  typed = client.post(
      f"/api/sessions/{session['id']}/browser/interactions",
      json={"action": "type", "text": "Ada"},
  )
  key = client.post(
      f"/api/sessions/{session['id']}/browser/interactions",
      json={"action": "key", "key": "Enter"},
  )
  scroll = client.post(
      f"/api/sessions/{session['id']}/browser/interactions",
      json={"action": "scroll", "delta_y": 420},
  )

  assert refresh.status_code == 200
  assert click.status_code == 200
  assert typed.status_code == 200
  assert key.status_code == 200
  assert scroll.status_code == 200
  assert fake.calls == [
      ("refresh", {"session_id": session["id"]}),
      ("click_at", {"session_id": session["id"], "x": 0.25, "y": 0.5, "button": "right", "capture_screenshot": True}),
      ("type_text", {"session_id": session["id"], "text": "Ada", "capture_screenshot": True}),
      ("press_keys", {"session_id": session["id"], "keys": "Enter", "capture_screenshot": True}),
      ("wheel", {"session_id": session["id"], "delta_x": 0, "delta_y": 420, "capture_screenshot": True}),
  ]
  assert scroll.json()["last_action"] == "Scrolled"


def test_browser_environment_websocket_streams_frames_and_interactions(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  class FakeBrowserManager:
    def __init__(self):
      self.calls: list[tuple[str, dict[str, object]]] = []

    async def ensure_live(self, *, session_id: str):
      self.calls.append(("ensure_live", {"session_id": session_id}))
      return browser_response(session_id, "Live")

    async def stream_frames(self, *, session_id: str):
      self.calls.append(("stream_frames", {"session_id": session_id}))
      yield b"frame-1"
      await asyncio.sleep(60)

    async def click_at(self, **kwargs):
      self.calls.append(("click_at", kwargs))
      return browser_response(str(kwargs["session_id"]), "Clicked")

    async def set_viewport(self, *, session_id, width, height, capture_screenshot=False):
      self.calls.append((
          "set_viewport",
          {
              "session_id": session_id,
              "width": width,
              "height": height,
              "capture_screenshot": capture_screenshot,
          },
      ))
      return browser_response(str(session_id), "Resized")

    def has_live_session(self, session_id):
      return True

    async def mark_error(self, **kwargs):
      return {
          "success": False,
          "status": "error",
          "last_action": kwargs["action"],
          "last_error": kwargs["error"],
          "stream_url": f"/api/sessions/{kwargs['session_id']}/browser/stream",
      }

  def browser_response(session_id: str, action: str):
    return {
        "success": True,
        "status": "open",
        "url": "https://example.com",
        "title": "Example",
        "last_action": action,
        "last_error": None,
        "updated_at": "2026-06-08T19:00:00Z",
        "screenshot_url": f"/api/sessions/{session_id}/browser/screenshot",
        "stream_url": f"/api/sessions/{session_id}/browser/stream",
        "viewport": {"width": 1280, "height": 720},
    }

  fake = FakeBrowserManager()
  monkeypatch.setattr("src.api.routes.browser.default_browser_client", lambda: fake)
  app = create_app()
  client = TestClient(app)
  project_row = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project_row["id"]},
  ).json()

  with client.websocket_connect(f"/api/sessions/{session['id']}/browser/stream") as websocket:
    ready = websocket.receive_json()
    frame = websocket.receive_bytes()
    websocket.send_json({"action": "resize", "width": 800, "height": 900})
    resize_summary = websocket.receive_json()
    websocket.send_json({"action": "click", "x": 0.2, "y": 0.3})
    summary = websocket.receive_json()

  assert ready["type"] == "ready"
  assert ready["summary"]["stream_url"] == f"/api/sessions/{session['id']}/browser/stream"
  assert frame == b"frame-1"
  assert resize_summary["type"] == "summary"
  assert resize_summary["summary"]["last_action"] == "Resized"
  assert summary["type"] == "summary"
  assert summary["summary"]["last_action"] == "Clicked"
  sid = session["id"]
  assert fake.calls[:4] == [
      ("ensure_live", {"session_id": sid}),
      ("stream_frames", {"session_id": sid}),
      ("set_viewport", {"session_id": sid, "width": 800, "height": 900, "capture_screenshot": False}),
      ("click_at", {"session_id": sid, "x": 0.2, "y": 0.3, "button": "left", "capture_screenshot": False}),
  ]
  # On disconnect the live viewport is restored to the default 16:9 size.
  assert fake.calls[-1] == (
      "set_viewport",
      {"session_id": sid, "width": 1280, "height": 720, "capture_screenshot": False},
  )


def test_session_task_terminate_marks_agent_task_cancelled(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  killed: list[int] = []
  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )
  monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append(pid))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  client.patch("/api/settings", json={"model_config_id": "gemini-3.5-flash"})
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Research storage.",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )

  cancelled = client.post(
      f"/api/sessions/{parent['id']}/tasks/{task['id']}/terminate",
  ).json()
  conflict = client.post(
      f"/api/sessions/{parent['id']}/tasks/{task['id']}/terminate",
  )

  assert killed == [12345]
  assert cancelled["status"] == "cancelled"
  assert conflict.status_code == 409


def test_background_task_manager_delivers_subagent_completion_notification(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  client.patch("/api/settings", json={"model_config_id": "gemini-3.5-flash"})
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Research storage.",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  _mark_task_completed(parent["id"], task["id"], final_text="Child result.")

  ctx = app.state.web_context
  manager = BackgroundTaskManager(ctx, start_invocation_run=False)
  result = asyncio.run(manager.process_once())
  notifications = list_task_notifications(session_id=parent["id"])
  invocations = ctx.db.list_turns_for_session(parent["id"])

  assert result == {"created": 1, "delivered": 1, "blocked": 0}
  assert len(notifications) == 1
  assert notifications[0]["status"] == "delivered"
  assert notifications[0]["delivered_turn_id"] == invocations[-1]["id"]
  assert invocations[-1]["trigger_kind"] == "task_notification"
  assert invocations[-1]["model_config_id"] == "gemini-3.5-flash"
  assert "Child result." in invocations[-1]["input_text"]

  repeated = asyncio.run(manager.process_once())
  assert repeated["created"] == 0
  assert len(list_task_notifications(session_id=parent["id"])) == 1


def test_background_task_manager_dispatches_delivered_notification_invocation(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  seen: dict[str, str] = {}

  async def fake_run_agent_invocation(**kwargs):
    seen["input_text"] = kwargs["input_text"]
    return RunOutcome(final_text="Parent resumed from task notification.")

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Research storage.",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  _mark_task_completed(parent["id"], task["id"], final_text="Child result.")

  async def process_and_wait():
    ctx = app.state.web_context
    manager = BackgroundTaskManager(ctx, start_invocation_run=True)
    result = await manager.process_once()
    latest = ctx.db.list_turns_for_session(parent["id"])[-1]
    await execute_turn(ctx, latest["id"])
    return result

  result = asyncio.run(process_and_wait())
  invocations = app.state.web_context.db.list_turns_for_session(parent["id"])

  assert result == {"created": 1, "delivered": 1, "blocked": 0}
  assert invocations[-1]["trigger_kind"] == "task_notification"
  assert invocations[-1]["status"] == "completed"
  assert invocations[-1]["final_text"] == "Parent resumed from task notification."
  assert "System notification:" in seen["input_text"]
  assert "Child result." in seen["input_text"]


def test_background_task_manager_keeps_notification_pending_with_active_invocation(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  ctx.db.create_turn(
      session_id=parent["id"],
      title="Active user request",
      input_text="Still running.",
  )
  active = ctx.db.get_latest_turn_for_session(parent["id"])
  ctx.db.update_turn(active["id"], status="running")
  task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Research storage.",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  _mark_task_completed(parent["id"], task["id"], final_text="Child result.")

  manager = BackgroundTaskManager(ctx, start_invocation_run=False)
  result = asyncio.run(manager.process_once())
  notifications = list_task_notifications(session_id=parent["id"])

  assert result == {"created": 1, "delivered": 0, "blocked": 0}
  assert notifications[0]["status"] == "pending"
  assert len(ctx.db.list_turns_for_session(parent["id"])) == 1


def test_background_task_manager_keeps_nested_notification_pending_while_child_loop_runs(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  child_a_task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Run child A.",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  child_a_task_state = load_task(child_a_task["id"], session_id=parent["id"])
  child_a_task_state["status"] = "running"
  save_task(child_a_task_state)
  child_b_task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Run child B.",
      session_id=child_a_task["child_session_id"],
      user_id="user",
      app_name="handa",
      depth=1,
  )
  _mark_task_completed(
      child_a_task["child_session_id"],
      child_b_task["id"],
      final_text="Nested child result.",
  )

  ctx = app.state.web_context
  manager = BackgroundTaskManager(ctx, start_invocation_run=False)
  first = asyncio.run(manager.process_once())
  notifications = list_task_notifications(session_id=child_a_task["child_session_id"])

  assert first == {"created": 1, "delivered": 0, "blocked": 0}
  assert notifications[0]["status"] == "pending"
  assert ctx.db.list_turns_for_session(child_a_task["child_session_id"]) == []

  child_a_task_state = load_task(child_a_task["id"], session_id=parent["id"])
  child_a_task_state["status"] = "succeeded"
  child_a_task_state["returncode"] = 0
  save_task(child_a_task_state)
  second = asyncio.run(manager.process_once())
  invocations = ctx.db.list_turns_for_session(child_a_task["child_session_id"])
  notifications = list_task_notifications(session_id=child_a_task["child_session_id"])

  assert second == {"created": 0, "delivered": 1, "blocked": 0}
  assert notifications[0]["status"] == "delivered"
  assert invocations[-1]["trigger_kind"] == "task_notification"
  assert "Nested child result." in invocations[-1]["input_text"]


def test_background_task_manager_delivers_command_completion_notification(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_background_task(
      "python3 -c \"print('ok')\"",
      summary="Run smoke command",
      session_id=parent["id"],
  )
  task_state = load_task(task["id"], session_id=parent["id"])
  task_state["status"] = "succeeded"
  task_state["returncode"] = 0
  save_task(task_state)
  append_task_event(
      "task.completed",
      f"Task {task['id']} completed",
      session_id=parent["id"],
      task_id=task["id"],
  )

  ctx = app.state.web_context
  manager = BackgroundTaskManager(ctx, start_invocation_run=False)
  result = asyncio.run(manager.process_once())
  notifications = list_task_notifications(session_id=parent["id"])
  invocations = ctx.db.list_turns_for_session(parent["id"])

  assert result == {"created": 1, "delivered": 1, "blocked": 0}
  assert notifications[0]["payload"]["task_kind"] == "command"
  assert invocations[-1]["trigger_kind"] == "task_notification"


def test_invocation_queue_prioritizes_task_notification_before_user_message(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  user_invocation = ctx.db.create_turn(
      session_id=session["id"],
      title="User pending",
      input_text="next user message",
  )
  notification_invocation = ctx.db.create_turn(
      session_id=session["id"],
      title="Task notification",
      input_text="System notification",
      trigger_kind="task_notification",
  )
  started: list[str] = []

  def fake_execute(ctx, invocation_id):
    started.append(invocation_id)
    ctx.db.update_turn(invocation_id, status="completed", final_text="done")

  dispatch_next_queued_turn(ctx, session["id"], executor=fake_execute)

  assert started == [notification_invocation["id"]]
  assert ctx.db.get_turn(notification_invocation["id"])["status"] == "completed"
  assert ctx.db.get_turn(user_invocation["id"])["status"] == "queued"


def test_execute_turn_finalizes_waiting_parent_task(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(project))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.contract.task_store.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  async def fake_run_agent_invocation(**kwargs):
    return RunOutcome(final_text="Parent child resumed and finished.")

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  parent = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  task = start_run_agent_task(
      agent_id="orca_adk",
      prompt="Run child A.",
      session_id=parent["id"],
      user_id="user",
      app_name="handa",
  )
  task_state = load_task(task["id"], session_id=parent["id"])
  task_state["status"] = "waiting"
  save_task(task_state)
  app.state.web_context.db.create_session(
      session_id=task["child_session_id"],
      project_id=project["id"],
      agent_id="orca_adk",
      parent_session_id=parent["id"],
      parent_task_id=task["id"],
  )
  invocation = app.state.web_context.db.create_turn(
      session_id=task["child_session_id"],
      title="Task notification",
      input_text="System notification",
      trigger_kind="task_notification",
  )

  asyncio.run(execute_turn(app.state.web_context, invocation["id"]))
  finalized = load_task(task["id"], session_id=parent["id"])
  result = json.loads(task_result_file(task["id"], session_id=parent["id"]).read_text())
  events = [event["kind"] for event in list_task_events(session_id=parent["id"], limit=20)]

  assert finalized["status"] == "succeeded"
  assert finalized["returncode"] == 0
  assert result["final_text"] == "Parent child resumed and finished."
  assert "task.completed" in events


def _mark_task_completed(session_id: str, task_id: str, *, final_text: str) -> None:
  task = load_task(task_id, session_id=session_id)
  task["status"] = "succeeded"
  task["returncode"] = 0
  save_task(task)
  task_result_file(task_id, session_id=session_id).write_text(
      json.dumps(
          {
              "success": True,
              "task_id": task_id,
              "kind": task["kind"],
              "agent_id": task.get("agent_id"),
              "child_session_id": task["child_session_id"],
              "final_text": final_text,
          }
      )
      + "\n",
      encoding="utf-8",
  )
  append_task_event(
      "task.completed",
      f"{task['kind']} {task_id} completed",
      session_id=session_id,
      task_id=task_id,
      payload={"child_session_id": task["child_session_id"]},
  )


def test_session_title_generation_updates_session(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  async def fake_generate_session_title(prompt):
    assert prompt == "feat: design session naming rules"
    return "Design session naming rules"

  monkeypatch.setattr(
      "src.api.session_bootstrap.generate_session_title",
      fake_generate_session_title,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
      title="design session naming rules",
  )

  asyncio.run(
      generate_and_store_session_title(
          ctx,
          "session-1",
          "feat: design session naming rules",
      )
  )

  assert ctx.db.get_session_meta("session-1")["title"] == "Design session naming rules"


def test_session_title_generation_keeps_fallback_on_empty_model_output(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  async def fake_generate_session_title(prompt):
    return None

  monkeypatch.setattr(
      "src.api.session_bootstrap.generate_session_title",
      fake_generate_session_title,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
      title="fallback title",
  )

  asyncio.run(
      generate_and_store_session_title(ctx, "session-1", "hello")
  )

  assert ctx.db.get_session_meta("session-1")["title"] == "fallback title"


def test_session_title_auto_generation_does_not_override_manual_rename(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  async def fake_generate_session_title(prompt):
    return "Auto generated"

  monkeypatch.setattr(
      "src.api.session_bootstrap.generate_session_title",
      fake_generate_session_title,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
      title="seed",
  )
  renamed = client.patch(
      "/api/sessions/session-1",
      json={"title": "My manual name"},
  ).json()
  assert renamed["title"] == "My manual name"

  asyncio.run(generate_and_store_session_title(ctx, "session-1", "hello"))

  assert ctx.db.get_session_meta("session-1")["title"] == "My manual name"


def test_execute_turn_uses_project_root(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  seen = {}

  async def fake_run_agent_invocation(**kwargs):
    seen["project_root"] = kwargs["project_root"]
    seen["input_text"] = kwargs["input_text"]
    seen["model_config_id"] = kwargs["model_config_id"]
    seen["streaming_mode_enabled"] = kwargs["streaming_mode_enabled"]
    return RunOutcome(final_text="done")

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
  )
  invocation = ctx.db.create_turn(
      session_id="session-1",
      model_config_id="gemini-3.5-flash",
      title="hello",
      input_text="hello",
  )
  ctx.db.set_user_setting(
      user_id=ctx.settings.user_id,
      key="streaming_mode_enabled",
      value="false",
  )

  asyncio.run(execute_turn(ctx, invocation["id"]))
  fetched = ctx.db.get_turn(invocation["id"])

  assert seen["project_root"] == str(project_path)
  assert seen["input_text"] == "hello"
  assert seen["model_config_id"] == "gemini-3.5-flash"
  assert seen["streaming_mode_enabled"] is False
  assert fetched["status"] == "completed"


def test_execute_turn_runs_langgraph_agent(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  (project / "README.md").write_text("demo\n", encoding="utf-8")
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

  from types import SimpleNamespace

  from google.genai import types
  from src.agents.handa_langgraph import orca as lg_main

  captured_user_texts: list[str] = []
  scripted = [
      types.Content(
          role="model",
          parts=[
              types.Part(
                  function_call=types.FunctionCall(
                      name="files_read", args={"path": "README.md"}
                  )
              )
          ],
      ),
      types.Content(role="model", parts=[types.Part(text="LLM inspected README.md.")]),
  ]
  call_count: list[int] = []

  async def fake_generate(*, client, model, contents, config):
    call_count.append(1)
    first_text = contents[0].parts[0].text if contents and contents[0].parts else ""
    captured_user_texts.append(str(first_text))
    content = scripted[len(call_count) - 1]
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

  monkeypatch.setattr(lg_main, "_generate_model_response", fake_generate)

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-langgraph",
          state={
              "handa:agent_id": "orca",
              "handa:project_id": project["id"],
              "handa:project_root": str(project),
          },
      )
  )
  ctx.db.create_session(
      session_id="session-langgraph",
      project_id=project["id"],
      agent_id="orca",
      agent_runtime="langgraph",
      title="LangGraph run",
  )
  previous = ctx.db.create_turn(
      session_id="session-langgraph",
      title="Previous",
      input_text="What agent are you?",
  )
  ctx.db.update_turn(
      previous["id"],
      status="completed",
      final_text="I am Orca.",
  )
  invocation = ctx.db.create_turn(
      session_id="session-langgraph",
      title="LangGraph run",
      input_text="Inspect project.",
  )

  asyncio.run(execute_turn(ctx, invocation["id"]))

  fetched = ctx.db.get_turn(invocation["id"])
  events = ctx.db.list_steps_for_turn(turn_id=invocation["id"])
  detail = client.get("/api/sessions/session-langgraph/detail").json()

  assert fetched["status"] == "completed"
  assert fetched["final_text"] == "LLM inspected README.md."
  kinds = [event["kind"] for event in events]
  # Lifecycle bookkeeping (langgraph.started) is filtered out of the timeline.
  assert "runtime_step" not in kinds
  assert kinds[-1] == "agent_text"
  assert "tool_call" in kinds
  assert "tool_response" in kinds
  assert events[-1]["id"].startswith("lg_")
  assert events[-1]["payload"]["final"] is True
  tool_response = next(event for event in events if event["kind"] == "tool_response")
  assert tool_response["payload"]["name"] == "files_read"
  assert tool_response["payload"]["response"]["path"] == "README.md"
  from src.storage.runtime_event_store import RuntimeEventStore

  raw_kinds = [
      item["event"]["kind"]
      for item in RuntimeEventStore(storage_root).list_events(
          session_id="session-langgraph",
          runtime="langgraph",
      )
  ]
  assert "langgraph.tool_call" in raw_kinds
  assert "langgraph.tool_result" in raw_kinds
  # Cross-turn memory lives in the LangGraph checkpointer; no conversation
  # context text is folded into the user message anymore.
  assert captured_user_texts[0] == "Inspect project."
  assert detail["agent_runtime"] == "langgraph"
  assert [step["kind"] for step in detail["steps"]] == kinds


def test_langgraph_child_session_detail_replays_runtime_events(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  ctx = app.state.web_context
  parent_id = "parent-session"
  child_id = "child-session"
  task_id = "task_langgraph"
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=parent_id,
          state={
              "handa:agent_id": "orca_adk",
              "handa:project_id": project["id"],
              "handa:project_root": str(project_path),
          },
      )
  )
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id=child_id,
          state={
              "handa:parent_session_id": parent_id,
              "handa:parent_task_id": task_id,
              "handa:agent_runtime": "langgraph",
              "handa:target_agent_id": "orca",
              "handa:project_id": project["id"],
              "handa:project_root": str(project_path),
              "handa:run_agent_prompt": "Inspect the project.",
          },
      )
  )
  ctx.db.create_session(
      session_id=parent_id,
      project_id=project["id"],
      agent_id="orca_adk",
      agent_runtime="adk",
  )
  save_task(
      {
          "id": task_id,
          "session_id": parent_id,
          "kind": "run_agent",
          "status": "running",
          "agent_runtime": "langgraph",
          "agent_id": "orca",
          "child_session_id": child_id,
          "project_root": str(project_path),
          "prompt": "Inspect the project.",
          "created_ts": 1,
      }
  )

  from src.storage.runtime_event_store import RuntimeEventStore

  store = RuntimeEventStore(storage_root)
  store.append(
      session_id=child_id,
      turn_id=f"session:{child_id}",
      runtime="langgraph",
      event={
          "id": "lg_call_event",
          "kind": "langgraph.tool_call",
          "summary": "call artifacts_save_text",
          "payload": {
              "call_id": "lg_call_123",
              "name": "artifacts_save_text",
              "args": {"filename": "report.md"},
          },
      },
  )
  store.append(
      session_id=child_id,
      turn_id=f"session:{child_id}",
      runtime="langgraph",
      event={
          "id": "lg_result_event",
          "kind": "langgraph.tool_result",
          "summary": "artifacts_save_text -> ok=True",
          "payload": {
              "call_id": "lg_call_123",
              "name": "artifacts_save_text",
              "ok": True,
              "result": {"ok": True, "filename": "report.md", "version": 2},
          },
      },
  )
  store.append(
      session_id=child_id,
      turn_id=f"session:{child_id}",
      runtime="langgraph",
      event={
          "id": "lg_final",
          "kind": "agent_text",
          "summary": "Orca response",
          "payload": {"text": "Done.", "final": True},
      },
  )

  detail = client.get(f"/api/sessions/{child_id}/detail").json()
  steps = client.get(f"/api/sessions/{child_id}/steps").json()
  kinds = [step["kind"] for step in detail["steps"]]

  assert detail["agent_runtime"] == "langgraph"
  assert detail["parent_session_id"] == parent_id
  # The tool_result event projects a tool_response plus an artifact_delta; each
  # is now its own top-level step rather than nested under payload.projections.
  assert kinds == ["tool_call", "tool_response", "artifact_delta", "agent_text"]
  assert "projections" not in detail["steps"][1]["payload"]
  assert detail["steps"][1]["id"] == "lg_result_event"
  assert detail["steps"][2]["id"] == "lg_result_event#1"
  assert detail["steps"][2]["payload"]["filename"] == "report.md"
  # The DB-materialized /steps endpoint and the live /detail projection agree.
  assert steps == detail["steps"]


def test_execute_turn_tracks_current_context_token_usage(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  async def fake_run_agent_invocation(**kwargs):
    services = kwargs["services"]
    session = services.session_service._read_session(kwargs["session_id"])
    for event in (_usage_event(250, 40), _usage_event(100, 10)):
      appended = await services.session_service.append_event(session, event)
      await kwargs["on_event"](appended)
    return RunOutcome(final_text="done")

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
  )
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id="user",
          session_id="session-1",
      )
  )
  invocation = ctx.db.create_turn(
      session_id="session-1",
      title="hello",
      input_text="hello",
  )

  asyncio.run(execute_turn(ctx, invocation["id"]))
  fetched = client.get(f"/api/turns/{invocation['id']}").json()

  assert fetched["input_token_count"] == 100
  assert fetched["output_token_count"] == 50
  assert fetched["total_token_count"] == 400


def test_invocation_summary_uses_stored_usage_without_reading_events(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  invocation = ctx.db.create_turn(
      session_id=session["id"],
      title="hello",
      input_text="hello",
  )
  ctx.db.add_turn_token_usage(
      invocation["id"],
      input_token_count=11,
      output_token_count=7,
      total_token_count=25,
  )

  def fail_event_read(**_kwargs):
    raise AssertionError("summary endpoint must not read invocation events")

  monkeypatch.setattr(ctx.db, "list_steps_for_turn", fail_event_read)

  listed = client.get(f"/api/turns?session_id={session['id']}").json()
  fetched = client.get(f"/api/turns/{invocation['id']}").json()

  assert listed[0]["input_token_count"] == 11
  assert listed[0]["output_token_count"] == 7
  assert listed[0]["total_token_count"] == 25
  assert fetched["total_token_count"] == 25


def test_session_invocation_events_returns_session_events_in_one_request(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  first = ctx.db.create_turn(
      session_id=session["id"],
      title="first",
      input_text="first",
  )
  second = ctx.db.create_turn(
      session_id=session["id"],
      title="second",
      input_text="second",
  )
  ctx.db.append_step(
      turn_id=first["id"],
      id="event-1",
      kind="agent_text",
      summary="First",
      payload={"text": "first"},
  )
  ctx.db.append_step(
      turn_id=first["id"],
      id="event-1b",
      kind="agent_text_delta",
      summary="First delta",
      payload={"text": "first delta"},
  )
  ctx.db.append_step(
      turn_id=second["id"],
      id="event-2",
      kind="tool_call",
      summary="Second",
      payload={"name": "tool"},
  )

  events = client.get(f"/api/sessions/{session['id']}/steps").json()

  assert [event["turn_id"] for event in events] == [first["id"], first["id"], second["id"]]
  assert [event["turn_id"] for event in events] == [first["id"], first["id"], second["id"]]
  assert all("session_id" not in event for event in events)
  assert [event["seq"] for event in events] == [1, 2, 1]
  assert [event["session_seq"] for event in events] == [1, 2, 3]
  assert [event["summary"] for event in events] == ["First", "First delta", "Second"]

  after_first_turn = client.get(
      f"/api/sessions/{session['id']}/steps?after_seq=2"
  ).json()

  assert [event["summary"] for event in after_first_turn] == ["Second"]


def test_web_database_session_turn_step_aliases(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context

  session = ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
      title="Session title",
  )
  turn = ctx.db.create_turn(
      session_id=session["id"],
      title="Turn title",
      input_text="hello",
  )
  step = ctx.db.append_step(
      turn_id=turn["id"],
      id="runtime-event-1",
      kind="agent_text",
      summary="Step summary",
      payload={"text": "hello"},
  )

  assert ctx.db.get_session("session-1")["title"] == "Session title"
  assert ctx.db.get_turn(turn["id"])["input_text"] == "hello"
  assert "session_id" not in step
  assert step["turn_id"] == turn["id"]
  assert step["session_seq"] == 1
  assert ctx.db.list_turns_for_session("session-1")[0]["id"] == turn["id"]
  assert ctx.db.list_steps_for_turn(turn_id=turn["id"])[0]["summary"] == "Step summary"
  assert ctx.db.list_steps_for_session(session_id="session-1")[0]["id"] == "runtime-event-1"


def test_web_database_backfills_session_seq_for_existing_invocation_events(tmp_path):
  db_path = tmp_path / "handa.sqlite3"
  connection = sqlite3.connect(str(db_path))
  connection.executescript(
      """
      create table web_invocations (
        id text primary key,
        session_id text not null,
        project_id text,
        agent_id text not null,
        agent_runtime text not null default 'adk',
        model_config_id text,
        title text,
        input_text text not null,
        trigger_kind text not null default 'user_message',
        status text not null,
        created_at text not null,
        updated_at text not null,
        started_at text,
        finished_at text,
        cancel_requested_at text,
        input_token_count integer not null default 0,
        output_token_count integer not null default 0,
        total_token_count integer not null default 0,
        final_text text,
        error_type text,
        error_message text
      );
      create table web_invocation_events (
        invocation_id text not null,
        seq integer not null,
        session_id text not null,
        adk_event_id text,
        adk_invocation_id text,
        runtime_event_id text,
        runtime_invocation_id text,
        kind text not null,
        summary text not null,
        payload_json text not null,
        raw_event_json text not null,
        created_at text not null,
        primary key (invocation_id, seq)
      );
      """
  )
  connection.execute(
      "insert into web_invocations (id, session_id, project_id, agent_id, title,"
      " input_text, status, created_at, updated_at) values"
      " ('i1','S','p','main','First','first','completed',"
      "'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
  )
  connection.execute(
      "insert into web_invocations (id, session_id, project_id, agent_id, title,"
      " input_text, status, created_at, updated_at) values"
      " ('i2','S','p','main','Second','second','completed',"
      "'2026-01-01T00:01:00Z','2026-01-01T00:01:00Z')"
  )
  connection.execute(
      "insert into web_invocation_events (invocation_id, seq, session_id,"
      " adk_event_id, kind, summary, payload_json, raw_event_json, created_at)"
      " values ('i1',1,'S','e1','agent_text','first','{}','{}','2026-01-01T00:00:01Z')"
  )
  connection.execute(
      "insert into web_invocation_events (invocation_id, seq, session_id,"
      " adk_event_id, kind, summary, payload_json, raw_event_json, created_at)"
      " values ('i2',1,'S','e2','agent_text','second','{}','{}','2026-01-01T00:01:01Z')"
  )
  connection.commit()
  connection.close()

  db = WebDatabase(db_path)
  db.init_schema()

  steps = db.list_steps_for_session(session_id="S")
  assert [step["summary"] for step in steps] == ["first", "second"]
  assert [step["seq"] for step in steps] == [1, 1]
  assert [step["session_seq"] for step in steps] == [1, 2]
  assert [step["summary"] for step in db.list_steps_for_session(session_id="S", after_seq=1)] == [
      "second"
  ]


def test_execute_turn_persists_cancelled_status(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  async def fake_run_agent_invocation(**kwargs):
    raise asyncio.CancelledError()

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
  )
  invocation = ctx.db.create_turn(
      session_id="session-1",
      title="hello",
      input_text="hello",
  )

  asyncio.run(execute_turn(ctx, invocation["id"]))
  fetched = ctx.db.get_turn(invocation["id"])
  events = ctx.db.list_steps_for_turn(turn_id=invocation["id"])

  assert fetched["status"] == "cancelled"
  assert fetched["error_type"] == "Cancelled"
  assert any(event["kind"] == "turn_cancelled" for event in events)


def _usage_event(input_tokens: int, output_tokens: int):
  return Event(
      invocation_id="adk-inv-1",
      author="handa",
      content=types.Content(role="model", parts=[types.Part(text="chunk")]),
      usage_metadata=types.GenerateContentResponseUsageMetadata(
          prompt_token_count=input_tokens,
          candidates_token_count=output_tokens,
          total_token_count=input_tokens + output_tokens,
      ),
  )


def test_web_api_terminate_invocation_marks_active_run_cancelled(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  def fake_execute_turn(ctx, invocation_id):
    ctx.db.update_turn(invocation_id, status="running")

  async def fake_generate_session_title(prompt):
    return None

  monkeypatch.setattr(
      "src.api.turn_queue.spawn_turn_worker",
      fake_execute_turn,
  )
  monkeypatch.setattr(
      "src.api.session_bootstrap.generate_session_title",
      fake_generate_session_title,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  created = client.post(
      "/api/turns",
      data={"agent_id": "orca_adk", "input_text": "hello", "project_id": project["id"]},
  ).json()

  terminated = client.post(f"/api/turns/{created['id']}/terminate").json()
  fetched = client.get(f"/api/turns/{created['id']}").json()

  assert terminated["status"] == "cancelled"
  assert fetched["status"] == "cancelled"
  assert fetched["cancel_requested_at"] is not None


def test_create_turn_persists_attachments_and_serves_bytes(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  seen = {}

  async def fake_run_agent_invocation(**kwargs):
    seen["attachments"] = kwargs.get("attachments")
    return RunOutcome(final_text="done")

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  created = client.post(
      "/api/turns",
      data={"agent_id": "orca_adk", "input_text": "look at this", "project_id": project["id"]},
      files=[("files", ("hello.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
  ).json()

  assert len(created["attachments"]) == 1
  attachment = created["attachments"][0]
  assert attachment["filename"] == "hello.png"
  assert attachment["kind"] == "image"
  assert attachment["mime_type"] == "image/png"

  fetched = client.get(f"/api/turns/{created['id']}").json()
  assert len(fetched["attachments"]) == 1

  blob = client.get(
      f"/api/turns/{created['id']}/attachments/{attachment['id']}"
  )
  assert blob.status_code == 200
  assert blob.content == b"\x89PNG\r\n\x1a\n"
  assert blob.headers["content-type"].startswith("image/png")

  ctx = app.state.web_context
  asyncio.run(execute_turn(ctx, created["id"]))
  assert seen["attachments"]
  assert seen["attachments"][0]["filename"] == "hello.png"
  attachment_columns = {
      row["name"]
      for row in ctx.db._connection.execute(  # noqa: SLF001
          "pragma table_info(web_turn_attachments)"
      )
  }
  assert "session_id" not in attachment_columns


def test_create_turn_allows_attachment_without_text(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  response = client.post(
      "/api/turns",
      data={"agent_id": "orca_adk", "input_text": "", "project_id": project["id"]},
      files=[("files", ("notes.txt", b"hi", "text/plain"))],
  )

  assert response.status_code == 200
  assert len(response.json()["attachments"]) == 1


def test_create_turn_requires_text_or_files(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  response = client.post(
      "/api/turns",
      data={"agent_id": "orca_adk", "input_text": "", "project_id": project["id"]},
  )

  assert response.status_code == 422


def test_web_api_queues_second_invocation_for_active_session(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  session = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  active = ctx.db.create_turn(
      session_id=session["id"],
      title="active",
      input_text="active",
  )
  ctx.db.update_turn(active["id"], status="running")

  response = client.post(
      "/api/turns",
      data={
          "agent_id": "orca_adk",
          "input_text": "second",
          "project_id": project["id"],
          "session_id": session["id"],
      },
  )

  queued = response.json()
  invocations = ctx.db.list_turns_for_session(session["id"])

  assert response.status_code == 200
  assert queued["status"] == "queued"
  assert queued["input_text"] == "second"
  assert [item["id"] for item in invocations] == [active["id"], queued["id"]]


def test_web_database_cancels_stale_active_invocations(tmp_path):
  db = WebDatabase(tmp_path / "handa.sqlite3")
  db.init_schema()
  db.create_session(
      session_id="session-1",
      project_id="project-1",
      agent_id="orca_adk",
  )
  invocation = db.create_turn(
      session_id="session-1",
      title="stale",
      input_text="stale",
  )
  db.update_turn(invocation["id"], status="running")

  changed = db.cancel_stale_active_turns()
  fetched = db.get_turn(invocation["id"])

  assert changed == 1
  assert fetched["status"] == "cancelled"
  assert fetched["error_type"] == "ServerRestarted"


def test_web_api_invocation_creation_requires_project(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  response = client.post(
      "/api/turns",
      data={"agent_id": "orca_adk", "input_text": "hello", "project_id": "missing"},
  )

  assert response.status_code == 404


def test_web_database_migrates_runs_tables_to_invocations(tmp_path):
  db_path = tmp_path / "handa.sqlite3"
  connection = sqlite3.connect(db_path)
  connection.executescript(
      """
      create table web_runs (
        id text primary key,
        session_id text not null,
        project_id text,
        agent_id text not null,
        title text,
        prompt text not null,
        status text not null,
        created_at text not null,
        updated_at text not null,
        started_at text,
        finished_at text,
        final_text text,
        error_type text,
        error_message text
      );
      create table web_run_events (
        run_id text not null,
        seq integer not null,
        session_id text not null,
        adk_event_id text,
        kind text not null,
        summary text not null,
        payload_json text not null,
        raw_event_json text not null,
        created_at text not null,
        primary key (run_id, seq)
      );
      """
  )
  connection.execute(
      """
      insert into web_runs (
        id, session_id, project_id, agent_id, title, prompt, status,
        created_at, updated_at, final_text
      ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
          "run_old",
          "session-1",
          "project-1",
          "orca_adk",
          "Old title",
          "Old prompt",
          "completed",
          "2026-05-17T00:00:00Z",
          "2026-05-17T00:00:01Z",
          "Old response",
      ),
  )
  connection.execute(
      """
      insert into web_run_events (
        run_id, seq, session_id, adk_event_id, kind, summary,
        payload_json, raw_event_json, created_at
      ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
          "run_old",
          1,
          "session-1",
          "event-1",
          "agent_text",
          "Assistant response",
          json.dumps({"text": "Old response"}),
          json.dumps({"id": "event-1", "invocation_id": "adk-inv-1"}),
          "2026-05-17T00:00:02Z",
      ),
  )
  connection.commit()
  connection.close()

  db = WebDatabase(db_path)
  db.init_schema()

  invocation = db.get_turn("run_old")
  assert invocation is not None
  assert invocation["input_text"] == "Old prompt"
  assert invocation["trigger_kind"] == "user_message"
  assert invocation["model_config_id"] == DEFAULT_MODEL_CONFIG_ID
  events = db.list_steps_for_turn(turn_id="run_old")
  assert events[0]["id"] == "event-1"
  assert "runtime_turn_id" not in events[0]
  assert events[0]["session_seq"] == 1
  assert "session_id" not in events[0]
  assert events[0]["turn_id"] == "run_old"
  trace_path = tmp_path / "sessions" / "session-1" / "runtime" / "adk" / "events.jsonl"
  trace_event = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
  assert trace_event["id"] == "event-1"
  assert trace_event["event"]["invocation_id"] == "adk-inv-1"
  step_columns = {
      row["name"]
      for row in db._connection.execute("pragma table_info(web_steps)")  # noqa: SLF001
  }
  assert "raw_event_json" not in step_columns
  assert "adk_event_id" not in step_columns
  assert "adk_invocation_id" not in step_columns
  assert "runtime_event_id" not in step_columns
  assert "runtime_event_seq" not in step_columns
  assert "runtime_event_offset" not in step_columns
  assert "runtime_turn_id" not in step_columns
  assert "session_id" not in step_columns

  tables = {
      row["name"]
      for row in db._connection.execute(  # noqa: SLF001
          "select name from sqlite_master where type = 'table'"
      )
  }
  assert "web_turns" in tables
  assert "web_steps" in tables
  assert "web_runs" not in tables
  assert "web_run_events" not in tables


def test_session_title_is_canonical_not_latest_invocation(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project)}).json()
  ctx = app.state.web_context
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-1",
          state={"handa:agent_id": "orca_adk", "handa:project_id": project["id"]},
      )
  )
  ctx.db.create_session(
      session_id="session-1",
      project_id=project["id"],
      agent_id="orca_adk",
      title="First message title",
  )
  ctx.db.create_turn(
      session_id="session-1",
      title="First message title",
      input_text="first",
  )
  ctx.db.create_turn(
      session_id="session-1",
      title="Second message title",
      input_text="second",
  )

  listed = client.get("/api/sessions").json()
  summary = next(item for item in listed if item["id"] == "session-1")
  detail = client.get("/api/sessions/session-1/detail").json()

  assert summary["title"] == "First message title"
  assert detail["title"] == "First message title"


def test_backfill_web_sessions_from_legacy_tables(tmp_path):
  db_path = tmp_path / "handa.sqlite3"
  connection = sqlite3.connect(str(db_path))
  connection.executescript(
      """
      create table web_invocations (
        id text primary key, session_id text not null, project_id text,
        agent_id text not null, model_config_id text, title text,
        input_text text not null,
        trigger_kind text not null default 'user_message',
        status text not null, created_at text not null, updated_at text not null,
        started_at text, finished_at text, cancel_requested_at text,
        input_token_count integer not null default 0,
        output_token_count integer not null default 0,
        final_text text, error_type text, error_message text
      );
      create table web_session_stars (
        project_id text not null, session_id text not null,
        starred_at text not null, primary key (project_id, session_id)
      );
      """
  )
  connection.execute(
      "insert into web_invocations (id, session_id, project_id, agent_id, title,"
      " input_text, status, created_at, updated_at) values"
      " ('i1','S','p','main','First','first','completed',"
      "'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
  )
  connection.execute(
      "insert into web_invocations (id, session_id, project_id, agent_id, title,"
      " input_text, status, created_at, updated_at) values"
      " ('i2','S','p','main','Second','second','completed',"
      "'2026-01-01T00:05:00Z','2026-01-01T00:05:00Z')"
  )
  connection.execute(
      "insert into web_session_stars (project_id, session_id, starred_at)"
      " values ('p','S','2026-01-02T00:00:00Z')"
  )
  connection.commit()
  connection.close()

  db = WebDatabase(db_path)
  db.init_schema()

  meta = db.get_session_meta("S")
  assert meta is not None
  assert meta["title"] == "First"
  assert meta["title_source"] == "auto"
  assert meta["starred_at"] == "2026-01-02T00:00:00Z"
  assert db._table_exists("web_session_stars") is False  # noqa: SLF001


def test_init_schema_strips_legacy_fork_title_prefix(tmp_path):
  db = WebDatabase(tmp_path / "handa.sqlite3")
  db.init_schema()
  # Legacy auto-generated fork title carries the redundant "Fork: " prefix.
  db.create_session(
      session_id="fork-auto",
      project_id="p",
      agent_id="orca_adk",
      title="Fork: Analyze code",
      title_source="fork",
      forked_from_session_id="src",
  )
  # User renamed their fork — keep exactly what they typed.
  db.create_session(
      session_id="fork-manual",
      project_id="p",
      agent_id="orca_adk",
      title="Fork: My keeper",
      title_source="manual",
      forked_from_session_id="src",
  )

  db.init_schema()  # re-run exercises the one-time cleanup; must be idempotent
  db.init_schema()

  assert db.get_session_meta("fork-auto")["title"] == "Analyze code"
  assert db.get_session_meta("fork-manual")["title"] == "Fork: My keeper"


def test_init_schema_normalizes_legacy_web_turns_before_creating_indexes(tmp_path):
  db_path = tmp_path / "handa.sqlite3"
  connection = sqlite3.connect(str(db_path))
  connection.executescript(
      """
      create table web_turns (
        id text primary key,
        thread_id text not null,
        project_id text,
        agent_id text not null,
        title text,
        input_text text not null,
        status text not null,
        created_at text not null,
        updated_at text not null
      );
      insert into web_turns (
        id, thread_id, project_id, agent_id, title, input_text,
        status, created_at, updated_at
      ) values (
        'turn-1', 'legacy-session-1', 'project-1', 'main',
        'Legacy chat', 'hello', 'completed',
        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
      );
      """
  )
  connection.commit()
  connection.close()

  db = WebDatabase(db_path)
  db.init_schema()

  columns = {
      row["name"]
      for row in db._connection.execute("pragma table_info(web_turns)")  # noqa: SLF001
  }
  indexes = {
      row["name"]
      for row in db._connection.execute(  # noqa: SLF001
          "select name from sqlite_master where type = 'index'"
      )
  }
  meta = db.get_session_meta("legacy-session-1")
  turns = db.list_turns_for_session("legacy-session-1")

  assert "thread_id" not in columns
  assert "session_id" in columns
  assert "idx_web_turns_session" in indexes
  assert meta is not None
  assert meta["title"] == "Legacy chat"
  assert turns[0]["id"] == "turn-1"
  assert turns[0]["session_id"] == "legacy-session-1"


def test_init_schema_backfills_session_project_id_from_session_state(tmp_path):
  db_path = tmp_path / "handa.sqlite3"
  session_dir = tmp_path / "sessions" / "session-with-state"
  session_dir.mkdir(parents=True)
  (session_dir / "session.json").write_text(
      json.dumps(
          {
              "id": "session-with-state",
              "state": {
                  "handa:project_id": "project-from-state",
                  "handa:project_root": str(tmp_path / "project"),
              },
          }
      ),
      encoding="utf-8",
  )
  connection = sqlite3.connect(str(db_path))
  connection.executescript(
      """
      create table web_sessions (
        id text primary key,
        project_id text,
        agent_id text not null,
        agent_runtime text not null default 'adk',
        title text,
        title_source text not null default 'auto',
        parent_session_id text,
        parent_task_id text,
        forked_from_session_id text,
        forked_from_turn_id text,
        forked_at text,
        starred_at text,
        archived_at text,
        deleted_at text,
        unread_at text,
        created_at text not null
      );
      insert into web_sessions (
        id, project_id, agent_id, agent_runtime, title, title_source, created_at
      ) values (
        'session-with-state', null, 'main', 'adk', 'State-backed chat', 'auto',
        '2026-01-01T00:00:00Z'
      );
      """
  )
  connection.commit()
  connection.close()

  db = WebDatabase(db_path)
  db.init_schema()

  meta = db.get_session_meta("session-with-state")
  assert meta is not None
  assert meta["project_id"] == "project-from-state"


def _patch_resume_spawn_inline(monkeypatch):
  import threading

  from src import turn_worker as _turn_worker

  def fake_spawn(task, *, extra_env=None):
    thread = threading.Thread(
        target=lambda: asyncio.run(
            _turn_worker._run_turn(task["session_id"], task["id"])
        ),
        daemon=True,
    )
    thread.start()
    return task

  monkeypatch.setattr("src.contract.task_store.spawn_web_turn_worker", fake_spawn)


def test_turn_user_input_pause_submit_resume_flow(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  questions = [
      {
          "id": "approach",
          "prompt": "Which approach?",
          "options": [{"label": "A (recommended)"}, {"label": "B"}],
          "multi_select": False,
          "allow_free_text": True,
      }
  ]
  pending = {
      "request_id": "uireq_test",
      "runtime": "langgraph",
      "tool_name": "request_user_input",
      "questions": questions,
  }
  holder = {}
  seen = {}

  async def fake_run_agent_invocation(**kwargs):
    if kwargs.get("resume_user_input") is not None:
      seen["resume_user_input"] = kwargs["resume_user_input"]
      return RunOutcome(final_text="Done after input.")
    if kwargs["input_text"] != "Do the thing.":
      # Follow-up turns dispatched by the background loop complete plainly.
      return RunOutcome(final_text="done")
    # Mimic the runtime persisting the pending request in session state and
    # emitting its own user_input_requested trace event.
    holder["ctx"].services.session_service.merge_state_sync(
        kwargs["session_id"],
        {"handa:pending_user_input": pending},
    )
    await kwargs["on_event"](
        {
            "kind": "langgraph.user_input_requested",
            "summary": "Orca is waiting for user input",
            "payload": {"pending_user_input": pending},
        }
    )
    return RunOutcome(pending_user_input=pending)

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )
  _patch_resume_spawn_inline(monkeypatch)

  app = create_app()
  client = TestClient(app)
  client.__enter__()
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  ctx = app.state.web_context
  holder["ctx"] = ctx
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-input",
          state={},
      )
  )
  ctx.db.create_session(
      session_id="session-input",
      project_id=project["id"],
      agent_id="orca",
      agent_runtime="langgraph",
  )
  turn = ctx.db.create_turn(
      session_id="session-input",
      title="Ask me",
      input_text="Do the thing.",
  )

  asyncio.run(execute_turn(ctx, turn["id"]))

  fetched = ctx.db.get_turn(turn["id"])
  assert fetched["status"] == "waiting_input"
  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  requested = [step for step in steps if step["kind"] == "user_input_requested"]
  assert len(requested) == 1
  assert requested[0]["payload"]["pending_user_input"]["request_id"] == "uireq_test"

  # A waiting_input turn blocks dispatching queued turns in the same session.
  follow_up = ctx.db.create_turn(
      session_id="session-input",
      title="Next message",
      input_text="Another request.",
  )
  assert ctx.db.claim_next_queued_turn_for_session("session-input") is None

  # Terminate is rejected; the form must be answered or cancelled.
  response = client.post(f"/api/turns/{turn['id']}/terminate")
  assert response.status_code == 409
  assert "user-input" in response.json()["detail"]

  # Wrong request_id and invalid answers are rejected.
  response = client.post(
      f"/api/turns/{turn['id']}/user-input",
      json={"request_id": "nope", "answers": []},
  )
  assert response.status_code == 409
  response = client.post(
      f"/api/turns/{turn['id']}/user-input",
      json={"request_id": "uireq_test", "answers": []},
  )
  assert response.status_code == 422

  # Valid submission resumes the turn to completion.
  response = client.post(
      f"/api/turns/{turn['id']}/user-input",
      json={
          "request_id": "uireq_test",
          "answers": [{"id": "approach", "selected": ["B"]}],
      },
  )
  assert response.status_code == 200

  import time

  for _ in range(100):
    fetched = ctx.db.get_turn(turn["id"])
    if fetched["status"] not in {"running", "waiting_input"}:
      break
    time.sleep(0.02)
  assert fetched["status"] == "completed"
  assert fetched["final_text"] == "Done after input."
  assert seen["resume_user_input"]["request_id"] == "uireq_test"
  assert seen["resume_user_input"]["response"]["answers"][0]["selected"] == ["B"]
  state = ctx.services.session_service.read_state_sync("session-input")
  assert not state.get("handa:pending_user_input")
  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  assert any(step["kind"] == "user_input_submitted" for step in steps)

  # Duplicate submission is rejected once the turn is no longer waiting.
  response = client.post(
      f"/api/turns/{turn['id']}/user-input",
      json={
          "request_id": "uireq_test",
          "answers": [{"id": "approach", "selected": ["B"]}],
      },
  )
  assert response.status_code == 409

  # The queue is unblocked: either we can claim the follow-up here, or the
  # background dispatcher already picked it up.
  claimed = ctx.db.claim_next_queued_turn_for_session("session-input")
  follow_up_status = ctx.db.get_turn(follow_up["id"])["status"]
  assert claimed is not None or follow_up_status != "queued"
  client.__exit__(None, None, None)


def test_turn_user_input_cancelled_resumes_with_cancellation(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  pending = {
      "request_id": "uireq_cancel",
      "runtime": "langgraph",
      "tool_name": "request_user_input",
      "questions": [
          {
              "id": "q1",
              "prompt": "Pick one",
              "options": [{"label": "A"}, {"label": "B"}],
              "multi_select": False,
              "allow_free_text": True,
          }
      ],
  }
  holder = {}
  seen = {}

  async def fake_run_agent_invocation(**kwargs):
    if kwargs.get("resume_user_input") is not None:
      seen["resume_user_input"] = kwargs["resume_user_input"]
      return RunOutcome(final_text="Proceeding with defaults.")
    if kwargs["input_text"] != "Do the thing.":
      return RunOutcome(final_text="done")
    # Mimic the runtime persisting the pending request in session state and
    # emitting its own user_input_requested trace event.
    holder["ctx"].services.session_service.merge_state_sync(
        kwargs["session_id"],
        {"handa:pending_user_input": pending},
    )
    await kwargs["on_event"](
        {
            "kind": "langgraph.user_input_requested",
            "summary": "Orca is waiting for user input",
            "payload": {"pending_user_input": pending},
        }
    )
    return RunOutcome(pending_user_input=pending)

  monkeypatch.setattr(
      "src.turn_worker.run_agent_invocation",
      fake_run_agent_invocation,
  )
  _patch_resume_spawn_inline(monkeypatch)

  app = create_app()
  client = TestClient(app)
  client.__enter__()
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  ctx = app.state.web_context
  holder["ctx"] = ctx
  asyncio.run(
      ctx.services.session_service.create_session(
          app_name=APP_NAME,
          user_id=ctx.settings.user_id,
          session_id="session-cancel",
          state={},
      )
  )
  ctx.db.create_session(
      session_id="session-cancel",
      project_id=project["id"],
      agent_id="orca",
      agent_runtime="langgraph",
  )
  turn = ctx.db.create_turn(
      session_id="session-cancel",
      title="Ask me",
      input_text="Do the thing.",
  )
  asyncio.run(execute_turn(ctx, turn["id"]))
  assert ctx.db.get_turn(turn["id"])["status"] == "waiting_input"

  response = client.post(
      f"/api/turns/{turn['id']}/user-input",
      json={"request_id": "uireq_cancel", "cancelled": True},
  )
  assert response.status_code == 200

  import time

  for _ in range(100):
    fetched = ctx.db.get_turn(turn["id"])
    if fetched["status"] not in {"running", "waiting_input"}:
      break
    time.sleep(0.02)
  assert fetched["status"] == "completed"
  assert fetched["final_text"] == "Proceeding with defaults."
  assert seen["resume_user_input"]["response"] == {"cancelled": True}
  client.__exit__(None, None, None)


def test_web_api_agent_context_usage_static_preview(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_dir = tmp_path / "project"
  project_dir.mkdir()
  (project_dir / "AGENTS.md").write_text("# AGENTS\n\nUse uv for everything.\n", encoding="utf-8")
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_dir)}).json()

  response = client.get(
      "/api/agents/orca/context-usage",
      params={"project_id": project["id"]},
  )

  assert response.status_code == 200
  body = response.json()
  assert body["agent_id"] == "orca"
  assert body["agent_runtime"] == "langgraph"
  assert body["total_token_count"] > 0
  by_id = {item["id"]: item for item in body["breakdown"]}
  assert by_id["instruction"]["token_count"] > 0
  instruction_children = {child["id"] for child in by_id["instruction"]["children"]}
  assert "project_config" in instruction_children
  assert by_id["user_messages"]["token_count"] == 0

  missing = client.get("/api/agents/not_a_real_agent/context-usage")
  assert missing.status_code == 404


def test_web_api_retry_turn_reruns_failed_turn_with_same_input(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  dispatched = []
  monkeypatch.setattr(
      "src.api.routes.turns.dispatch_next_queued_turn",
      lambda ctx, session_id: dispatched.append(session_id),
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  ctx.db.update_session_title(source["id"], "Investigate", source="manual")

  kept = ctx.db.create_turn(
      session_id=source["id"],
      title="Kept",
      input_text="Keep this.",
  )
  ctx.db.update_turn(kept["id"], status="completed", final_text="Kept.")
  failed = ctx.db.create_turn(
      session_id=source["id"],
      title="Boom",
      input_text="Do the thing.",
  )
  ctx.db.update_turn(failed["id"], status="failed", error_type="APIError", error_message="boom")
  attachment_path = storage_root / "sessions" / source["id"] / "attachments" / "spec.txt"
  attachment_path.parent.mkdir(parents=True)
  attachment_path.write_text("attached spec", encoding="utf-8")
  ctx.db.create_attachment(
      turn_id=failed["id"],
      ordinal=0,
      filename="spec.txt",
      mime_type="text/plain",
      kind="text",
      byte_count=13,
      storage_path=str(attachment_path),
  )

  response = client.post(f"/api/turns/{failed['id']}/retry")

  assert response.status_code == 200, response.json()
  retried = response.json()
  turns = ctx.db.list_turns_for_session(source["id"])
  assert [turn["id"] for turn in turns] == [kept["id"], retried["id"]]
  assert retried["id"] != failed["id"]
  assert retried["input_text"] == "Do the thing."
  assert retried["status"] == "queued"
  cloned = ctx.db.list_attachments_for_turn(retried["id"])
  assert [item["filename"] for item in cloned] == ["spec.txt"]
  assert cloned[0]["storage_path"] == str(attachment_path)
  assert dispatched == [source["id"]]


def test_web_api_retry_turn_rejects_live_turn(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr(
      "src.api.routes.turns.dispatch_next_queued_turn",
      lambda ctx, session_id: None,
  )

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  running = ctx.db.create_turn(session_id=source["id"], title="Live", input_text="hi")
  ctx.db.update_turn(running["id"], status="running")

  response = client.post(f"/api/turns/{running['id']}/retry")
  assert response.status_code == 409, response.json()


def test_web_api_terminate_turn_cancels_child_agent_runs(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  monkeypatch.setattr(os, "killpg", lambda pid, sig: None)

  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  source = client.post(
      "/api/sessions",
      json={"agent_id": "orca_adk", "project_id": project["id"]},
  ).json()
  ctx = app.state.web_context
  turn = ctx.db.create_turn(session_id=source["id"], title="Parent", input_text="spawn a child")
  ctx.db.update_turn(turn["id"], status="running")
  save_task(
      {
          "id": "child-run",
          "session_id": source["id"],
          "kind": "run_agent",
          "status": "running",
          "worker_pid": 999999,
          "child_session_id": "child-sess",
          "created_ts": 1.0,
      }
  )

  response = client.post(f"/api/turns/{turn['id']}/terminate")

  assert response.status_code == 200, response.json()
  assert response.json()["status"] == "cancelled"
  assert load_task("child-run", session_id=source["id"])["status"] == "cancelled"
