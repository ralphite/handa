from __future__ import annotations

from pathlib import Path


PROJECT_AGENTS_FILENAME = "AGENTS.md"


def load_project_agents_md(project_root: str | Path | None) -> str:
  if project_root is None or not str(project_root).strip():
    return ""
  path = Path(project_root).expanduser().resolve() / PROJECT_AGENTS_FILENAME
  if not path.is_file():
    return ""
  return path.read_text(encoding="utf-8").strip()


def render_project_agents_instruction(project_root: str | Path | None) -> str:
  agents_md = load_project_agents_md(project_root)
  if not agents_md:
    return ""
  return (
      "# Project Instructions (project_root/AGENTS.md)\n\n"
      f"{agents_md}"
  )


def append_project_agents_instruction(
    instruction: str,
    project_root: str | Path | None,
) -> str:
  project_instruction = render_project_agents_instruction(project_root)
  if not project_instruction:
    return instruction
  if not instruction.strip():
    return project_instruction
  return f"{instruction.rstrip()}\n\n{project_instruction}"
