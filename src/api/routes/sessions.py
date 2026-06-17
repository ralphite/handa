from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request

from ...contract.product import DEFAULT_WEB_AGENT_ID
from ...contract.product import get_agent_definition
from ...contract.services import APP_NAME
from ...contract.goals import GOAL_STATE_KEY
from ...contract.goals import active_goal_from_state
from ...contract.goals import cleared_goal_state
from ...contract.goals import goal_state_for_text
from ...contract.task_store import cancel_descendant_runs
from ...contract.task_store import cancel_task
from ...contract.storage import create_session_id
from ..context import WebApiContext
from ..context import get_context
from ..presenters.session_presenter import present_session
from ..schemas import BackgroundRunSummary
from ..schemas import StepSummary
from ..schemas import SessionArchiveUpdateRequest
from ..schemas import SessionCreateRequest
from ..schemas import SessionDeleteSummary
from ..schemas import SessionForkRequest
from ..schemas import SessionDetail
from ..schemas import SessionGoal
from ..schemas import SessionGoalUpdateRequest
from ..schemas import SessionRenameRequest
from ..schemas import SessionStarSummary
from ..schemas import SessionStarUpdateRequest
from ..schemas import SessionSummary
from ..schemas import SessionUnreadUpdateRequest
from ..session_detail import _created_at_from_session_id
from ..session_detail import _turn_status
from ..session_detail import _updated_at
from ..session_detail import build_session_detail
from ..steps_projection import ingest_session_streams
from ..steps_projection import mark_session_events_ingested


router = APIRouter(prefix="/api/sessions")


def _status_for_session(ctx: WebApiContext, session_id: str) -> str:
  running = ctx.db.get_active_turn_for_session(session_id)
  if running is not None:
    return _turn_status(running.get("status"))
  turn = ctx.db.get_latest_turn_for_session(session_id)
  if turn is None:
    return "idle"
  return _turn_status(turn.get("status"))


def _automated_task_id_from_state(state: dict[str, Any] | None) -> str | None:
  value = (state or {}).get("handa:automated_task_id")
  if value is None:
    return None
  text = str(value).strip()
  return text or None


async def _session_updated_at(ctx: WebApiContext, session_id: str) -> str:
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  return _updated_at(session) if session is not None else _created_at_from_session_id(session_id)


async def _session_automated_task_id(ctx: WebApiContext, session_id: str) -> str | None:
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  return _automated_task_id_from_state(session.state) if session is not None else None


def _present_goal(state: dict[str, Any] | None) -> dict[str, Any]:
  goal = active_goal_from_state(state)
  if goal is not None:
    return goal
  raw = (state or {}).get(GOAL_STATE_KEY)
  if isinstance(raw, dict):
    status = str(raw.get("status") or "cleared")
    return {
        "goal_id": raw.get("goal_id"),
        "text": "" if status == "cleared" else str(raw.get("text") or ""),
        "status": status,
        "created_turn_id": raw.get("created_turn_id"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "max_attempts": raw.get("max_attempts"),
        "reason": raw.get("reason"),
    }
  return {
      "goal_id": None,
      "text": "",
      "status": "cleared",
      "created_turn_id": None,
      "created_at": None,
      "updated_at": None,
      "max_attempts": None,
      "reason": None,
  }


async def _load_session_for_goal(ctx: WebApiContext, session_id: str):
  if ctx.db.get_session_meta(session_id, include_deleted=True) is None:
    raise HTTPException(status_code=404, detail="Session not found")
  if ctx.db.is_session_deleted(session_id):
    raise HTTPException(status_code=404, detail="Session not found")
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if session is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return session


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    project_id: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    archived: bool | None = Query(default=None),
) -> list[SessionSummary]:
  ctx = get_context(request)
  metas = ctx.db.list_session_metas(
      project_id=project_id,
      include_archived=include_archived,
      archived=archived,
  )
  stored_sessions = await ctx.services.session_service.list_sessions(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
  )
  updated_by_id: dict[str, str] = {
      session.id: _updated_at(session) for session in stored_sessions.sessions
  }
  automated_task_id_by_id: dict[str, str] = {
      session.id: automated_task_id
      for session in stored_sessions.sessions
      if (automated_task_id := _automated_task_id_from_state(session.state))
  }
  existing_session_ids = set(updated_by_id)
  return [
      present_session(
          meta,
          status=_status_for_session(ctx, str(meta["id"])),
          updated_at=updated_by_id.get(str(meta["id"]), str(meta["created_at"])),
          automated_task_id=automated_task_id_by_id.get(str(meta["id"])),
      )
      for meta in metas
      if str(meta["id"]) in existing_session_ids
  ]


