from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from typing import TYPE_CHECKING

from ..contract.product import get_agent_definition
from ..contract.services import APP_NAME
from .title_generation import fallback_title
from .title_generation import generate_session_title
from .turn_queue import dispatch_next_queued_turn

if TYPE_CHECKING:
  from .context import WebApiContext


async def generate_and_store_session_title(
    ctx: WebApiContext,
    session_id: str,
    input_text: str,
) -> None:
  title = await generate_session_title(input_text)
  if not title:
    return
  ctx.db.update_session_title(session_id, title, source="auto")


def seed_session_title(
    ctx: WebApiContext,
    session_id: str,
    project_id: str,
    agent_id: str,
    seed_text: str,
    input_text: str,
) -> None:
  """Materialize the web_sessions meta row (so the session shows up in lists)
  and kick off async LLM title generation. This is the *only* place the
  web_sessions row gets created on the new-session path, so callers that want a
  session visible in the UI must run it."""
  meta = ctx.db.get_session_meta(session_id)
  if meta is None:
    definition = get_agent_definition(agent_id)
    ctx.db.create_session(
        session_id=session_id,
        project_id=project_id,
        agent_id=agent_id,
        agent_runtime=definition.runtime,
        title=fallback_title(seed_text),
        title_source="auto",
    )
  elif not str(meta.get("title") or "").strip():
    ctx.db.update_session_title(session_id, fallback_title(seed_text), source="auto")
  else:
    return
  if input_text:
    asyncio.create_task(
        generate_and_store_session_title(ctx, session_id, input_text)
    )


async def start_new_session_turn(
    ctx: WebApiContext,
    *,
    project_id: str,
    agent_id: str,
    model_config_id: str,
    input_text: str,
    trigger_kind: str,
    seed_text: str | None = None,
    extra_session_state: dict[str, Any] | None = None,
    on_turn_created: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, dict[str, Any]]:
  """Create a brand-new root session, queue its first turn, and dispatch it.

  This is the shared bootstrap for both the manual `POST /api/turns`
  (no-session branch) and Automated Task launches. It runs the exact sequence
  the manual path relies on — runtime session state, the queued web_turns row,
  the lazy web_sessions meta row (via seed_session_title), then dispatch — so
  an automated session is indistinguishable from a hand-started one and shows
  up in the session list. `on_turn_created` runs after the turn row exists but
  before dispatch (used by the manual path to persist attachments).
  """
  project = ctx.db.get_project(project_id)
  if project is None:
    raise ValueError(f"Project not found: {project_id}")

  state = {
      "handa:agent_id": agent_id,
      "handa:project_id": project_id,
      "handa:project_root": project["root_path"],
      "handa:model_config_id": model_config_id,
  }
  if extra_session_state:
    state.update(extra_session_state)

  session = await ctx.services.session_service.create_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      state=state,
  )
  session_id = session.id

  if seed_text is None:
    seed_text = input_text or "New session"
  turn = ctx.db.create_turn(
      session_id=session_id,
      model_config_id=model_config_id,
      title=fallback_title(seed_text),
      input_text=input_text,
      trigger_kind=trigger_kind,
  )

  if on_turn_created is not None:
    await on_turn_created(session_id, turn)

  seed_session_title(ctx, session_id, project_id, agent_id, seed_text, input_text)
  dispatch_next_queued_turn(ctx, session_id)
  return session_id, turn
