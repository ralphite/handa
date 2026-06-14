from __future__ import annotations

from typing import Any


# Default ceiling for a single text read returned to the model. Larger reads
# page through `offset`/`max_chars`; the metadata (`char_count`/`line_count`) is
# always present so the caller can tell how much content remains without
# pulling the whole payload into context. Kept below the LangGraph per-response
# budget (MAX_TOOL_RESULT_CHARS, 12000) so the dispatch-level truncation never
# re-trims a window and leaves `next_offset` pointing past the real content.
DEFAULT_READ_MAX_CHARS = 8000


def window_text(
    text: str,
    *,
    offset: int = 0,
    max_chars: int | None = None,
    metadata_only: bool = False,
) -> dict[str, Any]:
  """Build the content fields for a bounded, pageable text read.

  Always returns `char_count` and `line_count`. With `metadata_only` it stops
  there (no content). Otherwise it returns a `content` slice starting at
  `offset` characters, capped at `max_chars` (default `DEFAULT_READ_MAX_CHARS`),
  and adds `offset`/`truncated`/`next_offset` so a caller can resume reading
  from where the slice ended instead of re-fetching the whole artifact.
  """
  total = len(text)
  fields: dict[str, Any] = {
      "char_count": total,
      "line_count": (text.count("\n") + 1) if text else 0,
  }
  if metadata_only:
    return fields
  start = min(max(offset, 0), total)
  limit = DEFAULT_READ_MAX_CHARS if max_chars is None else max(max_chars, 0)
  end = min(start + limit, total)
  fields["content"] = text[start:end]
  if start > 0:
    fields["offset"] = start
  if end < total:
    fields["truncated"] = True
    fields["next_offset"] = end
  return fields
