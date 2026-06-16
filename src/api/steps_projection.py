from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from ..contract.run_events import extract_event_facts
from ..contract.storage import runtime_events_path
from ..contract.storage import session_dir
from ..contract.turn_trace import append_runtime_trace_event
from ..contract.turn_trace import append_web_step_event
from ..contract.turn_trace import WEB_EVENT_KIND_PREFIX
from ..contract.turn_trace import WEB_TRACE_RUNTIME
from .context import WebApiContext
from .presenters.runtime_event_presenter import project_runtime_event
from .presenters.tool_summary import response_indicates_failed_outcome


def record_runtime_event(
    ctx: WebApiContext,
    *,
    session_id: str,
    turn_id: str,
    runtime: str,
    event: Any,
) -> None:
  append_runtime_trace_event(
      ctx.settings.storage_root,
      session_id=session_id,
      turn_id=turn_id,
      runtime=runtime,
      event=event,
  )
  ingest_session_events(ctx, session_id=session_id, runtime=runtime)


def emit_web_step(
    ctx: WebApiContext,
    *,
    session_id: str,
    turn_id: str,
    kind: str,
    summary: str,
    payload: dict[str, Any],
) -> None:
  """Record a Web-originated lifecycle step as a trace event.

  The event log stays the single source for step materialization, so steps the
  Web layer used to write straight into sqlite (user input prompts, terminate,
  errors) go through the same append+ingest path as runtime events.
  """
  append_web_step_event(
      ctx.settings.storage_root,
      session_id=session_id,
      turn_id=turn_id,
      kind=kind,
      summary=summary,
      payload=payload,
  )
  ingest_session_events(ctx, session_id=session_id, runtime=WEB_TRACE_RUNTIME)


def ingest_session_streams(
    ctx: WebApiContext,
    *,
    session_id: str,
    runtime: str,
) -> int:
  """Ingest the session's runtime stream plus the web trace stream.

  New envelopes from both streams are merged by event time before
  materializing, so consuming runtime and web streams one after the other does
  not reorder closely spaced lifecycle and agent events.
  """
  streams = [runtime]
  if runtime != WEB_TRACE_RUNTIME:
    streams.append(WEB_TRACE_RUNTIME)

  pending: list[tuple[dict[str, Any], str]] = []
  next_offsets: dict[str, int] = {}
  for stream in streams:
    envelopes, next_offset = _collect_new_envelopes(ctx, session_id=session_id, runtime=stream)
    if next_offset is not None:
      next_offsets[stream] = next_offset
    pending.extend((envelope, stream) for envelope in envelopes)

  pending.sort(key=lambda item: _event_sort_key(item[0]))
  ingested = 0
  for envelope, stream in pending:
    if _ingest_envelope(ctx, envelope, runtime=stream):
      ingested += 1
  for stream, offset in next_offsets.items():
    ctx.db.advance_event_cursor(
        session_id=session_id,
        runtime=stream,
        byte_offset=offset,
    )
  return ingested


def mark_session_events_ingested(ctx: WebApiContext, session_id: str) -> None:
  """Fast-forward cursors past the current event logs without projecting.

  Used after a session fork: cloned steps already cover the copied events but
  carry fresh step ids, so re-projecting the copied logs would duplicate them.
  """
  runtime_root = session_dir(ctx.settings.storage_root, session_id) / "runtime"
  if not runtime_root.is_dir():
    return
  for events_path in runtime_root.glob("*/events.jsonl"):
    runtime = events_path.parent.name
    ctx.db.advance_event_cursor(
        session_id=session_id,
        runtime=runtime,
        byte_offset=events_path.stat().st_size,
    )


def reset_session_event_cursors(ctx: WebApiContext, session_id: str) -> None:
  """Rewind cursors after the event logs were rewritten (session truncate)."""
  runtime_root = session_dir(ctx.settings.storage_root, session_id) / "runtime"
  if not runtime_root.is_dir():
    return
  for events_path in runtime_root.glob("*/events.jsonl"):
    ctx.db.reset_event_cursor(
        session_id=session_id,
        runtime=events_path.parent.name,
    )


def ingest_session_events(
    ctx: WebApiContext,
    *,
    session_id: str,
    runtime: str,
) -> int:
  """Project new runtime events into web_steps and turn token usage.

  Consumes the session's event JSONL from the persisted byte cursor. Steps are
  idempotent by event id, and token usage is only accumulated when a step is
  newly inserted, so overlapping ingests (poll + write path) are safe.
  """
  envelopes, next_offset = _collect_new_envelopes(
      ctx,
      session_id=session_id,
      runtime=runtime,
  )
  ingested = 0
  for envelope in envelopes:
    if _ingest_envelope(ctx, envelope, runtime=runtime):
      ingested += 1
  if next_offset is not None:
    ctx.db.advance_event_cursor(
        session_id=session_id,
        runtime=runtime,
        byte_offset=next_offset,
    )
  return ingested


