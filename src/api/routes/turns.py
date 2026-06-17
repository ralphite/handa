from __future__ import annotations

import json
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import Response

from ...contract.product import DEFAULT_WEB_AGENT_ID
from ...contract.product import get_agent_definition
from ...contract.product import validate_model_config_id
from ...contract.goals import GOAL_STATE_KEY
from ...contract.goals import goal_state_for_text
from ...contract.services import APP_NAME
from ...contract.task_store import cancel_descendant_runs
from ...contract.task_store import cancel_task
from ...contract.task_store import list_task_notifications
from ...contract.task_store import load_task
from ...contract.task_store import now_iso
from ...contract.storage import attachments_dir
from ...contract.user_input import cancelled_response
from ...contract.user_input import PENDING_USER_INPUT_STATE_KEY
from ...contract.user_input import validate_answers
from ..attachments import MAX_ATTACHMENT_BYTES
from ..attachments import MAX_ATTACHMENT_COUNT
from ..attachments import attachment_summary
from ..attachments import classify_kind
from ..context import get_context
from ..turn_queue import dispatch_next_queued_turn
from ..turn_run_sync import sync_turn_with_run_record
from ..turn_spawn import respawn_turn_worker_for_resume
from ..steps_projection import emit_web_step
from ..steps_projection import ingest_session_streams
from ..steps_projection import reset_session_event_cursors
from ..schemas import StepSummary
from ..schemas import TurnSummary
from ..schemas import UserInputSubmission
from ..session_bootstrap import generate_and_store_session_title as generate_and_store_session_title
from ..session_bootstrap import seed_session_title
from ..session_bootstrap import start_new_session_turn
from ..title_generation import fallback_title


router = APIRouter(prefix="/api/turns")


_TERMINAL_TURN_STATUSES = {"completed", "failed", "cancelled"}


def _session_runtime(ctx, session_id: str) -> str:
  meta = ctx.db.get_session_meta(session_id, include_deleted=True)
  return str((meta or {}).get("agent_runtime") or "native")


def _parse_iso(value: object) -> float | None:
  if not value:
    return None
  text = str(value).strip()
  if text.endswith("Z"):
    text = f"{text[:-1]}+00:00"
  try:
    return datetime.fromisoformat(text).timestamp()
  except ValueError:
    return None


def _active_seconds(turn: dict, waiting_seconds: float) -> float:
  """Agent working time: wall-clock from start to end, minus paused spans."""
  start = _parse_iso(turn.get("started_at") or turn.get("created_at"))
  if start is None:
    return 0.0
  status = str(turn.get("status") or "")
  if turn.get("finished_at"):
    end = _parse_iso(turn["finished_at"])
  elif status in _TERMINAL_TURN_STATUSES or status == "waiting_input":
    # Terminal-but-unfinished and paused turns freeze at their last update.
    end = _parse_iso(turn.get("updated_at"))
  else:
    # Still running (or queued) — count up to now.
    end = datetime.now(tz=timezone.utc).timestamp()
  if end is None:
    return 0.0
  return max(0.0, (end - start) - max(0.0, waiting_seconds))


def _system_run_label(turn: dict) -> str | None:
  if str(turn.get("trigger_kind") or "") != "task_notification":
    return None
  notification = _notification_for_turn(turn)
  payload = notification.get("payload") if notification else {}
  if not isinstance(payload, dict):
    payload = {}

  task_id = (
      str((notification or {}).get("task_id") or "").strip()
      or _notification_line_value(turn, "task_id")
  )
  task = _load_notification_task(str(turn.get("session_id") or ""), task_id)
  task_kind = (
      str((task or {}).get("kind") or "").strip()
      or str(payload.get("task_kind") or "").strip()
      or _notification_line_value(turn, "task_kind")
  )
  task_status = (
      str((task or {}).get("status") or "").strip()
      or str(payload.get("task_status") or "").strip()
      or _notification_line_value(turn, "status")
  )
  subject = _system_run_subject(task_kind, task)
  return f"{subject} {_system_run_status_label(task_status)}"


