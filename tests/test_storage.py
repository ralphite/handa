from __future__ import annotations

import json
import asyncio
import re

from google.genai import types
from google.adk.events import Event

from src.storage import HandaArtifactService
from src.storage import HandaSessionService
from src.storage.artifact_service import artifact_display_filename
from src.storage.artifact_service import artifact_stored_filename
from src.storage.paths import resolve_storage_root


def test_session_service_persists_session_json(tmp_path):
  asyncio.run(_assert_session_service_persists_session_json(tmp_path))


async def _assert_session_service_persists_session_json(tmp_path):
  service = HandaSessionService(root=str(tmp_path / ".handa"))

  session = await service.create_session(
      app_name="handa",
      user_id="user",
      state={"goal": "test"},
      session_id="session-1",
  )
  loaded = await service.get_session(
      app_name="handa",
      user_id="user",
      session_id=session.id,
  )

  session_file = tmp_path / ".handa" / "sessions" / "session-1" / "session.json"
  assert session_file.exists()
  assert loaded is not None
  assert loaded.state["goal"] == "test"
  assert json.loads(session_file.read_text(encoding="utf-8"))["id"] == "session-1"


def test_session_service_stores_events_in_runtime_jsonl(tmp_path):
  asyncio.run(_assert_session_service_stores_events_in_runtime_jsonl(tmp_path))


async def _assert_session_service_stores_events_in_runtime_jsonl(tmp_path):
  root = tmp_path / ".handa"
  service = HandaSessionService(root=str(root))
  session = await service.create_session(
      app_name="handa",
      user_id="user",
      state={"handa:active_turn_id": "turn-1"},
      session_id="session-1",
  )
  event = Event(
      invocation_id="runtime-turn-1",
      author="agent",
      content=types.Content(parts=[types.Part(text="hello")]),
  )

  await service.append_event(session, event)

  session_file = root / "sessions" / "session-1" / "session.json"
  trace_file = root / "sessions" / "session-1" / "runtime" / "adk" / "events.jsonl"
  stored_session = json.loads(session_file.read_text(encoding="utf-8"))
  trace = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[0])
  loaded = await service.get_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )

  assert stored_session["events"] == []
  assert trace["turn_id"] == "turn-1"
  assert "runtime_turn_id" not in trace
  assert trace["id"] == trace["event"]["id"]
  assert trace["event"]["author"] == "agent"
  assert loaded is not None
  assert len(loaded.events) == 1
  assert loaded.events[0].invocation_id == "runtime-turn-1"


def test_services_use_process_handa_directory(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  session_service = HandaSessionService()
  artifact_service = HandaArtifactService()

  assert session_service.root == str(storage_root)
  assert artifact_service.root == str(storage_root)


def test_storage_root_defaults_to_home_handa_directory(tmp_path, monkeypatch):
  monkeypatch.delenv("HANDA_STORAGE_ROOT", raising=False)
  monkeypatch.setenv("HOME", str(tmp_path))

  assert resolve_storage_root() == tmp_path / ".handa"


def test_session_service_generates_sortable_short_default_id(tmp_path):
  asyncio.run(_assert_session_service_generates_sortable_short_default_id(tmp_path))


async def _assert_session_service_generates_sortable_short_default_id(tmp_path):
  service = HandaSessionService(root=str(tmp_path / ".handa"))

  session = await service.create_session(
      app_name="handa",
      user_id="user",
  )

  assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-z]{6}", session.id)
  assert (
      tmp_path / ".handa" / "sessions" / session.id / "session.json"
  ).exists()


def test_artifact_service_writes_versioned_files(tmp_path):
  asyncio.run(_assert_artifact_service_writes_versioned_files(tmp_path))


async def _assert_artifact_service_writes_versioned_files(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  service = HandaArtifactService(root=str(root))

  first = await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="main.agent.json",
      artifact=types.Part(text="one"),
  )
  second = await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="main.agent.json",
      artifact=types.Part(text="two"),
  )

  artifact_dir = root / "sessions" / "session-1" / "artifacts"
  assert first == 0
  assert second == 1
  assert (artifact_dir / "main.v1.agent.json").read_text(encoding="utf-8") == "one"
  assert (artifact_dir / "main.v2.agent.json").read_text(encoding="utf-8") == "two"
  assert await service.list_artifact_keys(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  ) == ["main.v1.agent.json", "main.v2.agent.json"]

  latest = await service.load_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="main.agent.json",
  )
  assert latest is not None
  assert latest.text == "two"
  assert await service.list_versions(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="main.agent.json",
  ) == [0, 1]


def test_artifact_service_strips_untyped_version_suffix(tmp_path):
  asyncio.run(_assert_artifact_service_strips_untyped_version_suffix(tmp_path))


async def _assert_artifact_service_strips_untyped_version_suffix(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  service = HandaArtifactService(root=str(root))

  version = await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="security_audit_report.v1.md",
      artifact=types.Part(text="report"),
  )

  artifact_dir = root / "sessions" / "session-1" / "artifacts"
  assert version == 0
  assert (artifact_dir / "security_audit_report.v1.artifact.md").exists()
  assert not (artifact_dir / "security_audit_report.v1.v1.md").exists()
  assert artifact_display_filename("security_audit_report.v1.md") == "security_audit_report.md"
  assert artifact_stored_filename("security_audit_report.v1.md", version) == "security_audit_report.v1.artifact.md"


def test_artifact_service_serializes_concurrent_writes(tmp_path):
  asyncio.run(_assert_artifact_service_serializes_concurrent_writes(tmp_path))


async def _assert_artifact_service_serializes_concurrent_writes(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  service = HandaArtifactService(root=str(root))

  versions = await asyncio.gather(
      *[
          service.save_artifact(
              app_name="handa",
              user_id="user",
              session_id="session-1",
              filename="qa_plan.plan.md",
              artifact=types.Part(text=f"write {index}"),
          )
          for index in range(20)
      ]
  )

  artifact_dir = root / "sessions" / "session-1" / "artifacts"
  assert sorted(versions) == list(range(20))
  assert len(list(artifact_dir.glob("qa_plan.v*.plan.md"))) == 20
  assert await service.list_versions(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="qa_plan.plan.md",
  ) == list(range(20))
