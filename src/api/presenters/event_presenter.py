from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from ...contract.run_events import extract_event_facts
from .tool_summary import summarize_error
from .tool_summary import summarize_tool_call
from .tool_summary import summarize_tool_response


def project_adk_event(event: Any) -> list[dict[str, Any]]:
  facts = extract_event_facts(event)
  projected: list[dict[str, Any]] = []

  if facts.text and not _is_user_author(facts.author):
    kind = "agent_text_delta" if facts.partial else "agent_text"
    projected.append(
        {
            "kind": kind,
            "summary": "Assistant response",
            "payload": {
                "text": facts.text,
                "partial": facts.partial,
                "final": facts.final,
            },
        }
    )

  for call in facts.function_calls:
    projected.append(
        {
            "kind": "tool_call",
            "summary": summarize_tool_call(call.name, call.args),
            "payload": _jsonable(asdict(call)),
        }
    )

  for response in facts.function_responses:
    projected.append(
        {
            "kind": "tool_response",
            "summary": summarize_tool_response(response.name, response.response),
            "payload": _jsonable(asdict(response)),
        }
    )
    progress_delta = _progress_delta(response.name, response.response)
    if progress_delta is not None:
      projected.append(progress_delta)

  for filename, version in facts.artifact_delta.items():
    projected.append(
        {
            "kind": "artifact_delta",
            "summary": f"Updated artifact {filename}",
            "payload": {"filename": filename, "version": version},
        }
    )

  if facts.error_code or facts.error_message or facts.interrupted:
    projected.append(
        {
            "kind": "error",
            "summary": summarize_error(
                facts.error_code,
                facts.error_message,
                fallback="Interrupted",
            ),
            "payload": {
                # `error_type` is carried (null here) so error steps share one
                # field shape with the worker's exception step, which reports
                # `error_type` and a null `error_code`.
                "error_type": None,
                "error_code": facts.error_code,
                "error_message": facts.error_message,
                "interrupted": facts.interrupted,
            },
        }
    )

  if not projected:
    if facts.partial:
      # Empty streaming markers (no text, calls, or deltas) are timeline noise.
      return []
    projected.append(
        {
            "kind": "adk_event",
            "summary": "ADK event",
            "payload": {
                "author": facts.author,
                "partial": facts.partial,
                "final": facts.final,
            },
        }
    )

  return projected


def _is_user_author(author: str | None) -> bool:
  return (author or "").strip().lower() == "user"


def _progress_delta(name: str, response: Any) -> dict[str, Any] | None:
  if name != "progress_update":
    return None
  if not isinstance(response, dict) or response.get("ok") is False:
    return None
  if response.get("success") is not True:
    return None
  # The tool response no longer inlines `progress_items` (the canonical copy
  # lives in session state, surfaced via the session-detail endpoint). Fall back
  # to `count` and an empty item list so the delta event still fires.
  items = response.get("progress_items")
  items = items if isinstance(items, list) else []
  count = response.get("count")
  if not isinstance(count, int):
    count = len(items)
  return {
      "kind": "progress_delta",
      "summary": f"Updated progress ({count} items)",
      "payload": {
          "count": count,
          "items": _jsonable(items),
      },
  }


def _jsonable(value: Any) -> Any:
  return json.loads(json.dumps(value, default=str))
