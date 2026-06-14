from __future__ import annotations

import json
import logging
from typing import Any
from typing import TYPE_CHECKING

from ...contract.task_store import now_iso
from .runner import launch_automated_task_run
from .schedule import compute_next_fire

if TYPE_CHECKING:
  from ..context import WebApiContext

logger = logging.getLogger(__name__)


def _trigger_config(row: dict[str, Any]) -> dict[str, Any]:
  try:
    config = json.loads(row.get("config_json") or "{}")
  except (TypeError, ValueError):
    return {}
  return config if isinstance(config, dict) else {}


async def dispatch_due_time_triggers(ctx: WebApiContext) -> int:
  """Fire every time trigger whose next_fire_at has arrived, then advance it.

  Called every tick of the background loop. Properties:
  - Idempotent per (trigger, fire-time): dedup_key is UNIQUE, so a retry or a
    concurrent loop never double-launches the same slot.
  - No backlog storm: a missed slot (server was down) fires once, and the next
    slot is computed from *now*, collapsing a long outage into a single catch-up.
  - Fault-isolated: one bad trigger is logged and skipped; the trigger is still
    advanced so it can't wedge the loop on every tick.
  Returns the number of runs launched.
  """
  now = now_iso()
  fired = 0
  for due in ctx.db.list_due_time_triggers(now=now):
    trigger_id = str(due["trigger_id"])
    task_id = str(due["task_id"])
    fire_at = str(due["next_fire_at"])
    config = _trigger_config(due)
    cron = str(config.get("cron") or "")
    tz = str(config.get("timezone") or "UTC")
    next_fire = compute_next_fire(cron, tz, after=now)
    try:
      task = ctx.db.get_automated_task(task_id)
      if task is not None and task.get("enabled"):
        run = await launch_automated_task_run(
            ctx,
            task=task,
            trigger_kind="time",
            trigger_id=trigger_id,
            dedup_key=f"{trigger_id}:{fire_at}",
            trigger_context={"scheduled_for": fire_at, "cron": cron, "timezone": tz},
        )
        if run is not None:
          fired += 1
    except Exception:  # noqa: BLE001 - never let one trigger stall the loop
      logger.exception("automated task time trigger %s failed to dispatch", trigger_id)
    finally:
      # Always advance, even on a skipped/failed fire, so the same slot is not
      # re-queried every second.
      ctx.db.advance_automated_task_trigger(
          trigger_id,
          next_fire_at=next_fire,
          last_fired_at=fire_at,
      )
  return fired


def backfill_time_trigger_schedules(ctx: WebApiContext) -> int:
  """Give every live time trigger a next_fire_at if it lacks one.

  Run once at startup so triggers created before the scheduler existed (or whose
  computation was skipped) start firing. New triggers get next_fire_at at
  create/update time, so they don't depend on this. Returns the count scheduled.
  """
  scheduled = 0
  for row in ctx.db.list_time_triggers_missing_next_fire():
    config = _trigger_config(row)
    next_fire = compute_next_fire(
        str(config.get("cron") or ""),
        str(config.get("timezone") or "UTC"),
    )
    if next_fire is not None:
      ctx.db.set_automated_task_trigger_next_fire(
          str(row["trigger_id"]),
          next_fire_at=next_fire,
      )
      scheduled += 1
  return scheduled
