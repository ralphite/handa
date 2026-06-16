from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any

from .agent_runtime import get_agent_definition
from .agents.native_loader import load_agent as load_native_agent
from .run_outcome import RunOutcome
from .run_retry import run_with_retries
from .runner import HandaServices
from .runtime import get_project_root
from .runtime import project_context


EventCallback = Callable[[Any], Awaitable[None]]
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
  _handler = logging.StreamHandler()
  _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
  LOGGER.addHandler(_handler)
LOGGER.propagate = False


async def run_agent_invocation(
    *,
    services: HandaServices,
    session_id: str,
    user_id: str,
    agent_id: str,
    input_text: str,
    on_event: EventCallback,
    attachments: list[dict[str, Any]] | None = None,
    project_root: str | None = None,
    model_config_id: str | None = None,
    streaming_mode_enabled: bool = True,
    resume_user_input: dict[str, Any] | None = None,
) -> RunOutcome:
  _ = services, streaming_mode_enabled
  if project_root:
    with project_context(project_root):
      return await _run_native_agent_invocation_in_project(
          agent_id=agent_id,
          session_id=session_id,
          user_id=user_id,
          input_text=input_text,
          attachments=attachments,
          on_event=on_event,
          project_root=str(get_project_root()),
          model_config_id=model_config_id,
          resume_user_input=resume_user_input,
      )
  return await _run_native_agent_invocation_in_project(
      agent_id=agent_id,
      session_id=session_id,
      user_id=user_id,
      input_text=input_text,
      attachments=attachments,
      on_event=on_event,
      project_root=str(get_project_root()),
      model_config_id=model_config_id,
      resume_user_input=resume_user_input,
  )


async def _run_native_agent_invocation_in_project(
    *,
    agent_id: str,
    session_id: str,
    user_id: str,
    input_text: str,
    on_event: EventCallback,
    attachments: list[dict[str, Any]] | None = None,
    project_root: str | None = None,
    model_config_id: str | None = None,
    resume_user_input: dict[str, Any] | None = None,
) -> RunOutcome:
  definition = get_agent_definition(agent_id)
  if definition.runtime != "native":
    raise ValueError(f"Unsupported agent runtime: {definition.runtime!r}")

  runner = load_native_agent(definition.id)
  produced_output = False

  async def emit_event(event: dict[str, Any]) -> None:
    nonlocal produced_output
    if _event_counts_as_user_visible_output(event):
      produced_output = True
    await on_event(event)

  resume_response = (
      dict(resume_user_input)
      if resume_user_input is not None
      else None
  )

  async def _attempt() -> RunOutcome:
    return await runner(
        prompt=input_text,
        attachments=attachments,
        project_root=project_root or str(Path.cwd()),
        emit_event=emit_event,
        model_config_id=model_config_id,
        session_id=session_id,
        user_id=user_id,
        resume_user_input=resume_response,
    )

  return await run_with_retries(
      _attempt,
      should_retry=lambda: not produced_output,
      on_retry=lambda attempt_no, delay_sec, exc: LOGGER.warning(
          "Retrying web turn after transient error: attempt=%s "
          "next_delay_sec=%s error=%s session_id=%s",
          attempt_no,
          delay_sec,
          exc,
          session_id,
      ),
  )


def _event_counts_as_user_visible_output(event: Any) -> bool:
  if not isinstance(event, dict):
    return True
  kind = str(event.get("kind") or "")
  if not kind:
    return True
  if kind.endswith(".started") or kind.endswith(".history_boundary"):
    return False
  return True
