from __future__ import annotations

from typing import Any

from ..contract import task_store
from ..contract.product import DEFAULT_WEB_AGENT_ID
from ..contract.product import get_agent_definition
from ..contract.product import resolve_agent_id_for_runtime
from ..contract.task_store import create_web_turn_task
from ..contract.task_store import now_iso
from ..contract.task_store import resume_web_turn_task
from .context import WebApiContext


def spawn_turn_worker(ctx: WebApiContext, turn_id: str) -> None:
  """Persist the run record for a claimed turn and spawn its worker process.

  Replaces the old in-process execute_turn: the record carries every input the
  worker needs (the worker never reads web sqlite), and worker-owned status is
  mirrored back by turn_run_sync.
  """
  task = create_turn_run_record(ctx, turn_id)
  if task is None:
    return
  # Looked up through the module so tests can monkeypatch the spawn.
  task_store.spawn_web_turn_worker(task, extra_env=_worker_env(ctx))


def create_turn_run_record(ctx: WebApiContext, turn_id: str) -> dict[str, Any] | None:
  """Write the run record for a claimed turn; marks the turn failed when the
  session or project it needs is gone."""
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    return None
  session_id = str(turn["session_id"])
  session = ctx.db.get_session_meta(session_id, include_deleted=True)
  if session is None:
    _fail_before_spawn(ctx, turn_id, "SessionNotFound", "Session not found")
    return None
  project_id = str(session.get("project_id") or "")
  project = ctx.db.get_project(project_id) if project_id else None
  if project is None:
    _fail_before_spawn(ctx, turn_id, "ProjectNotFound", "Project not found")
    return None

  raw_agent_id = str(session.get("agent_id") or DEFAULT_WEB_AGENT_ID)
  agent_runtime = str(
      session.get("agent_runtime") or get_agent_definition(raw_agent_id).runtime
  )
  agent_id = resolve_agent_id_for_runtime(
      raw_agent_id,
      agent_runtime,
  )
  return create_web_turn_task(
      session_id=session_id,
      turn_id=turn_id,
      project_root=str(project["root_path"]),
      agent_id=agent_id,
      agent_runtime=agent_runtime,
      input_text=str(turn.get("input_text") or ""),
      user_id=ctx.settings.user_id,
      model_config_id=turn.get("model_config_id"),
      streaming_mode_enabled=_streaming_enabled(ctx),
      attachments=ctx.db.list_attachments_for_turn(turn_id),
  )


def respawn_turn_worker_for_resume(
    ctx: WebApiContext,
    turn_id: str,
    *,
    resume_user_input: dict[str, Any],
) -> None:
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    raise KeyError(f"Turn not found: {turn_id}")
  resume_web_turn_task(
      session_id=str(turn["session_id"]),
      turn_id=turn_id,
      resume_user_input=resume_user_input,
      extra_env=_worker_env(ctx),
  )


def _worker_env(ctx: WebApiContext) -> dict[str, str]:
  settings = ctx.db.get_web_settings(user_id=ctx.settings.user_id)
  env: dict[str, str] = {}
  api_key = str(settings.get("gemini_api_key") or "").strip()
  if api_key:
    env["GOOGLE_API_KEY"] = api_key
    env["GEMINI_API_KEY"] = api_key
  return env


def _streaming_enabled(ctx: WebApiContext) -> bool:
  settings = ctx.db.get_web_settings(user_id=ctx.settings.user_id)
  return bool(settings.get("streaming_mode_enabled"))


def _fail_before_spawn(
    ctx: WebApiContext,
    turn_id: str,
    error_type: str,
    error_message: str,
) -> None:
  ctx.db.update_turn(
      turn_id,
      status="failed",
      finished_at=now_iso(),
      error_type=error_type,
      error_message=error_message,
  )