def _collect_new_envelopes(
    ctx: WebApiContext,
    *,
    session_id: str,
    runtime: str,
) -> tuple[list[dict[str, Any]], int | None]:
  """Read complete envelopes past the cursor; returns them with the new offset."""
  path = runtime_events_path(ctx.settings.storage_root, session_id, runtime)
  if not path.exists():
    return [], None
  offset = ctx.db.get_event_cursor(session_id=session_id, runtime=runtime)
  size = path.stat().st_size
  if size < offset:
    # The log shrank: session truncate/fork rewrote it. Re-scan from the top;
    # step-id dedupe drops everything already materialized.
    ctx.db.reset_event_cursor(session_id=session_id, runtime=runtime)
    offset = 0
  if size <= offset:
    return [], None

  envelopes: list[dict[str, Any]] = []
  next_offset = offset
  with path.open("rb") as handle:
    handle.seek(offset)
    while True:
      raw_line = handle.readline()
      if not raw_line:
        break
      if not raw_line.endswith(b"\n"):
        # Incomplete tail write; leave the cursor before it for the next pass.
        break
      next_offset = handle.tell()
      line = raw_line.strip()
      if not line:
        continue
      try:
        envelope = json.loads(line.decode("utf-8"))
      except (json.JSONDecodeError, UnicodeDecodeError):
        continue
      if isinstance(envelope, dict):
        envelopes.append(envelope)
  if next_offset <= offset:
    return [], None
  return envelopes, next_offset


def _event_sort_key(envelope: dict[str, Any]) -> float:
  """Epoch seconds for cross-stream merge ordering.

  Runtime streams may stamp envelopes with either float timestamps or ISO
  strings; normalize both so merged ingestion follows event time. Unparseable
  values sort first, preserving stream order.
  """
  raw = envelope.get("created_at")
  if raw is None:
    return 0.0
  text = str(raw).strip()
  try:
    return float(text)
  except ValueError:
    pass
  if text.endswith("Z"):
    text = f"{text[:-1]}+00:00"
  try:
    return datetime.fromisoformat(text).timestamp()
  except ValueError:
    return 0.0


def _ingest_envelope(
    ctx: WebApiContext,
    envelope: dict[str, Any],
    *,
    runtime: str,
) -> bool:
  turn_id = _optional_str(envelope.get("turn_id"))
  if not turn_id:
    # Session-level traces (child agent runs) have no web turn to attach to.
    return False
  raw_event = envelope.get("event")
  if not isinstance(raw_event, dict):
    return False
  if ctx.db.get_turn(turn_id) is None:
    return False
  step_id = _optional_str(envelope.get("id") or raw_event.get("id"))
  if not step_id:
    return False
  projections = _project(raw_event, runtime=runtime)
  if not projections:
    return False
  created_at = _optional_str(envelope.get("created_at"))

  # Each projection from one runtime event becomes its own top-level step, so a
  # tool call/response pair (or a tool_response + artifact_delta carried in one
  # event) never hides inside a sibling's payload. The base event id keys the
  # first step; extras take a deterministic `#index` suffix so a cursor rewind
  # re-ingests to the same ids (and dedupes by id).
  base_inserted = False
  any_inserted = False
  for index, projection in enumerate(projections):
    kind = str(projection["kind"])
    # A failed turn surfaces its error twice (the runtime's error event and the
    # worker's exception step, with different fields); keep one canonical error
    # per turn so failure counts don't double.
    if kind == "error" and ctx.db.turn_has_step_kind(turn_id, "error"):
      continue
    inserted = ctx.db.ingest_step(
        turn_id=turn_id,
        id=step_id if index == 0 else f"{step_id}#{index}",
        kind=kind,
        summary=str(projection["summary"]),
        payload=projection.get("payload") or {},
        created_at=created_at,
    )
    if inserted is not None:
      any_inserted = True
      if index == 0:
        base_inserted = True

  # Token usage and activity stats are per-event, so tie them to the base step's
  # first insertion: a cursor rewind (or partial re-ingest of the extra steps)
  # then never double-counts them.
  if base_inserted:
    usage = _usage_counts(raw_event, runtime=runtime)
    if usage is not None:
      ctx.db.add_turn_token_usage(
          turn_id,
          input_token_count=usage["input_token_count"],
          output_token_count=usage["output_token_count"],
          total_token_count=usage["total_token_count"],
      )
    activity = _activity_counts(projections)
    if activity is not None:
      ctx.db.add_turn_activity_stats(turn_id, **activity)
  return any_inserted


