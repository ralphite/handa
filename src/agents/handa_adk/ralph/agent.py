from __future__ import annotations

from pathlib import Path
import json
import os
import re
import textwrap
from typing import Any
from typing import AsyncGenerator
from typing import cast

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ....config import load_agent_config_from_path
from ....config import resolve_agent_config_model_config_id
from ....model_configs import resolve_model_config
from ....project_instructions import render_project_agents_instruction
from ....runner import APP_NAME
from ....runner import DEFAULT_USER_ID
from .loop import AgentVerifierNode
from .loop import DEFAULT_RALPH_MAX_ROUNDS
from .loop import RalphLoopRunner
from .loop import SystemAgentConfigNode
from .loop import VERIFIER_INDEPENDENCE_GUARD
from .loop import format_report


load_dotenv()
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
  os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_BUILDER_CONFIG = load_agent_config_from_path(
    CONFIG_DIR / "ralph_builder.agent.json"
)
DEFAULT_VERIFIER_CONFIG = load_agent_config_from_path(
    CONFIG_DIR / "ralph_verifier.agent.json"
)
DEFAULT_PLANNER_CONFIG = load_agent_config_from_path(
    CONFIG_DIR / "ralph_planner.agent.json"
)

# Chinese phrases here are user-input compatibility matchers, not prompt text.
_RALPH_STRONG_CONTROL_PATTERNS = (
    "第一轮只生成",
    "首轮只生成",
    "只生成 ralph plan",
    "first turn only",
    "first round only",
    "等待用户确认",
    "等用户确认",
    "回复“确认”",
    "回复\"确认\"",
    "回复确认",
    "确认后才",
    "不要启动 builder",
    "不启动 builder",
    "不要启动builder",
    "不启动builder",
    "不要启动 builder/verifier",
    "不启动 builder/verifier",
    "不要启动 builder / verifier",
    "不启动 builder / verifier",
    "do not start builder",
    "don't start builder",
    "do not run builder",
    "wait for confirmation",
)
_RALPH_WEAK_CONTROL_PATTERNS = (
    "不要修改 project",
    "不修改 project",
    "不要修改project",
    "不修改project",
    "不要修改项目",
    "不修改项目",
    "do not modify project",
    "don't modify project",
)


