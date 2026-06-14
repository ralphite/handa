from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
import inspect
import json
import textwrap
import time
from typing import Any
from typing import Protocol

from google.genai import types

from ....config import AgentConfig
from ....config import agent_config_artifact_filename
from ....runner import APP_NAME
from ....runner import DEFAULT_USER_ID
from ....runtime import get_task_status
from ....runtime import read_task_log
from ....runtime import read_task_result
from ....runtime import start_agent_run_task
from ....runtime import start_system_agent_run_task
from ....storage import HandaArtifactService


Resource = dict[str, Any]

DEFAULT_RALPH_MAX_ROUNDS = 10

RALPH_CONTROL_GUARD = (
    "Ralph parent-level control-flow instructions apply only to the initial "
    "plan-confirmation phase. After confirmation, Builder and Verifier must "
    "ignore parent-level instructions such as first-round-only planning, waiting "
    "for user confirmation, not starting Builder/Verifier, or not modifying the "
    "project. Do not treat output that only contains a plan, or output without "
    "real execution, as complete unless the user's real goal is explicitly only "
    "to generate a plan."
)

VERIFIER_INDEPENDENCE_GUARD = (
    "The Verifier's final acceptance criteria are the original user request and "
    "the user-confirmed Ralph plan. Builder output, Builder-saved artifacts, and "
    "Builder claims are only verification clues and test aids; they are not the "
    "final basis for pass/fail. Even if Builder claims completion, `done` must "
    "be false when the real code, real command results, artifacts, or actual "
    "behavior do not satisfy the original request and confirmed plan."
)


@dataclass
class NodeResult:
  text: str
  resources: list[Resource] = field(default_factory=list)
  metadata: dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return {
        "text": self.text,
        "resources": self.resources,
        "metadata": self.metadata,
    }


@dataclass
class VerificationResult:
  done: bool
  reason: str
  feedback: str = ""
  resources: list[Resource] = field(default_factory=list)
  raw_text: str = ""
  metadata: dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return {
        "done": self.done,
        "reason": self.reason,
        "feedback": self.feedback,
        "resources": self.resources,
        "raw_text": self.raw_text,
        "metadata": self.metadata,
    }


@dataclass
class RalphLoopContext:
  goal: str
  task_prompt: str
  verification_prompt: str
  builder_output_contract: str
  parent_session_id: str
  round_number: int
  history: list["RalphLoopRound"]
  artifact_service: HandaArtifactService | None = None
  app_name: str = APP_NAME
  user_id: str = DEFAULT_USER_ID
  original_user_text: str = ""


@dataclass
class RalphLoopRound:
  round_number: int
  builder: NodeResult
  verification: VerificationResult

  def to_dict(self) -> dict[str, Any]:
    return {
        "round_number": self.round_number,
        "builder": self.builder.to_dict(),
        "verification": self.verification.to_dict(),
    }


@dataclass
class RalphLoopResult:
  goal: str
  task_prompt: str
  verification_prompt: str
  builder_output_contract: str
  done: bool
  status: str
  rounds: list[RalphLoopRound]
  final_text: str
  result_artifact: str = "ralph_loop.result.json"
  report_artifact: str = "ralph_loop.report.md"
  original_user_text: str = ""

  def to_dict(self) -> dict[str, Any]:
    return {
        "goal": self.goal,
        "original_user_text": self.original_user_text,
        "task_prompt": self.task_prompt,
        "verification_prompt": self.verification_prompt,
        "builder_output_contract": self.builder_output_contract,
        "done": self.done,
        "status": self.status,
        "rounds": [round_result.to_dict() for round_result in self.rounds],
        "final_text": self.final_text,
        "result_artifact": self.result_artifact,
        "report_artifact": self.report_artifact,
    }


class BuilderNode(Protocol):
  async def run(self, context: RalphLoopContext, prompt: str) -> NodeResult:
    ...


class VerifierNode(Protocol):
  async def run(
      self,
      context: RalphLoopContext,
      prompt: str,
      builder_result: NodeResult,
  ) -> VerificationResult:
    ...


BuilderCallable = Callable[
    [RalphLoopContext, str],
    NodeResult | str | dict[str, Any] | Awaitable[NodeResult | str | dict[str, Any]],
]
VerifierCallable = Callable[
    [RalphLoopContext, str, NodeResult],
    VerificationResult
    | dict[str, Any]
    | str
    | Awaitable[VerificationResult | dict[str, Any] | str],
]


