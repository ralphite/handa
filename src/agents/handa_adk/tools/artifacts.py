from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext
from google.genai import types

from ....storage.artifact_service import artifact_display_filename
from ....storage.artifact_service import artifact_stored_filename
from ....tools.text_window import window_text


async def save_text(
    filename: str,
    content: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
  """Save a text artifact in the current session.

  Use filenames like `testing_quality.plan.md`, `pytest_result.verification.md`,
  or `testing_quality.agent.json`. The storage service adds `.vN.` when writing.
  """
  version = await tool_context.save_artifact(
      filename,
      types.Part.from_text(text=content),
  )
  return {
      "success": True,
      "filename": artifact_display_filename(filename),
      "stored_filename": artifact_stored_filename(filename, version),
      "version": version,
      "display_version": version + 1,
  }


async def list(tool_context: ToolContext) -> dict[str, Any]:
  """List artifact filenames saved in the current session."""
  artifacts = await tool_context.list_artifacts()
  return {"artifacts": artifacts, "count": len(artifacts)}


async def read(
    filename: str,
    tool_context: ToolContext,
    version: int | None = None,
    offset: int = 0,
    max_chars: int | None = None,
    metadata_only: bool = False,
) -> dict[str, Any]:
  """Read a text artifact from the current session.

  Large artifacts are bounded: the reply carries `char_count`/`line_count` plus
  a windowed `content` slice. Pass `metadata_only=True` to fetch just the size,
  or page through a big artifact with `offset` (start char) and `max_chars`.
  """
  artifact = await tool_context.load_artifact(filename, version=version)
  if artifact is None:
    return {"found": False, "filename": filename, "version": version}
  if artifact.text is not None:
    return {
        "found": True,
        "filename": filename,
        "version": version,
        **window_text(
            artifact.text,
            offset=offset,
            max_chars=max_chars,
            metadata_only=metadata_only,
        ),
    }
  inline_data = artifact.inline_data
  return {
      "found": True,
      "filename": filename,
      "version": version,
      "mime_type": inline_data.mime_type if inline_data else None,
      "byte_count": len(inline_data.data or b"") if inline_data else 0,
  }