class RalphAgent(BaseAgent):
  """Parent custom agent that runs the real Ralph builder/verifier loop."""

  builder_config_name: str = DEFAULT_BUILDER_CONFIG.name
  verifier_config_name: str = DEFAULT_VERIFIER_CONFIG.name
  max_rounds: int = DEFAULT_RALPH_MAX_ROUNDS
  planner: Any = None
  project_agents_instruction: str = ""

  async def _run_async_impl(
      self,
      ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    user_text = _content_text(ctx.user_content)
    session = ctx.session
    user_id = session.user_id or DEFAULT_USER_ID

    if _is_confirmation(user_text) and _has_pending_plan(session.state):
      plan = await _load_pending_plan(ctx, session.id, user_id)
      session.state["ralph:plan_status"] = "confirmed"
      session.state["ralph:confirmed_plan_artifact"] = "ralph_loop.plan.json"
      result = await _run_confirmed_plan(
          ctx=ctx,
          user_id=user_id,
          plan=plan,
          max_rounds=self.max_rounds,
      )
      yield _event(ctx, format_report(result))
      return

    current_plan = None
    if _has_pending_plan(session.state):
      current_plan = await _load_pending_plan(ctx, session.id, user_id)

    plan = await _create_or_update_plan(
        ctx=ctx,
        planner=self.planner,
        user_id=user_id,
        user_text=user_text,
        current_plan=current_plan,
        max_rounds=self.max_rounds,
    )
    await _save_plan_artifacts(
        ctx=ctx,
        session_id=session.id,
        user_id=user_id,
        plan=plan,
    )
    session.state["ralph:plan_status"] = "pending_confirmation"
    session.state["ralph:pending_plan_artifact"] = "ralph_loop.plan.json"
    yield _event(ctx, _format_plan_for_confirmation(plan))


async def _run_confirmed_plan(
    *,
    ctx: InvocationContext,
    user_id: str,
    plan: dict[str, object],
    max_rounds: int,
):
  if ctx.artifact_service is None:
    raise ValueError("Artifact service is required for Ralph agent.")
  session = ctx.session

  runner = RalphLoopRunner(
      builder_node=SystemAgentConfigNode(DEFAULT_BUILDER_CONFIG),
      verifier_node=AgentVerifierNode(SystemAgentConfigNode(DEFAULT_VERIFIER_CONFIG)),  # type: ignore[arg-type]
      artifact_service=ctx.artifact_service,  # type: ignore[arg-type]
      app_name=APP_NAME,
      user_id=user_id,
      max_rounds=int(str(plan.get("max_rounds") or max_rounds)),
  )
  return await runner.run(
      goal=str(plan["goal"]),
      original_user_text=str(plan["original_user_text"]),
      task_prompt=str(plan["task_prompt"]),
      verification_prompt=str(plan["verification_prompt"]),
      builder_output_contract=str(plan["builder_output_contract"]),
      parent_session_id=session.id,
  )


async def _save_plan_artifacts(
    *,
    ctx: InvocationContext,
    session_id: str,
    user_id: str,
    plan: dict[str, object],
) -> None:
  if ctx.artifact_service is None:
    raise ValueError("Artifact service required.")
  payload = json.dumps(plan, indent=2, ensure_ascii=False)
  await ctx.artifact_service.save_artifact(
      app_name=APP_NAME,
      user_id=user_id,
      session_id=session_id,
      filename="ralph_loop.plan.json",
      artifact=types.Part.from_text(text=payload),
  )
  await ctx.artifact_service.save_artifact(
      app_name=APP_NAME,
      user_id=user_id,
      session_id=session_id,
      filename="ralph_loop.plan.md",
      artifact=types.Part.from_text(text=_format_plan_markdown(plan)),
  )


async def _load_pending_plan(
    ctx: InvocationContext,
    session_id: str,
    user_id: str,
) -> dict[str, object]:
  if ctx.artifact_service is None:
    raise ValueError("Artifact service required.")
  artifact = await ctx.artifact_service.load_artifact(
      app_name=APP_NAME,
      user_id=user_id,
      session_id=session_id,
      filename="ralph_loop.plan.json",
  )
  if artifact is None or artifact.text is None:
    raise ValueError("Ralph plan artifact not found.")
  return json.loads(artifact.text)


class RalphLoopPlanModel(BaseModel):
  model_config = ConfigDict(extra="ignore")

  goal: str = Field(min_length=1)
  original_user_text: str = Field(min_length=1)
  task_prompt: str = Field(min_length=1)
  verification_prompt: str = Field(min_length=1)
  builder_output_contract: str = Field(min_length=1)
  builder_config_ref: dict[str, str] = Field(default_factory=dict)
  verifier_config_ref: dict[str, str] = Field(default_factory=dict)
  max_rounds: int = Field(default=DEFAULT_RALPH_MAX_ROUNDS, ge=1)
  requires_user_confirmation: bool = True


class LlmRalphPlanPlanner:
  """LLM-backed planner for Ralph's user-facing plan negotiation phase."""

  def __init__(
      self,
      config=DEFAULT_PLANNER_CONFIG,
      *,
      project_agents_instruction: str = "",
  ):
    self.config = config
    self.project_agents_instruction = project_agents_instruction

  async def create_or_update_plan(
      self,
      *,
      ctx: InvocationContext,
      user_id: str,
      user_text: str,
      current_plan: dict[str, object] | None,
      max_rounds: int,
  ) -> dict[str, object]:
    model_config = resolve_model_config(
        resolve_agent_config_model_config_id(self.config)
    )
    instruction = self.config.custom_instruction or ""
    if self.project_agents_instruction:
      instruction = (
          f"{instruction.rstrip()}\n\n{self.project_agents_instruction}"
      ).strip()

    agent = Agent(
        name=self.config.name,
        model=model_config.model,
        description=self.config.description,
        instruction=instruction,
        generate_content_config=model_config.generate_content_config,
    )
    session_id = await _ensure_planner_session(ctx, user_id)
    runner = Runner(
        app_name=ctx.session.app_name or APP_NAME,
        agent=agent,
        artifact_service=ctx.artifact_service,  # type: ignore[arg-type]
        session_service=ctx.session_service,
        memory_service=ctx.memory_service,
        credential_service=ctx.credential_service,
    )
    prompt = _planner_prompt(
        user_text=user_text,
        current_plan=current_plan,
        max_rounds=max_rounds,
    )
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
      if hasattr(event, "is_final_response") and event.is_final_response():
        text = _event_text(event)
        if text:
          final_text = text
    if not final_text:
      raise ValueError("Ralph planner did not return a plan.")
    return _parse_plan_text(final_text)


async def _create_or_update_plan(
    *,
    ctx: InvocationContext,
    planner: Any,
    user_id: str,
    user_text: str,
    current_plan: dict[str, object] | None,
    max_rounds: int,
) -> dict[str, object]:
  active_planner = planner or LlmRalphPlanPlanner(
      project_agents_instruction=getattr(ctx.agent, "project_agents_instruction", "")
  )
  raw_plan = await active_planner.create_or_update_plan(
      ctx=ctx,
      user_id=user_id,
      user_text=user_text,
      current_plan=current_plan,
      max_rounds=max_rounds,
  )
  return _normalize_plan_payload(
      raw_plan,
      user_text=user_text,
      current_plan=current_plan,
      max_rounds=max_rounds,
  )


async def _ensure_planner_session(ctx: InvocationContext, user_id: str) -> str:
  app_name = ctx.session.app_name or APP_NAME
  existing_id = ctx.session.state.get("ralph:planner_session_id")
  if existing_id:
    existing = await ctx.session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=str(existing_id),
    )
    if existing is not None:
      return str(existing_id)

  session = await ctx.session_service.create_session(
      app_name=app_name,
      user_id=user_id,
      state={
          "handa:agent_id": "ralph",
          "handa:parent_session_id": ctx.session.id,
          "handa:ralph_role": "planner",
      },
  )
  ctx.session.state["ralph:planner_session_id"] = session.id
  return session.id