def _notification_for_turn(turn: dict) -> dict | None:
  session_id = str(turn.get("session_id") or "")
  turn_id = str(turn.get("id") or "")
  if not session_id or not turn_id:
    return None
  try:
    notifications = list_task_notifications(session_id=session_id)
  except (OSError, ValueError, KeyError):
    return None
  return next(
      (
          notification
          for notification in notifications
          if str(
              notification.get("delivered_turn_id")
              or notification.get("delivered_invocation_id")
              or ""
          ) == turn_id
      ),
      None,
  )


def _load_notification_task(session_id: str, task_id: str) -> dict | None:
  if not session_id or not task_id:
    return None
  try:
    return load_task(task_id, session_id=session_id)
  except (FileNotFoundError, OSError, KeyError, ValueError):
    return None


def _notification_line_value(turn: dict, key: str) -> str:
  prefix = f"{key}:"
  for line in str(turn.get("input_text") or "").splitlines():
    if line.startswith(prefix):
      return line[len(prefix):].strip()
  return ""


def _system_run_subject(task_kind: str, task: dict | None) -> str:
  normalized = task_kind.strip().lower()
  name = str(
      (task or {}).get("config_name") or (task or {}).get("agent_id") or ""
  ).strip()
  if normalized in {"agent_run", "run_agent", "system_agent_run"}:
    return f"Agent {name}" if name else "Agent run"
  if normalized == "command":
    return "Background command"
  return "Background run"


def _system_run_status_label(status: str) -> str:
  normalized = status.strip().lower()
  if normalized in {"succeeded", "completed", "done"}:
    return "completed"
  if normalized == "failed":
    return "failed"
  if normalized in {"cancelled", "canceled"}:
    return "cancelled"
  return "finished"


def _present_turn(
    turn: dict,
    attachments: list[dict] | None = None,
    waiting_seconds: float = 0.0,
) -> dict:
  turn = dict(turn)
  input_tokens = int(turn.get("input_token_count") or 0)
  output_tokens = int(turn.get("output_token_count") or 0)
  turn["total_token_count"] = (
      int(turn.get("total_token_count") or 0)
      if turn.get("total_token_count") is not None
      else input_tokens + output_tokens
  )
  if turn["total_token_count"] == 0:
    turn["total_token_count"] = input_tokens + output_tokens
  turn["active_seconds"] = _active_seconds(turn, waiting_seconds)
  turn["system_run_label"] = _system_run_label(turn)
  turn["attachments"] = [attachment_summary(row) for row in attachments or []]
  return turn


def _present_owned_turn(ctx, turn: dict) -> dict:
  turn = sync_turn_with_run_record(ctx, turn)
  tid = str(turn["id"])
  return _present_turn(
      turn,
      ctx.db.list_attachments_for_turn(tid),
      ctx.db.waiting_seconds_for_turns([tid]).get(tid, 0.0),
  )


@router.get("", response_model=list[TurnSummary])
def list_turns(
    request: Request,
    session_id: str | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
):
  ctx = get_context(request)
  turns = (
      ctx.db.list_turns_for_session(session_id)
      if session_id
      else ctx.db.list_turns(limit=limit)
  )
  turns = [sync_turn_with_run_record(ctx, turn) for turn in turns]
  turn_ids = [str(turn["id"]) for turn in turns]
  attachments = ctx.db.list_attachments_for_turns(turn_ids)
  waiting = ctx.db.waiting_seconds_for_turns(turn_ids)
  return [
      _present_turn(
          turn,
          attachments.get(str(turn["id"]), []),
          waiting.get(str(turn["id"]), 0.0),
      )
      for turn in turns
  ]


