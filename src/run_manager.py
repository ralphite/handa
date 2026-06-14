from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
import json
import logging
from pathlib import Path
from typing import Any

from google.adk.agents.run_config import RunConfig
from google.adk.agents.run_config import StreamingMode
from google.genai import types

from .agent_runtime import get_agent_definition
from .agents.handa_adk.loader import load_agent
from .agents.handa_langgraph.loader import load_agent as load_langgraph_agent
from .message_parts import build_message_parts
from .model_configs import resolve_model_config
from .model_configs import validate_model_config_id
from .run_outcome import RunOutcome
from .run_retry import run_with_retries
from .runner import create_runner
from .runner import HandaServices
from .runtime import get_project_root
from .runtime import project_context
from .tools.user_input import build_pending_request
from .tools.user_input import PENDING_USER_INPUT_STATE_KEY
from .tools.user_input import USER_INPUT_TOOL_NAME
from .tools.user_input import validate_questions


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
  if project_root:
    with project_context(project_root):
      return await _run_agent_invocation_in_project(
          services=services,
          session_id=session_id,
          user_id=user_id,
          agent_id=agent_id,
          input_text=input_text,
          attachments=attachments,
          on_event=on_event,
          model_config_id=model_config_id,
          streaming_mode_enabled=streaming_mode_enabled,
          resume_user_input=resume_user_input,
          project_agents_root=str(get_project_root()),
      )
  return await _run_agent_invocation_in_project(
      services=services,
      session_id=session_id,
      user_id=user_id,
      agent_id=agent_id,
      input_text=input_text,
      attachments=attachments,
      on_event=on_event,
      model_config_id=model_config_id,
      streaming_mode_enabled=streaming_mode_enabled,
      resume_user_input=resume_user_input,
      project_agents_root=None,
  )


async def _run_agent_invocation_in_project(
    *,
    services: HandaServices,
    session_id: str,
    user_id: str,
    agent_id: str,
    input_text: str,
    on_event: EventCallback,
    attachments: list[dict[str, Any]] | None = None,
    model_config_id: str | None = None,
    streaming_mode_enabled: bool = True,
    resume_user_input: dict[str, Any] | None = None,
    project_agents_root: str | None = None,
) -> RunOutcome:
  definition = get_agent_definition(agent_id)
  if definition.runtime == "langgraph":
    return await _run_langgraph_agent_invocation_in_project(
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
  return await _run_adk_agent_invocation_in_project(
      services=services,
      session_id=session_id,
      user_id=user_id,
      agent_id=agent_id,
      input_text=input_text,
      attachments=attachments,
      on_event=on_event,
      model_config_id=model_config_id,
      streaming_mode_enabled=streaming_mode_enabled,
      resume_user_input=resume_user_input,
      project_agents_root=project_agents_root,
  )


async def _run_adk_agent_invocation_in_project(
    *,
    services: HandaServices,
    session_id: str,
    user_id: str,
    agent_id: str,
    input_text: str,
    on_event: EventCallback,
    attachments: list[dict[str, Any]] | None = None,
    model_config_id: str | None = None,
    streaming_mode_enabled: bool = True,
    resume_user_input: dict[str, Any] | None = None,
    project_agents_root: str | None = None,
) -> RunOutcome:
  agent = load_agent(agent_id, project_root=project_agents_root)
  resolved_model_config_id = validate_model_config_id(model_config_id)
  runtime_model_config = _apply_model_config(agent, resolved_model_config_id)
  LOGGER.info(
      "Gemini request: model_config_id=%s model=%s generate_content_config=%s "
      "agent_id=%s session_id=%s user_id=%s input_chars=%s",
      resolved_model_config_id,
      runtime_model_config.model,
      _json_summary(runtime_model_config.generate_content_config),
      agent_id,
      session_id,
      user_id,
      len(input_text),
  )
  runner = create_runner(services, agent)

  if resume_user_input is not None:
    new_message = _user_input_response_message(resume_user_input)
  else:
    new_message = types.Content(
        role="user",
        parts=build_message_parts(input_text, attachments),
    )

  produced_output = False

  async def _attempt() -> RunOutcome:
    nonlocal produced_output
    final_text = ""
    pending_user_input: dict[str, Any] | None = None
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
        run_config=RunConfig(
            streaming_mode=(
                StreamingMode.SSE if streaming_mode_enabled else StreamingMode.NONE
            ),
        ),
    ):
      produced_output = True
      await on_event(event)
      pending_user_input = _pending_adk_user_input(event) or pending_user_input
      if hasattr(event, "is_final_response") and event.is_final_response():
        text = _event_text(event)
        if text:
          final_text = text
        LOGGER.info(
            "Gemini response: model_config_id=%s model=%s event_metadata=%s "
            "session_id=%s final_text_chars=%s",
            resolved_model_config_id,
            runtime_model_config.model,
            _json_summary(_event_metadata(event)),
            session_id,
            len(text),
        )

    if pending_user_input is not None:
      services.session_service.merge_state_sync(
          session_id,
          {PENDING_USER_INPUT_STATE_KEY: pending_user_input},
      )
      return RunOutcome(pending_user_input=pending_user_input)
    return RunOutcome(final_text=final_text)

  # Transient model errors (rate limits, 5xx) before any event is streamed are
  # retried with backoff; once output has been produced we let the failure
  # surface (the user can re-run via the manual Retry action) so partial
  # streamed output is never duplicated.
  return await run_with_retries(
      _attempt,
      should_retry=lambda: not produced_output,
      on_retry=lambda attempt_no, delay_sec, exc: LOGGER.warning(
          "Retrying web turn after transient error: attempt=%s next_delay_sec=%s "
          "error=%s session_id=%s",
          attempt_no,
          delay_sec,
          exc,
          session_id,
      ),
  )


