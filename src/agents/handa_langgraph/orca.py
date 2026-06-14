from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Annotated
from typing import Any
from typing import TypedDict

import aiosqlite
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.types import Command
from langgraph.types import interrupt

from ...config import load_agent_config_from_path
from ...instructions import render_instruction
from ...message_parts import build_message_parts
from ...model_configs import resolve_model_config
from ...model_configs import validate_model_config_id
from ...project_instructions import append_project_agents_instruction
from ...run_outcome import RunOutcome
from ...runner import DEFAULT_USER_ID
from ...runtime import project_context
from ...storage import HandaSessionService
from ...storage.paths import langgraph_checkpoints_path
from ...tools.user_input import build_pending_request
from ...tools.user_input import PENDING_USER_INPUT_STATE_KEY
from ...tools.user_input import USER_INPUT_TOOL_NAME
from ...tools.user_input import validate_questions
from ..skill_prompt import render_skill_instructions
from ..subagent_prompt import render_subagent_instructions
from .loader import AgentEventEmitter
from .tools import Toolset
from .tools import build_session_context
from .tools import build_toolset


load_dotenv()
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
  os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

CONFIG = load_agent_config_from_path(Path(__file__).with_name("orca.agent.json"))

# Upper bound on tool-call rounds before the agent is forced to answer in text.
MAX_TOOL_ROUNDS = 24
DEFAULT_MAX_OUTPUT_TOKENS = 8192


@asynccontextmanager
async def _open_checkpointer():
  path = langgraph_checkpoints_path()
  path.parent.mkdir(parents=True, exist_ok=True)
  async with aiosqlite.connect(str(path)) as conn:
    yield AsyncSqliteSaver(
        conn,
        serde=JsonPlusSerializer(
            # Graph state stores Gemini history; Content covers its nested parts.
            allowed_msgpack_modules=[("google.genai.types", "Content")],
        ),
    )


def _append_history(
    left: list[types.Content],
    right: list[types.Content],
) -> list[types.Content]:
  return [*left, *right]