@router.post("", response_model=TurnSummary)
async def create_turn(
    request: Request,
    input_text: str = Form(default=""),
    project_id: str = Form(...),
    session_id: str | None = Form(default=None),
    agent_id: str = Form(default=DEFAULT_WEB_AGENT_ID),
    trigger_kind: str = Form(default="user_message"),
    model_config_id: str | None = Form(default=None),
    existing_attachment_ids: str = Form(default=""),
    goal: bool = Form(default=False),
    files: list[UploadFile] = File(default=[]),
) -> dict:
  ctx = get_context(request)
  input_text = input_text.strip()
  uploads = [f for f in files if f is not None and f.filename]
  attachment_ids = _parse_existing_attachment_ids(existing_attachment_ids)
  if not input_text and not uploads and not attachment_ids:
    raise HTTPException(status_code=422, detail="input_text or files required")
  if goal and not input_text:
    raise HTTPException(status_code=422, detail="Goal text must not be empty.")
  if len(uploads) + len(attachment_ids) > MAX_ATTACHMENT_COUNT:
    raise HTTPException(
        status_code=413,
        detail=f"Too many attachments (max {MAX_ATTACHMENT_COUNT})",
    )
  _validate_existing_attachments(ctx, attachment_ids)

  try:
    requested_definition = get_agent_definition(agent_id)
    definition = requested_definition
    agent_id = requested_definition.id
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  try:
    model_config_id = validate_model_config_id(
        model_config_id
        or ctx.db.get_web_settings(user_id=ctx.settings.user_id).get("model_config_id")
    )
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  project = ctx.db.get_project(project_id)
  if project is None:
    raise HTTPException(status_code=404, detail="Project not found")
  ctx.db.touch_project(project_id)

  seed_text = input_text or (uploads[0].filename if uploads else "Attachment")

  async def _persist(target_session_id: str, target_turn: dict) -> None:
    _persist_existing_attachments(ctx, target_turn, attachment_ids)
    await _persist_attachments(
        ctx,
        target_turn,
        target_session_id,
        uploads,
        starting_ordinal=len(attachment_ids),
    )

  if session_id:
    if ctx.db.is_session_deleted(session_id):
      raise HTTPException(status_code=404, detail="Session not found")
    meta = ctx.db.get_session_meta(session_id)
    if meta is None:
      raise HTTPException(status_code=404, detail="Session not found")
    if meta.get("project_id") != project_id:
      raise HTTPException(status_code=400, detail="Session belongs to another project")
    session = await ctx.services.session_service.get_session(
        app_name=APP_NAME,
        user_id=ctx.settings.user_id,
        session_id=session_id,
    )
    if session is None:
      raise HTTPException(status_code=404, detail="Session not found")
    agent_id = str(meta.get("agent_id") or agent_id)
    try:
      definition = get_agent_definition(agent_id)
      agent_id = definition.id
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.state = session.state or {}
    session.state["handa:model_config_id"] = model_config_id
    turn = ctx.db.create_turn(
        session_id=session_id,
        model_config_id=model_config_id,
        title=fallback_title(seed_text),
        input_text=input_text,
        trigger_kind=trigger_kind,
    )
    if goal:
      try:
        session.state[GOAL_STATE_KEY] = goal_state_for_text(
            input_text,
            previous=session.state.get(GOAL_STATE_KEY),
            created_turn_id=str(turn["id"]),
        )
      except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ctx.services.session_service._write_session(session)
    await _persist(session_id, turn)
    seed_session_title(ctx, session_id, project_id, agent_id, seed_text, input_text)
    dispatch_next_queued_turn(ctx, session_id)
  else:
    async def _persist_new_goal(target_session_id: str, target_turn: dict) -> None:
      await _persist(target_session_id, target_turn)
      if not goal:
        return
      session_state = ctx.services.session_service.read_state_sync(target_session_id)
      try:
        goal_state = goal_state_for_text(
            input_text,
            previous=session_state.get(GOAL_STATE_KEY),
            created_turn_id=str(target_turn["id"]),
        )
      except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
      ctx.services.session_service.merge_state_sync(
          target_session_id,
          {GOAL_STATE_KEY: goal_state},
      )

    _, turn = await start_new_session_turn(
        ctx,
        project_id=project_id,
        agent_id=agent_id,
        model_config_id=model_config_id,
        input_text=input_text,
        trigger_kind=trigger_kind,
        seed_text=seed_text,
        extra_session_state=None,
        on_turn_created=_persist_new_goal,
    )

  return _present_owned_turn(ctx, turn)


