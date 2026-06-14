from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request

from ...contract.product import get_agent_definition
from ...contract.product import validate_model_config_id
from ..automated_tasks.runner import launch_automated_task_run
from ..automated_tasks.schedule import compute_next_fire
from ..context import get_context
from ..title_generation import fallback_title
from ..title_generation import generate_session_title
from ..schemas import AutomatedTaskCreateRequest
from ..schemas import AutomatedTaskDeleteSummary
from ..schemas import AutomatedTaskDetail
from ..schemas import AutomatedTaskRunSummary
from ..schemas import AutomatedTaskSummary
from ..schemas import AutomatedTaskUpdateRequest


router = APIRouter(prefix="/api/automated-tasks")


def _present_trigger(row: dict[str, Any]) -> dict[str, Any]:
  try:
    config = json.loads(row.get("config_json") or "{}")
  except (TypeError, ValueError):
    config = {}
  return {
      "id": row["id"],
      "type": row.get("type") or "",
      "enabled": bool(row.get("enabled")),
      "config": config if isinstance(config, dict) else {},
      "next_fire_at": row.get("next_fire_at"),
      "last_fired_at": row.get("last_fired_at"),
  }


def _present_task(ctx, row: dict[str, Any]) -> dict[str, Any]:
  triggers = [
      _present_trigger(trig)
      for trig in ctx.db.list_automated_task_triggers(str(row["id"]))
  ]
  return {
      "id": row["id"],
      "project_id": row.get("project_id"),
      "name": row.get("name") or "",
      "description": row.get("description"),
      "enabled": bool(row.get("enabled")),
      "agent_id": row.get("agent_id") or "",
      "model_config_id": row.get("model_config_id"),
      "prompt": row.get("prompt") or "",
      "last_triggered_at": row.get("last_triggered_at"),
      "last_run_session_id": row.get("last_run_session_id"),
      "last_run_status": row.get("last_run_status"),
      "created_at": row.get("created_at"),
      "updated_at": row.get("updated_at"),
      "triggers": triggers,
  }


def _present_run(row: dict[str, Any]) -> dict[str, Any]:
  return {
      "id": row["id"],
      "automated_task_id": row.get("automated_task_id"),
      "trigger_kind": row.get("trigger_kind") or "",
      "trigger_id": row.get("trigger_id"),
      "status": row.get("status") or "",
      "session_id": row.get("session_id"),
      "turn_id": row.get("turn_id"),
      "error_message": row.get("error_message"),
      "created_at": row.get("created_at"),
      "updated_at": row.get("updated_at"),
  }


def _trigger_rows_with_schedule(triggers: list[Any]) -> list[dict[str, Any]]:
  """Serialize trigger payloads and pre-compute next_fire_at for time triggers,
  so the dispatcher can pick them up on the next tick instead of waiting for the
  startup backfill."""
  rows: list[dict[str, Any]] = []
  for trig in triggers:
    row = trig.model_dump()
    if row.get("type") == "time":
      config = row.get("config") or {}
      row["next_fire_at"] = compute_next_fire(
          str(config.get("cron") or ""),
          str(config.get("timezone") or "UTC"),
      )
    rows.append(row)
  return rows


def _resolve_config(ctx, *, project_id: str, agent_id: str, model_config_id: str | None):
  if ctx.db.get_project(project_id) is None:
    raise HTTPException(status_code=404, detail="Project not found")
  try:
    agent_id = get_agent_definition(agent_id).id
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  if model_config_id:
    try:
      model_config_id = validate_model_config_id(model_config_id)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
  return agent_id, model_config_id


def _detail(ctx, task_id: str) -> dict[str, Any]:
  row = ctx.db.get_automated_task(task_id)
  if row is None:
    raise HTTPException(status_code=404, detail="Automated task not found")
  detail = _present_task(ctx, row)
  detail["runs"] = [
      _present_run(run) for run in ctx.db.list_automated_task_runs(task_id, limit=20)
  ]
  return detail


@router.get("", response_model=list[AutomatedTaskSummary])
def list_automated_tasks(
    request: Request,
    project_id: str | None = Query(default=None),
) -> list[dict]:
  ctx = get_context(request)
  return [
      _present_task(ctx, row)
      for row in ctx.db.list_automated_tasks(project_id=project_id)
  ]


async def _generate_and_store_task_title(
    ctx,
    task_id: str,
    prompt: str,
    fallback: str,
) -> None:
  """Upgrade the prompt-derived fallback name to a concise LLM title, the same
  way a session gets named. Only overwrites if the name is still the fallback,
  so a manual rename is never clobbered."""
  title = await generate_session_title(prompt)
  if not title:
    return
  current = ctx.db.get_automated_task(task_id, include_deleted=True)
  if current is not None and str(current.get("name") or "") == fallback:
    ctx.db.update_automated_task(task_id, name=title)


