from __future__ import annotations

from src.project_instructions import append_project_agents_instruction
from src.project_instructions import load_project_agents_md
from src.project_instructions import render_project_agents_instruction


def test_render_project_agents_instruction_reads_root_only(tmp_path):
  nested = tmp_path / "src"
  nested.mkdir()
  (tmp_path / "AGENTS.md").write_text("Root rule.\n", encoding="utf-8")
  (nested / "AGENTS.md").write_text("Nested rule.\n", encoding="utf-8")

  instruction = render_project_agents_instruction(tmp_path)

  assert load_project_agents_md(tmp_path) == "Root rule."
  assert "Project Instructions (project_root/AGENTS.md)" in instruction
  assert "Root rule." in instruction
  assert "Nested rule." not in instruction


def test_append_project_agents_instruction_keeps_instruction_when_missing(tmp_path):
  assert append_project_agents_instruction("Base instruction.", tmp_path) == (
      "Base instruction."
  )


def test_append_project_agents_instruction_appends_after_base(tmp_path):
  (tmp_path / "AGENTS.md").write_text("Root rule.\n", encoding="utf-8")

  instruction = append_project_agents_instruction("Base instruction.", tmp_path)

  assert instruction == (
      "Base instruction.\n\n"
      "# Project Instructions (project_root/AGENTS.md)\n\n"
      "Root rule."
  )