async def _persist_attachments(
    ctx,
    turn: dict,
    session_id: str,
    uploads: list[UploadFile],
    *,
    starting_ordinal: int = 0,
) -> None:
  if not uploads:
    return
  directory = attachments_dir(ctx.settings.storage_root, session_id)
  directory.mkdir(parents=True, exist_ok=True)
  for ordinal, upload in enumerate(uploads):
    data = await upload.read()
    if len(data) > MAX_ATTACHMENT_BYTES:
      raise HTTPException(
          status_code=413,
          detail=(
              f"Attachment '{upload.filename}' too large "
              f"({len(data)} bytes, max {MAX_ATTACHMENT_BYTES})"
          ),
      )
    mime_type = upload.content_type or "application/octet-stream"
    suffix = Path(upload.filename or "").suffix
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    payload_path = directory / stored_name
    payload_path.write_bytes(data)
    ctx.db.create_attachment(
        turn_id=turn["id"],
        ordinal=starting_ordinal + ordinal,
        filename=upload.filename or stored_name,
        mime_type=mime_type,
        kind=classify_kind(mime_type),
        byte_count=len(data),
        storage_path=str(payload_path),
    )


def _persist_existing_attachments(
    ctx,
    turn: dict,
    attachment_ids: list[str],
) -> None:
  if not attachment_ids:
    return
  try:
    ctx.db.clone_attachments_to_turn(
        attachment_ids=attachment_ids,
        target_turn_id=turn["id"],
    )
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


def _parse_existing_attachment_ids(raw: str) -> list[str]:
  if not raw.strip():
    return []
  try:
    loaded = json.loads(raw)
  except json.JSONDecodeError as exc:
    raise HTTPException(status_code=422, detail="Invalid existing_attachment_ids") from exc
  if not isinstance(loaded, list):
    raise HTTPException(status_code=422, detail="existing_attachment_ids must be a list")
  result: list[str] = []
  seen: set[str] = set()
  for item in loaded:
    attachment_id = str(item or "").strip()
    if not attachment_id or attachment_id in seen:
      continue
    seen.add(attachment_id)
    result.append(attachment_id)
  return result


def _validate_existing_attachments(ctx, attachment_ids: list[str]) -> None:
  _existing_attachment_rows(ctx, attachment_ids)


def _existing_attachment_rows(ctx, attachment_ids: list[str]) -> list[dict]:
  rows: list[dict] = []
  for attachment_id in attachment_ids:
    attachment = ctx.db.get_attachment(attachment_id)
    if attachment is None:
      raise HTTPException(status_code=404, detail=f"Attachment not found: {attachment_id}")
    rows.append(attachment)
  return rows


def _persist_existing_attachment_rows(
    ctx,
    turn: dict,
    attachments: list[dict],
) -> None:
  for ordinal, attachment in enumerate(attachments):
    ctx.db.create_attachment(
        turn_id=turn["id"],
        ordinal=ordinal,
        filename=str(attachment.get("filename") or "attachment"),
        mime_type=str(attachment.get("mime_type") or "application/octet-stream"),
        kind=str(attachment.get("kind") or classify_kind(str(attachment.get("mime_type") or ""))),
        byte_count=int(attachment.get("byte_count") or 0),
        storage_path=str(attachment.get("storage_path") or ""),
    )


