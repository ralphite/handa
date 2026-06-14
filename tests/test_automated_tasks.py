from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.automated_tasks.dispatcher import dispatch_due_time_triggers
from src.api.automated_tasks.run_sync import sync_automated_task_runs
from src.api.automated_tasks.schedule import compute_next_fire


def _client(tmp_path, monkeypatch, *, complete_turns=True):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  def fake_execute_turn(ctx, turn_id):
    if complete_turns:
      ctx.db.update_turn(turn_id, status="completed", final_text="done")

  async def fake_generate_session_title(prompt):
    return None

  monkeypatch.setattr("src.api.turn_queue.spawn_turn_worker", fake_execute_turn)
  monkeypatch.setattr(
      "src.api.session_bootstrap.generate_session_title",
      fake_generate_session_title,
  )
  # Task titles are LLM-generated like session names; stub it (no Gemini in tests).
  monkeypatch.setattr(
      "src.api.routes.automated_tasks.generate_session_title",
      fake_generate_session_title,
  )

  app = create_app()
  return app, TestClient(app)


def _make_project(client, tmp_path, name="project"):
  root = tmp_path / name
  root.mkdir()
  return client.post("/api/projects", json={"root_path": str(root)}).json()


def _make_task(client, project_id, **overrides):
  payload = {
      "name": "Nightly review",
      "project_id": project_id,
      "prompt": "Review the repo",
      "agent_id": "orca_adk",
      "model_config_id": "gemini-3.5-flash",
  }
  payload.update(overrides)
  return client.post("/api/automated-tasks", json=payload)


def test_automated_task_crud(tmp_path, monkeypatch):
  _, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)

  resp = _make_task(
      client,
      project["id"],
      description="nightly",
      triggers=[{"type": "time", "config": {"cron": "0 6 * * *"}}],
  )
  assert resp.status_code == 200
  task = resp.json()
  tid = task["id"]
  assert task["name"] == "Nightly review"
  assert task["enabled"] is False  # default disabled
  assert task["agent_id"] == "orca_adk"
  assert task["model_config_id"] == "gemini-3.5-flash"
  assert task["prompt"] == "Review the repo"
  assert task["runs"] == []
  assert len(task["triggers"]) == 1
  assert task["triggers"][0]["type"] == "time"
  assert task["triggers"][0]["config"]["cron"] == "0 6 * * *"

  listed = client.get("/api/automated-tasks", params={"project_id": project["id"]}).json()
  assert [t["id"] for t in listed] == [tid]

  patched = client.patch(
      f"/api/automated-tasks/{tid}",
      json={"name": "Renamed", "prompt": "New prompt", "triggers": []},
  ).json()
  assert patched["name"] == "Renamed"
  assert patched["prompt"] == "New prompt"
  assert patched["triggers"] == []

  assert client.post(f"/api/automated-tasks/{tid}/enable").json()["enabled"] is True
  assert client.post(f"/api/automated-tasks/{tid}/disable").json()["enabled"] is False

  assert client.delete(f"/api/automated-tasks/{tid}").json() == {"id": tid, "removed": True}
  assert client.get(f"/api/automated-tasks/{tid}").status_code == 404
  assert client.get("/api/automated-tasks", params={"project_id": project["id"]}).json() == []


def test_run_now_creates_visible_session(tmp_path, monkeypatch):
  app, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"]).json()

  run = client.post(f"/api/automated-tasks/{task['id']}/run").json()
  assert run["trigger_kind"] == "manual"
  assert run["session_id"]
  assert run["turn_id"]

  # The auto session is an ordinary session — it shows up in the session list.
  sessions = client.get("/api/sessions").json()
  assert run["session_id"] in [s["id"] for s in sessions]
  listed_session = next(s for s in sessions if s["id"] == run["session_id"])
  assert listed_session["automated_task_id"] == task["id"]

  session_detail = client.get(f"/api/sessions/{run['session_id']}/detail").json()
  assert session_detail["automated_task_id"] == task["id"]

  # Its first turn carries the automated_task trigger kind.
  turn = client.get(f"/api/turns/{run['turn_id']}").json()
  assert turn["trigger_kind"] == "automated_task"
  assert turn["input_text"] == "Review the repo"

  # Session state records the source for the "Automated" badge.
  state = app.state.web_context.services.session_service._read_session(
      run["session_id"]
  ).state
  assert state["handa:automated_task_id"] == task["id"]
  assert state["handa:automated_task_run_id"] == run["id"]

  # Task last-run pointers update; run history lists the run.
  detail = client.get(f"/api/automated-tasks/{task['id']}").json()
  assert detail["last_run_session_id"] == run["session_id"]
  assert detail["last_triggered_at"]
  runs = client.get(f"/api/automated-tasks/{task['id']}/runs").json()
  assert [r["id"] for r in runs] == [run["id"]]


def test_run_status_mirrors_first_turn(tmp_path, monkeypatch):
  app, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"]).json()

  run = client.post(f"/api/automated-tasks/{task['id']}/run").json()
  assert run["status"] == "launched"

  # The fake worker completed the turn at dispatch; the background loop mirrors
  # that terminal state onto the run.
  moved = sync_automated_task_runs(app.state.web_context)
  assert moved == 1
  refreshed = client.get(f"/api/automated-tasks/{task['id']}/runs").json()[0]
  assert refreshed["status"] == "completed"


