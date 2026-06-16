from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.storage.paths import runtime_events_path
from src.storage.runtime_event_store import RuntimeEventStore
from src.api.app import create_app
from src.api.steps_projection import emit_web_step
from src.api.steps_projection import ingest_session_events
from src.api.steps_projection import record_runtime_event
from src.api.steps_projection import WEB_TRACE_RUNTIME


def _make_ctx(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_path = tmp_path / "project"
  project_path.mkdir(exist_ok=True)
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  app = create_app()
  client = TestClient(app)
  project = client.post("/api/projects", json={"root_path": str(project_path)}).json()
  ctx = app.state.web_context
  return ctx, client, project, storage_root


def _make_turn(ctx, client, project, *, agent_id: str = "orca") -> tuple[str, dict]:
  session = client.post(
      "/api/sessions",
      json={"agent_id": agent_id, "project_id": project["id"]},
  ).json()
  turn = ctx.db.create_turn(
      session_id=session["id"],
      title="hello",
      input_text="hello",
  )
  return session["id"], turn


def test_emit_web_step_materializes_step_and_event_log(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  emit_web_step(
      ctx,
      session_id=session_id,
      turn_id=turn["id"],
      kind="user_input_requested",
      summary="Waiting for user input",
      payload={"pending_user_input": {"request_id": "req-1"}},
  )

  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  assert [step["kind"] for step in steps] == ["user_input_requested"]
  assert steps[0]["payload"]["pending_user_input"]["request_id"] == "req-1"

  events_file = runtime_events_path(storage_root, session_id, WEB_TRACE_RUNTIME)
  envelopes = [json.loads(line) for line in events_file.read_text().splitlines()]
  assert [env["event"]["kind"] for env in envelopes] == ["web.user_input_requested"]
  assert envelopes[0]["turn_id"] == turn["id"]


def test_reingest_after_cursor_reset_is_idempotent(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project, agent_id="orca")

  for index in range(2):
    RuntimeEventStore(storage_root).append(
        session_id=session_id,
        turn_id=turn["id"],
        runtime="native",
        event={
            "id": f"lg_evt_{index}",
            "kind": "agent_text",
            "payload": {
                "text": f"part {index}",
                "usage_metadata": {
                    "prompt_token_count": 100,
                    "candidates_token_count": 10,
                    "total_token_count": 110,
                },
            },
        },
    )
  ingest_session_events(ctx, session_id=session_id, runtime="native")

  first_steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  first_usage = ctx.db.get_turn(turn["id"])
  assert len(first_steps) == 2
  assert first_usage["output_token_count"] == 20

  ctx.db.reset_event_cursor(session_id=session_id, runtime="native")
  ingest_session_events(ctx, session_id=session_id, runtime="native")

  again_steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  again_usage = ctx.db.get_turn(turn["id"])
  assert [step["id"] for step in again_steps] == [step["id"] for step in first_steps]
  assert again_usage["output_token_count"] == first_usage["output_token_count"]
  assert again_usage["total_token_count"] == first_usage["total_token_count"]


def test_ingest_accumulates_tool_and_file_stats(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project, agent_id="orca")

  store = RuntimeEventStore(storage_root)
  store.append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime="native",
      event={
          "id": "lg_tool_0",
          "kind": "orca.tool_result",
          "payload": {
              "name": "files_write",
              "result": {"success": True, "lines_added": 5, "lines_removed": 2},
          },
      },
  )
  store.append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime="native",
      event={
          "id": "lg_tool_1",
          "kind": "orca.tool_result",
          "payload": {
              "name": "commands_run",
              "result": {
                  "success": False,
                  "returncode": 1,
                  "duration_sec": 1.5,
                  "command": "false",
              },
          },
      },
  )
  ingest_session_events(ctx, session_id=session_id, runtime="native")

  stats = ctx.db.get_turn(turn["id"])
  assert stats["tool_call_count"] == 2
  assert stats["tool_success_count"] == 1
  assert stats["tool_fail_count"] == 1
  assert stats["tool_duration_ms"] == 1500
  assert stats["file_lines_added"] == 5
  assert stats["file_lines_removed"] == 2

  # Re-ingesting the same events (after a cursor reset) must not double-count.
  ctx.db.reset_event_cursor(session_id=session_id, runtime="native")
  ingest_session_events(ctx, session_id=session_id, runtime="native")

  again = ctx.db.get_turn(turn["id"])
  assert again["tool_call_count"] == 2
  assert again["tool_fail_count"] == 1
  assert again["tool_duration_ms"] == 1500
  assert again["file_lines_added"] == 5
  assert again["file_lines_removed"] == 2