@router.post("", response_model=AutomatedTaskDetail)
async def create_automated_task(payload: AutomatedTaskCreateRequest, request: Request) -> dict:
  ctx = get_context(request)
  agent_id, model_config_id = _resolve_config(
      ctx,
      project_id=payload.project_id,
      agent_id=payload.agent_id,
      model_config_id=payload.model_config_id,
  )
  prompt = payload.prompt.strip()
  # No Name field in the UI — title the task from its prompt like a session:
  # the fallback shows immediately, then an LLM title replaces it async.
  explicit_name = (payload.name or "").strip()
  fallback = explicit_name or fallback_title(prompt)
  task = ctx.db.create_automated_task(
      project_id=payload.project_id,
      name=fallback,
      prompt=prompt,
      agent_id=agent_id,
      model_config_id=model_config_id,
      description=payload.description,
      enabled=payload.enabled,
  )
  if payload.triggers:
    ctx.db.replace_automated_task_triggers(
        str(task["id"]),
        _trigger_rows_with_schedule(payload.triggers),
    )
  if not explicit_name:
    asyncio.create_task(
        _generate_and_store_task_title(ctx, str(task["id"]), prompt, fallback)
    )
  return _detail(ctx, str(task["id"]))


@router.get("/{task_id}", response_model=AutomatedTaskDetail)
def get_automated_task(task_id: str, request: Request) -> dict:
  ctx = get_context(request)
  return _detail(ctx, task_id)


@router.patch("/{task_id}", response_model=AutomatedTaskDetail)
def update_automated_task(
    task_id: str,
    payload: AutomatedTaskUpdateRequest,
    request: Request,
) -> dict:
  ctx = get_context(request)
  existing = ctx.db.get_automated_task(task_id)
  if existing is None:
    raise HTTPException(status_code=404, detail="Automated task not found")

  agent_id = payload.agent_id or str(existing["agent_id"])
  model_config_id = (
      payload.model_config_id
      if payload.model_config_id is not None
      else existing.get("model_config_id")
  )
  agent_id, model_config_id = _resolve_config(
      ctx,
      project_id=str(existing["project_id"]),
      agent_id=agent_id,
      model_config_id=model_config_id,
  )

  fields: dict[str, Any] = {"agent_id": agent_id, "model_config_id": model_config_id}
  if payload.name is not None:
    name = payload.name.strip()
    if not name:
      raise HTTPException(status_code=400, detail="Task name is required")
    fields["name"] = name
  if payload.prompt is not None:
    prompt = payload.prompt.strip()
    if not prompt:
      raise HTTPException(status_code=400, detail="Prompt is required")
    fields["prompt"] = prompt
  if payload.description is not None:
    fields["description"] = payload.description
  ctx.db.update_automated_task(task_id, **fields)
  if payload.triggers is not None:
    ctx.db.replace_automated_task_triggers(
        task_id,
        _trigger_rows_with_schedule(payload.triggers),
    )
  return _detail(ctx, task_id)


@router.delete("/{task_id}", response_model=AutomatedTaskDeleteSummary)
def delete_automated_task(task_id: str, request: Request) -> dict:
  ctx = get_context(request)
  if ctx.db.delete_automated_task(task_id) is None:
    raise HTTPException(status_code=404, detail="Automated task not found")
  return {"id": task_id, "removed": True}


@router.post("/{task_id}/enable", response_model=AutomatedTaskDetail)
def enable_automated_task(task_id: str, request: Request) -> dict:
  ctx = get_context(request)
  if ctx.db.get_automated_task(task_id) is None:
    raise HTTPException(status_code=404, detail="Automated task not found")
  ctx.db.set_automated_task_enabled(task_id, enabled=True)
  return _detail(ctx, task_id)


@router.post("/{task_id}/disable", response_model=AutomatedTaskDetail)
def disable_automated_task(task_id: str, request: Request) -> dict:
  ctx = get_context(request)
  if ctx.db.get_automated_task(task_id) is None:
    raise HTTPException(status_code=404, detail="Automated task not found")
  ctx.db.set_automated_task_enabled(task_id, enabled=False)
  return _detail(ctx, task_id)


@router.post("/{task_id}/run", response_model=AutomatedTaskRunSummary)
async def run_automated_task_now(task_id: str, request: Request) -> dict:
  ctx = get_context(request)
  task = ctx.db.get_automated_task(task_id)
  if task is None:
    raise HTTPException(status_code=404, detail="Automated task not found")
  run = await launch_automated_task_run(ctx, task=task, trigger_kind="manual")
  if run is None:
    raise HTTPException(status_code=409, detail="Run could not be created")
  return _present_run(run)


@router.get("/{task_id}/runs", response_model=list[AutomatedTaskRunSummary])
def list_automated_task_runs(
    task_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
  ctx = get_context(request)
  if ctx.db.get_automated_task(task_id) is None:
    raise HTTPException(status_code=404, detail="Automated task not found")
  return [
      _present_run(run)
      for run in ctx.db.list_automated_task_runs(task_id, limit=limit)
  ]