def _pending_adk_user_input(event: Any) -> dict[str, Any] | None:
  """Detect a paused request_user_input long-running call on an ADK event."""
  long_running_ids = {
      str(value) for value in (_get(event, "long_running_tool_ids") or set())
  }
  if not long_running_ids:
    return None
  content = _get(event, "content")
  for part in _get(content, "parts") or []:
    function_call = _get(part, "function_call")
    if function_call is None:
      continue
    name = str(_get(function_call, "name") or "")
    call_id = str(_get(function_call, "id") or "")
    if name != USER_INPUT_TOOL_NAME or call_id not in long_running_ids:
      continue
    args = dict(_get(function_call, "args") or {})
    try:
      questions = validate_questions(_jsonable(args.get("questions")))
    except ValueError:
      # Invalid arguments: the tool already answered with an error response
      # and the loop continues, so there is nothing pending.
      return None
    return build_pending_request(
        runtime="adk",
        questions=questions,
        function_call_id=call_id,
    )
  return None


def _user_input_response_message(resume_user_input: dict[str, Any]) -> types.Content:
  function_call_id = str(resume_user_input.get("function_call_id") or "")
  if not function_call_id:
    raise ValueError("resume_user_input requires function_call_id for the ADK runtime")
  return types.Content(
      role="user",
      parts=[
          types.Part(
              function_response=types.FunctionResponse(
                  id=function_call_id,
                  name=USER_INPUT_TOOL_NAME,
                  response=dict(resume_user_input.get("response") or {}),
              )
          )
      ],
  )


async def _run_langgraph_agent_invocation_in_project(
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
  runner = load_langgraph_agent(agent_id)
  produced_output = False

  async def emit_event(event: dict[str, Any]) -> None:
    nonlocal produced_output
    produced_output = True
    await on_event(event)

  # The runner expects the bare tool response; the resume envelope's
  # function_call_id only matters for the ADK runtime.
  resume_response = (
      dict(resume_user_input.get("response") or {})
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
          "Retrying web turn (langgraph) after transient error: attempt=%s "
          "next_delay_sec=%s error=%s session_id=%s",
          attempt_no,
          delay_sec,
          exc,
          session_id,
      ),
  )


def _apply_model_config(agent: Any, model_config_id: str | None):
  model_config = resolve_model_config(model_config_id)
  if hasattr(agent, "model"):
    try:
      agent.model = model_config.model
    except Exception:
      pass
  if hasattr(agent, "generate_content_config"):
    try:
      agent.generate_content_config = model_config.generate_content_config
    except Exception:
      pass
  return model_config


def _event_metadata(event: Any) -> dict[str, Any]:
  fields = (
      "id",
      "invocation_id",
      "author",
      "model_version",
      "usage_metadata",
      "finish_reason",
      "error_code",
      "error_message",
  )
  return {
      field: _get(event, field)
      for field in fields
      if _get(event, field) is not None
  }


def _json_summary(value: Any) -> str:
  return json.dumps(_jsonable(value), ensure_ascii=True, sort_keys=True)


def _jsonable(value: Any) -> Any:
  if value is None or isinstance(value, (str, int, float, bool)):
    return value
  if isinstance(value, dict):
    return {str(key): _jsonable(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_jsonable(item) for item in value]
  if hasattr(value, "model_dump"):
    try:
      return _jsonable(value.model_dump(mode="json", by_alias=True))
    except TypeError:
      return _jsonable(value.model_dump())
  if hasattr(value, "__dict__"):
    return _jsonable(vars(value))
  return str(value)


def _get(value: Any, name: str) -> Any:
  if value is None:
    return None
  if isinstance(value, dict):
    return value.get(name)
  return getattr(value, name, None)


def _event_text(event: Any) -> str:
  content = getattr(event, "content", None)
  parts = getattr(content, "parts", None) if content else None
  if not parts:
    return ""
  return "\n".join(
      str(getattr(part, "text", ""))
      for part in parts
      if getattr(part, "text", "")
  )
