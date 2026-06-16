from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING
from typing import Any

from ..contract.introspection import read_tool_definitions
from ..contract.product import AgentConfig
from ..contract.product import agent_config_artifact_filename
from ..contract.product import BROWSER_MAIN_CONFIG_PATH
from ..contract.product import ORCA_MAIN_CONFIG_PATH
from ..contract.product import RALPH_MAIN_CONFIG_PATH
from ..contract.product import load_agent_config_from_path
from ..contract.product import render_instruction
from ..contract.product import render_project_agents_instruction
from ..contract.product import render_skill_instructions
from ..contract.run_events import extract_event_facts
from ..contract.services import APP_NAME
from ..contract.storage import RuntimeEventStore

if TYPE_CHECKING:
  from .context import WebApiContext
  from ..storage.session_service import Session


BREAKDOWN_CATEGORIES = (
    ("instruction", "Instruction"),
    ("system_tools", "System tools"),
    ("user_messages", "User Messages"),
    ("tool_call_responses", "Tool Call Responses"),
    ("llm_responses", "LLM Responses"),
    ("skills", "Skills"),
)
INSTRUCTION_CHILD_CATEGORIES = (
    ("system_instruction", "System"),
    ("project_config", "Project"),
)
INSTRUCTION_SOURCE_KEYS = tuple(key for key, _ in INSTRUCTION_CHILD_CATEGORIES)
LLM_RESPONSE_CHILD_CATEGORIES = (
    ("llm_response_thought", "Thought"),
    ("llm_response_text", "Text"),
    ("llm_response_tool_call_request", "Tool Call Request"),
)
LLM_RESPONSE_SOURCE_KEYS = tuple(key for key, _ in LLM_RESPONSE_CHILD_CATEGORIES)
STATIC_SOURCE_KEYS = ("instruction", "system_tools", "skills")
RESIDUAL_SOURCE_KEYS = ("user_messages", "tool_call_responses", "llm_responses")
# Tool payloads are JSON: quotes, braces, and repeated keys tokenize denser
# than prose, so they get fewer chars per token than the prose default.
DENSE_JSON_SOURCE_KEYS = frozenset({"llm_response_tool_call_request", "tool_call_responses"})
PROSE_CHARS_PER_TOKEN = 4.0
JSON_CHARS_PER_TOKEN = 3.5
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


async def build_context_usage_breakdown(
    ctx: WebApiContext,
    session: Session,
    *,
    task: dict[str, Any] | None,
    agent_id: str,
    agent_runtime: str,
    project_root: str | None,
    prompt: str | None,
    target_token_count: int,
) -> list[dict[str, Any]]:
  config = await _load_agent_config(
      ctx,
      task=task,
      agent_id=agent_id,
      agent_runtime=agent_runtime,
  )
  sources = {
      "user_messages": _user_messages_text(ctx, session, task=task, prompt=prompt),
      "tool_call_responses": _tool_call_response_text(ctx, session, agent_runtime),
      "llm_response_text": _llm_response_text(ctx, session, agent_runtime),
      "llm_response_thought": _llm_response_thought_tokens(ctx, session, agent_runtime),
      "llm_response_tool_call_request": _tool_call_request_text(ctx, session, agent_runtime),
      "system_instruction": _safe_source(lambda: _prompt_text(config)),
      "system_tools": _safe_source(lambda: _tools_text(config)),
      "skills": _safe_source(lambda: _skills_text(config)),
      "project_config": _safe_source(lambda: _project_config_text(project_root)),
  }
  return estimate_context_usage_breakdown(sources, target_token_count)


def build_static_context_usage_breakdown(
    *,
    agent_id: str,
    agent_runtime: str,
    project_root: str | None,
) -> list[dict[str, Any]]:
  """Estimate the context an agent occupies before any conversation happens.

  Used by the new-chat view to preview the static prompt: system instruction,
  tool definitions, skills, and project config. Counts are raw estimates
  (no runtime token total exists yet to scale against).
  """
  config = load_agent_config_for_runtime(agent_id=agent_id, agent_runtime=agent_runtime)
  sources = {
      "system_instruction": _safe_source(lambda: _prompt_text(config)),
      "system_tools": _safe_source(lambda: _tools_text(config)),
      "skills": _safe_source(lambda: _skills_text(config)),
      "project_config": _safe_source(lambda: _project_config_text(project_root)),
  }
  return estimate_context_usage_breakdown(sources, 0)


