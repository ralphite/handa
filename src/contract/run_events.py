from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class FunctionCallFacts:
  id: str | None
  name: str
  args: dict[str, Any]
  partial_args: Any
  will_continue: bool | None


@dataclass(frozen=True)
class FunctionResponseFacts:
  id: str | None
  name: str
  response: Any
  will_continue: bool | None
  scheduling: Any


@dataclass(frozen=True)
class AdkEventFacts:
  id: str | None
  invocation_id: str | None
  author: str | None
  timestamp: float | None
  partial: bool
  turn_complete: bool | None
  final: bool
  interrupted: bool
  error_code: str | None
  error_message: str | None
  text: str
  function_calls: list[FunctionCallFacts]
  function_responses: list[FunctionResponseFacts]
  artifact_delta: dict[str, int]
  input_token_count: int
  output_token_count: int
  total_token_count: int


def extract_event_facts(event: Any) -> AdkEventFacts:
  content = _get(event, "content")
  parts = list(_get(content, "parts") or [])
  actions = _get(event, "actions")
  texts: list[str] = []
  function_calls: list[FunctionCallFacts] = []
  function_responses: list[FunctionResponseFacts] = []

  for part in parts:
    text = _get(part, "text")
    if text:
      texts.append(str(text))

    function_call = _get(part, "function_call")
    if function_call:
      function_calls.append(
          FunctionCallFacts(
              id=_optional_str(_get(function_call, "id")),
              name=str(_get(function_call, "name") or ""),
              args=dict(_get(function_call, "args") or {}),
              partial_args=_get(function_call, "partial_args"),
              will_continue=_optional_bool(_get(function_call, "will_continue")),
          )
      )

    function_response = _get(part, "function_response")
    if function_response:
      function_responses.append(
          FunctionResponseFacts(
              id=_optional_str(_get(function_response, "id")),
              name=str(_get(function_response, "name") or ""),
              response=_get(function_response, "response"),
              will_continue=_optional_bool(
                  _get(function_response, "will_continue")
              ),
              scheduling=_get(function_response, "scheduling"),
          )
      )

  input_token_count = _optional_int(
      _usage_value(event, "prompt_token_count", "promptTokenCount")
  ) or 0
  output_token_count = _optional_int(
      _usage_value(event, "candidates_token_count", "candidatesTokenCount")
  ) or 0
  total_token_count = _optional_int(
      _usage_value(event, "total_token_count", "totalTokenCount")
  ) or (input_token_count + output_token_count)

  return AdkEventFacts(
      id=_optional_str(_get(event, "id")),
      invocation_id=_optional_str(_get(event, "invocation_id")),
      author=_optional_str(_get(event, "author")),
      timestamp=_optional_float(_get(event, "timestamp")),
      partial=bool(_get(event, "partial") or False),
      turn_complete=_optional_bool(_get(event, "turn_complete")),
      final=_is_final_response(event),
      interrupted=bool(_get(event, "interrupted") or False),
      error_code=_optional_str(_get(event, "error_code")),
      error_message=_optional_str(_get(event, "error_message")),
      text="\n".join(texts),
      function_calls=function_calls,
      function_responses=function_responses,
      artifact_delta=dict(_get(actions, "artifact_delta") or {}),
      input_token_count=input_token_count,
      output_token_count=output_token_count,
      total_token_count=total_token_count,
  )


def serialize_adk_event(event: Any) -> dict[str, Any]:
  if isinstance(event, dict):
    return event

  serialized: Any
  if hasattr(event, "model_dump_json"):
    try:
      serialized = json.loads(event.model_dump_json(by_alias=True))
    except TypeError:
      serialized = json.loads(event.model_dump_json())
  elif hasattr(event, "model_dump"):
    try:
      serialized = _jsonable(event.model_dump(mode="json", by_alias=True))
    except TypeError:
      serialized = _jsonable(event.model_dump())
  else:
    serialized = _jsonable(event)

  # is_final_response() is a method, not a field, so model_dump loses it. Persist
  # it so facts extracted from the serialized form agree with the live object.
  if (
      isinstance(serialized, dict)
      and "is_final_response" not in serialized
      and callable(getattr(event, "is_final_response", None))
  ):
    serialized["is_final_response"] = _is_final_response(event)
  return serialized


def _get(value: Any, name: str) -> Any:
  if value is None:
    return None
  if isinstance(value, dict):
    if name in value:
      return value[name]
    return value.get(_camel_name(name))
  return getattr(value, name, None)


def _camel_name(name: str) -> str:
  # Serialized ADK events use by_alias=True, so dict keys are camelCase.
  head, *rest = name.split("_")
  return head + "".join(part.capitalize() for part in rest)


def _is_final_response(event: Any) -> bool:
  if isinstance(event, dict):
    return bool(event.get("is_final_response") or event.get("isFinalResponse") or False)
  method = getattr(event, "is_final_response", None)
  if not callable(method):
    return False
  try:
    return bool(method())
  except Exception:  # noqa: BLE001 - event inspection should not break runs.
    return False


def _optional_str(value: Any) -> str | None:
  return None if value is None else str(value)


def _optional_bool(value: Any) -> bool | None:
  return None if value is None else bool(value)


def _optional_float(value: Any) -> float | None:
  try:
    return None if value is None else float(value)
  except (TypeError, ValueError):
    return None


def _optional_int(value: Any) -> int | None:
  try:
    return None if value is None else int(value)
  except (TypeError, ValueError):
    return None


def _usage_value(event: Any, snake_name: str, camel_name: str) -> Any:
  usage_metadata = _get(event, "usage_metadata") or _get(event, "usageMetadata")
  if usage_metadata is None:
    return None
  return _get(usage_metadata, snake_name) or _get(usage_metadata, camel_name)


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