class FunctionBuilderNode:
  def __init__(self, function: BuilderCallable):
    self.function = function

  async def run(self, context: RalphLoopContext, prompt: str) -> NodeResult:
    return _normalize_node_result(await _maybe_await(self.function(context, prompt)))


class FunctionVerifierNode:
  def __init__(self, function: VerifierCallable):
    self.function = function

  async def run(
      self,
      context: RalphLoopContext,
      prompt: str,
      builder_result: NodeResult,
  ) -> VerificationResult:
    value = await _maybe_await(self.function(context, prompt, builder_result))
    return _normalize_verification_result(value)


class AgentConfigNode:
  """Runs one existing Agent Config as a child session under the parent session."""

  def __init__(
      self,
      config_name: str,
      *,
      config_version: int | None = None,
      timeout_sec: float = 180,
      poll_interval_sec: float = 0.5,
      summary: str | None = None,
  ):
    self.config_name = config_name
    self.config_version = config_version
    self.timeout_sec = timeout_sec
    self.poll_interval_sec = poll_interval_sec
    self.summary = summary

  async def run(self, context: RalphLoopContext, prompt: str) -> NodeResult:
    await self._validate_config_exists(context)
    task = start_agent_run_task(
        config_name=self.config_name,
        prompt=prompt,
        context=_history_context(context.history),
        summary=self.summary or f"Ralph loop node {self.config_name}",
        config_version=self.config_version,
        session_id=context.parent_session_id,
        user_id=context.user_id,
        app_name=context.app_name,
    )
    completed = await self._wait_for_task(task["id"], context.parent_session_id)
    result = read_task_result(task["id"], session_id=context.parent_session_id)
    if not result.get("found"):
      raise RuntimeError(f"Agent node {self.config_name} finished without result.")

    payload = result.get("result", {})
    final_text = payload.get("final_text")
    if not isinstance(final_text, str):
      final_text = json.dumps(payload, ensure_ascii=True)

    child_artifacts: list[str] = []
    if context.artifact_service is not None:
      child_artifacts = await context.artifact_service.list_artifact_keys(
          app_name=context.app_name,
          user_id=context.user_id,
          session_id=task["child_session_id"],
      )

    return NodeResult(
        text=final_text,
        resources=[
            {
                "kind": "agent_run",
                "task_id": task["id"],
                "child_session_id": task["child_session_id"],
                "config_name": self.config_name,
                "summary_artifact": completed.get("summary_artifact"),
                "child_artifacts": child_artifacts,
            }
        ],
        metadata={
            "task_id": task["id"],
            "child_session_id": task["child_session_id"],
            "status": completed.get("status"),
            "child_artifacts": child_artifacts,
        },
    )

  async def _validate_config_exists(self, context: RalphLoopContext) -> None:
    if context.artifact_service is None:
      return
    filename = agent_config_artifact_filename(self.config_name)
    artifact = await context.artifact_service.load_artifact(
        app_name=context.app_name,
        user_id=context.user_id,
        session_id=context.parent_session_id,
        filename=filename,
        version=self.config_version,
    )
    if artifact is None or artifact.text is None:
      raise ValueError(f"Agent Config artifact not found: {filename}")

  async def _wait_for_task(
      self,
      task_id: str,
      parent_session_id: str,
  ) -> dict[str, Any]:
    deadline = time.monotonic() + self.timeout_sec
    while time.monotonic() < deadline:
      task = get_task_status(task_id, session_id=parent_session_id)
      if task.get("status") in {"succeeded", "failed", "cancelled"}:
        if task["status"] != "succeeded":
          log = read_task_log(task_id, session_id=parent_session_id).get("log", "")
          raise RuntimeError(
              f"Ralph node {self.config_name} {task['status']}: {log[-1000:]}"
          )
        return task
      await asyncio.sleep(self.poll_interval_sec)
    raise TimeoutError(f"Ralph node {self.config_name} execution timed out.")


