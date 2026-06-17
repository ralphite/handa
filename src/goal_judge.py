from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any

from google import genai
from google.genai import types

from .agents.native_runner import generate_model_response
from .model_configs import resolve_model_config


MAX_HISTORY_CHARS = 120_000
MAX_EVENT_PAYLOAD_CHARS = 4000
VALID_STATUSES = {"achieved", "continue", "blocked"}


@dataclass(frozen=True)
class GoalJudgeVerdict:
  status: str
  reason: str
  next_request: str = ""
  citations: list[str] | None = None

  def model_dump(self) -> dict[str, Any]:
    return {
        "status": self.status,
        "reason": self.reason,
        "next_request": self.next_request,
        "citations": list(self.citations or []),
    }


async def judge_goal_completion(
    *,
    goal: dict[str, Any],
    session_state: dict[str, Any],
    candidate_final_answer: str,
    attempt_number: int,
    max_attempts: int,
    emitted_events: list[dict[str, Any]],
    attempt_id: str | None = None,
    model_config_id: str | None = None,
) -> GoalJudgeVerdict:
  api_key = _api_key()
  if not api_key:
    return GoalJudgeVerdict(
        status="blocked",
        reason="Goal judge could not run because Gemini API key is not configured.",
    )

  runtime_model_config = resolve_model_config(model_config_id)
  config = (
      runtime_model_config.generate_content_config.model_copy(deep=True)
      if runtime_model_config.generate_content_config
      else types.GenerateContentConfig()
  )
  config.tools = []
  config.system_instruction = _judge_system_instruction()
  config.response_mime_type = "application/json"

  response = await generate_model_response(
      client=genai.Client(api_key=api_key),
      model=runtime_model_config.model,
      contents=[
          types.Content(
              role="user",
              parts=[
                  types.Part.from_text(
                      text=_judge_user_prompt(
                          goal=goal,
                          session_state=session_state,
                          candidate_final_answer=candidate_final_answer,
                          attempt_number=attempt_number,
                          attempt_id=attempt_id,
                          max_attempts=max_attempts,
                          emitted_events=emitted_events,
                      )
                  )
              ],
          )
      ],
      config=config,
  )
  verdict = parse_goal_judge_response(_response_text(response))
  if verdict.status == "continue" and not verdict.next_request.strip():
    return GoalJudgeVerdict(
        status="continue",
        reason=verdict.reason,
        next_request="Continue working on the same goal. Address the missing work before finalizing.",
        citations=verdict.citations,
    )
  return verdict


def parse_goal_judge_response(text: str) -> GoalJudgeVerdict:
  try:
    raw = json.loads(text)
  except json.JSONDecodeError:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
      return GoalJudgeVerdict(
          status="continue",
          reason="Goal judge returned invalid JSON.",
          next_request="Continue working on the same goal and provide clearer proof before finalizing.",
      )
    try:
      raw = json.loads(match.group(0))
    except json.JSONDecodeError:
      return GoalJudgeVerdict(
          status="continue",
          reason="Goal judge returned invalid JSON.",
          next_request="Continue working on the same goal and provide clearer proof before finalizing.",
      )

  if not isinstance(raw, dict):
    return GoalJudgeVerdict(
        status="continue",
        reason="Goal judge returned a non-object verdict.",
        next_request="Continue working on the same goal and provide clearer proof before finalizing.",
    )

  status = str(raw.get("status") or "").strip().lower()
  if status not in VALID_STATUSES:
    status = "continue"
  reason = str(raw.get("reason") or "").strip()
  if not reason:
    reason = "Goal judge did not provide a reason."
  citations = raw.get("citations")
  return GoalJudgeVerdict(
      status=status,
      reason=reason,
      next_request=str(raw.get("next_request") or "").strip(),
      citations=[
          str(item).strip()
          for item in citations
          if str(item).strip()
      ] if isinstance(citations, list) else [],
  )


