from __future__ import annotations

from pathlib import Path
from typing import Any

from google.genai import types

from ...config import load_agent_config_from_path
from ...run_outcome import RunOutcome
from ..native_runner import AgentEventEmitter
from ..native_runner import generate_model_response as _default_generate_model_response
from ..native_runner import run_native_agent
from .tools import build_session_context
from .tools import build_toolset


CONFIG = load_agent_config_from_path(Path(__file__).with_name("orca.agent.json"))
MAX_TOOL_ROUNDS = 24
DEFAULT_MAX_OUTPUT_TOKENS = 8192
HISTORY_STATE_KEY = "handa:orca_history"
PENDING_ROUNDS_STATE_KEY = "handa:orca_pending_rounds"
HISTORY_BOUNDARY_EVENT_KIND = "orca.history_boundary"


async def run(
    *,
    prompt: str,
    context: str = "",
    attachments: list[dict[str, Any]] | None = None,
    project_root: str,
    emit_event: AgentEventEmitter,
    model_config_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    resume_user_input: dict[str, Any] | None = None,
) -> RunOutcome:
  return await run_native_agent(
      config=CONFIG,
      prompt=prompt,
      context=context,
      attachments=attachments,
      project_root=project_root,
      emit_event=emit_event,
      build_session_context=build_session_context,
      build_toolset=build_toolset,
      generate_model_response=_generate_model_response,
      model_config_id=model_config_id,
      session_id=session_id,
      user_id=user_id,
      resume_user_input=resume_user_input,
      display_name="Orca",
      event_prefix="orca",
      event_id_prefix="orca",
      call_id_prefix="orca_call",
      fallback_session_prefix="orca",
      history_state_key=HISTORY_STATE_KEY,
      pending_rounds_state_key=PENDING_ROUNDS_STATE_KEY,
      history_boundary_event_kind=HISTORY_BOUNDARY_EVENT_KIND,
      max_tool_rounds=MAX_TOOL_ROUNDS,
      default_max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
  )


async def _generate_model_response(
    *,
    client: Any,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
) -> Any:
  return await _default_generate_model_response(
      client=client,
      model=model,
      contents=contents,
      config=config,
  )