class SystemAgentConfigNode:
  """Runs an immutable system Agent Config as a child session."""

  def __init__(
      self,
      config: AgentConfig,
      *,
      timeout_sec: float = 180,
      poll_interval_sec: float = 0.5,
      summary: str | None = None,
  ):
    self.config = config
    self.config_name = config.name
    self.timeout_sec = timeout_sec
    self.poll_interval_sec = poll_interval_sec
    self.summary = summary

  async def run(self, context: RalphLoopContext, prompt: str) -> NodeResult:
    task = start_system_agent_run_task(
        config=self.config.model_dump(),
        prompt=prompt,
        context=_history_context(context.history),
        summary=self.summary or f"Ralph loop system node {self.config_name}",
        session_id=context.parent_session_id,
        user_id=context.user_id,
        app_name=context.app_name,
    )
    completed = await self._wait_for_task(task["id"], context.parent_session_id)
    result = read_task_result(task["id"], session_id=context.parent_session_id)
    if not result.get("found"):
      raise RuntimeError(f"System agent node {self.config_name} finished without result.")

    payload = result.get("result", {})
    final_text = payload.get("final_text")
    if not isinstance(final_text, str):
      final_text = json.dumps(payload, ensure_ascii=True)

    child_artifacts: list[str] = []
    if context.artifact_service is not None:
      child_artifacts = await context.artifact_service.list_artifact_keys(
          app_name=context.app_name,
          user_id=context.user_id,
          session_id=task["child_session_id"],
      )

    return NodeResult(
        text=final_text,
        resources=[
            {
                "kind": "system_agent_run",
                "task_id": task["id"],
                "child_session_id": task["child_session_id"],
                "config_name": self.config_name,
                "config_source": "system",
                "summary_artifact": completed.get("summary_artifact"),
                "child_artifacts": child_artifacts,
            }
        ],
        metadata={
            "task_id": task["id"],
            "child_session_id": task["child_session_id"],
            "status": completed.get("status"),
            "config_name": self.config_name,
            "config_source": "system",
            "child_artifacts": child_artifacts,
        },
    )

  async def _wait_for_task(
      self,
      task_id: str,
      parent_session_id: str,
  ) -> dict[str, Any]:
    deadline = time.monotonic() + self.timeout_sec
    while time.monotonic() < deadline:
      task = get_task_status(task_id, session_id=parent_session_id)
      if task.get("status") in {"succeeded", "failed", "cancelled"}:
        if task["status"] != "succeeded":
          log = read_task_log(task_id, session_id=parent_session_id).get("log", "")
          raise RuntimeError(
              f"Ralph system node {self.config_name} {task['status']}: {log[-1000:]}"
          )
        return task
      await asyncio.sleep(self.poll_interval_sec)
    raise TimeoutError(f"Ralph system node {self.config_name} execution timed out.")


class AgentVerifierNode:
  def __init__(self, agent_node: AgentConfigNode):
    self.agent_node = agent_node

  async def run(
      self,
      context: RalphLoopContext,
      prompt: str,
      builder_result: NodeResult,
  ) -> VerificationResult:
    result = await self.agent_node.run(context, prompt)
    parsed = parse_verification_result(result.text)
    parsed.resources.extend(result.resources)
    parsed.metadata.update(result.metadata)
    return parsed