def _judge_system_instruction() -> str:
  return (
      "You are the goal judge. Given the user's goal and the session history, "
      "decide whether the goal is achieved. Do not trust the assistant's claim "
      "by itself. Use only evidence visible in the provided history. Return "
      "JSON only with keys: status, reason, next_request, citations. status "
      "must be one of achieved, continue, blocked."
  )


def _judge_user_prompt(
    *,
    goal: dict[str, Any],
    session_state: dict[str, Any],
    candidate_final_answer: str,
    attempt_number: int,
    attempt_id: str | None,
    max_attempts: int,
    emitted_events: list[dict[str, Any]],
) -> str:
  payload = {
      "goal": {
          "goal_id": goal.get("goal_id"),
          "text": goal.get("text"),
          "status": goal.get("status"),
      },
      "attempt": {
          "id": attempt_id,
          "number": attempt_number,
          "max_attempts": max_attempts,
      },
      "candidate_final_answer": candidate_final_answer,
      "native_history": _native_history_summary(session_state),
      "current_turn_events": _events_summary(emitted_events),
  }
  return json.dumps(payload, ensure_ascii=True, indent=2, default=str)


def _native_history_summary(session_state: dict[str, Any]) -> list[dict[str, Any]]:
  result: list[dict[str, Any]] = []
  for key, value in sorted(session_state.items()):
    if not key.startswith("handa:") or not key.endswith("_history"):
      continue
    if not isinstance(value, list):
      continue
    for item in value:
      if isinstance(item, dict):
        result.append({"state_key": key, **_compact_history_item(item)})
  return _trim_json_list(result, MAX_HISTORY_CHARS)


def _compact_history_item(item: dict[str, Any]) -> dict[str, Any]:
  parts = item.get("parts") if isinstance(item.get("parts"), list) else []
  compact_parts: list[dict[str, Any]] = []
  for part in parts:
    if not isinstance(part, dict):
      continue
    if "text" in part:
      compact_parts.append({"text": _truncate(str(part.get("text") or ""), 8000)})
    elif "function_call" in part:
      compact_parts.append({"function_call": _truncate_json(part.get("function_call"))})
    elif "function_response" in part:
      compact_parts.append({"function_response": _truncate_json(part.get("function_response"))})
  return {
      "role": item.get("role"),
      "parts": compact_parts,
  }


def _events_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
  summary: list[dict[str, Any]] = []
  for event in events:
    if not isinstance(event, dict):
      continue
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    summary.append(
        {
            "id": event.get("id"),
            "kind": event.get("kind"),
            "summary": event.get("summary"),
            "payload": _truncate_json(payload, MAX_EVENT_PAYLOAD_CHARS),
        }
    )
  return _trim_json_list(summary, MAX_HISTORY_CHARS)


def _trim_json_list(items: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
  kept: list[dict[str, Any]] = []
  size = 2
  for item in reversed(items):
    item_size = len(json.dumps(item, ensure_ascii=True, default=str))
    if kept and size + item_size > max_chars:
      break
    kept.append(item)
    size += item_size + 1
  kept.reverse()
  return kept


def _response_text(response: Any) -> str:
  texts: list[str] = []
  for candidate in getattr(response, "candidates", None) or []:
    content = getattr(candidate, "content", None)
    for part in getattr(content, "parts", None) or []:
      text = str(getattr(part, "text", "") or "")
      if text:
        texts.append(text)
  return "\n".join(texts).strip()


def _truncate_json(value: Any, max_chars: int = 8000) -> Any:
  text = json.dumps(value, ensure_ascii=True, default=str)
  if len(text) <= max_chars:
    try:
      return json.loads(text)
    except json.JSONDecodeError:
      return text
  return text[: max_chars - 20] + "...[truncated]"


def _truncate(text: str, max_chars: int) -> str:
  return text if len(text) <= max_chars else text[: max_chars - 20] + "...[truncated]"


def _api_key() -> str | None:
  configured = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
  return configured.strip() or None
