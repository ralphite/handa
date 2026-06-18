from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import nullcontext
import logging
from pathlib import Path
from typing import Any
import uuid

from .agent_runtime import get_agent_definition
from .agents.native_loader import load_agent as load_native_agent
from .contract.goals import active_goal_from_state
from .contract.goals import apply_goal_to_prompt
from .contract.goals import finished_goal_state
from .contract.goals import GOAL_STATE_KEY
from .contract.goals import GOAL_STATUS_ACHIEVED
from .contract.goals import GOAL_STATUS_BLOCKED
from .contract.goals import GOAL_STATUS_MAX_ATTEMPTS
from .contract.hooks import run_hooks
from .contract.product import hooks_for_agent
from .goal_judge import GoalJudgeVerdict
from .goal_judge import judge_goal_completion
from .observability import trace_span
from .run_outcome import RunOutcome
from .run_retry import run_with_retries
from .runtime import get_project_root
from .runtime import project_context
from .storage import HandaSessionService


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
    session_id: str,
    user_id: str,
    agent_id: str,
    input_text: str,
    on_event: EventCallback,
    attachments: list[dict[str, Any]] | None = None,
    project_root: str | None = None,
    model_config_id: str | None = None,
    resume_user_input: dict[str, Any] | None = None,
    hooks: list[dict[str, Any]] | None = None,
) -> RunOutcome:
  with project_context(project_root) if project_root else nullcontext():
    resolved_project_root = str(get_project_root())
    with trace_span(
        "handa.agent_invocation",
        {
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "project_root": resolved_project_root,
            "model_config_id": model_config_id,
        },
    ):
      return await _run_agent_invocation_in_project(
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        input_text=input_text,
        attachments=attachments,
        on_event=on_event,
        project_root=resolved_project_root,
        model_config_id=model_config_id,
        resume_user_input=resume_user_input,
        hooks=hooks,
      )