class RalphLoopRunner:
  def __init__(
      self,
      *,
      builder_node: BuilderNode,
      verifier_node: VerifierNode,
      artifact_service: HandaArtifactService,
      app_name: str = APP_NAME,
      user_id: str = DEFAULT_USER_ID,
      max_rounds: int = DEFAULT_RALPH_MAX_ROUNDS,
  ):
    if max_rounds < 1:
      raise ValueError("max_rounds must be at least 1.")
    self.builder_node = builder_node
    self.verifier_node = verifier_node
    self.artifact_service = artifact_service
    self.app_name = app_name
    self.user_id = user_id
    self.max_rounds = max_rounds

  async def run(
      self,
      *,
      goal: str,
      original_user_text: str | None = None,
      task_prompt: str | None = None,
      verification_prompt: str | None = None,
      builder_output_contract: str | None = None,
      parent_session_id: str,
  ) -> RalphLoopResult:
    task_prompt = task_prompt or goal
    original_user_text = original_user_text or goal
    verification_prompt = (
        verification_prompt
        or "Judge whether the task is complete from the original goal and real verification results."
    )
    builder_output_contract = (
        builder_output_contract
        or "Each round must state what was done, which files changed, what verification ran, which artifacts were saved, and what risk remains."
    )
    history: list[RalphLoopRound] = []
    status = "max_rounds_exceeded"
    final_text = ""

    for round_number in range(1, self.max_rounds + 1):
      context = RalphLoopContext(
          goal=goal,
          task_prompt=task_prompt,
          verification_prompt=verification_prompt,
          builder_output_contract=builder_output_contract,
          parent_session_id=parent_session_id,
          round_number=round_number,
          history=history,
          artifact_service=self.artifact_service,
          app_name=self.app_name,
          user_id=self.user_id,
          original_user_text=original_user_text,
      )
      builder_prompt = _builder_prompt(context)
      builder_result = await self.builder_node.run(context, builder_prompt)
      verifier_prompt = _verification_prompt(context, builder_result)
      verification_result = await self.verifier_node.run(
          context,
          verifier_prompt,
          builder_result,
      )

      round_result = RalphLoopRound(
          round_number=round_number,
          builder=builder_result,
          verification=verification_result,
      )
      history.append(round_result)
      done = verification_result.done
      status = "completed" if done else "max_rounds_exceeded"
      final_text = builder_result.text if done else verification_result.feedback

      snapshot = RalphLoopResult(
          goal=goal,
          original_user_text=original_user_text,
          task_prompt=task_prompt,
          verification_prompt=verification_prompt,
          builder_output_contract=builder_output_contract,
          done=done,
          status=status,
          rounds=list(history),
          final_text=final_text,
      )
      await self._save_artifacts(snapshot, parent_session_id)
      if done:
        return snapshot

    return RalphLoopResult(
        goal=goal,
        original_user_text=original_user_text,
        task_prompt=task_prompt,
        verification_prompt=verification_prompt,
        builder_output_contract=builder_output_contract,
        done=False,
        status=status,
        rounds=history,
        final_text=final_text,
    )

  async def _save_artifacts(
      self,
      result: RalphLoopResult,
      parent_session_id: str,
  ) -> None:
    await self.artifact_service.save_artifact(
        app_name=self.app_name,
        user_id=self.user_id,
        session_id=parent_session_id,
        filename=result.result_artifact,
        artifact=types.Part.from_text(
            text=json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        ),
    )
    await self.artifact_service.save_artifact(
        app_name=self.app_name,
        user_id=self.user_id,
        session_id=parent_session_id,
        filename=result.report_artifact,
        artifact=types.Part.from_text(text=format_report(result)),
    )


def parse_verification_result(text: str) -> VerificationResult:
  try:
    payload = json.loads(_extract_json_object(text))
  except ValueError:
    return VerificationResult(
        done=False,
        reason="Verifier did not return valid JSON.",
        feedback=text.strip(),
        raw_text=text,
    )
  return _normalize_verification_result(payload, raw_text=text)


def format_report(result: RalphLoopResult) -> str:
  report = textwrap.dedent(
      f"""
      # Ralph Loop Report

      - status: `{result.status}`
      - done: `{str(result.done).lower()}`
      - rounds: `{len(result.rounds)}`

      ## Goal

      {result.goal}

      ## Original User Input

      {result.original_user_text or result.goal}

      ## Builder Task

      {result.task_prompt}

      ## Verifier Acceptance Method

      {result.verification_prompt}

      ## Rounds
      """
  ).strip()
  round_sections: list[str] = []
  for round_result in result.rounds:
    round_sections.append(
        textwrap.dedent(
            f"""
            ### Round {round_result.round_number}

            - verification_done: `{str(round_result.verification.done).lower()}`
            - verification_reason: {round_result.verification.reason}
            - feedback: {round_result.verification.feedback or "(none)"}

            Builder Output:

            {_truncate(round_result.builder.text, 1600)}
            """
        ).strip()
    )
  final_section = textwrap.dedent(
      f"""
      ## Final Result

      {result.final_text or "(none)"}
      """
  ).strip()
  return "\n\n".join([report, *round_sections, final_section]) + "\n"