def _project(raw_event: dict[str, Any], *, runtime: str) -> list[dict[str, Any]]:
  kind = str(raw_event.get("kind") or "")
  if kind.startswith(WEB_EVENT_KIND_PREFIX):
    payload = raw_event.get("payload")
    return [
        {
            "kind": kind[len(WEB_EVENT_KIND_PREFIX):],
            "summary": str(raw_event.get("summary") or ""),
            "payload": payload if isinstance(payload, dict) else {},
        }
    ]
  if runtime == WEB_TRACE_RUNTIME and not kind:
    # User-authored legacy model events are conversation history with no step
    # counterpart.
    if str(raw_event.get("author") or "").strip().lower() == "user":
      return []
  return project_runtime_event(raw_event, runtime=runtime)


def _usage_counts(
    raw_event: dict[str, Any],
    *,
    runtime: str,
) -> dict[str, int] | None:
  if str(raw_event.get("kind") or "").startswith(WEB_EVENT_KIND_PREFIX):
    return None
  facts = extract_event_facts(raw_event)
  if (
      facts.input_token_count > 0
      or facts.output_token_count > 0
      or facts.total_token_count > 0
  ):
    return {
        "input_token_count": facts.input_token_count,
        "output_token_count": facts.output_token_count,
        "total_token_count": facts.total_token_count,
    }
  return _runtime_usage_counts(raw_event)


def _activity_counts(
    projections: list[dict[str, Any]],
) -> dict[str, int] | None:
  """Tool-call and file-change counters from one event's projected steps.

  Runtime projection normalizes tool activity into `tool_response` steps
  carrying the tool name and response, so counting from projections works
  uniformly. A completed call counts as success unless its response signals a failed
  outcome; a command's duration and a file edit's line deltas ride on the
  response. Returns None when the event contributed no tool activity.
  """
  calls = success = fail = duration_ms = lines_added = lines_removed = 0
  for projection in projections:
    if projection.get("kind") != "tool_response":
      continue
    payload = projection.get("payload")
    response = payload.get("response") if isinstance(payload, dict) else None
    calls += 1
    if response_indicates_failed_outcome(response):
      fail += 1
    else:
      success += 1
    if isinstance(response, dict):
      duration_ms += _duration_ms(response.get("duration_sec"))
      lines_added += _non_negative_int(response.get("lines_added"))
      lines_removed += _non_negative_int(response.get("lines_removed"))
  if calls == 0:
    return None
  return {
      "tool_call_count": calls,
      "tool_success_count": success,
      "tool_fail_count": fail,
      "tool_duration_ms": duration_ms,
      "file_lines_added": lines_added,
      "file_lines_removed": lines_removed,
  }


def _duration_ms(value: Any) -> int:
  try:
    seconds = float(value)
  except (TypeError, ValueError):
    return 0
  return max(0, round(seconds * 1000))


def _non_negative_int(value: Any) -> int:
  try:
    return max(0, int(value))
  except (TypeError, ValueError):
    return 0


def _runtime_usage_counts(raw_event: dict[str, Any]) -> dict[str, int] | None:
  payload = raw_event.get("payload")
  metadata = None
  if isinstance(payload, dict):
    metadata = payload.get("usage_metadata") or payload.get("usageMetadata")
  if not isinstance(metadata, dict):
    metadata = raw_event.get("usage_metadata") or raw_event.get("usageMetadata")
  if not isinstance(metadata, dict):
    return None
  input_count = _int_field(metadata, "prompt_token_count", "promptTokenCount")
  output_count = _int_field(
      metadata,
      "candidates_token_count",
      "candidatesTokenCount",
  )
  thoughts_count = _int_field(metadata, "thoughts_token_count", "thoughtsTokenCount")
  tool_prompt_count = _int_field(
      metadata,
      "tool_use_prompt_token_count",
      "toolUsePromptTokenCount",
  )
  total_count = _int_field(metadata, "total_token_count", "totalTokenCount")
  total_count = total_count or input_count + output_count + thoughts_count + tool_prompt_count
  if input_count == 0 and output_count == 0 and total_count == 0:
    return None
  return {
      "input_token_count": input_count,
      "output_token_count": output_count,
      "total_token_count": total_count,
  }


def _int_field(value: dict[str, Any], snake_name: str, camel_name: str) -> int:
  raw = value.get(snake_name)
  if raw is None:
    raw = value.get(camel_name)
  try:
    return max(0, int(raw or 0))
  except (TypeError, ValueError):
    return 0


def _optional_str(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None