def test_run_records_error_when_project_missing(tmp_path, monkeypatch):
  app, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"]).json()

  # Project deleted between task creation and the fire: launch records `error`
  # and never spawns a session.
  app.state.web_context.db.delete_project(project["id"])
  run = client.post(f"/api/automated-tasks/{task['id']}/run").json()
  assert run["status"] == "error"
  assert run["session_id"] is None
  assert run["error_message"]


def test_manual_runs_are_not_deduped(tmp_path, monkeypatch):
  _, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"]).json()

  first = client.post(f"/api/automated-tasks/{task['id']}/run").json()
  second = client.post(f"/api/automated-tasks/{task['id']}/run").json()
  assert first["id"] != second["id"]
  runs = client.get(f"/api/automated-tasks/{task['id']}/runs").json()
  assert len(runs) == 2


def test_create_derives_name_from_prompt(tmp_path, monkeypatch):
  # The UI has no Name field; the task is titled from its prompt like a session.
  _, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  resp = client.post(
      "/api/automated-tasks",
      json={
          "project_id": project["id"],
          "prompt": "Summarize open PRs every morning",
          "agent_id": "orca_adk",
      },
  )
  assert resp.status_code == 200
  task = resp.json()
  assert task["name"]
  assert "Summarize open PRs" in task["name"]


def test_create_validation(tmp_path, monkeypatch):
  _, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)

  assert _make_task(client, "proj_missing").status_code == 404
  assert _make_task(client, project["id"], agent_id="nope").status_code == 400
  assert _make_task(client, project["id"], model_config_id="not-a-model").status_code == 400


def _time_trigger(cron="0 6 * * *", timezone="UTC"):
  return {"type": "time", "config": {"cron": cron, "timezone": timezone}}


def _first_trigger(client, task_id):
  return client.get(f"/api/automated-tasks/{task_id}").json()["triggers"][0]


def test_compute_next_fire_basics():
  # Daily 06:00 UTC, evaluated after 07:00 → next day 06:00.
  assert compute_next_fire("0 6 * * *", "UTC", after="2026-06-14T07:00:00Z") == "2026-06-15T06:00:00Z"
  # DST-aware: 06:00 New York in June (EDT, UTC-4) == 10:00 UTC, same day.
  assert (
      compute_next_fire("0 6 * * *", "America/New_York", after="2026-06-14T07:00:00Z")
      == "2026-06-14T10:00:00Z"
  )
  # Unparseable / empty cron is inert, never raises.
  assert compute_next_fire("not a cron", "UTC") is None
  assert compute_next_fire("", "UTC") is None


def test_create_time_trigger_schedules_next_fire(tmp_path, monkeypatch):
  _, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"], triggers=[_time_trigger()]).json()
  trig = task["triggers"][0]
  # Creating a time trigger computes its first fire (06:00 UTC) up front.
  assert trig["next_fire_at"] and trig["next_fire_at"].endswith("06:00:00Z")


def test_time_trigger_dispatch_fires_run(tmp_path, monkeypatch):
  app, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"], triggers=[_time_trigger()]).json()
  tid = task["id"]
  client.post(f"/api/automated-tasks/{tid}/enable")

  ctx = app.state.web_context
  trigger_id = _first_trigger(client, tid)["id"]
  # Force the slot into the past so it is due right now.
  ctx.db.set_automated_task_trigger_next_fire(trigger_id, next_fire_at="2000-01-01T00:00:00Z")

  fired = asyncio.run(dispatch_due_time_triggers(ctx))
  assert fired == 1

  runs = client.get(f"/api/automated-tasks/{tid}/runs").json()
  assert len(runs) == 1
  assert runs[0]["trigger_kind"] == "time"
  assert runs[0]["session_id"]

  # The slot is consumed: last_fired_at recorded, next_fire_at advanced forward.
  trig = _first_trigger(client, tid)
  assert trig["last_fired_at"] == "2000-01-01T00:00:00Z"
  assert trig["next_fire_at"] not in (None, "2000-01-01T00:00:00Z")


def test_time_trigger_dispatch_is_idempotent_per_slot(tmp_path, monkeypatch):
  app, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  task = _make_task(client, project["id"], triggers=[_time_trigger()]).json()
  tid = task["id"]
  client.post(f"/api/automated-tasks/{tid}/enable")

  ctx = app.state.web_context
  trigger_id = _first_trigger(client, tid)["id"]
  ctx.db.set_automated_task_trigger_next_fire(trigger_id, next_fire_at="2000-01-01T00:00:00Z")
  asyncio.run(dispatch_due_time_triggers(ctx))

  # Re-arm the SAME slot: dedup_key is identical, so the second pass launches nothing.
  ctx.db.set_automated_task_trigger_next_fire(trigger_id, next_fire_at="2000-01-01T00:00:00Z")
  second = asyncio.run(dispatch_due_time_triggers(ctx))
  assert second == 0
  assert len(client.get(f"/api/automated-tasks/{tid}/runs").json()) == 1


def test_disabled_task_time_trigger_not_dispatched(tmp_path, monkeypatch):
  app, client = _client(tmp_path, monkeypatch)
  project = _make_project(client, tmp_path)
  # Task left disabled (the default), even though the slot is due.
  task = _make_task(client, project["id"], triggers=[_time_trigger()]).json()
  tid = task["id"]

  ctx = app.state.web_context
  trigger_id = _first_trigger(client, tid)["id"]
  ctx.db.set_automated_task_trigger_next_fire(trigger_id, next_fire_at="2000-01-01T00:00:00Z")

  fired = asyncio.run(dispatch_due_time_triggers(ctx))
  assert fired == 0
  assert client.get(f"/api/automated-tasks/{tid}/runs").json() == []