@router.post("", response_model=SessionSummary)
async def create_session(
    payload: SessionCreateRequest,
    request: Request,
) -> SessionSummary:
  ctx = get_context(request)
  try:
    definition = get_agent_definition(payload.agent_id)
    agent_id = definition.id
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  project = ctx.db.get_project(payload.project_id)
  if project is None:
    raise HTTPException(status_code=404, detail="Project not found")
  ctx.db.touch_project(payload.project_id)
  stored_session = await ctx.services.session_service.create_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      state={
          "handa:agent_id": agent_id,
          "handa:project_id": payload.project_id,
          "handa:project_root": project["root_path"],
      },
  )
  meta = ctx.db.create_session(
      session_id=stored_session.id,
      project_id=payload.project_id,
      agent_id=agent_id,
      agent_runtime=definition.runtime,
  )
  return present_session(meta, status="idle", updated_at=_updated_at(stored_session))


@router.get("/{session_id}/goal", response_model=SessionGoal)
async def get_session_goal(session_id: str, request: Request) -> dict[str, Any]:
  ctx = get_context(request)
  session = await _load_session_for_goal(ctx, session_id)
  return _present_goal(session.state)


@router.put("/{session_id}/goal", response_model=SessionGoal)
async def update_session_goal(
    session_id: str,
    payload: SessionGoalUpdateRequest,
    request: Request,
) -> dict[str, Any]:
  ctx = get_context(request)
  session = await _load_session_for_goal(ctx, session_id)
  state = session.state or {}
  try:
    goal = goal_state_for_text(payload.text, previous=state.get(GOAL_STATE_KEY))
  except ValueError as exc:
    raise HTTPException(status_code=422, detail=str(exc)) from exc
  updated = ctx.services.session_service.merge_state_sync(
      session_id,
      {GOAL_STATE_KEY: goal},
  )
  return _present_goal(updated)


@router.delete("/{session_id}/goal", response_model=SessionGoal)
async def clear_session_goal(session_id: str, request: Request) -> dict[str, Any]:
  ctx = get_context(request)
  session = await _load_session_for_goal(ctx, session_id)
  state = session.state or {}
  cleared = cleared_goal_state(previous=state.get(GOAL_STATE_KEY))
  updated = ctx.services.session_service.merge_state_sync(
      session_id,
      {GOAL_STATE_KEY: cleared},
  )
  return _present_goal(updated)


@router.patch("/{session_id}", response_model=SessionSummary)
async def rename_session(
    session_id: str,
    payload: SessionRenameRequest,
    request: Request,
) -> SessionSummary:
  ctx = get_context(request)
  if ctx.db.get_session_meta(session_id) is None:
    raise HTTPException(status_code=404, detail="Session not found")
  meta = ctx.db.update_session_title(session_id, payload.title.strip(), source="manual")
  if meta is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return present_session(
      meta,
      status=_status_for_session(ctx, session_id),
      updated_at=await _session_updated_at(ctx, session_id),
      automated_task_id=await _session_automated_task_id(ctx, session_id),
  )


@router.post("/{session_id}/fork", response_model=SessionSummary)
async def fork_session(
    session_id: str,
    payload: SessionForkRequest,
    request: Request,
) -> SessionSummary:
  ctx = get_context(request)
  source_meta = ctx.db.get_session_meta(session_id)
  if source_meta is None:
    raise HTTPException(status_code=404, detail="Session not found")
  source_session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if source_session is None:
    raise HTTPException(status_code=404, detail="Session not found")

  source_turn_id = (payload.source_turn_id or "").strip() or None
  target_session_id = create_session_id()
  try:
    fork = ctx.db.fork_session_history(
        source_session_id=session_id,
        target_session_id=target_session_id,
        source_turn_id=source_turn_id,
        include_source_turn=payload.include_source_turn,
    )
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc
  except ValueError as exc:
    raise HTTPException(status_code=409, detail=str(exc)) from exc

  project_id = source_meta.get("project_id")
  project = ctx.db.get_project(str(project_id)) if project_id else None
  fork_meta = fork["meta"]
  source_agent_id = str(source_meta.get("agent_id") or DEFAULT_WEB_AGENT_ID)
  source_agent_runtime = str(
      source_meta.get("agent_runtime") or get_agent_definition(source_agent_id).runtime
  )
  state_updates = {
      "handa:forked_from_session_id": session_id,
      "handa:forked_at": str(fork_meta.get("forked_at") or ""),
      "handa:agent_id": source_agent_id,
      "handa:agent_runtime": source_agent_runtime,
  }
  if source_turn_id:
    state_updates["handa:forked_from_turn_id"] = source_turn_id
  if project_id:
    state_updates["handa:project_id"] = str(project_id)
  if project:
    state_updates["handa:project_root"] = str(project["root_path"])
    ctx.db.touch_project(str(project_id))

  fork_session = await ctx.services.session_service.fork_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      source_session_id=session_id,
      target_session_id=target_session_id,
      state_updates=state_updates,
      source_turn_ids=fork["source_turn_ids"],
      turn_id_map=fork["turn_id_map"],
      artifact_refs=fork.get("artifact_refs"),
  )
  if fork_session is None:
    raise HTTPException(status_code=404, detail="Session not found")

  # The forked web_steps were cloned with fresh ids, so the copied event logs
  # must not be re-projected; start the ingest cursors at their current end.
  mark_session_events_ingested(ctx, target_session_id)

  return present_session(
      fork_meta,
      status=_status_for_session(ctx, target_session_id),
      updated_at=_updated_at(fork_session),
  )


