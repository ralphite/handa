from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Iterable


@dataclass(frozen=True)
class TokenUsageSummary:
  context_token_count: int = 0
  output_token_count: int = 0
  total_token_count: int = 0


def summarize_token_usage(events: Iterable[dict[str, Any]]) -> TokenUsageSummary:
  context_token_count = 0
  output_token_count = 0
  total_token_count = 0
  seen_event_ids: set[str] = set()

  for event in events:
    raw_event = event.get("raw_event") or {}
    event_id = _event_identity(event, raw_event)
    if event_id:
      if event_id in seen_event_ids:
        continue
      seen_event_ids.add(event_id)
    if raw_event.get("partial") is True:
      continue

    metadata = _usage_metadata(raw_event)
    if metadata is None:
      continue

    prompt_tokens = _int_field(metadata, "prompt_token_count", "promptTokenCount")
    candidate_tokens = _int_field(
        metadata,
        "candidates_token_count",
        "candidatesTokenCount",
    )
    thoughts_tokens = _int_field(metadata, "thoughts_token_count", "thoughtsTokenCount")
    tool_prompt_tokens = _int_field(
        metadata,
        "tool_use_prompt_token_count",
        "toolUsePromptTokenCount",
    )
    event_total = _int_field(metadata, "total_token_count", "totalTokenCount")

    if prompt_tokens > 0:
      context_token_count = prompt_tokens
    # Billing-wise thinking tokens are output: Gemini reports
    # totalTokenCount = prompt + candidates + thoughts, and OpenInference
    # (Phoenix) reports completion = candidates + reasoning.
    output_token_count += candidate_tokens + thoughts_tokens
    total_token_count += event_total or (
        prompt_tokens + candidate_tokens + thoughts_tokens + tool_prompt_tokens
    )

  return TokenUsageSummary(
      context_token_count=context_token_count,
      output_token_count=output_token_count,
      total_token_count=total_token_count,
  )


def _event_identity(event: dict[str, Any], raw_event: dict[str, Any]) -> str | None:
  value = (
      event.get("id")
      or event.get("runtime_event_id")
      or raw_event.get("id")
      or raw_event.get("event_id")
      or raw_event.get("eventId")
  )
  return str(value) if value else None


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
