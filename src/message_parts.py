from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google.genai import types


LOGGER = logging.getLogger(__name__)


def build_message_parts(
    input_text: str,
    attachments: list[dict[str, Any]] | None,
) -> list[types.Part]:
  """Build genai content parts from input text plus attachments.

  Images and PDFs are forwarded as binary ``inline_data`` so the model can see
  them; text files are inlined as text; other binaries fall back to inline data
  or, failing that, a textual reference. Shared by the ADK and LangGraph
  runtimes so both deliver identical multimodal input to the model.
  """
  parts: list[types.Part] = []
  if input_text:
    parts.append(types.Part(text=input_text))

  for attachment in attachments or []:
    storage_path = attachment.get("storage_path")
    if not storage_path:
      continue
    try:
      data = Path(storage_path).read_bytes()
    except OSError as exc:
      LOGGER.warning("Skipping attachment %s: %s", storage_path, exc)
      continue
    mime_type = attachment.get("mime_type") or "application/octet-stream"
    kind = attachment.get("kind") or "binary"
    filename = attachment.get("filename") or "attachment"

    if kind in ("image", "pdf"):
      parts.append(types.Part(inline_data=types.Blob(mime_type=mime_type, data=data)))
    elif kind == "text":
      try:
        text = data.decode("utf-8")
      except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
      parts.append(types.Part(text=f"\n\n--- {filename} ---\n{text}"))
    else:
      try:
        parts.append(types.Part(inline_data=types.Blob(mime_type=mime_type, data=data)))
      except Exception:  # noqa: BLE001 - fall back to a textual reference.
        parts.append(types.Part(text=f"\n\n[attached file: {filename} ({mime_type})]"))

  if not parts:
    parts.append(types.Part(text=""))
  return parts
