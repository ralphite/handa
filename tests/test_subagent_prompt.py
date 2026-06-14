from __future__ import annotations

from src.agents.subagent_prompt import render_subagent_instructions


def test_empty_returns_blank():
  assert render_subagent_instructions([]) == ""


def test_renders_self_and_predefined_browser():
  rendered = render_subagent_instructions(["self", "browser"])

  assert "<subagents>" in rendered
  assert "<name>self</name>" in rendered
  assert "<kind>self</kind>" in rendered
  assert "<name>browser</name>" in rendered
  assert "<kind>predefined</kind>" in rendered
  # Predefined agents resolve their real description from <name>.agent.json.
  assert "<description>A saved agent config" not in rendered.split("browser")[1]
  assert "run_agent" in rendered


def test_unknown_name_renders_as_saved_config_without_raising():
  rendered = render_subagent_instructions(["totally_made_up_agent"])

  assert "<name>totally_made_up_agent</name>" in rendered
  assert "<kind>config</kind>" in rendered
  assert "agents_start_run" in rendered