@router.post("/rewrite", response_model=TurnSummary)
async def rewrite_session_from_turn(
    request: Request,
    session_id: str = Form(...),
    source_turn_id: str = Form(...),
    input_text: str = Form(default=""),
    model_config_id: str | None = Form(default=None),
    existing_attachment_ids: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
) -> dict:
  ctx = get_context(request)
  session_id = session_id.strip()
  source_turn_id = source_turn_id.strip()
  input_text = input_text.strip()
  uploads = [f for f in files if f is not None and f.filename]
  attachment_ids = _parse_existing_attachment_ids(existing_attachment_ids)
  if not input_text and not uploads and not attachment_ids:
    raise HTTPException(status_code=422, detail="input_text or files required")
  if len(uploads) + len(attachment_ids) > MAX_ATTACHMENT_COUNT:
    raise HTTPException(
        status_code=413,
        detail=f"Too many attachments (max {MAX_ATTACHMENT_COUNT})",
    )
  attachment_rows = _existing_attachment_rows(ctx, attachment_ids)

  meta = ctx.db.get_session_meta(session_id)
  if meta is None or ctx.db.is_session_deleted(session_id):
    raise HTTPException(status_code=404, detail="Session not found")
  project_id = str(meta.get("project_id") or "")
  if not project_id:
    raise HTTPException(status_code=400, detail="Session has no project")
  project = ctx.db.get_project(project_id)
  if project is None:
    raise HTTPException(status_code=404, detail="Project not found")
  source_turn = ctx.db.get_turn(source_turn_id)
  if source_turn is None or source_turn.get("session_id") != session_id:
    raise HTTPException(status_code=404, detail="Turn not found")
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if session is None:
    raise HTTPException(status_code=404, detail="Session not found")
  try:
    model_config_id = validate_model_config_id(
        model_config_id
        or ctx.db.get_web_settings(user_id=ctx.settings.user_id).get("model_config_id")
    )
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc

  return await _truncate_and_create_turn(
      ctx,
      session_id=session_id,
      source_turn_id=source_turn_id,
      input_text=input_text,
      model_config_id=model_config_id,
      attachment_rows=attachment_rows,
      uploads=uploads,
  )


async def _truncate_and_create_turn(
    ctx,
    *,
    session_id: str,
    source_turn_id: str,
    input_text: str,
    model_config_id: str,
    attachment_rows: list[dict],
    uploads: list[UploadFile],
) -> dict:
  """Truncate a session at source_turn_id, then create + dispatch a fresh turn.

  Shared by rewrite (user edits a past message) and retry (re-run a failed turn
  with the same input). truncate_session_before_turn removes the source turn and
  everything after it, so the new turn replaces the source in place. Callers must
  have validated the session/project/turn and resolved model_config_id and
  attachment_rows before calling.
  """
  try:
    rewrite = ctx.db.truncate_session_before_turn(
        session_id=session_id,
        source_turn_id=source_turn_id,
    )
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc
  except ValueError as exc:
    raise HTTPException(status_code=409, detail=str(exc)) from exc

  truncated_session = await ctx.services.session_service.truncate_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
      kept_turn_ids=rewrite["kept_turn_ids"],
      artifact_refs=rewrite["artifact_refs"],
  )
  if truncated_session is None:
    raise HTTPException(status_code=404, detail="Session not found")
  # The truncate rewrote the event logs; rewind ingest cursors so the byte
  # offsets never point into the middle of the rewritten files.
  reset_session_event_cursors(ctx, session_id)
  truncated_session.state = truncated_session.state or {}
  truncated_session.state["handa:model_config_id"] = model_config_id
  ctx.services.session_service._write_session(truncated_session)

  meta = ctx.db.get_session_meta(session_id)
  project_id = str((meta or {}).get("project_id") or "")
  agent_id = str((meta or {}).get("agent_id") or DEFAULT_WEB_AGENT_ID)
  definition = get_agent_definition(agent_id)
  if project_id:
    ctx.db.touch_project(project_id)
  seed_text = input_text or (uploads[0].filename if uploads else "Attachment")
  turn = ctx.db.create_turn(
      session_id=session_id,
      model_config_id=model_config_id,
      title=fallback_title(seed_text),
      input_text=input_text,
      trigger_kind="user_message",
  )
  _persist_existing_attachment_rows(ctx, turn, attachment_rows)
  await _persist_attachments(
      ctx,
      turn,
      session_id,
      uploads,
      starting_ordinal=len(attachment_rows),
  )
  seed_session_title(ctx, session_id, project_id, definition.id, seed_text, input_text)
  dispatch_next_queued_turn(ctx, session_id)
  return _present_owned_turn(ctx, turn)