def estimate_context_usage_breakdown(
    sources: dict[str, Any],
    target_token_count: int = 0,
) -> list[dict[str, Any]]:
  instruction_raw_counts = {
      key: _source_token_estimate(sources, key)
      for key in INSTRUCTION_SOURCE_KEYS
  }
  llm_response_raw_counts = {
      key: _source_token_estimate(sources, key)
      for key in LLM_RESPONSE_SOURCE_KEYS
  }
  raw_counts = {
      key: _source_token_estimate(sources, key)
      for key, _ in BREAKDOWN_CATEGORIES
  }
  raw_counts["instruction"] = sum(instruction_raw_counts.values())
  raw_counts["llm_responses"] = sum(llm_response_raw_counts.values())
  # Thought counts come from API usage metadata, not character estimates, so
  # they must survive residual scaling unchanged.
  exact_thought_tokens = llm_response_raw_counts.get("llm_response_thought", 0)
  counts = _source_counts(
      raw_counts,
      target_token_count,
      exact_llm_tokens=exact_thought_tokens,
  )
  total = max(0, int(target_token_count or 0)) or sum(counts.values())
  child_counts = _instruction_child_counts(
      raw_counts=instruction_raw_counts,
      instruction_token_count=counts["instruction"],
  )
  llm_response_child_counts = _llm_response_child_counts(
      raw_counts=llm_response_raw_counts,
      llm_response_token_count=counts["llm_responses"],
      exact_thought_tokens=exact_thought_tokens,
  )
  items: list[dict[str, Any]] = []
  for key, label in BREAKDOWN_CATEGORIES:
    item = {
        "id": key,
        "label": label,
        "token_count": counts[key],
        "percent": round((counts[key] / total) * 100, 1) if total > 0 else 0.0,
    }
    if key == "instruction":
      item["children"] = [
          {
              "id": child_key,
              "label": child_label,
              "token_count": child_counts[child_key],
              "percent": (
                  round((child_counts[child_key] / total) * 100, 1)
                  if total > 0
                  else 0.0
              ),
          }
          for child_key, child_label in INSTRUCTION_CHILD_CATEGORIES
          if child_counts[child_key] > 0
      ]
    elif key == "llm_responses":
      item["children"] = [
          {
              "id": child_key,
              "label": child_label,
              "token_count": llm_response_child_counts[child_key],
              "percent": (
                  round((llm_response_child_counts[child_key] / total) * 100, 1)
                  if total > 0
                  else 0.0
              ),
          }
          for child_key, child_label in LLM_RESPONSE_CHILD_CATEGORIES
          if llm_response_child_counts[child_key] > 0
      ]
    items.append(item)
  return items


def _instruction_child_counts(
    *,
    raw_counts: dict[str, int],
    instruction_token_count: int,
) -> dict[str, int]:
  target = max(0, int(instruction_token_count or 0))
  raw_values = [raw_counts.get(key, 0) for key in INSTRUCTION_SOURCE_KEYS]
  if target <= 0:
    return dict(zip(INSTRUCTION_SOURCE_KEYS, [0] * len(INSTRUCTION_SOURCE_KEYS), strict=True))
  if sum(raw_values) == target:
    return {key: raw_counts.get(key, 0) for key in INSTRUCTION_SOURCE_KEYS}
  scaled = _scale_counts(raw_values, target)
  return dict(zip(INSTRUCTION_SOURCE_KEYS, scaled, strict=True))


def _llm_response_child_counts(
    *,
    raw_counts: dict[str, int],
    llm_response_token_count: int,
    exact_thought_tokens: int = 0,
) -> dict[str, int]:
  target = max(0, int(llm_response_token_count or 0))
  raw_values = [raw_counts.get(key, 0) for key in LLM_RESPONSE_SOURCE_KEYS]
  if target <= 0:
    return dict(zip(LLM_RESPONSE_SOURCE_KEYS, [0] * len(LLM_RESPONSE_SOURCE_KEYS), strict=True))
  if sum(raw_values) == target:
    return {key: raw_counts.get(key, 0) for key in LLM_RESPONSE_SOURCE_KEYS}
  thought = min(max(0, int(exact_thought_tokens or 0)), target)
  estimated_keys = [key for key in LLM_RESPONSE_SOURCE_KEYS if key != "llm_response_thought"]
  scaled = _scale_counts(
      [raw_counts.get(key, 0) for key in estimated_keys],
      target - thought,
  )
  result = dict(zip(estimated_keys, scaled, strict=True))
  result["llm_response_thought"] = thought
  return {key: result.get(key, 0) for key in LLM_RESPONSE_SOURCE_KEYS}