def _planner_prompt(
    *,
    user_text: str,
    current_plan: dict[str, object] | None,
    max_rounds: int,
) -> str:
  current_plan_text = (
      json.dumps(current_plan, indent=2, ensure_ascii=False)
      if current_plan
      else "null"
  )
  mode = "update the existing draft plan" if current_plan else "create a new draft plan"
  return textwrap.dedent(
      f"""
      Task: {mode} for user confirmation before running the Ralph builder/verifier loop.

      Latest user input:
      {user_text.strip()}

      Current draft plan (null when absent):
      {current_plan_text}

      Output requirements:
      - Output exactly one JSON object, with no Markdown and no explanation.
      - The plan must be based on the user's real goal and latest feedback. Do not use a fixed template.
      - If a current draft plan exists, preserve constraints that are still valid and modify only the parts the user asked to change.
      - If the user's input contains Ralph parent-level control instructions such as first-round-only planning, waiting for confirmation, not starting Builder/Verifier, or not modifying the project, do not copy those control instructions into task_prompt.
      - task_prompt is written for Builder and must explain what to complete in the real project.
      - verification_prompt is written for Verifier and must explain how to judge completion from real files, real commands, artifacts, and Builder output.
      - verification_prompt must make this explicit: the Verifier's final acceptance criteria are the original user request and the user-confirmed Ralph plan. Builder output, Builder artifacts, and Builder claims are only verification clues and test aids; they are not the final basis for pass/fail.
      - builder_output_contract defines the evidence Builder must provide each round. Do not invent fake implementation steps or fake results for Builder.
      - max_rounds must be an integer from 1 to {max_rounds}.
      - If the user did not explicitly request a round count, max_rounds must be {max_rounds}; do not default to 3.
      - builder_config_ref is fixed to {{"source":"system","name":"{DEFAULT_BUILDER_CONFIG.name}"}}.
      - verifier_config_ref is fixed to {{"source":"system","name":"{DEFAULT_VERIFIER_CONFIG.name}"}}.
      - requires_user_confirmation is fixed to true.

      JSON fields:
      goal, original_user_text, task_prompt, verification_prompt,
      builder_output_contract, builder_config_ref, verifier_config_ref,
      max_rounds, requires_user_confirmation
      """
  ).strip()


def _parse_plan_text(text: str) -> dict[str, object]:
  return json.loads(_extract_json_object(text))


def _extract_json_object(text: str) -> str:
  stripped = text.strip()
  fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
  if fence:
    return fence.group(1)
  start = stripped.find("{")
  end = stripped.rfind("}")
  if start == -1 or end == -1 or end < start:
    raise ValueError("Ralph planner response did not contain a JSON object.")
  return stripped[start : end + 1]


def _normalize_plan_payload(
    payload: dict[str, object],
    *,
    user_text: str,
    current_plan: dict[str, object] | None,
    max_rounds: int,
) -> dict[str, object]:
  baseline_original = (
      str(current_plan.get("original_user_text"))
      if current_plan and current_plan.get("original_user_text")
      else user_text.strip()
  )
  candidate = dict(payload)
  candidate["original_user_text"] = baseline_original
  candidate["builder_config_ref"] = {
      "source": "system",
      "name": DEFAULT_BUILDER_CONFIG.name,
  }
  candidate["verifier_config_ref"] = {
      "source": "system",
      "name": DEFAULT_VERIFIER_CONFIG.name,
  }
  candidate["requires_user_confirmation"] = True
  if _user_requested_max_rounds(user_text):
    candidate_max_rounds = int(str(candidate.get("max_rounds") or max_rounds))
  elif current_plan and current_plan.get("max_rounds"):
    candidate_max_rounds = int(str(current_plan["max_rounds"]))
  else:
    candidate_max_rounds = max_rounds
  candidate["max_rounds"] = max(1, min(candidate_max_rounds, max_rounds))

  plan = RalphLoopPlanModel.model_validate(candidate)
  result = plan.model_dump()
  result["goal"] = _strip_ralph_control_instructions(result["goal"]).strip()
  result["task_prompt"] = _strip_ralph_control_instructions(
      result["task_prompt"]
  ).strip()
  result["verification_prompt"] = _ensure_verifier_independence_guard(
      result["verification_prompt"]
  )
  if not result["goal"] or not result["task_prompt"]:
    raise ValueError("Ralph planner returned an empty goal or task_prompt.")
  return result