@router.post("/{turn_id}/retry", response_model=TurnSummary)
async def retry_turn(turn_id: str, request: Request) -> dict:
  """Re-run a failed or cancelled turn with its original input.

  Regenerate-in-place: the failed turn (and anything after it) is removed and a
  fresh turn with the same input_text + attachments is queued, mirroring the
  rewrite flow but sourcing the prompt from the turn itself.
  """
  ctx = get_context(request)
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    raise HTTPException(status_code=404, detail="Turn not found")
  if turn["status"] not in {"failed", "cancelled"}:
    raise HTTPException(
        status_code=409,
        detail=f"Only a failed or cancelled turn can be retried (status: {turn['status']})",
    )
  session_id = str(turn["session_id"])
  if ctx.db.is_session_deleted(session_id):
    raise HTTPException(status_code=404, detail="Session not found")
  meta = ctx.db.get_session_meta(session_id)
  if meta is None:
    raise HTTPException(status_code=404, detail="Session not found")
  if not str(meta.get("project_id") or ""):
    raise HTTPException(status_code=400, detail="Session has no project")
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if session is None:
    raise HTTPException(status_code=404, detail="Session not found")
  input_text = str(turn.get("input_text") or "")
  attachment_rows = ctx.db.list_attachments_for_turn(turn_id)
  if not input_text and not attachment_rows:
    raise HTTPException(status_code=422, detail="Turn has no input to retry")
  try:
    model_config_id = validate_model_config_id(
        turn.get("model_config_id")
        or ctx.db.get_web_settings(user_id=ctx.settings.user_id).get("model_config_id")
    )
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return await _truncate_and_create_turn(
      ctx,
      session_id=session_id,
      source_turn_id=turn_id,
      input_text=input_text,
      model_config_id=model_config_id,
      attachment_rows=attachment_rows,
      uploads=[],
  )


@router.get("/{turn_id}", response_model=TurnSummary)
def get_turn(turn_id: str, request: Request) -> dict:
  ctx = get_context(request)
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    raise HTTPException(status_code=404, detail="Turn not found")
  turn = sync_turn_with_run_record(ctx, turn)
  return _present_turn(
      turn,
      ctx.db.list_attachments_for_turn(turn_id),
  )


@router.get("/{turn_id}/attachments/{attachment_id}")
def get_attachment(turn_id: str, attachment_id: str, request: Request):
  ctx = get_context(request)
  attachment = ctx.db.get_attachment(attachment_id)
  if attachment is None or attachment["turn_id"] != turn_id:
    raise HTTPException(status_code=404, detail="Attachment not found")
  path = Path(attachment["storage_path"])
  if not path.is_file():
    raise HTTPException(status_code=404, detail="Attachment file missing")
  return Response(
      content=path.read_bytes(),
      media_type=attachment["mime_type"] or "application/octet-stream",
      headers={
          "Content-Disposition": f'inline; filename="{attachment["filename"]}"',
      },
  )