def _source_counts(
    raw_counts: dict[str, int],
    target_token_count: int,
    exact_llm_tokens: int = 0,
) -> dict[str, int]:
  target = max(0, int(target_token_count or 0))
  if target <= 0:
    return {key: raw_counts.get(key, 0) for key, _ in BREAKDOWN_CATEGORIES}

  static_total = sum(raw_counts.get(key, 0) for key in STATIC_SOURCE_KEYS)
  if static_total <= target:
    counts = {key: raw_counts.get(key, 0) for key in STATIC_SOURCE_KEYS}
    # Exact counts (API usage metadata) keep their value; only the
    # character-estimated residual sources stretch to fill what's left.
    exact = min(max(0, int(exact_llm_tokens or 0)), target - static_total)
    residual_raw = [raw_counts.get(key, 0) for key in RESIDUAL_SOURCE_KEYS]
    llm_index = RESIDUAL_SOURCE_KEYS.index("llm_responses")
    residual_raw[llm_index] = max(0, residual_raw[llm_index] - exact)
    residual_counts = _scale_counts(residual_raw, target - static_total - exact)
    counts.update(dict(zip(RESIDUAL_SOURCE_KEYS, residual_counts, strict=True)))
    counts["llm_responses"] += exact
    return {key: counts.get(key, 0) for key, _ in BREAKDOWN_CATEGORIES}

  scaled_static = _scale_counts(
      [raw_counts.get(key, 0) for key in STATIC_SOURCE_KEYS],
      target,
  )
  counts = dict(zip(STATIC_SOURCE_KEYS, scaled_static, strict=True))
  counts.update({key: 0 for key in RESIDUAL_SOURCE_KEYS})
  return {key: counts.get(key, 0) for key, _ in BREAKDOWN_CATEGORIES}


async def _load_agent_config(
    ctx: WebApiContext,
    *,
    task: dict[str, Any] | None,
    agent_id: str,
    agent_runtime: str,
) -> AgentConfig | None:
  if task and task.get("kind") == "system_agent_run" and isinstance(task.get("config"), dict):
    return AgentConfig.model_validate(task["config"])
  if task and task.get("kind") == "agent_run":
    config = await _load_agent_run_config(ctx, task)
    if config is not None:
      return config
  return load_agent_config_for_runtime(agent_id=agent_id, agent_runtime=agent_runtime)


def load_agent_config_for_runtime(
    *,
    agent_id: str,
    agent_runtime: str,
) -> AgentConfig | None:
  if agent_runtime == "native" and agent_id == "orca":
    return load_agent_config_from_path(ORCA_MAIN_CONFIG_PATH)
  if agent_runtime == "native" and agent_id == "browser":
    return load_agent_config_from_path(BROWSER_MAIN_CONFIG_PATH)
  if agent_runtime == "native" and agent_id == "ralph":
    return load_agent_config_from_path(RALPH_MAIN_CONFIG_PATH)
  return None


async def _load_agent_run_config(
    ctx: WebApiContext,
    task: dict[str, Any],
) -> AgentConfig | None:
  config_name = str(task.get("config_name") or "").strip()
  parent_session_id = str(task.get("session_id") or "").strip()
  if not config_name or not parent_session_id:
    return None
  artifact = await ctx.services.artifact_service.load_artifact(
      app_name=APP_NAME,
      user_id=str(task.get("user_id") or ctx.settings.user_id),
      session_id=parent_session_id,
      filename=agent_config_artifact_filename(config_name),
      version=task.get("config_version"),
  )
  if artifact is None or artifact.text is None:
    return None
  return AgentConfig.model_validate_json(artifact.text)