def _ensure_verifier_independence_guard(verification_prompt: str) -> str:
  prompt = verification_prompt.strip()
  if VERIFIER_INDEPENDENCE_GUARD in prompt:
    return prompt
  return f"{prompt}\n\nFixed acceptance principle: {VERIFIER_INDEPENDENCE_GUARD}"


def _user_requested_max_rounds(text: str) -> bool:
  cleaned = _strip_ralph_control_instructions(text).lower()
  return bool(
      re.search(
          r"(max[_ -]?rounds?|最多\s*[一二两三四五六七八九十百\d]+\s*轮|"
          r"[一二两三四五六七八九十百\d]+\s*轮|轮数|回合)",
          cleaned,
      )
  )


def _strip_ralph_control_instructions(text: str) -> str:
  stripped = text.strip()
  if not stripped:
    return ""

  normalized_text = stripped.lower()
  has_parent_control = any(
      pattern in normalized_text for pattern in _RALPH_STRONG_CONTROL_PATTERNS
  )
  if not has_parent_control:
    return stripped

  pieces = re.split(r"([。；;，,\n])", stripped)
  kept: list[str] = []
  for index in range(0, len(pieces), 2):
    fragment = pieces[index]
    separator = pieces[index + 1] if index + 1 < len(pieces) else ""
    if _is_ralph_parent_control_fragment(fragment, has_parent_control):
      continue
    kept.append(fragment + separator)

  cleaned = "".join(kept).strip()
  return cleaned or stripped


def _is_ralph_parent_control_fragment(
    fragment: str,
    has_parent_control: bool,
) -> bool:
  normalized = fragment.strip().lower()
  if not normalized:
    return False
  if any(pattern in normalized for pattern in _RALPH_STRONG_CONTROL_PATTERNS):
    return True
  return has_parent_control and any(
      pattern in normalized for pattern in _RALPH_WEAK_CONTROL_PATTERNS
  )


def _format_plan_for_confirmation(plan: dict[str, object]) -> str:
  plan_markdown = _format_plan_markdown(plan)
  return textwrap.dedent(
      f"""
      {plan_markdown}

      Please confirm whether to run the Ralph loop with this plan. Reply `confirm` to start Builder / Verifier.
      """
  ).strip()


def _format_plan_markdown(plan: dict[str, object]) -> str:
  return textwrap.dedent(
      f"""
      # Ralph Loop Plan

      ## Builder Task

      {plan["task_prompt"]}

      ## Verifier Acceptance Method

      {plan["verification_prompt"]}

      ## Builder Output Contract

      {plan["builder_output_contract"]}

      ## System Nodes

      - builder: `{DEFAULT_BUILDER_CONFIG.name}` (system)
      - verifier: `{DEFAULT_VERIFIER_CONFIG.name}` (system)
      - max_rounds: `{plan["max_rounds"]}`
      """
  ).strip()


def _has_pending_plan(state: dict) -> bool:
  return state.get("ralph:plan_status") == "pending_confirmation"


def _is_confirmation(text: str) -> bool:
  normalized = text.strip().lower()
  # Chinese phrases here keep confirmation compatible with Chinese users.
  return normalized in {
      "确认",
      "确认执行",
      "同意",
      "可以",
      "开始",
      "开始执行",
      "ok",
      "yes",
      "y",
      "go",
      "run",
      "confirm",
      "approved",
  }


def _event(ctx: InvocationContext, text: str) -> Event:
  return Event(
      invocation_id=ctx.invocation_id,
      author=ctx.agent.name if ctx.agent else "system",
      content=types.Content(
          role="model",
          parts=[types.Part(text=text)],
      ),
  )


def _event_text(event: Any) -> str:
  content = getattr(event, "content", None)
  parts = getattr(content, "parts", None) if content else None
  if not parts:
    return ""
  return "\n".join(
      getattr(part, "text", "") for part in parts if getattr(part, "text", "")
  )


def _content_text(content: types.Content | None) -> str:
  if content is None or not content.parts:
    return ""
  return "\n".join(part.text for part in content.parts if part.text)


def build_agent(*, project_root: str | None = None) -> RalphAgent:
  return RalphAgent(
      name="ralph_agent",
      description="Runs the real Ralph builder/verifier coding loop.",
      project_agents_instruction=render_project_agents_instruction(project_root),
  )
