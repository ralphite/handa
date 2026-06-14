"""Shared schema and validation for the `request_user_input` control-flow tool.

Both agent runtimes (LangGraph interrupt, ADK long-running tool) expose the
same model-facing tool and persist the same pending payload in session state,
so the Web layer can render the form and route answers without knowing which
runtime is paused.
"""

from __future__ import annotations

from typing import Any
import uuid

from pydantic import BaseModel


USER_INPUT_TOOL_NAME = "request_user_input"
PENDING_USER_INPUT_STATE_KEY = "handa:pending_user_input"
MAX_QUESTIONS = 4
MIN_OPTIONS = 2
MAX_OPTIONS = 4


class UserInputOption(BaseModel):
  """One selectable option. `description` explains the trade-off."""

  label: str
  description: str | None = None


class UserInputQuestion(BaseModel):
  """One question shown to the user.

  `id` pairs the answer with the question. `multi_select` allows choosing
  several options. `allow_free_text` lets the user type a custom answer
  instead of (or in addition to) picking an option.
  """

  id: str
  prompt: str
  options: list[UserInputOption]
  multi_select: bool = False
  allow_free_text: bool = True


def new_request_id() -> str:
  return f"uireq_{uuid.uuid4().hex[:12]}"


def validate_questions(questions: Any) -> list[dict[str, Any]]:
  """Validate and normalize the model-provided questions payload.

  Raises ValueError with a model-actionable message on invalid input.
  """
  if not isinstance(questions, list) or not questions:
    raise ValueError("questions must be a non-empty list")
  if len(questions) > MAX_QUESTIONS:
    raise ValueError(f"questions must contain at most {MAX_QUESTIONS} items")
  normalized: list[dict[str, Any]] = []
  seen_ids: set[str] = set()
  for index, question in enumerate(questions):
    if not isinstance(question, dict):
      raise ValueError(f"questions[{index}] must be an object")
    question_id = str(question.get("id") or "").strip()
    if not question_id:
      raise ValueError(f"questions[{index}].id is required")
    if question_id in seen_ids:
      raise ValueError(f"duplicate question id: {question_id}")
    seen_ids.add(question_id)
    prompt = str(question.get("prompt") or "").strip()
    if not prompt:
      raise ValueError(f"questions[{index}].prompt is required")
    options = _validate_options(question.get("options"), index)
    normalized.append(
        {
            "id": question_id,
            "prompt": prompt,
            "options": options,
            "multi_select": bool(question.get("multi_select", False)),
            "allow_free_text": bool(question.get("allow_free_text", True)),
        }
    )
  return normalized


def build_pending_request(
    *,
    runtime: str,
    questions: list[dict[str, Any]],
    request_id: str | None = None,
    function_call_id: str | None = None,
) -> dict[str, Any]:
  """Build the payload stored under PENDING_USER_INPUT_STATE_KEY.

  `function_call_id` is required for the ADK runtime: the resume path must
  pair the answer FunctionResponse with the original FunctionCall id.
  """
  pending: dict[str, Any] = {
      "request_id": request_id or new_request_id(),
      "runtime": runtime,
      "tool_name": USER_INPUT_TOOL_NAME,
      "questions": questions,
  }
  if function_call_id:
    pending["function_call_id"] = function_call_id
  return pending


def validate_answers(
    questions: list[dict[str, Any]],
    payload: Any,
) -> dict[str, Any]:
  """Validate a user-submitted answers payload against the pending questions.

  Returns the normalized tool response: `{"answers": [...]}` or
  `{"cancelled": True}`. This is exactly what the paused model turn receives
  as the function response.
  """
  if not isinstance(payload, dict):
    raise ValueError("answers payload must be an object")
  if payload.get("cancelled"):
    return {"cancelled": True}
  raw_answers = payload.get("answers")
  if not isinstance(raw_answers, list):
    raise ValueError("answers must be a list")
  by_id = {question["id"]: question for question in questions}
  answered: dict[str, dict[str, Any]] = {}
  for index, answer in enumerate(raw_answers):
    if not isinstance(answer, dict):
      raise ValueError(f"answers[{index}] must be an object")
    answer_id = str(answer.get("id") or "").strip()
    question = by_id.get(answer_id)
    if question is None:
      raise ValueError(f"answers[{index}].id does not match a question: {answer_id!r}")
    if answer_id in answered:
      raise ValueError(f"duplicate answer id: {answer_id}")
    answered[answer_id] = _validate_answer(question, answer, index)
  missing = [question_id for question_id in by_id if question_id not in answered]
  if missing:
    raise ValueError(f"missing answers for questions: {', '.join(missing)}")
  return {"answers": [answered[question["id"]] for question in questions]}


def _validate_options(options: Any, question_index: int) -> list[dict[str, Any]]:
  if not isinstance(options, list):
    raise ValueError(f"questions[{question_index}].options must be a list")
  if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
    raise ValueError(
        f"questions[{question_index}].options must contain "
        f"{MIN_OPTIONS} to {MAX_OPTIONS} items"
    )
  normalized: list[dict[str, Any]] = []
  seen_labels: set[str] = set()
  for option_index, option in enumerate(options):
    if isinstance(option, str):
      option = {"label": option}
    if not isinstance(option, dict):
      raise ValueError(
          f"questions[{question_index}].options[{option_index}] must be an object"
      )
    label = str(option.get("label") or "").strip()
    if not label:
      raise ValueError(
          f"questions[{question_index}].options[{option_index}].label is required"
      )
    if label in seen_labels:
      raise ValueError(
          f"questions[{question_index}] has duplicate option label: {label!r}"
      )
    seen_labels.add(label)
    item: dict[str, Any] = {"label": label}
    description = str(option.get("description") or "").strip()
    if description:
      item["description"] = description
    normalized.append(item)
  return normalized


def _validate_answer(
    question: dict[str, Any],
    answer: dict[str, Any],
    index: int,
) -> dict[str, Any]:
  labels = {option["label"] for option in question["options"]}
  raw_selected = answer.get("selected") or []
  if not isinstance(raw_selected, list):
    raise ValueError(f"answers[{index}].selected must be a list")
  selected: list[str] = []
  for value in raw_selected:
    label = str(value or "").strip()
    if label not in labels:
      raise ValueError(f"answers[{index}] has unknown option: {label!r}")
    if label not in selected:
      selected.append(label)
  if not question["multi_select"] and len(selected) > 1:
    raise ValueError(
        f"answers[{index}] selected multiple options for a single-select question"
    )
  free_text = str(answer.get("free_text") or "").strip()
  if free_text and not question["allow_free_text"]:
    raise ValueError(f"answers[{index}] free_text is not allowed for this question")
  if not selected and not free_text:
    raise ValueError(f"answers[{index}] must select an option or provide free_text")
  result: dict[str, Any] = {"id": question["id"], "selected": selected}
  if free_text:
    result["free_text"] = free_text
  return result


def cancelled_response() -> dict[str, Any]:
  return {"cancelled": True}
