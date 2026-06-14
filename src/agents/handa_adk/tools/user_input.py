from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext
from pydantic import BaseModel

from ....tools.user_input import UserInputQuestion
from ....tools.user_input import validate_questions


def request_user_input(
    questions: list[UserInputQuestion],
    tool_context: ToolContext,
) -> None:
  """Ask the user structured questions and pause this turn until they answer.

  Use this when ambiguity about intent, requirements, approach, or the next
  step would change your plan, and when asking the user to confirm a plan.
  Provide at most 4 questions per call; each question needs 2-4 concrete,
  mutually exclusive options (put the recommended option first and mark it).
  Set multi_select=true when several answers can apply. The turn pauses
  after this call; the answers arrive as the tool response, either
  {"answers": [{"id", "selected", "free_text"?}]} or {"cancelled": true}
  when the user skipped the form. Call it at most once per turn, and do not
  call any other tool in the same turn.
  """
  depth = _coerce_int(tool_context.state.get("handa:agent_run_depth"), 0)
  if depth > 0:
    raise ValueError(
        "request_user_input is not available in child agent runs; decide with "
        "reasonable defaults instead"
    )
  validate_questions(
      [
          item.model_dump() if isinstance(item, BaseModel) else item
          for item in questions or []
      ]
  )
  # Returning None makes ADK skip the function response: the invocation pauses
  # on this long-running call and the Web layer delivers the answer later as a
  # FunctionResponse message paired by function_call_id.
  return None


def _coerce_int(value: Any, default: int) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return default