@router.put("/{session_id}/star", response_model=SessionStarSummary)
def update_session_star(
    session_id: str,
    payload: SessionStarUpdateRequest,
    request: Request,
) -> dict[str, Any]:
  ctx = get_context(request)
  if ctx.db.get_session_meta(session_id) is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return ctx.db.set_session_star(session_id=session_id, starred=payload.starred)


@router.put("/{session_id}/archive", response_model=SessionSummary)
async def update_session_archive(
    session_id: str,
    payload: SessionArchiveUpdateRequest,
    request: Request,
) -> SessionSummary:
  ctx = get_context(request)
  if ctx.db.get_session_meta(session_id) is None:
    raise HTTPException(status_code=404, detail="Session not found")
  meta = ctx.db.set_session_archive(session_id=session_id, archived=payload.archived)
  if meta is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return present_session(
      meta,
      status=_status_for_session(ctx, session_id),
      updated_at=await _session_updated_at(ctx, session_id),
      automated_task_id=await _session_automated_task_id(ctx, session_id),
  )


@router.put("/{session_id}/unread", response_model=SessionSummary)
async def update_session_unread(
    session_id: str,
    payload: SessionUnreadUpdateRequest,
    request: Request,
) -> SessionSummary:
  ctx = get_context(request)
  if ctx.db.get_session_meta(session_id) is None:
    raise HTTPException(status_code=404, detail="Session not found")
  meta = ctx.db.set_session_unread(session_id=session_id, unread=payload.unread)
  if meta is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return present_session(
      meta,
      status=_status_for_session(ctx, session_id),
      updated_at=await _session_updated_at(ctx, session_id),
      automated_task_id=await _session_automated_task_id(ctx, session_id),
  )


@router.delete("/{session_id}", response_model=SessionDeleteSummary)
def delete_session(session_id: str, request: Request) -> dict[str, Any]:
  ctx = get_context(request)
  if ctx.db.get_session_meta(session_id) is None:
    raise HTTPException(status_code=404, detail="Session not found")
  meta = ctx.db.soft_delete_session(session_id)
  if meta is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return {
      "session_id": session_id,
      "deleted": True,
      "deleted_at": meta.get("deleted_at"),
  }


@router.get("/{session_id}/steps", response_model=list[StepSummary])
async def list_session_steps(
    session_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
) -> list[dict]:
  ctx = get_context(request)
  if ctx.db.is_session_deleted(session_id):
    raise HTTPException(status_code=404, detail="Session not found")
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if session is None:
    raise HTTPException(status_code=404, detail="Session not found")
  meta = ctx.db.get_session_meta(session_id, include_deleted=True)
  ingest_session_streams(
      ctx,
      session_id=session_id,
      runtime=str((meta or {}).get("agent_runtime") or "native"),
  )
  steps = ctx.db.list_steps_for_session(
      session_id=session_id,
      after_seq=after_seq,
  )
  if steps:
    return steps
  detail = await build_session_detail(ctx, session_id, include_events=True)
  if detail is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return [
      step
      for step in detail["steps"]
      if int(step.get("session_seq") or step.get("seq") or 0) > after_seq
  ]


@router.get("/{session_id}/detail", response_model=SessionDetail)
async def get_session_detail(
    session_id: str,
    request: Request,
    include_events: bool = Query(default=True),
) -> dict:
  ctx = get_context(request)
  detail = await build_session_detail(ctx, session_id, include_events=include_events)
  if detail is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return detail


@router.post(
    "/{session_id}/tasks/{task_id}/terminate",
    response_model=BackgroundRunSummary,
)
async def terminate_session_task(
    session_id: str,
    task_id: str,
    request: Request,
) -> dict:
  ctx = get_context(request)
  detail = await build_session_detail(ctx, session_id, include_events=False)
  if detail is None:
    raise HTTPException(status_code=404, detail="Session not found")
  run = next(
      (item for item in detail["background_runs"] if item["id"] == task_id),
      None,
  )
  if run is None:
    raise HTTPException(status_code=404, detail="Task not found")
  if run["status"] not in {"queued", "running", "waiting"}:
    raise HTTPException(status_code=409, detail=f"Task is already {run['status']}")
  result = cancel_task(task_id, session_id=session_id)
  if not result.get("success"):
    raise HTTPException(status_code=409, detail=result.get("error") or "Could not terminate task")
  # Cascade to this run's own in-flight sub-runs so they don't orphan.
  child_session_id = run.get("child_session_id")
  if child_session_id:
    cancel_descendant_runs(str(child_session_id))
  refreshed = await build_session_detail(ctx, session_id, include_events=False)
  if refreshed is None:
    raise HTTPException(status_code=404, detail="Session not found")
  return next(
      (item for item in refreshed["background_runs"] if item["id"] == task_id),
      {**run, "status": "cancelled"},
  )