def test_activity_counts_reads_tool_responses():
  from src.api.steps_projection import _activity_counts

  projections = [
      {"kind": "agent_text", "payload": {"text": "hi"}},
      {
          "kind": "tool_response",
          "payload": {
              "name": "files_write",
              "response": {"success": True, "lines_added": 5, "lines_removed": 2},
          },
      },
      {
          "kind": "tool_response",
          "payload": {
              "name": "commands_run",
              "response": {"success": False, "returncode": 1, "duration_sec": 1.5},
          },
      },
  ]

  assert _activity_counts(projections) == {
      "tool_call_count": 2,
      "tool_success_count": 1,
      "tool_fail_count": 1,
      "tool_duration_ms": 1500,
      "file_lines_added": 5,
      "file_lines_removed": 2,
  }

  # No tool_response steps -> None, so ingestion skips the activity write.
  assert _activity_counts([{"kind": "agent_text", "payload": {"text": "hi"}}]) is None


def test_ingest_splits_multi_projection_event_into_separate_steps(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project, agent_id="orca")

  # One tool_result for artifacts_save_text projects to a tool_response *and* an
  # artifact_delta; both must materialize as their own top-level steps.
  RuntimeEventStore(storage_root).append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime="native",
      event_id="lg_save",
      event={
          "id": "lg_save",
          "kind": "orca.tool_result",
          "payload": {
              "name": "artifacts_save_text",
              "result": {"ok": True, "filename": "report.md", "version": 1},
          },
      },
  )
  ingest_session_events(ctx, session_id=session_id, runtime="native")

  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  assert [s["kind"] for s in steps] == ["tool_response", "artifact_delta"]
  assert [s["id"] for s in steps] == ["lg_save", "lg_save#1"]
  # The nested-projection payload is gone; the artifact_delta stands on its own.
  assert "projections" not in steps[0]["payload"]
  assert steps[1]["payload"]["filename"] == "report.md"

  # Re-ingesting (cursor rewind) keeps the same ids and adds no duplicates.
  ctx.db.reset_event_cursor(session_id=session_id, runtime="native")
  ingest_session_events(ctx, session_id=session_id, runtime="native")
  again = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  assert [s["id"] for s in again] == ["lg_save", "lg_save#1"]


def test_ingest_keeps_single_canonical_error_per_turn(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  store = RuntimeEventStore(storage_root)
  # A failed turn surfaces the error twice: the runtime's event (error_code) and
  # the worker's exception step (error_type). Only one canonical error survives.
  store.append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime=WEB_TRACE_RUNTIME,
      event_id="err_runtime",
      event={
          "id": "err_runtime",
          "kind": "web.error",
          "summary": "429 RESOURCE_EXHAUSTED.",
          "payload": {
              "error_type": None,
              "error_code": "_ResourceExhaustedError",
              "error_message": "429 RESOURCE_EXHAUSTED.",
          },
      },
  )
  store.append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime=WEB_TRACE_RUNTIME,
      event_id="err_worker",
      event={
          "id": "err_worker",
          "kind": "web.error",
          "summary": "boom",
          "payload": {
              "error_type": "_ResourceExhaustedError",
              "error_code": None,
              "error_message": "boom",
          },
      },
  )
  ingest_session_events(ctx, session_id=session_id, runtime=WEB_TRACE_RUNTIME)

  errors = [
      s for s in ctx.db.list_steps_for_turn(turn_id=turn["id"]) if s["kind"] == "error"
  ]
  assert len(errors) == 1
  assert errors[0]["id"] == "err_runtime"  # the first error wins
  # Both canonical fields are present regardless of which producer won.
  assert errors[0]["payload"]["error_code"] == "_ResourceExhaustedError"
  assert "error_type" in errors[0]["payload"]


