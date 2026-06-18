from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

from ..config import AgentConfig
from ..contract.hooks import HookBlockedError
from ..contract.hooks import run_hooks
from ..config import load_agent_config_from_path
from ..instructions import render_instruction
from ..message_parts import build_message_parts
from ..model_configs import resolve_model_config
from ..model_configs import validate_model_config_id
from ..project_instructions import append_project_agents_instruction
from ..run_outcome import RunOutcome
from ..runner import DEFAULT_USER_ID
from ..runtime import project_context
from ..storage import HandaSessionService
from ..tools.user_input import build_pending_request
from ..tools.user_input import PENDING_USER_INPUT_STATE_KEY
from ..tools.user_input import USER_INPUT_TOOL_NAME
from ..tools.user_input import validate_questions
from .skill_prompt import render_skill_instructions
from .subagent_prompt import render_subagent_instructions


load_dotenv()
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
  os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

LOGGER = logging.getLogger("handa.native_runner")

# The genai SDK retries on HTTP status codes (429/5xx) but not on transport
# failures like a dropped/reset connection (httpx.ReadError/ConnectError) or a
# ReadTimeout. Those are transient too, so retry the single model call here — at
# the per-call layer, so a mid-turn drop is recovered without re-running the
# whole invocation (which is forbidden once output has streamed this turn).
MODEL_TRANSPORT_RETRY_ATTEMPTS = 3
MODEL_TRANSPORT_RETRY_BASE_DELAY_SEC = 1.0
DEFAULT_NATIVE_MAX_OUTPUT_TOKENS = 8192
CODE_AGENT_MAX_OUTPUT_TOKENS = 32768

AgentEventEmitter = Callable[[dict[str, Any]], Awaitable[None]]
BuildSessionContext = Callable[..., Any]
BuildToolset = Callable[[list[str], Any], Any]
GenerateModelResponse = Callable[..., Awaitable[Any]]


def make_native_agent_run(
    *,
    config_path: Path,
    prefix: str,
    display_name: str,
    build_session_context: BuildSessionContext,
    build_toolset: BuildToolset,
    default_max_output_tokens: int = DEFAULT_NATIVE_MAX_OUTPUT_TOKENS,
) -> Callable[..., Awaitable[RunOutcome]]:
  """Build a registered agent's ``run`` entrypoint from a config file + prefix.

  All per-agent event and session-state keys are derived from ``prefix`` so each
  agent keeps its own persisted history namespace (e.g. ``handa:orca_history``).
  """
  config = load_agent_config_from_path(config_path)

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
      emit_final_agent_text: bool = True,
  ) -> RunOutcome:
    return await run_native_agent(
        config=config,
        prompt=prompt,
        context=context,
        attachments=attachments,
        project_root=project_root,
        emit_event=emit_event,
        build_session_context=build_session_context,
        build_toolset=build_toolset,
        generate_model_response=generate_model_response,
        model_config_id=model_config_id,
        session_id=session_id,
        user_id=user_id,
        resume_user_input=resume_user_input,
        emit_final_agent_text=emit_final_agent_text,
        display_name=display_name,
        event_prefix=prefix,
        event_id_prefix=prefix,
        call_id_prefix=f"{prefix}_call",
        fallback_session_prefix=prefix,
        history_state_key=f"handa:{prefix}_history",
        pending_rounds_state_key=f"handa:{prefix}_pending_rounds",
        history_boundary_event_kind=f"{prefix}.history_boundary",
        default_max_output_tokens=default_max_output_tokens,
    )

  return run


