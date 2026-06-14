from __future__ import annotations

from typing import Any
from typing import TYPE_CHECKING

from ...contract.product import get_agent_definition
from ...contract.product import validate_model_config_id
from ..session_bootstrap import start_new_session_turn

if TYPE_CHECKING:
  from ..context import WebApiContext


async def launch_automated_task_run(
    ctx: WebApiContext,
    *,
    task: dict[str, Any],
    trigger_kind: str,
    trigger_id: str | None = None,
    dedup_key: str | None = None,
    trigger_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
  """Create one run for a single trigger fire and bootstrap its session.

  Idempotent on dedup_key (UNIQUE): a duplicate fire returns None without
  creating a second session. A NULL dedup_key (manual "Run now") always runs.
  Pre-session config failures (bad project / agent / model) land the run in
  `error` and never spawn a session. The run row is the audit anchor; its
  terminal status is mirrored later from the first turn (see run_sync).
  """
  run = ctx.db.create_automated_task_run(
      automated_task_id=str(task["id"]),
      trigger_kind=trigger_kind,
      trigger_id=trigger_id,
      dedup_key=dedup_key,
      trigger_context=trigger_context,
  )
  if run is None:
    # dedup_key already launched this fire.
    return None

  try:
    agent_id = get_agent_definition(str(task["agent_id"])).id
    model_config_id = validate_model_config_id(
        task.get("model_config_id")
        or ctx.db.get_web_settings(user_id=ctx.settings.user_id).get("model_config_id")
    )
    if ctx.db.get_project(str(task["project_id"])) is None:
      raise ValueError("Project not found")
  except ValueError as exc:
    return ctx.db.update_automated_task_run(
        str(run["id"]),
        status="error",
        error_message=str(exc),
    )

  try:
    session_id, turn = await start_new_session_turn(
        ctx,
        project_id=str(task["project_id"]),
        agent_id=agent_id,
        model_config_id=model_config_id,
        input_text=str(task.get("prompt") or ""),
        trigger_kind="automated_task",
        seed_text=(str(task.get("name") or "").strip() or None),
        extra_session_state={
            "handa:automated_task_id": str(task["id"]),
            "handa:automated_task_run_id": str(run["id"]),
            "handa:trigger_kind": "automated_task",
        },
    )
  except Exception as exc:  # noqa: BLE001 - record any bootstrap failure on the run
    return ctx.db.update_automated_task_run(
        str(run["id"]),
        status="error",
        error_message=f"Failed to start session: {exc}",
    )

  ctx.db.attach_automated_task_run_session(
      str(run["id"]),
      session_id=session_id,
      turn_id=str(turn["id"]),
  )
  ctx.db.mark_automated_task_triggered(str(task["id"]), session_id=session_id)
  return ctx.db.get_automated_task_run(str(run["id"]))