def _user_messages_text(
    ctx: WebApiContext,
    session: Session,
    *,
    task: dict[str, Any] | None,
    prompt: str | None,
) -> str:
  texts: list[str] = []
  for turn in ctx.db.list_turns_for_session(session.id):
    _append_unique(texts, turn.get("input_text"))
  for event in session.events or []:
    facts = extract_event_facts(event)
    if _is_user_author(facts.author):
      _append_unique(texts, facts.text)
  _append_unique(texts, prompt)
  _append_unique(texts, (task or {}).get("context"))
  return "\n\n".join(text for text in texts if text)


def _llm_response_text(
    ctx: WebApiContext,
    session: Session,
    agent_runtime: str,
) -> str:
  texts: list[str] = []
  for turn in ctx.db.list_turns_for_session(session.id):
    _append_unique(texts, turn.get("final_text"))
  for raw_event in _runtime_raw_events(ctx, session, agent_runtime):
    facts = extract_event_facts(raw_event)
    if facts.text and not _is_user_author(facts.author):
      _append_unique(texts, facts.text)
      continue
    kind = str(raw_event.get("kind") or "")
    payload = raw_event.get("payload") if isinstance(raw_event.get("payload"), dict) else {}
    if _is_runtime_kind(kind, "model_text") or kind == "agent_text":
      _append_unique(texts, payload.get("text"))
  return "\n\n".join(text for text in texts if text)


def _tool_call_request_text(
    ctx: WebApiContext,
    session: Session,
  agent_runtime: str,
) -> str:
  chunks: list[str] = []
  for raw_event in _runtime_raw_events(ctx, session, agent_runtime):
    facts = extract_event_facts(raw_event)
    for call in facts.function_calls:
      chunks.append(_tool_call_text(call.name, call.args))
    kind = str(raw_event.get("kind") or "")
    if not _is_runtime_kind(kind, "tool_call"):
      continue
    payload = raw_event.get("payload") if isinstance(raw_event.get("payload"), dict) else {}
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    chunks.append(_tool_call_text(str(payload.get("name") or ""), args))
  return "\n\n".join(chunk for chunk in chunks if chunk)


def _tool_call_response_text(
    ctx: WebApiContext,
    session: Session,
  agent_runtime: str,
) -> str:
  chunks: list[str] = []
  for raw_event in _runtime_raw_events(ctx, session, agent_runtime):
    facts = extract_event_facts(raw_event)
    for response in facts.function_responses:
      chunks.append(_tool_response_text(response.name, response.response))
    kind = str(raw_event.get("kind") or "")
    if not (_is_runtime_kind(kind, "tool_result") or _is_runtime_kind(kind, "user_input_result")):
      continue
    payload = raw_event.get("payload") if isinstance(raw_event.get("payload"), dict) else {}
    chunks.append(_tool_response_text(str(payload.get("name") or ""), payload.get("result")))
  return "\n\n".join(chunk for chunk in chunks if chunk)


def _llm_response_thought_tokens(
    ctx: WebApiContext,
    session: Session,
    agent_runtime: str,
) -> int:
  """Thoughts still occupying the live context window.

  Thought signatures replay thinking into every later request's prompt. Only
  the final response's thoughts are not in a prompt yet, because the context
  size shown is the latest request's prompt count, so they are excluded.
  """
  per_event: list[int] = []
  for raw_event in _usage_raw_events(ctx, session, agent_runtime):
    if raw_event.get("partial") is True:
      continue
    metadata = _usage_metadata(raw_event)
    if metadata is None:
      continue
    per_event.append(_int_field(metadata, "thoughts_token_count", "thoughtsTokenCount"))
  return sum(per_event) - per_event[-1] if per_event else 0


def _runtime_raw_events(
    ctx: WebApiContext,
    session: Session,
    runtime: str,
) -> list[dict[str, Any]]:
  events: list[dict[str, Any]] = []
  for item in RuntimeEventStore(ctx.settings.storage_root).list_events(
      session_id=session.id,
      runtime=runtime,
  ):
    raw_event = item.get("event")
    if isinstance(raw_event, dict):
      events.append(raw_event)
  return events


def _usage_raw_events(
    ctx: WebApiContext,
    session: Session,
    runtime: str,
) -> list[dict[str, Any]]:
  return _runtime_raw_events(ctx, session, runtime)