async def run_native_agent(
    *,
    config: AgentConfig,
    prompt: str,
    context: str = "",
    attachments: list[dict[str, Any]] | None = None,
    project_root: str,
    emit_event: AgentEventEmitter,
    build_session_context: BuildSessionContext,
    build_toolset: BuildToolset,
    generate_model_response: GenerateModelResponse,
    model_config_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    resume_user_input: dict[str, Any] | None = None,
    emit_final_agent_text: bool = True,
    display_name: str,
    event_prefix: str,
    event_id_prefix: str,
    call_id_prefix: str,
    fallback_session_prefix: str,
    history_state_key: str,
    pending_rounds_state_key: str,
    history_boundary_event_kind: str,
    default_max_output_tokens: int = 8192,
) -> RunOutcome:
  root = _require_project_root(project_root, display_name=display_name)
  resolved_session_id = (
      session_id or _fallback_session_id(fallback_session_prefix, root)
  ).strip()
  resolved_user_id = (user_id or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
  api_key = _api_key()
  if not api_key:
    raise RuntimeError(f"Gemini API key is required for {display_name}.")

  session_service = HandaSessionService()
  await emit_event(
      _event(
          f"{event_prefix}.started",
          f"{display_name} started",
          event_id_prefix=event_id_prefix,
      )
  )

  resolved_model_config_id = validate_model_config_id(
      model_config_id or config.model_config_id
  )
  runtime_model_config = resolve_model_config(resolved_model_config_id)
  client = genai.Client(api_key=api_key)
  session_context = build_session_context(
      session_id=resolved_session_id,
      user_id=resolved_user_id,
      model_config_id=resolved_model_config_id,
      project_root=str(root),
  )
  tool_names = list(config.tools)
  if getattr(session_context, "agent_run_depth", 0) > 0 and USER_INPUT_TOOL_NAME in tool_names:
    tool_names.remove(USER_INPUT_TOOL_NAME)
  toolset = build_toolset(tool_names, session_context)
  base_config = _base_generate_config(
      runtime_model_config.generate_content_config,
      default_max_output_tokens=default_max_output_tokens,
  )
  base_config.system_instruction = _build_instruction(config, root)
  genai_tool = toolset.as_genai_tool(client)

  history = _load_history(session_service, resolved_session_id, history_state_key)
  rounds = 0
  if resume_user_input is not None:
    rounds = _coerce_int(
        session_service.read_state_sync(resolved_session_id).get(
            pending_rounds_state_key
        ),
        0,
    )
    response = dict(resume_user_input.get("response") or {})
    if not response:
      raise RuntimeError(
          f"resume_user_input requires a response payload for {display_name}"
      )
    if not history or not _function_calls(history[-1]):
      raise RuntimeError(f"No pending {display_name} tool call is available to resume")
    executed = await _execute_tools(
        content=history[-1],
        toolset=toolset,
        emit_event=emit_event,
        root=root,
        resume_user_input=response,
        hooks=config.hooks,
        hook_context_base=_hook_context_base(
            resolved_session_id=resolved_session_id,
            resolved_user_id=resolved_user_id,
            project_root=root,
            display_name=display_name,
            event_prefix=event_prefix,
            model_config_id=resolved_model_config_id,
        ),
        event_prefix=event_prefix,
        event_id_prefix=event_id_prefix,
        call_id_prefix=call_id_prefix,
    )
    history.append(types.Content(role="user", parts=executed.response_parts))
    rounds += 1
    _save_history(
        session_service,
        resolved_session_id,
        history,
        history_state_key=history_state_key,
        pending_rounds_state_key=pending_rounds_state_key,
        pending_rounds=None,
    )
  else:
    initial_message = types.Content(
        role="user",
        parts=build_message_parts(_compose_user_text(prompt, context), attachments),
    )
    history.append(initial_message)
    _save_history(
        session_service,
        resolved_session_id,
        history,
        history_state_key=history_state_key,
        pending_rounds_state_key=pending_rounds_state_key,
        pending_rounds=None,
    )

  while True:
    model_config = base_config.model_copy(deep=True)
    model_config.tools = [genai_tool]
    model_config.automatic_function_calling = types.AutomaticFunctionCallingConfig(
        disable=True
    )
    response = await generate_model_response(
        client=client,
        model=runtime_model_config.model,
        contents=history,
        config=model_config,
    )
    content = _response_content(response)
    calls = _function_calls(content)
    text = _content_text(content)
    finish_reason = _finish_reason(response)
    usage_metadata = _usage_payload(response)
    history.append(content)
    _save_history(
        session_service,
        resolved_session_id,
        history,
        history_state_key=history_state_key,
        pending_rounds_state_key=pending_rounds_state_key,
        pending_rounds=None,
    )
    if text:
      payload: dict[str, Any] = {"text": text, "has_tool_calls": bool(calls)}
      if finish_reason:
        payload["finish_reason"] = finish_reason
      if usage_metadata:
        payload["usage_metadata"] = usage_metadata
      await emit_event(
          _event(
              f"{event_prefix}.model_text",
              f"{display_name} step",
              payload,
              event_id_prefix=event_id_prefix,
          )
      )
    if not calls:
      final_text = text or f"({display_name} produced no text response)"
      if emit_final_agent_text:
        await emit_event(
            _event(
                "agent_text",
                f"{display_name} response",
                {"text": final_text, "final": True, "model": runtime_model_config.model},
                event_id_prefix=event_id_prefix,
            )
        )
      await _emit_history_boundary(
          emit_event,
          len(history),
          event_id_prefix=event_id_prefix,
          history_boundary_event_kind=history_boundary_event_kind,
      )
      return RunOutcome(final_text=final_text)

    executed = await _execute_tools(
        content=content,
        toolset=toolset,
        emit_event=emit_event,
        root=root,
        resume_user_input=None,
        hooks=config.hooks,
        hook_context_base=_hook_context_base(
            resolved_session_id=resolved_session_id,
            resolved_user_id=resolved_user_id,
            project_root=root,
            display_name=display_name,
            event_prefix=event_prefix,
            model_config_id=resolved_model_config_id,
        ),
        event_prefix=event_prefix,
        event_id_prefix=event_id_prefix,
        call_id_prefix=call_id_prefix,
    )
    if executed.pending_user_input is not None:
      session_service.merge_state_sync(
          resolved_session_id,
          {
              PENDING_USER_INPUT_STATE_KEY: executed.pending_user_input,
              pending_rounds_state_key: rounds,
          },
      )
      await emit_event(
          _event(
              f"{event_prefix}.user_input_requested",
              f"{display_name} is waiting for user input",
              {"pending_user_input": executed.pending_user_input},
              event_id_prefix=event_id_prefix,
          )
      )
      return RunOutcome(pending_user_input=executed.pending_user_input)

    history.append(types.Content(role="user", parts=executed.response_parts))
    rounds += 1
    _save_history(
        session_service,
        resolved_session_id,
        history,
        history_state_key=history_state_key,
        pending_rounds_state_key=pending_rounds_state_key,
        pending_rounds=None,
    )


class _ExecutedTools:
  def __init__(
      self,
      *,
      response_parts: list[types.Part],
      pending_user_input: dict[str, Any] | None = None,
  ) -> None:
    self.response_parts = response_parts
    self.pending_user_input = pending_user_input


async def _execute_tools(
    *,
    content: types.Content,
    toolset: Any,
    emit_event: AgentEventEmitter,
    root: Path,
    resume_user_input: dict[str, Any] | None,
    hooks: list[dict[str, Any]],
    hook_context_base: dict[str, Any],
    event_prefix: str,
    event_id_prefix: str,
    call_id_prefix: str,
) -> _ExecutedTools:
  function_calls = _function_calls(content)
  user_input_response = _prepare_user_input_response(function_calls)
  if user_input_response is not None and "pending" in user_input_response:
    if resume_user_input is None:
      return _ExecutedTools(
          response_parts=[],
          pending_user_input=user_input_response["pending"],
      )
    user_input_response = {"response": {"ok": True, **resume_user_input}}
    await emit_event(
        _event(
            f"{event_prefix}.user_input_result",
            "user answered request_user_input",
            {
                "name": USER_INPUT_TOOL_NAME,
                "result": _jsonable(user_input_response["response"]),
            },
            event_id_prefix=event_id_prefix,
        )
    )

  response_parts: list[types.Part] = []
  user_input_answered = False
  with project_context(root):
    for function_call in function_calls:
      name = function_call.name or ""
      args = dict(function_call.args or {})
      if name == USER_INPUT_TOOL_NAME:
        if not user_input_answered and user_input_response is not None:
          result = user_input_response["response"]
          user_input_answered = True
        else:
          result = _tool_error_payload(
              name,
              "only one request_user_input call is allowed per turn",
          )
        response_parts.append(
            types.Part.from_function_response(name=name, response=result)
        )
        continue
      call_id = f"{call_id_prefix}_{uuid.uuid4().hex[:12]}"
      await emit_event(
          _event(
              f"{event_prefix}.tool_call",
              f"call {name}",
              {"call_id": call_id, "name": name, "args": args},
              event_id_prefix=event_id_prefix,
          )
      )
      hook_context = {
          **hook_context_base,
          "trigger": "pre_tool",
          "tool_name": name,
          "tool_args": _jsonable(args),
          "call_id": call_id,
      }
      blocked = await _pre_tool_block(
          hooks,
          context=hook_context,
          root=root,
          emit_event=emit_event,
      )
      if blocked is not None:
        result = blocked
      else:
        result = await toolset.dispatch(name, args)
      await _post_tool_hooks(
          hooks,
          context={
              **hook_context_base,
              "trigger": "post_tool",
              "tool_name": name,
              "tool_args": _jsonable(args),
              "tool_result": _jsonable(result),
              "call_id": call_id,
              "status": "ok" if bool(result.get("ok")) else "failed",
          },
          root=root,
          emit_event=emit_event,
      )
      await emit_event(
          _event(
              f"{event_prefix}.tool_result",
              f"{name} -> ok={result.get('ok')}",
              {
                  "call_id": call_id,
                  "name": name,
                  "ok": bool(result.get("ok")),
                  "result": _jsonable(result),
              },
              event_id_prefix=event_id_prefix,
          )
      )
      response_parts.append(
          types.Part.from_function_response(name=name, response=result)
      )
  return _ExecutedTools(response_parts=response_parts)


async def _pre_tool_block(
    hooks: list[dict[str, Any]],
    *,
    context: dict[str, Any],
    root: Path,
    emit_event: AgentEventEmitter,
) -> dict[str, Any] | None:
  try:
    await run_hooks(
        hooks,
        trigger="pre_tool",
        context=context,
        project_root=root,
        emit_event=emit_event,
    )
    return None
  except HookBlockedError as exc:
    return _tool_error_payload(
        str(context.get("tool_name") or ""),
        str(exc) or f"pre_tool hook {exc.hook_id} blocked this tool call",
    )


async def _post_tool_hooks(
    hooks: list[dict[str, Any]],
    *,
    context: dict[str, Any],
    root: Path,
    emit_event: AgentEventEmitter,
) -> None:
  try:
    await run_hooks(
        hooks,
        trigger="post_tool",
        context=context,
        project_root=root,
        emit_event=emit_event,
    )
  except HookBlockedError:
    return


def _hook_context_base(
    *,
    resolved_session_id: str,
    resolved_user_id: str,
    project_root: Path,
    display_name: str,
    event_prefix: str,
    model_config_id: str,
) -> dict[str, Any]:
  return {
      "session_id": resolved_session_id,
      "user_id": resolved_user_id,
      "project_root": str(project_root),
      "agent_name": display_name,
      "agent_event_prefix": event_prefix,
      "model_config_id": model_config_id,
  }


def _prepare_user_input_response(
    function_calls: list[Any],
) -> dict[str, Any] | None:
  for function_call in function_calls:
    if (function_call.name or "") != USER_INPUT_TOOL_NAME:
      continue
    args = dict(function_call.args or {})
    try:
      questions = validate_questions(_jsonable(args.get("questions")))
    except ValueError as exc:
      return {"response": _tool_error_payload(USER_INPUT_TOOL_NAME, str(exc))}
    return {"pending": build_pending_request(runtime="native", questions=questions)}
  return None


def _tool_error_payload(name: str, message: str) -> dict[str, Any]:
  return {
      "ok": False,
      "error": {"type": "ValueError", "message": message, "tool": name},
  }


async def generate_model_response(
    *,
    client: Any,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
) -> Any:
  delay_sec = MODEL_TRANSPORT_RETRY_BASE_DELAY_SEC
  for attempt_no in range(1, MODEL_TRANSPORT_RETRY_ATTEMPTS + 1):
    try:
      return await client.aio.models.generate_content(
          model=model,
          contents=contents,
          config=config,
      )
    except httpx.TransportError as exc:
      if attempt_no >= MODEL_TRANSPORT_RETRY_ATTEMPTS:
        raise
      LOGGER.warning(
          "Retrying model call after transport error: attempt=%s "
          "next_delay_sec=%s model=%s error=%s",
          attempt_no,
          delay_sec,
          model,
          f"{type(exc).__name__}: {exc}",
      )
      await asyncio.sleep(delay_sec)
      delay_sec *= 2
  raise AssertionError(  # pragma: no cover - loop returns or raises above.
      "generate_model_response: unreachable"
  )


def _build_instruction(config: AgentConfig, project_root: Path) -> str:
  instruction = render_instruction(
      section_names=config.instruction_sections,
      params={
          "agent_name": config.name.upper(),
          "project_name": "handa",
      },
  )
  skill_instruction = render_skill_instructions(config.skills)
  if skill_instruction:
    instruction = f"{instruction}\n\n{skill_instruction}"
  subagent_instruction = render_subagent_instructions(config.subagents)
  if subagent_instruction:
    instruction = f"{instruction}\n\n{subagent_instruction}"
  if config.custom_instruction and config.custom_instruction.strip():
    instruction = f"{instruction}\n\n{config.custom_instruction.strip()}"
  return append_project_agents_instruction(instruction, project_root)


def _base_generate_config(
    source: types.GenerateContentConfig | None,
    *,
    default_max_output_tokens: int,
) -> types.GenerateContentConfig:
  config = source.model_copy(deep=True) if source else types.GenerateContentConfig()
  if config.max_output_tokens is None:
    config.max_output_tokens = default_max_output_tokens
  return config


def _compose_user_text(prompt: str, context: str) -> str:
  text = prompt.strip() or "(empty request)"
  if context and context.strip():
    text = f"{text}\n\n# Context\n{context.strip()}"
  return text


def _response_content(response: Any) -> types.Content:
  for candidate in getattr(response, "candidates", None) or []:
    content = getattr(candidate, "content", None)
    if content is not None:
      return content
  return types.Content(role="model", parts=[types.Part(text="")])


def _function_calls(content: Any) -> list[Any]:
  return [
      part.function_call
      for part in getattr(content, "parts", None) or []
      if getattr(part, "function_call", None)
  ]


def _content_text(content: Any) -> str:
  texts = [
      str(getattr(part, "text", "") or "")
      for part in getattr(content, "parts", None) or []
      if getattr(part, "text", "")
  ]
  return "\n".join(text for text in texts if text.strip()).strip()


def _usage_payload(response: Any) -> dict[str, Any] | None:
  value = getattr(response, "usage_metadata", None) or getattr(
      response,
      "usageMetadata",
      None,
  )
  if value is None:
    return None
  if isinstance(value, dict):
    jsonable = _jsonable(value)
    return jsonable if isinstance(jsonable, dict) else None
  if hasattr(value, "model_dump"):
    jsonable = _jsonable(value.model_dump(by_alias=True))
    return jsonable if isinstance(jsonable, dict) else None
  jsonable = _jsonable(value)
  return jsonable if isinstance(jsonable, dict) else None


def _finish_reason(response: Any) -> str | None:
  for candidate in getattr(response, "candidates", None) or []:
    value = getattr(candidate, "finish_reason", None) or getattr(
        candidate,
        "finishReason",
        None,
    )
    if value is None:
      continue
    if hasattr(value, "name"):
      return str(value.name)
    if hasattr(value, "value"):
      return str(value.value)
    text = str(value).strip()
    return text or None
  return None


def _load_history(
    session_service: HandaSessionService,
    session_id: str,
    history_state_key: str,
) -> list[types.Content]:
  state = session_service.read_state_sync(session_id)
  raw_history = state.get(history_state_key)
  if not isinstance(raw_history, list):
    return []
  history: list[types.Content] = []
  for item in raw_history:
    if not isinstance(item, dict):
      continue
    try:
      history.append(types.Content.model_validate(item))
    except Exception:
      continue
  return history


def _save_history(
    session_service: HandaSessionService,
    session_id: str,
    history: list[types.Content],
    *,
    history_state_key: str,
    pending_rounds_state_key: str,
    pending_rounds: int | None,
) -> None:
  updates: dict[str, Any] = {
      history_state_key: [
          item.model_dump(mode="json", exclude_none=True) for item in history
      ],
      pending_rounds_state_key: pending_rounds,
  }
  session_service.merge_state_sync(session_id, updates)


async def _emit_history_boundary(
    emit_event: AgentEventEmitter,
    history_length: int,
    *,
    event_id_prefix: str,
    history_boundary_event_kind: str,
) -> None:
  await emit_event(
      _event(
          history_boundary_event_kind,
          "history boundary",
          {"history_length": history_length},
          event_id_prefix=event_id_prefix,
      )
  )

def _require_project_root(
    project_root: str | Path | None,
    *,
    display_name: str,
) -> Path:
  if project_root is None or not str(project_root).strip():
    raise RuntimeError(f"project_root is required for {display_name}.")
  root = Path(project_root).expanduser().resolve()
  if not root.is_dir():
    raise RuntimeError(f"project_root does not exist: {root}")
  return root


def _fallback_session_id(prefix: str, root: Path) -> str:
  digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
  return f"{prefix}_{digest}"


def _api_key() -> str | None:
  configured = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
  return configured.strip() or None


def _coerce_int(value: Any, default: int) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _jsonable(value: Any) -> Any:
  return json.loads(json.dumps(value, ensure_ascii=True, default=str))


def _event(
    kind: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    *,
    event_id_prefix: str,
) -> dict[str, Any]:
  return {
      "id": f"{event_id_prefix}_{uuid.uuid4().hex[:12]}",
      "kind": kind,
      "summary": summary,
      "payload": payload or {},
  }
