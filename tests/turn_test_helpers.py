from __future__ import annotations

from typing import Any

from src import turn_worker
from src.runtime import load_task
from src.runtime import save_task
from src.api.background_task_manager import BackgroundTaskManager
from src.api.turn_run_sync import sync_turn_with_run_record
from src.api.turn_spawn import create_turn_run_record


async def execute_turn(
    ctx,
    turn_id: str,
    *,
    resume_user_input: dict[str, Any] | None = None,
) -> None:
  """Drive one turn through the real worker code path, in-process.

  Mirrors production: create the run record (as spawn would), run
  turn_worker._run_turn (so tests can monkeypatch
  src.turn_worker.run_agent_invocation), then project worker-owned state back
  into web_turns and apply terminal side effects the BackgroundTaskManager
  loop would.
  """
  turn = ctx.db.get_turn(turn_id)
  if turn is None:
    return
  session_id = str(turn["session_id"])
  try:
    task = load_task(turn_id, session_id=session_id)
  except (FileNotFoundError, KeyError, ValueError):
    task = create_turn_run_record(ctx, turn_id)
    if task is None:
      return
  if resume_user_input is not None:
    task["resume_user_input"] = resume_user_input
    task["pending_user_input"] = None
    task["status"] = "queued"
    task["finished_at"] = None
    task["returncode"] = None
    save_task(task)
  ctx.db.update_turn(turn_id, status="running")

  await turn_worker._run_turn(session_id, turn_id)  # noqa: SLF001

  before = ctx.db.get_turn(turn_id)
  updated = sync_turn_with_run_record(ctx, before)
  if str(updated.get("status") or "") in {"completed", "failed", "cancelled"}:
    manager = BackgroundTaskManager(ctx, start_invocation_run=False)
    await manager._finalize_waiting_parent_task_if_ready(updated)  # noqa: SLF001