@router.post("/{turn_id}/user-input", response_model=TurnSummary)
async def submit_turn_user_input(
    turn_id: str,
    body: UserInputSubmission,
    request: Request,
) -> dict:
  ctx = get_context(request)
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    raise HTTPException(status_code=404, detail="Turn not found")
  if turn["status"] != "waiting_input":
    raise HTTPException(
        status_code=409,
        detail=f"Turn is not waiting for user input (status: {turn['status']})",
    )
  session_id = str(turn["session_id"])
  pending = ctx.services.session_service.read_state_sync(session_id).get(
      PENDING_USER_INPUT_STATE_KEY
  )
  if not isinstance(pending, dict) or pending.get("request_id") != body.request_id:
    raise HTTPException(
        status_code=409,
        detail="request_id does not match the pending user input request",
    )
  if body.cancelled:
    response = cancelled_response()
  else:
    try:
      response = validate_answers(
          list(pending.get("questions") or []),
          {"answers": body.answers or []},
      )
    except ValueError as exc:
      raise HTTPException(status_code=422, detail=str(exc)) from exc
  ctx.services.session_service.merge_state_sync(
      session_id,
      {PENDING_USER_INPUT_STATE_KEY: None},
  )
  emit_web_step(
      ctx,
      session_id=session_id,
      turn_id=turn_id,
      kind="user_input_submitted",
      summary="User cancelled the form" if body.cancelled else "User input submitted",
      payload={"request_id": body.request_id, "response": response},
  )
  resume_payload = {
      "request_id": body.request_id,
      "function_call_id": pending.get("function_call_id"),
      "response": response,
  }
  try:
    respawn_turn_worker_for_resume(
        ctx,
        turn_id,
        resume_user_input=resume_payload,
    )
  except (FileNotFoundError, KeyError) as exc:
    raise HTTPException(status_code=409, detail="Turn run record is missing") from exc
  except ValueError as exc:
    raise HTTPException(status_code=409, detail=str(exc)) from exc
  ctx.db.update_turn(turn_id, status="running")
  return _present_owned_turn(ctx, ctx.db.get_turn(turn_id))


@router.post("/{turn_id}/terminate", response_model=TurnSummary)
def terminate_turn(turn_id: str, request: Request) -> dict:
  ctx = get_context(request)
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    raise HTTPException(status_code=404, detail="Turn not found")
  if turn["status"] == "waiting_input":
    raise HTTPException(
        status_code=409,
        detail=(
            "Turn is waiting for user input; submit the form or cancel it via "
            "the user-input endpoint instead of terminating"
        ),
    )
  if turn["status"] not in {"queued", "running"}:
    raise HTTPException(
        status_code=409,
        detail=f"Turn is already {turn['status']}",
    )

  requested_at = now_iso()
  ctx.db.update_turn(
      turn_id,
      cancel_requested_at=requested_at,
  )
  session_id = str(turn["session_id"])
  try:
    cancel_task(turn_id, session_id=session_id)
  except (FileNotFoundError, KeyError, ValueError):
    # No run record (the turn never spawned, or predates worker turns); the
    # Web-side cancelled state below is all there is to write.
    pass
  # The turn's in-flight child agent runs (run_agent / start_run) are detached
  # process groups under this same session; cancel them too so they don't run on
  # as orphans after the parent turn is gone.
  cancel_descendant_runs(session_id)
  emit_web_step(
      ctx,
      session_id=session_id,
      turn_id=turn_id,
      kind="turn_cancelled",
      summary="Turn terminated",
      payload={"reason": "User terminated the turn."},
  )
  return ctx.db.update_turn(
      turn_id,
      status="cancelled",
      finished_at=requested_at,
      error_type="Cancelled",
      error_message="User terminated the turn.",
  )


@router.get("/{turn_id}/steps", response_model=list[StepSummary])
def list_steps_for_turn(
    turn_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
) -> list[dict]:
  ctx = get_context(request)
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    raise HTTPException(status_code=404, detail="Turn not found")
  session_id = str(turn["session_id"])
  ingest_session_streams(
      ctx,
      session_id=session_id,
      runtime=_session_runtime(ctx, session_id),
  )
  return ctx.db.list_steps_for_turn(
      turn_id=turn_id,
      after_seq=after_seq,
  )
