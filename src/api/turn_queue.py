from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .turn_spawn import spawn_turn_worker

if TYPE_CHECKING:
  from .context import WebApiContext


def dispatch_next_queued_turn(
    ctx: WebApiContext,
    session_id: str,
    *,
    executor: Callable[[WebApiContext, str], None] | None = None,
) -> dict | None:
  """Claim the next queued turn for the session and hand it to the executor.

  The claim flips queued→running atomically, so a turn is never dispatched
  twice; per-session serialization comes from the claim skipping sessions with
  a running or waiting turn. The default executor resolves at call time so
  tests can monkeypatch spawn_turn_worker.
  """
  turn = ctx.db.claim_next_queued_turn_for_session(session_id)
  if turn is None:
    return None
  (executor or spawn_turn_worker)(ctx, turn["id"])
  return turn


def dispatch_queued_turns(ctx: WebApiContext) -> int:
  started = 0
  for session_id in ctx.db.list_queued_turn_session_ids():
    if dispatch_next_queued_turn(ctx, session_id) is not None:
      started += 1
  return started