class MainState(TypedDict, total=False):
  # `history` accumulates the full Gemini conversation: user prompt, model
  # turns (text and/or function calls), and function responses. The reducer
  # appends node outputs instead of overwriting.
  history: Annotated[list[types.Content], _append_history]
  rounds: int
  final_text: str


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
  root = _require_project_root(project_root)
  resolved_session_id = (session_id or _fallback_session_id(root)).strip()
  resolved_user_id = (user_id or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
  api_key = _api_key()
  if not api_key:
    raise RuntimeError("Gemini API key is required for Orca.")

  await emit_event(_event("langgraph.started", "Orca started"))

  resolved_model_config_id = validate_model_config_id(
      model_config_id or CONFIG.model_config_id
  )
  runtime_model_config = resolve_model_config(resolved_model_config_id)
  client = genai.Client(api_key=api_key)
  session_context = build_session_context(
      session_id=resolved_session_id,
      user_id=resolved_user_id,
      model_config_id=resolved_model_config_id,
  )
  # Child agent runs must not pause on user input; only the root session
  # exposes the request_user_input tool.
  tool_names = list(CONFIG.tools)
  if session_context.agent_run_depth > 0 and USER_INPUT_TOOL_NAME in tool_names:
    tool_names.remove(USER_INPUT_TOOL_NAME)
  toolset = build_toolset(tool_names, session_context)
  base_config = _base_generate_config(runtime_model_config.generate_content_config)
  base_config.system_instruction = _build_instruction(root)
  genai_tool = toolset.as_genai_tool(client)

  async with _open_checkpointer() as checkpointer:
    graph = _build_graph(
        client=client,
        model=runtime_model_config.model,
        base_config=base_config,
        genai_tool=genai_tool,
        toolset=toolset,
        emit_event=emit_event,
        root=root,
        checkpointer=checkpointer,
    )
    if resume_user_input is not None:
      graph_input: Any = Command(resume=resume_user_input, update={"final_text": ""})
    else:
      initial_message = types.Content(
          role="user",
          parts=build_message_parts(_compose_user_text(prompt, context), attachments),
      )
      graph_input = {"history": [initial_message], "rounds": 0, "final_text": ""}
    invoke_config = {
        # LangGraph names its checkpoint key `thread_id`; Handa maps the
        # product session id to that external protocol field at this boundary.
        "configurable": {"thread_id": resolved_session_id},
        "recursion_limit": MAX_TOOL_ROUNDS * 2 + 5,
    }
    state = await graph.ainvoke(graph_input, invoke_config)
    checkpoint_id = await _latest_checkpoint_id(graph, invoke_config)

  if checkpoint_id:
    # Turn-boundary marker: session rewrite/fork use it to roll back or copy
    # the checkpoint thread in step with the visible history.
    await emit_event(
        _event(
            "langgraph.checkpoint",
            "checkpoint boundary",
            {"checkpoint_id": checkpoint_id},
        )
    )

  pending = _pending_user_input(state)
  if pending is not None:
    HandaSessionService().merge_state_sync(
        resolved_session_id,
        {PENDING_USER_INPUT_STATE_KEY: pending},
    )
    await emit_event(
        _event(
            "langgraph.user_input_requested",
            "Orca is waiting for user input",
            {"pending_user_input": pending},
        )
    )
    return RunOutcome(pending_user_input=pending)

  final_text = str(state.get("final_text") or "").strip() or "(Orca produced no text response)"
  await emit_event(
      _event(
          "agent_text",
          "Orca response",
          {
              "text": final_text,
              "final": True,
              "model": runtime_model_config.model,
          },
      )
  )
  return RunOutcome(final_text=final_text)


async def _latest_checkpoint_id(graph: Any, invoke_config: dict[str, Any]) -> str | None:
  try:
    snapshot = await graph.aget_state(invoke_config)
  except Exception:  # noqa: BLE001 - the marker is best-effort bookkeeping.
    return None
  config = getattr(snapshot, "config", None) or {}
  configurable = config.get("configurable") if isinstance(config, dict) else {}
  value = (configurable or {}).get("checkpoint_id")
  text = str(value or "").strip()
  return text or None


def _pending_user_input(state: dict[str, Any]) -> dict[str, Any] | None:
  for item in state.get("__interrupt__") or []:
    value = getattr(item, "value", None)
    if isinstance(value, dict) and value.get("tool_name") == USER_INPUT_TOOL_NAME:
      return value
  return None


def _build_graph(
    *,
    client: Any,
    model: str,
    base_config: types.GenerateContentConfig,
    genai_tool: types.Tool,
    toolset: Toolset,
    emit_event: AgentEventEmitter,
    root: Path,
    checkpointer: Any,
):
  graph = StateGraph(MainState)
  graph.add_node(
      "call_model",
      _node_call_model(
          client=client,
          model=model,
          base_config=base_config,
          genai_tool=genai_tool,
          emit_event=emit_event,
      ),
  )
  graph.add_node(
      "execute_tools",
      _node_execute_tools(toolset=toolset, emit_event=emit_event, root=root),
  )
  graph.add_edge(START, "call_model")
  graph.add_conditional_edges(
      "call_model",
      _route_after_model,
      {"execute_tools": "execute_tools", END: END},
  )
  graph.add_edge("execute_tools", "call_model")
  return graph.compile(checkpointer=checkpointer)


def _node_call_model(
    *,
    client: Any,
    model: str,
    base_config: types.GenerateContentConfig,
    genai_tool: types.Tool,
    emit_event: AgentEventEmitter,
):

  async def call_model(state: MainState) -> MainState:
    rounds = int(state.get("rounds", 0))
    tools_enabled = rounds < MAX_TOOL_ROUNDS
    config = base_config.model_copy(deep=True)
    if tools_enabled:
      config.tools = [genai_tool]
      # We drive the tool loop ourselves; never let the SDK auto-execute.
      config.automatic_function_calling = types.AutomaticFunctionCallingConfig(
          disable=True
      )
    response = await _generate_model_response(
        client=client,
        model=model,
        contents=list(state.get("history", [])),
        config=config,
    )
    content = _response_content(response)
    calls = _function_calls(content)
    text = _content_text(content)
    usage_metadata = _usage_payload(response)
    if not tools_enabled and calls:
      final_text = _tool_round_limit_text(rounds)
      await emit_event(
          _event(
              "langgraph.tool_round_limit",
              "Orca stopped after tool round limit",
              {
                  "max_tool_rounds": MAX_TOOL_ROUNDS,
                  "rounds": rounds,
                  "requested_tools": [
                      str(call.name or "") for call in calls if call.name
                  ],
              },
          )
      )
      return {
          "history": [
              types.Content(role="model", parts=[types.Part(text=final_text)])
          ],
          "final_text": final_text,
      }
    if text:
      payload: dict[str, Any] = {"text": text, "has_tool_calls": bool(calls)}
      if usage_metadata:
        payload["usage_metadata"] = usage_metadata
      await emit_event(
          _event(
              "langgraph.model_text",
              "Orca step",
              payload,
          )
      )
    update: MainState = {"history": [content]}
    if not calls and text:
      update["final_text"] = text
    return update

  return call_model


def _node_execute_tools(
    *,
    toolset: Toolset,
    emit_event: AgentEventEmitter,
    root: Path,
):

  async def execute_tools(state: MainState) -> MainState:
    history = state.get("history", [])
    last = history[-1] if history else None
    function_calls = _function_calls(last) if last is not None else []

    # request_user_input pauses the graph via interrupt(). It must run before
    # any other tool in this round: interrupt() raises on the first pass and
    # the whole node re-executes on resume, so dispatching side-effect tools
    # first would execute them twice.
    user_input_response = _prepare_user_input_response(function_calls)
    if user_input_response is not None and "pending" in user_input_response:
      answers = interrupt(user_input_response["pending"])
      user_input_response = {"response": {"ok": True, **dict(answers or {})}}
      await emit_event(
          _event(
              "langgraph.user_input_result",
              "user answered request_user_input",
              {"name": USER_INPUT_TOOL_NAME, "result": _jsonable(user_input_response["response"])},
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
        call_id = f"lg_call_{uuid.uuid4().hex[:12]}"
        await emit_event(
            _event(
                "langgraph.tool_call",
                f"call {name}",
                {"call_id": call_id, "name": name, "args": args},
            )
        )
        result = await toolset.dispatch(name, args)
        await emit_event(
            _event(
                "langgraph.tool_result",
                f"{name} -> ok={result.get('ok')}",
                {
                    "call_id": call_id,
                    "name": name,
                    "ok": bool(result.get("ok")),
                    "result": _jsonable(result),
                },
            )
        )
        response_parts.append(
            types.Part.from_function_response(name=name, response=result)
        )
    return {
        "history": [types.Content(role="user", parts=response_parts)],
        "rounds": int(state.get("rounds", 0)) + 1,
    }

  return execute_tools


def _prepare_user_input_response(
    function_calls: list[Any],
) -> dict[str, Any] | None:
  """Inspect this round's request_user_input call, if any.

  Returns None when the round has no request_user_input call. Otherwise
  returns either `{"pending": ...}` (valid request, the caller must
  interrupt) or `{"response": ...}` (validation failed, respond with the
  error so the model can fix its arguments).
  """
  for function_call in function_calls:
    if (function_call.name or "") != USER_INPUT_TOOL_NAME:
      continue
    args = dict(function_call.args or {})
    try:
      questions = validate_questions(_jsonable(args.get("questions")))
    except ValueError as exc:
      return {"response": _tool_error_payload(USER_INPUT_TOOL_NAME, str(exc))}
    return {
        "pending": build_pending_request(runtime="langgraph", questions=questions)
    }
  return None


def _tool_error_payload(name: str, message: str) -> dict[str, Any]:
  return {
      "ok": False,
      "error": {"type": "ValueError", "message": message, "tool": name},
  }


def _route_after_model(state: MainState) -> str:
  if state.get("final_text"):
    return END
  history = state.get("history", [])
  last = history[-1] if history else None
  if last is not None and _function_calls(last):
    return "execute_tools"
  return END


def _tool_round_limit_text(rounds: int) -> str:
  round_label = "tool round" if rounds == 1 else "tool rounds"
  return (
      f"Stopped after {rounds} {round_label} without a final answer. "
      "The model kept requesting tools after Handa's tool budget was exhausted, "
      "so Handa ended the run before it could hit the LangGraph recursion limit. "
      "Please narrow the task or continue with a more specific instruction."
  )


async def _generate_model_response(
    *,
    client: Any,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
) -> Any:
  return await client.aio.models.generate_content(
      model=model,
      contents=contents,
      config=config,
  )


def _build_instruction(project_root: Path) -> str:
  instruction = render_instruction(
      section_names=CONFIG.instruction_sections,
      params={
          "agent_name": CONFIG.name.upper(),
          "project_name": "handa",
      },
  )
  skill_instruction = render_skill_instructions(CONFIG.skills)
  if skill_instruction:
    instruction = f"{instruction}\n\n{skill_instruction}"
  subagent_instruction = render_subagent_instructions(CONFIG.subagents)
  if subagent_instruction:
    instruction = f"{instruction}\n\n{subagent_instruction}"
  if CONFIG.custom_instruction and CONFIG.custom_instruction.strip():
    instruction = f"{instruction}\n\n{CONFIG.custom_instruction.strip()}"
  return append_project_agents_instruction(instruction, project_root)


def _base_generate_config(
    source: types.GenerateContentConfig | None,
) -> types.GenerateContentConfig:
  config = source.model_copy(deep=True) if source else types.GenerateContentConfig()
  if config.max_output_tokens is None:
    config.max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
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


def _require_project_root(project_root: str | Path | None) -> Path:
  if project_root is None or not str(project_root).strip():
    raise RuntimeError("project_root is required for Orca.")
  root = Path(project_root).expanduser().resolve()
  if not root.is_dir():
    raise RuntimeError(f"project_root does not exist: {root}")
  return root


def _fallback_session_id(root: Path) -> str:
  digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
  return f"orca_{digest}"


def _api_key() -> str | None:
  configured = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
  return configured.strip() or None


def _jsonable(value: Any) -> Any:
  return json.loads(json.dumps(value, ensure_ascii=True, default=str))


def _event(
    kind: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
  return {
      "id": f"lg_{uuid.uuid4().hex[:12]}",
      "kind": kind,
      "summary": summary,
      "payload": payload or {},
  }
