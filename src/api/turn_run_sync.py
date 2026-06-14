from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any

from ..contract.task_store import is_process_alive
from ..contract.task_store import load_task
from ..contract.task_store import now_iso
from .context import WebApiContext
from .steps_projection import ingest_session_streams


# web_turns statuses the Web layer owns; everything past dispatch mirrors the
# worker-owned run record (sessions/<sid>/tasks/<turn_id>/task.json).
ACTIVE_TURN_STATUSES = ("running", "waiting_input")

_TASK_TO_TURN_STATUS = {
    "queued": "running",  # spawned but the worker has not flipped it yet
    "running": "running",
    "waiting": "waiting_input",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}

TERMINAL_TURN_STATUSES = {"completed", "failed", "cancelled"}

# A claimed turn briefly has no run record (claim → record write → spawn), and
# a freshly spawned worker briefly has no pid; don't declare orphans inside
# this window.
SPAWN_GRACE_SECONDS = 30.0


def sync_turn_with_run_record(ctx: WebApiContext, turn: dict[str, Any]) -> dict[str, Any]:
  """Mirror worker-owned execution state from the run record into web_turns.

  Returns the up-to-date turn row. Safe to call from sync read endpoints: it
  only projects status fields. Terminal side effects (parent-task finalize)
  belong to the BackgroundTaskManager loop, keyed off the status transition
  this function performs.
  """
  status = str(turn.get("status") or "")
  if status not in ACTIVE_TURN_STATUSES:
    return turn
  turn_id = str(turn["id"])
  session_id = str(turn["session_id"])
  try:
    task = load_task(turn_id, session_id=session_id)
  except (FileNotFoundError, KeyError, ValueError):
    if _seconds_since(turn.get("updated_at")) <= SPAWN_GRACE_SECONDS:
      return turn
    return _fail_orphan_turn(ctx, turn, reason="Turn run record is missing.")

  mapped = _TASK_TO_TURN_STATUS.get(str(task.get("status") or ""))
  if mapped is None:
    return turn

  if mapped == "running" and status == "running":
    if _worker_is_gone(task):
      return _fail_orphan_turn(ctx, turn, reason="Turn worker exited unexpectedly.")
    return turn
  if mapped == status:
    return turn

  if mapped in TERMINAL_TURN_STATUSES or mapped == "waiting_input":
    # Materialize any tail events before the status flips so the FE never sees
    # a terminal turn with missing steps (or a waiting turn without its
    # user_input_requested step).
    ingest_session_streams(
        ctx,
        session_id=session_id,
        runtime=str(task.get("agent_runtime") or "adk"),
    )
  if mapped in TERMINAL_TURN_STATUSES:
    # The final agent_text supersedes the streaming deltas; drop them so the
    # timeline (and the table) don't keep one row per flushed chunk.
    ctx.db.delete_turn_steps_of_kind(turn_id=turn_id, kind="agent_text_delta")
  updates: dict[str, Any] = {"status": mapped}
  if mapped == "completed":
    updates["final_text"] = task.get("final_text")
    updates["finished_at"] = task.get("finished_at") or now_iso()
  elif mapped == "failed":
    updates["error_type"] = task.get("error_type") or "WorkerFailed"
    updates["error_message"] = task.get("error_message") or "Turn worker failed."
    updates["finished_at"] = task.get("finished_at") or now_iso()
  elif mapped == "cancelled":
    updates["error_type"] = "Cancelled"
    updates["error_message"] = "User terminated the turn."
    updates["finished_at"] = task.get("finished_at") or now_iso()
  return ctx.db.update_turn(turn_id, **updates)


def sync_active_turns(ctx: WebApiContext) -> list[dict[str, Any]]:
  """Project all active turns; returns the rows that reached a terminal state."""
  finished: list[dict[str, Any]] = []
  for turn in ctx.db.list_turns_with_statuses(ACTIVE_TURN_STATUSES):
    updated = sync_turn_with_run_record(ctx, turn)
    if (
        str(updated.get("status") or "") in TERMINAL_TURN_STATUSES
        and str(turn.get("status") or "") not in TERMINAL_TURN_STATUSES
    ):
      finished.append(updated)
  return finished


def _worker_is_gone(task: dict[str, Any]) -> bool:
  pid = task.get("worker_pid")
  if pid is None:
    # Spawned moments ago; the pid lands right after Popen returns.
    return _seconds_since(task.get("created_at")) > SPAWN_GRACE_SECONDS
  return not is_process_alive(pid)


def _fail_orphan_turn(
    ctx: WebApiContext,
    turn: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
  if str(turn.get("status") or "") == "waiting_input":
    # A waiting turn has no live worker by design; nothing to reconcile.
    return turn
  return ctx.db.update_turn(
      str(turn["id"]),
      status="failed",
      finished_at=now_iso(),
      error_type="WorkerExited",
      error_message=reason,
  )


def _seconds_since(value: Any) -> float:
  if not value:
    return float("inf")
  text = str(value).strip()
  if text.endswith("Z"):
    text = f"{text[:-1]}+00:00"
  try:
    moment = datetime.fromisoformat(text)
  except ValueError:
    return float("inf")
  if moment.tzinfo is None:
    moment = moment.replace(tzinfo=timezone.utc)
  return (datetime.now(tz=timezone.utc) - moment).total_seconds()