def _usage_metadata(raw_event: dict[str, Any]) -> dict[str, Any] | None:
  value = raw_event.get("usageMetadata") or raw_event.get("usage_metadata")
  if value is None:
    payload = raw_event.get("payload")
    if isinstance(payload, dict):
      value = payload.get("usageMetadata") or payload.get("usage_metadata")
  return value if isinstance(value, dict) else None


def _int_field(value: dict[str, Any], snake_name: str, camel_name: str) -> int:
  raw = value.get(snake_name)
  if raw is None:
    raw = value.get(camel_name)
  try:
    return max(0, int(raw or 0))
  except (TypeError, ValueError):
    return 0


def _tool_call_text(name: str, args: dict[str, Any]) -> str:
  payload = {"name": name, "args": args}
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _tool_response_text(name: str, response: Any) -> str:
  payload = {"name": name, "response": response}
  return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _is_runtime_kind(kind: str, suffix: str) -> bool:
  return kind.endswith(f".{suffix}")


def _is_user_author(author: str | None) -> bool:
  return (author or "").strip().lower() == "user"


def _prompt_text(config: AgentConfig | None) -> str:
  if config is None:
    return ""
  instruction = render_instruction(
      section_names=config.instruction_sections,
      params={
          "agent_name": config.name.upper(),
          "project_name": "handa",
      },
  )
  if config.custom_instruction and config.custom_instruction.strip():
    instruction = f"{instruction}\n\n{config.custom_instruction.strip()}"
  return instruction


def _project_config_text(project_root: str | None) -> str:
  return render_project_agents_instruction(project_root)


def _tools_text(config: AgentConfig | None) -> str:
  if config is None or not config.tools:
    return ""
  # Definition texts are exported by `python -m src.agent_introspection`
  # (refreshed at Web startup); inspecting live functions here would mean
  # importing every tool implementation into the Web process.
  definitions = read_tool_definitions()
  chunks: list[str] = []
  for tool_name in config.tools:
    chunks.append(definitions.get(tool_name) or tool_name)
  return "\n\n".join(chunks)


def _skills_text(config: AgentConfig | None) -> str:
  if config is None or not config.skills:
    return ""
  return render_skill_instructions(config.skills)


def _scale_counts(raw_counts: list[int], target_token_count: int) -> list[int]:
  target = max(0, int(target_token_count or 0))
  if target <= 0:
    return raw_counts
  raw_total = sum(raw_counts)
  if raw_total <= 0:
    return [target, *([0] * (len(raw_counts) - 1))]
  scaled = [(count * target) / raw_total for count in raw_counts]
  counts = [math.floor(value) for value in scaled]
  remainder = target - sum(counts)
  order = sorted(
      range(len(scaled)),
      key=lambda index: scaled[index] - counts[index],
      reverse=True,
  )
  for index in order[:remainder]:
    counts[index] += 1
  return counts


def _estimate_tokens(text: str, *, chars_per_token: float = PROSE_CHARS_PER_TOKEN) -> int:
  normalized = _clean_text(text)
  if not normalized:
    return 0
  cjk_chars = len(_CJK_RE.findall(normalized))
  ascii_chars = len(normalized) - cjk_chars
  return max(1, math.ceil((ascii_chars / chars_per_token) + (cjk_chars / 1.8)))


def _source_token_estimate(sources: dict[str, Any], key: str) -> int:
  value = sources.get(key, "")
  if isinstance(value, int):
    return max(0, value)
  if isinstance(value, float):
    return max(0, int(value))
  chars_per_token = (
      JSON_CHARS_PER_TOKEN if key in DENSE_JSON_SOURCE_KEYS else PROSE_CHARS_PER_TOKEN
  )
  return _estimate_tokens(str(value), chars_per_token=chars_per_token)


def _append_unique(texts: list[str], value: Any) -> None:
  text = _clean_text(value)
  if text and text not in texts:
    texts.append(text)


def _clean_text(value: Any) -> str:
  if value is None:
    return ""
  return str(value).strip()


def _safe_source(build: Any) -> str:
  try:
    return build()
  except Exception:  # noqa: BLE001 - usage estimates must not break session detail.
    return ""