def _builder_prompt(context: RalphLoopContext) -> str:
  feedback = context.history[-1].verification.feedback if context.history else ""
  return textwrap.dedent(
      f"""
      You are the Builder node in the Ralph loop.

      Original goal:
      {context.goal}

      Builder task for this round:
      {context.task_prompt}

      Builder output contract:
      {context.builder_output_contract}

      Current round: {context.round_number}

      Previous Verifier feedback:
      {feedback or "(none)"}

      Ralph control-flow boundary:
      {RALPH_CONTROL_GUARD}

      Advance only one clearly bounded, verifiable real change or check. Work from the real project and real tools; do not fabricate results. End by stating what this round did, what verification ran, the result, and remaining risk.
      """
  ).strip()


def _verification_prompt(context: RalphLoopContext, builder_result: NodeResult) -> str:
  builder_resources = json.dumps(
      {
          "resources": builder_result.resources,
          "metadata": builder_result.metadata,
      },
      ensure_ascii=False,
      indent=2,
  )
  return textwrap.dedent(
      f"""
      You are the Verifier node in the Ralph loop.

      Original user request:
      {context.original_user_text or context.goal}

      User-confirmed Ralph plan (the final acceptance contract):

      goal:
      {context.goal}

      task_prompt:
      {context.task_prompt}

      verification_prompt:
      {context.verification_prompt}

      builder_output_contract:
      {context.builder_output_contract}

      Current round: {context.round_number}

      Builder output:
      {builder_result.text}

      Builder actual resources (provided by the Ralph runner from real task/session records):
      {builder_resources}

      Ralph control-flow boundary:
      {RALPH_CONTROL_GUARD}

      Verifier independent acceptance principle:
      {VERIFIER_INDEPENDENCE_GUARD}

      Judge completion from the original user request, the user-confirmed Ralph plan, real code state, real verification results, and Builder output. Output only a JSON object, with no Markdown. Required fields: done, reason, feedback, resources. When incomplete, feedback must give the next Builder round the smallest executable instruction. When complete, feedback must be an empty string.
      """
  ).strip()


def _history_context(history: list[RalphLoopRound]) -> str:
  if not history:
    return ""
  round_summaries = []
  for item in history:
    round_summaries.append(
        textwrap.dedent(
            f"""
            - Round {item.round_number}:
              verification_done: {item.verification.done}
              verification_reason: {_truncate(item.verification.reason, 240)}
              feedback: {_truncate(item.verification.feedback, 400)}
            """
        ).strip()
    )
  return "Previous Ralph loop rounds:\n" + "\n".join(round_summaries)


async def _maybe_await(value: Any) -> Any:
  if inspect.isawaitable(value):
    return await value
  return value


def _normalize_node_result(value: NodeResult | str | dict[str, Any]) -> NodeResult:
  if isinstance(value, NodeResult):
    return value
  if isinstance(value, str):
    return NodeResult(text=value)
  return NodeResult(
      text=str(value.get("text", "")),
      resources=list(value.get("resources", [])),
      metadata=dict(value.get("metadata", {})),
  )


def _normalize_verification_result(
    value: VerificationResult | dict[str, Any] | str,
    *,
    raw_text: str = "",
) -> VerificationResult:
  if isinstance(value, VerificationResult):
    return value
  if isinstance(value, str):
    return parse_verification_result(value)
  return VerificationResult(
      done=_coerce_done(value.get("done", False)),
      reason=str(value.get("reason", "")),
      feedback=str(value.get("feedback", "")),
      resources=list(value.get("resources", [])),
      raw_text=raw_text,
      metadata=dict(value.get("metadata", {})),
  )


def _coerce_done(value: Any) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    normalized = value.strip().lower()
    # Chinese phrases here parse verifier output compatibility, not prompt text.
    return normalized in {
        "1",
        "true",
        "yes",
        "y",
        "done",
        "completed",
        "complete",
        "完成",
        "已完成",
        "是",
    }
  if isinstance(value, (int, float)):
    return value != 0
  return False


def _extract_json_object(text: str) -> str:
  stripped = text.strip()
  if not stripped:
    raise ValueError("empty verifier output")
  if stripped.startswith("{") and stripped.endswith("}"):
    return stripped
  start = stripped.find("{")
  end = stripped.rfind("}")
  if start < 0 or end <= start:
    raise ValueError("no JSON object found")
  return stripped[start : end + 1]


def _truncate(text: str, max_chars: int) -> str:
  if len(text) <= max_chars:
    return text
  return text[: max_chars - 3] + "..."
