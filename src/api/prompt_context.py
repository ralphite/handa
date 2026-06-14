"""Shared context builders for composer-assist endpoints.

Both /api/dictate and /api/optimize_prompt condition a one-shot Gemini call
on lightweight session + project context so the model can resolve
project-specific terms (file paths, agent names, custom jargon). This module
holds the context assembly and output normalisation they share.
"""

from __future__ import annotations

import os
from pathlib import Path

# How many prior turns from the session to include as context.
MAX_HISTORY_TURNS = 6
# How many bytes of project README-style files to include.
MAX_README_CHARS = 1800
# Files to scan, in priority order, for project high-level context.
CONTEXT_FILE_CANDIDATES = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "ARCHITECTURE.md",
)


def gemini_api_key() -> str | None:
  return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def normalise_model_text(text: str) -> str:
  """Strip whitespace + accidental wrapping quotes / code fences."""
  text = (text or "").strip()
  if text.startswith("```") and text.endswith("```"):
    text = text.strip("`").strip()
    if "\n" in text:
      text = text.split("\n", 1)[1]
  if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
    text = text[1:-1]
  return text.strip()


def project_context(ctx, project_id: str) -> str:
  project = ctx.db.get_project(project_id)
  if project is None:
    return ""
  root_path = project.get("root_path") or ""
  name = project.get("name") or ""
  lines: list[str] = []
  if name:
    lines.append(f"Project name: {name}")
  if root_path:
    lines.append(f"Project root: {root_path}")
    snippet = _read_first_existing(Path(root_path), CONTEXT_FILE_CANDIDATES)
    if snippet:
      filename, text = snippet
      lines.append(f"--- {filename} (excerpt) ---")
      lines.append(text)
  return "\n".join(lines).strip()


def _read_first_existing(
    root: Path,
    candidates: tuple[str, ...],
) -> tuple[str, str] | None:
  for name in candidates:
    path = root / name
    if not path.is_file():
      continue
    try:
      text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
      continue
    if len(text) > MAX_README_CHARS:
      text = text[:MAX_README_CHARS].rstrip() + "\n... [truncated]"
    return name, text
  return None


async def history_context(ctx, session_id: str) -> str:
  """Pull recent turn inputs + final replies for the session.

  We read from the turn projection used by the UI. This is a
  pragmatic shortcut: it captures what the user typed and what the agent
  most recently said. Tool calls and intermediate events are intentionally
  omitted to keep the system prompt lean.
  """
  with (
      ctx.db._lock
  ):  # noqa: SLF001 - small read, table is already locked-free elsewhere
    rows = ctx.db._connection.execute(  # noqa: SLF001
        """
    select input_text, final_text from web_turns
    where session_id = ?
    order by created_at desc, rowid desc
    limit ?
    """,
        (session_id, MAX_HISTORY_TURNS),
    ).fetchall()
  if not rows:
    return ""
  turns: list[str] = []
  for row in reversed(rows):  # chronological order
    prompt = (row["input_text"] or "").strip()
    reply = (row["final_text"] or "").strip()
    if prompt:
      turns.append(f"User: {prompt}")
    if reply:
      # Trim long agent replies — only the gist is useful as context.
      if len(reply) > 600:
        reply = reply[:600].rstrip() + " ..."
      turns.append(f"Agent: {reply}")
  return "\n".join(turns).strip()


def format_context(project: str, history: str) -> str:
  parts: list[str] = []
  if project:
    parts.append("# Project context\n" + project)
  if history:
    parts.append("# Recent chat history\n" + history)
  if not parts:
    return ""
  return "\n\n".join(parts)
