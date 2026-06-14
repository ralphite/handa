"""In-process agent driver for tests.

This is the execution path src.handacli used to own before it became a thin
web API client with no runtime code. Tests that need to drive a (possibly
fake) agent directly against real session/artifact services — without a web
API process — use this helper instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.agents.handa_adk.loader import DEFAULT_AGENT_ID
from src.agents.handa_adk.loader import load_agent
from src.run_retry import is_retryable_run_error
from src.runner import APP_NAME
from src.runner import create_handa_app
from src.runner import create_runner
from src.runner import DEFAULT_USER_ID


MAX_RUN_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 2.0


class InProcessRunResult(BaseModel):
  ok: bool
  session_id: str | None = None
  response: str = ""


def _event_text(event: Any) -> str:
  content = getattr(event, "content", None)
  parts = getattr(content, "parts", None) if content else None
  if not parts:
    return ""
  texts = [getattr(part, "text", "") for part in parts if getattr(part, "text", "")]
  return "\n".join(texts)


async def run_agent_once(
    *,
    project: Path,
    prompt: str,
    session_id: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    agent_id: str = DEFAULT_AGENT_ID,
    agent: Any | None = None,
) -> InProcessRunResult:
  from google.genai import types

  services = create_handa_app(project).services

  if session_id:
    session = await services.session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
      raise LookupError(f"Session not found: {session_id}")
  else:
    session = await services.session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
    )
    session_id = session.id

  if agent is None:
    agent = load_agent(agent_id, project_root=str(project))
  runner = create_runner(services, agent)

  final_text = ""
  delay_sec = RETRY_BASE_DELAY_SEC
  for attempt in range(1, MAX_RUN_ATTEMPTS + 1):
    try:
      async for event in runner.run_async(
          user_id=user_id,
          session_id=session_id,
          new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
      ):
        if hasattr(event, "is_final_response") and event.is_final_response():
          text = _event_text(event)
          if text:
            final_text = text
      break
    except Exception as exc:
      if not is_retryable_run_error(exc) or attempt >= MAX_RUN_ATTEMPTS:
        raise
      await asyncio.sleep(delay_sec)
      delay_sec *= 2
  return InProcessRunResult(ok=True, session_id=session_id, response=final_text)
