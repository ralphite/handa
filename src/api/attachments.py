from __future__ import annotations

from typing import Any

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENT_COUNT = 10

_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/x-sh",
    "application/x-python",
    "application/x-typescript",
}


def classify_kind(mime_type: str) -> str:
  mime = (mime_type or "").lower()
  if mime.startswith("image/"):
    return "image"
  if mime == "application/pdf":
    return "pdf"
  if mime.startswith(_TEXT_MIME_PREFIXES) or mime in _TEXT_MIME_EXACT:
    return "text"
  return "binary"


def attachment_summary(row: dict[str, Any]) -> dict[str, Any]:
  return {
      "id": row["id"],
      "turn_id": row["turn_id"],
      "filename": row["filename"],
      "mime_type": row["mime_type"],
      "kind": row["kind"],
      "byte_count": row.get("byte_count", 0) or 0,
  }