async def _run_agent_invocation_in_project(
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
    hooks: list[dict[str, Any]] | None = None,
) -> RunOutcome:
  definition = get_agent_definition(agent_id)
  runner = load_native_agent(definition.id)
  produced_output = False
  emitted_events: list[dict[str, Any]] = []
  current_goal_scope: dict[str, Any] | None = None
  resolved_project_root = project_root or str(Path.cwd())
  resolved_hooks = list(hooks if hooks is not None else hooks_for_agent(definition.id))
  session_service = HandaSessionService()
  session_state = session_service.read_state_sync(session_id)
  active_goal = active_goal_from_state(session_state)

  async def emit_event(event: dict[str, Any]) -> None:
    nonlocal produced_output
    if isinstance(event, dict) and current_goal_scope is not None:
      event = _event_with_goal_scope(event, current_goal_scope)
    if isinstance(event, dict):
      emitted_events.append(dict(event))
    if _event_counts_as_user_visible_output(event):
      produced_output = True
    await on_event(event)

  resume_response = (
      dict(resume_user_input)
      if resume_user_input is not None
      else None
  )

  def _set_goal_scope(scope: dict[str, Any] | None) -> None:
    nonlocal current_goal_scope
    current_goal_scope = scope

  async def _attempt(attempt_input_text: str) -> RunOutcome:
    return await runner(
        prompt=apply_goal_to_prompt(attempt_input_text, active_goal),
        attachments=attachments,
        project_root=resolved_project_root,
        emit_event=emit_event,
        model_config_id=model_config_id,
        session_id=session_id,
        user_id=user_id,
        resume_user_input=resume_response,
        emit_final_agent_text=active_goal is None,
    )

  hook_context = {
      "session_id": session_id,
      "agent_id": definition.id,
      "agent_runtime": definition.runtime,
      "project_root": resolved_project_root,
      "model_config_id": model_config_id,
      "goal": active_goal,
  }
  await run_hooks(
      resolved_hooks,
      trigger="pre_invocation",
      context={**hook_context, "trigger": "pre_invocation"},
      project_root=resolved_project_root,
      emit_event=emit_event,
  )
  if active_goal is not None:
    await emit_event(
        {
            "kind": "goal_started",
            "summary": "Goal run started",
            "payload": {
                "goal_id": active_goal.get("goal_id"),
                "goal": active_goal,
            },
        }
    )
  try:
    if active_goal is None:
      outcome = await run_with_retries(
          lambda: _attempt(input_text),
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
    else:
      outcome = await _run_goal_attempts(
          goal=active_goal,
          input_text=input_text,
          attempt=_attempt,
          emit_event=emit_event,
          session_service=session_service,
          session_id=session_id,
          model_config_id=model_config_id,
          emitted_events=emitted_events,
          set_goal_scope=_set_goal_scope,
          should_retry=lambda: not produced_output,
          on_retry=lambda attempt_no, delay_sec, exc: LOGGER.warning(
              "Retrying goal attempt after transient error: attempt=%s "
              "next_delay_sec=%s error=%s session_id=%s",
              attempt_no,
              delay_sec,
              exc,
              session_id,
          ),
      )
  except BaseException as exc:
    status = "cancelled" if type(exc).__name__ == "CancelledError" else "failed"
    await _run_post_invocation_hooks(
        resolved_hooks,
        context={**hook_context, "trigger": "post_invocation", "status": status, "error": type(exc).__name__},
        project_root=resolved_project_root,
        emit_event=emit_event,
    )
    raise
  await _run_post_invocation_hooks(
      resolved_hooks,
      context={
          **hook_context,
          "trigger": "post_invocation",
          "status": "waiting_input" if outcome.pending_user_input is not None else "completed",
          "final_text": outcome.final_text,
          "goal_status": outcome.goal_status,
          "goal_verdict": outcome.goal_verdict,
      },
      project_root=resolved_project_root,
      emit_event=emit_event,
  )
  return outcome


async def _run_goal_attempts(
    *,
    goal: dict[str, Any],
    input_text: str,
    attempt: Callable[[str], Awaitable[RunOutcome]],
    emit_event: EventCallback,
    session_service: HandaSessionService,
    session_id: str,
    model_config_id: str | None,
    emitted_events: list[dict[str, Any]],
    set_goal_scope: Callable[[dict[str, Any] | None], None],
    should_retry: Callable[[], bool],
    on_retry: Callable[[int, float, BaseException], None],
) -> RunOutcome:
  max_attempts = int(goal.get("max_attempts") or 5)
  attempt_input = input_text
  for attempt_number in range(1, max_attempts + 1):
    attempt_id = f"goal_attempt_{uuid.uuid4().hex[:12]}"
    scope = {
        "goal_id": goal.get("goal_id"),
        "goal_attempt_id": attempt_id,
        "goal_attempt_number": attempt_number,
    }
    await _emit_goal_attempt_started(
        emit_event,
        goal=goal,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
    )
    set_goal_scope(scope)
    try:
      outcome = await run_with_retries(
          lambda: attempt(attempt_input),
          should_retry=should_retry,
          on_retry=on_retry,
      )
    finally:
      set_goal_scope(None)
    if outcome.pending_user_input is not None:
      return outcome
    verdict = await judge_goal_completion(
        goal=goal,
        session_state=session_service.read_state_sync(session_id),
        candidate_final_answer=outcome.final_text,
        attempt_number=attempt_number,
        attempt_id=attempt_id,
        max_attempts=max_attempts,
        emitted_events=emitted_events,
        model_config_id=model_config_id,
    )
    await _emit_goal_verdict(
        emit_event,
        goal=goal,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        verdict=verdict,
    )
    if verdict.status == "achieved":
      session_service.merge_state_sync(
          session_id,
          {
              GOAL_STATE_KEY: finished_goal_state(
                  goal,
                  status=GOAL_STATUS_ACHIEVED,
                  reason=verdict.reason,
              )
          },
      )
      await _emit_final_agent_text(emit_event, outcome.final_text)
      await _emit_goal_finished(
          emit_event,
          goal=goal,
          status=GOAL_STATUS_ACHIEVED,
          reason=verdict.reason,
          attempt_id=attempt_id,
          attempt_number=attempt_number,
      )
      return RunOutcome(
          final_text=outcome.final_text,
          goal_status=GOAL_STATUS_ACHIEVED,
          goal_verdict=verdict.model_dump(),
      )
    if verdict.status == "blocked":
      return await _finish_incomplete_goal(
          emit_event=emit_event,
          session_service=session_service,
          session_id=session_id,
          goal=goal,
          status=GOAL_STATUS_BLOCKED,
          reason=verdict.reason,
          verdict=verdict,
          attempt_id=attempt_id,
          attempt_number=attempt_number,
      )

    if attempt_number >= max_attempts:
      return await _finish_incomplete_goal(
          emit_event=emit_event,
          session_service=session_service,
          session_id=session_id,
          goal=goal,
          status=GOAL_STATUS_MAX_ATTEMPTS,
          reason=(
              f"Goal was not judged complete after {max_attempts} attempts. "
              f"Last judge reason: {verdict.reason}"
          ),
          verdict=verdict,
          attempt_id=attempt_id,
          attempt_number=attempt_number,
      )

    attempt_input = _continuation_request(verdict)
    await _emit_goal_continue(
        emit_event,
        goal=goal,
        attempt_id=attempt_id,
        attempt_number=attempt_number + 1,
        verdict=verdict,
        next_request=attempt_input,
    )

  raise AssertionError("_run_goal_attempts: unreachable")


async def _finish_incomplete_goal(
    *,
    emit_event: EventCallback,
    session_service: HandaSessionService,
    session_id: str,
    goal: dict[str, Any],
    status: str,
    reason: str,
    verdict: GoalJudgeVerdict,
    attempt_id: str,
    attempt_number: int,
) -> RunOutcome:
  session_service.merge_state_sync(
      session_id,
      {GOAL_STATE_KEY: finished_goal_state(goal, status=status, reason=reason)},
  )
  final_text = f"Goal not completed.\n\nStatus: {status}\n\nReason: {reason}"
  await _emit_final_agent_text(emit_event, final_text)
  await _emit_goal_finished(
      emit_event,
      goal=goal,
      status=status,
      reason=reason,
      attempt_id=attempt_id,
      attempt_number=attempt_number,
  )
  return RunOutcome(
      final_text=final_text,
      goal_status=status,
      goal_verdict=verdict.model_dump(),
  )


def _continuation_request(verdict: GoalJudgeVerdict) -> str:
  next_request = verdict.next_request.strip() or (
      "Continue working on the same goal. Address the missing work before "
      "finalizing."
  )
  return (
      "The goal is not achieved yet.\n\n"
      f"Judge reason:\n{verdict.reason}\n\n"
      "# Required next action\n"
      f"{next_request}\n\n"
      "Do not respond with a promise or plan such as 'I will ...'. "
      "Continue the missing work now and include concrete proof before finalizing. "
      "Only stop if you are actually blocked, and explain the concrete blocker."
  )


async def _emit_goal_attempt_started(
    emit_event: EventCallback,
    *,
    goal: dict[str, Any],
    attempt_id: str,
    attempt_number: int,
) -> None:
  await emit_event(
      {
          "id": f"goal_attempt_started_{uuid.uuid4().hex[:12]}",
          "kind": "goal_attempt_started",
          "summary": f"Goal attempt {attempt_number} started",
          "payload": {
              "goal_id": goal.get("goal_id"),
              "goal_attempt_id": attempt_id,
              "attempt_number": attempt_number,
          },
      }
  )


async def _emit_goal_verdict(
    emit_event: EventCallback,
    *,
    goal: dict[str, Any],
    attempt_id: str,
    attempt_number: int,
    verdict: GoalJudgeVerdict,
) -> None:
  await emit_event(
      {
          "id": f"goal_judge_{uuid.uuid4().hex[:12]}",
          "kind": "goal_judge_verdict",
          "summary": f"Goal judge: {verdict.status}",
          "payload": {
              "goal_id": goal.get("goal_id"),
              "goal_attempt_id": attempt_id,
              "attempt_number": attempt_number,
              "verdict": verdict.model_dump(),
          },
      }
  )


async def _emit_goal_continue(
    emit_event: EventCallback,
    *,
    goal: dict[str, Any],
    attempt_id: str,
    attempt_number: int,
    verdict: GoalJudgeVerdict,
    next_request: str,
) -> None:
  await emit_event(
      {
          "id": f"goal_continue_{uuid.uuid4().hex[:12]}",
          "kind": "goal_continue",
          "summary": "Goal work continued",
          "payload": {
              "goal_id": goal.get("goal_id"),
              "previous_goal_attempt_id": attempt_id,
              "next_attempt_number": attempt_number,
              "judge_reason": verdict.reason,
              "next_request": next_request,
          },
      }
  )


async def _emit_goal_finished(
    emit_event: EventCallback,
    *,
    goal: dict[str, Any],
    status: str,
    reason: str,
    attempt_id: str,
    attempt_number: int,
) -> None:
  await emit_event(
      {
          "id": f"goal_finished_{uuid.uuid4().hex[:12]}",
          "kind": "goal_finished",
          "summary": f"Goal {status}",
          "payload": {
              "goal_id": goal.get("goal_id"),
              "goal_attempt_id": attempt_id,
              "attempt_number": attempt_number,
              "status": status,
              "reason": reason,
          },
      }
  )


async def _emit_final_agent_text(emit_event: EventCallback, final_text: str) -> None:
  await emit_event(
      {
          "id": f"goal_final_{uuid.uuid4().hex[:12]}",
          "kind": "agent_text",
          "summary": "Assistant response",
          "payload": {"text": final_text, "final": True},
      }
  )


def _event_with_goal_scope(event: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
  scoped = dict(event)
  payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
  scoped_payload = dict(payload)
  for key in ("goal_id", "goal_attempt_id", "goal_attempt_number"):
    if key not in scoped_payload:
      scoped_payload[key] = scope.get(key)
  scoped["payload"] = scoped_payload
  return scoped


async def _run_post_invocation_hooks(
    hooks: list[dict[str, Any]],
    *,
    context: dict[str, Any],
    project_root: str,
    emit_event: EventCallback,
) -> None:
  try:
    await run_hooks(
        hooks,
        trigger="post_invocation",
        context=context,
        project_root=project_root,
        emit_event=emit_event,
    )
  except Exception:  # noqa: BLE001 - post hooks must not rewrite run outcome.
    return


def _event_counts_as_user_visible_output(event: Any) -> bool:
  if not isinstance(event, dict):
    return True
  kind = str(event.get("kind") or "")
  if not kind:
    return True
  if kind.startswith("hook."):
    return False
  if kind.startswith("goal_"):
    return False
  if kind.endswith(".started") or kind.endswith(".history_boundary"):
    return False
  return True
