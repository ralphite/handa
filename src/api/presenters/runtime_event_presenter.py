from __future__ import annotations

import json
from typing import Any

from .tool_summary import summarize_error
from .tool_summary import summarize_tool_call
from .tool_summary import summarize_tool_response


# Runtime lifecycle bookkeeping that carries no user-facing information; a
# timeline entry for these is pure noise.
_LIFECYCLE_EVENT_KINDS = frozenset({
    "langgraph.started",
    "langgraph.checkpoint",
})


def project_runtime_event(
    event: dict[str, Any],
    *,
    runtime: str,
) -> list[dict[str, Any]]:
  if runtime != "langgraph":
    return [_fallback_projection(event, runtime=runtime)]
  kind = str(event.get("kind") or "")
  if kind in _LIFECYCLE_EVENT_KINDS:
    return []
  payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
  projected: list[dict[str, Any]] = []

  if kind == "langgraph.tool_call":
    name = str(payload.get("name") or "")
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    projected.append(
        {
            "kind": "tool_call",
            "summary": summarize_tool_call(name, args),
            "payload": _jsonable(
                {
                    "id": _call_id(event, payload),
                    "name": name,
                    "args": args,
                    "partial_args": None,
                    "will_continue": None,
                }
            ),
        }
    )
  elif kind == "langgraph.tool_result":
    name = str(payload.get("name") or "")
    response = payload.get("result")
    if response is None:
      response = {
          "ok": bool(payload.get("ok")),
      }
    projected.append(
        {
            "kind": "tool_response",
            "summary": summarize_tool_response(name, response),
            "payload": _jsonable(
                {
                    "id": _call_id(event, payload),
                    "name": name,
                    "response": response,
                    "will_continue": None,
                    "scheduling": None,
                }
            ),
        }
    )
    progress_delta = _progress_delta(name, response)
    if progress_delta is not None:
      projected.append(progress_delta)
    artifact_delta = _artifact_delta(name, response)
    if artifact_delta is not None:
      projected.append(artifact_delta)
  elif kind == "langgraph.model_text":
    text = str(payload.get("text") or "")
    if text:
      projected.append(
          {
              "kind": "agent_text",
              "summary": "Assistant response",
              "payload": {
                  "text": text,
                  "partial": False,
                  "final": False,
              },
          }
      )
  elif kind == "agent_text":
    text = str(payload.get("text") or "")
    if text:
      projected.append(
          {
              "kind": "agent_text",
              "summary": "Assistant response",
              "payload": {
                  "text": text,
                  "partial": False,
                  "final": bool(payload.get("final")),
              },
          }
      )
  elif kind == "langgraph.user_input_requested":
    pending = (
        payload.get("pending_user_input")
        if isinstance(payload.get("pending_user_input"), dict)
        else {}
    )
    questions = pending.get("questions") if isinstance(pending.get("questions"), list) else []
    projected.append(
        {
            "kind": "user_input_requested",
            "summary": f"Waiting for user input ({len(questions)} questions)",
            "payload": _jsonable({"pending_user_input": pending}),
        }
    )
  elif kind == "langgraph.user_input_result":
    projected.append(
        {
            "kind": "user_input_result",
            "summary": "User answered the form",
            "payload": _jsonable(
                {
                    "name": payload.get("name"),
                    "response": payload.get("result"),
                }
            ),
        }
    )
  elif kind in {"error", "langgraph.error"}:
    projected.append(
        {
            "kind": "error",
            "summary": summarize_error(
                payload.get("code"),
                event.get("summary") or payload.get("message"),
            ),
            "payload": _jsonable(payload),
        }
    )

  if not projected:
    projected.append(_fallback_projection(event, runtime=runtime))
  return projected


def _fallback_projection(event: dict[str, Any], *, runtime: str) -> dict[str, Any]:
  payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
  return {
      "kind": "runtime_step",
      "summary": str(event.get("summary") or event.get("kind") or "Runtime step"),
      "payload": _jsonable(
          {
              "runtime": runtime,
              "kind": event.get("kind"),
              **payload,
          }
      ),
  }


def _call_id(event: dict[str, Any], payload: dict[str, Any]) -> str:
  value = payload.get("call_id") or payload.get("id") or event.get("id")
  return str(value or "")


def _artifact_delta(name: str, response: Any) -> dict[str, Any] | None:
  if name not in {"artifacts_save_text", "agents_save_config"}:
    return None
  if not isinstance(response, dict) or response.get("ok") is False:
    return None
  filename = str(response.get("filename") or "").strip()
  version = response.get("version")
  if not filename:
    return None
  return {
      "kind": "artifact_delta",
      "summary": f"Updated artifact {filename}",
      "payload": {
          "filename": filename,
          "version": version,
      },
  }


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
  return json.loads(json.dumps(value, ensure_ascii=True, default=str))
