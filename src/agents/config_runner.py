from __future__ import annotations

import re
from typing import Any

from ..config import AgentConfig
from ..run_outcome import RunOutcome
from .native_runner import AgentEventEmitter
from .native_runner import CODE_AGENT_MAX_OUTPUT_TOKENS
from .native_runner import generate_model_response as _default_generate_model_response
from .native_runner import run_native_agent
from .orca.tools import build_session_context
from .orca.tools import build_toolset


DEFAULT_MAX_OUTPUT_TOKENS = CODE_AGENT_MAX_OUTPUT_TOKENS
HISTORY_STATE_KEY = "handa:native_config_history"
PENDING_ROUNDS_STATE_KEY = "handa:native_config_pending_rounds"
HISTORY_BOUNDARY_EVENT_KIND = "native_config.history_boundary"


async def run_config_agent(
    *,
    config: AgentConfig,
    prompt: str,
    context: str = "",
    project_root: str,
    emit_event: AgentEventEmitter,
    model_config_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    resume_user_input: dict[str, Any] | None = None,
) -> RunOutcome:
  display_name = config.name.strip() or "Agent Config"
  event_prefix = _event_prefix(display_name)
  return await run_native_agent(
      config=config,
      prompt=prompt,
      context=context,
      project_root=project_root,
      emit_event=emit_event,
      build_session_context=build_session_context,
      build_toolset=build_toolset,
      generate_model_response=_default_generate_model_response,
      model_config_id=model_config_id,
      session_id=session_id,
      user_id=user_id,
      resume_user_input=resume_user_input,
      display_name=display_name,
      event_prefix=event_prefix,
      event_id_prefix=event_prefix,
      call_id_prefix=f"{event_prefix}_call",
      fallback_session_prefix=event_prefix,
      history_state_key=HISTORY_STATE_KEY,
      pending_rounds_state_key=PENDING_ROUNDS_STATE_KEY,
      history_boundary_event_kind=HISTORY_BOUNDARY_EVENT_KIND,
      default_max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
  )


def _event_prefix(name: str) -> str:
  normalized = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().lower()).strip("_")
  if not normalized:
    return "native_config"
  if normalized[0].isdigit():
    normalized = f"agent_{normalized}"
  return normalized
