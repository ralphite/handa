from __future__ import annotations

from typing import TYPE_CHECKING

from ..turn_run_sync import TERMINAL_TURN_STATUSES

if TYPE_CHECKING:
  from ..context import WebApiContext


def sync_automated_task_runs(ctx: WebApiContext) -> int:
  """Mirror each launched run's terminal status from its first turn.

  Reuses the turn-status projection — web_turns is kept current by
  sync_active_turns in the same loop — so this works for every trigger path
  (manual / time / event) without separately polling the worker run record.
  Returns the number of runs moved to a terminal status.
  """
  updated = 0
  for run in ctx.db.list_automated_task_runs_with_status("launched"):
    turn_id = run.get("turn_id")
    if not turn_id:
      continue
    turn = ctx.db.get_turn(str(turn_id))
    if turn is None:
      continue
    status = str(turn.get("status") or "")
    if status in TERMINAL_TURN_STATUSES:
      ctx.db.update_automated_task_run(str(run["id"]), status=status)
      updated += 1
  return updated