def test_ingest_waits_for_incomplete_tail_line(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  events_file = runtime_events_path(storage_root, session_id, WEB_TRACE_RUNTIME)
  events_file.parent.mkdir(parents=True, exist_ok=True)
  complete = json.dumps(
      {
          "id": "evt_1",
          "turn_id": turn["id"],
          "event": {"kind": "web.note", "summary": "first", "payload": {}},
      }
  )
  partial = json.dumps(
      {
          "id": "evt_2",
          "turn_id": turn["id"],
          "event": {"kind": "web.note", "summary": "second", "payload": {}},
      }
  )
  events_file.write_text(complete + "\n" + partial[: len(partial) // 2])

  ingest_session_events(ctx, session_id=session_id, runtime=WEB_TRACE_RUNTIME)
  assert [step["summary"] for step in ctx.db.list_steps_for_turn(turn_id=turn["id"])] == ["first"]

  events_file.write_text(complete + "\n" + partial + "\n")
  ingest_session_events(ctx, session_id=session_id, runtime=WEB_TRACE_RUNTIME)
  assert [step["summary"] for step in ctx.db.list_steps_for_turn(turn_id=turn["id"])] == [
      "first",
      "second",
  ]


def test_record_runtime_partial_event_traces_to_web_stream(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  partial_event = SimpleNamespace(
      id="evt_partial_1",
      invocation_id="inv-1",
      author="handa",
      partial=True,
      content=SimpleNamespace(
          parts=[SimpleNamespace(text="strea", function_call=None, function_response=None)]
      ),
      actions=SimpleNamespace(artifact_delta={}),
      is_final_response=lambda: False,
  )
  record_runtime_event(
      ctx,
      session_id=session_id,
      turn_id=turn["id"],
      runtime=WEB_TRACE_RUNTIME,
      event=partial_event,
  )

  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  assert [step["kind"] for step in steps] == ["agent_text_delta"]
  events_file = runtime_events_path(storage_root, session_id, WEB_TRACE_RUNTIME)
  assert events_file.exists()
  native_file = runtime_events_path(storage_root, session_id, "native")
  assert not native_file.exists()


def test_ingest_session_service_persisted_native_event(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  # Mirror execute_turn: the active turn id keys the runner-persisted envelope.
  ctx.services.session_service.merge_state_sync(
      session_id,
      {"handa:active_turn_id": turn["id"]},
  )
  session = ctx.services.session_service._read_session(session_id)  # noqa: SLF001
  event = {
      "id": "evt_final",
      "invocation_id": "inv-1",
      "author": "handa",
      "content": {"role": "model", "parts": [{"text": "answer"}]},
      "usageMetadata": {
          "promptTokenCount": 100,
          "candidatesTokenCount": 10,
          "totalTokenCount": 110,
      },
      "is_final_response": True,
  }
  appended = asyncio.run(ctx.services.session_service.append_event(session, event))

  ingest_session_events(ctx, session_id=session_id, runtime="native")

  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])
  assert [step["kind"] for step in steps] == ["agent_text"]
  assert steps[0]["id"] == appended["id"]
  fetched = ctx.db.get_turn(turn["id"])
  assert fetched["input_token_count"] == 100
  assert fetched["output_token_count"] == 10
  # Native runner persistence should not create a duplicate web trace copy.
  web_file = runtime_events_path(storage_root, session_id, WEB_TRACE_RUNTIME)
  assert not web_file.exists()


def test_session_steps_endpoint_ingests_streams_lazily(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  # Append directly to the logs without ingesting, as an out-of-process
  # worker would; the read endpoint must materialize on demand.
  RuntimeEventStore(storage_root).append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime=WEB_TRACE_RUNTIME,
      event={"kind": "web.turn_cancelled", "summary": "Turn terminated", "payload": {}},
  )

  steps = client.get(f"/api/sessions/{session_id}/steps").json()
  assert [step["kind"] for step in steps] == ["turn_cancelled"]

  turn_steps = client.get(f"/api/turns/{turn['id']}/steps").json()
  assert [step["kind"] for step in turn_steps] == ["turn_cancelled"]


def test_forked_session_does_not_duplicate_cloned_steps(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)
  ctx.db.update_turn(turn["id"], status="completed", final_text="done")

  emit_web_step(
      ctx,
      session_id=session_id,
      turn_id=turn["id"],
      kind="note",
      summary="from source",
      payload={},
  )
  assert len(client.get(f"/api/sessions/{session_id}/steps").json()) == 1

  forked = client.post(f"/api/sessions/{session_id}/fork", json={})
  assert forked.status_code == 200, forked.json()
  fork_id = forked.json()["id"]

  fork_steps = client.get(f"/api/sessions/{fork_id}/steps").json()
  assert len(fork_steps) == 1
  assert fork_steps[0]["summary"] == "from source"


def test_ingest_skips_user_authored_web_trace_model_events(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  RuntimeEventStore(storage_root).append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime=WEB_TRACE_RUNTIME,
      event={
          "id": "evt_user_1",
          "author": "user",
          "content": {"parts": [{"text": "the turn input"}]},
      },
  )
  ingest_session_events(ctx, session_id=session_id, runtime=WEB_TRACE_RUNTIME)

  assert ctx.db.list_steps_for_turn(turn_id=turn["id"]) == []


def test_stream_merge_orders_deltas_before_final(tmp_path, monkeypatch):
  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)

  store = RuntimeEventStore(storage_root)
  # Worker wrote a streaming delta to the web stream (ISO created_at), then the
  # runner persisted the final to the native stream (float-epoch created_at).
  store.append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime=WEB_TRACE_RUNTIME,
      event_id="evt_delta",
      created_at="2026-06-12T06:00:01.000000+00:00",
      event={
          "id": "evt_delta",
          "author": "orca",
          "partial": True,
          "content": {"parts": [{"text": "Hel"}]},
      },
  )
  store.append(
      session_id=session_id,
      turn_id=turn["id"],
      runtime="native",
      event_id="evt_final",
      created_at=str(
          __import__("datetime").datetime(
              2026, 6, 12, 6, 0, 2, tzinfo=__import__("datetime").timezone.utc
          ).timestamp()
      ),
      event={
          "id": "evt_final",
          "author": "orca",
          "partial": False,
          "content": {"parts": [{"text": "Hello there."}]},
          "is_final_response": True,
      },
  )

  from src.api.steps_projection import ingest_session_streams

  ingest_session_streams(ctx, session_id=session_id, runtime="native")
  steps = ctx.db.list_steps_for_turn(turn_id=turn["id"])

  assert [step["kind"] for step in steps] == ["agent_text_delta", "agent_text"]


def test_terminal_sync_prunes_delta_steps(tmp_path, monkeypatch):
  from src.contract.task_store import create_web_turn_task
  from src.contract.task_store import load_task
  from src.contract.task_store import save_task
  from src.api.turn_run_sync import sync_turn_with_run_record

  ctx, client, project, storage_root = _make_ctx(tmp_path, monkeypatch)
  session_id, turn = _make_turn(ctx, client, project)
  ctx.db.update_turn(turn["id"], status="running")
  create_web_turn_task(
      session_id=session_id,
      turn_id=turn["id"],
      project_root=str(tmp_path / "project"),
      agent_id="orca",
      agent_runtime="native",
      input_text="hello",
      user_id="user",
  )

  for index in range(3):
    RuntimeEventStore(storage_root).append(
        session_id=session_id,
        turn_id=turn["id"],
        runtime=WEB_TRACE_RUNTIME,
        event_id=f"delta_{index}",
        event={
            "id": f"delta_{index}",
            "author": "orca",
            "partial": True,
            "content": {"parts": [{"text": f"chunk {index}"}]},
        },
    )
  ingest_session_events(ctx, session_id=session_id, runtime=WEB_TRACE_RUNTIME)
  assert [s["kind"] for s in ctx.db.list_steps_for_turn(turn_id=turn["id"])] == [
      "agent_text_delta",
      "agent_text_delta",
      "agent_text_delta",
  ]

  task = load_task(turn["id"], session_id=session_id)
  task["status"] = "succeeded"
  task["final_text"] = "full response"
  save_task(task)
  synced = sync_turn_with_run_record(ctx, ctx.db.get_turn(turn["id"]))

  assert synced["status"] == "completed"
  kinds = [s["kind"] for s in ctx.db.list_steps_for_turn(turn_id=turn["id"])]
  assert "agent_text_delta" not in kinds
