from __future__ import annotations

import re

import pytest

from src.config import AgentConfig
from src.instructions import get_default_main_agent_sections
from src.instructions import list_instruction_sections
from src.instructions import render_instruction


def test_render_default_instruction():
  instruction = render_instruction(
      section_names=get_default_main_agent_sections(),
      params={
          "agent_name": "HANDA",
      }
  )

  assert "# Identity" in instruction
  assert "# Tool Usage" in instruction
  assert "# HTML Output" in instruction
  assert "# Communication" in instruction
  assert "Respond in the user's language" in instruction
  assert "Respond in Chinese by default" not in instruction
  assert re.search(r"[\u4e00-\u9fff]", instruction) is None


def test_default_instruction_includes_chat_renderable_html_guidance():
  instruction = render_instruction(
      section_names=get_default_main_agent_sections(),
      params={
          "agent_name": "HANDA",
      },
  )

  assert "output only fragment-level HTML" in instruction
  assert "Do not use Markdown code fences" in instruction
  assert "Output single-line compact HTML only" in instruction
  assert "the final answer must not contain newline characters" in instruction
  assert "Do not split an HTML tag, attribute, style value" in instruction
  assert "Use inline CSS only" in instruction
  assert "Do not use `<style>`, classes, scripts" in instruction
  assert "You may embed SVG inside the HTML" in instruction
  assert "<div style=\"max-width:360px;" in instruction
  assert "<svg width=\"16\"" in instruction
  assert "```" not in instruction


def test_default_instruction_requires_user_confirmation_for_uncertainty():
  instruction = render_instruction(
      section_names=get_default_main_agent_sections(),
      params={
          "agent_name": "HANDA",
      },
  )

  assert "ask a clear question and wait for confirmation" in instruction
  assert "Do not guess, invent defaults" in instruction
  assert "replace confirmation with \"reasonable judgment\"" in instruction
  assert "first use the current repository and user goal to make a reasonable judgment" not in instruction


def test_render_selected_sections_with_params():
  instruction = render_instruction(
      section_names=["identity", "testing"],
      params={
          "agent_name": "TEST_AGENT",
      },
  )

  assert "You are TEST_AGENT" in instruction
  assert "# Testing And Verification" in instruction
  assert "# Tool Usage" not in instruction


def test_render_unknown_section_fails():
  with pytest.raises(ValueError, match="Unknown instruction section"):
    render_instruction(section_names=["missing_section"])


def test_render_requires_explicit_sections():
  with pytest.raises(ValueError, match="section_names must be provided"):
    render_instruction()


def test_list_instruction_sections_contains_default_sections():
  section_names = {section["name"] for section in list_instruction_sections()}

  assert set(get_default_main_agent_sections()).issubset(section_names)


def test_agent_config_can_select_instruction_sections():
  config = AgentConfig(
      name="handa",
      description="Test agent.",
      tools=["files_read"],
      skills=[],
      instruction_sections=["identity", "testing"],
      custom_instruction="Always write a concise report.",
  )

  instruction = render_instruction(
      section_names=config.instruction_sections,
      params={
          "agent_name": config.name.upper(),
      },
      custom_instruction=config.custom_instruction,
  )

  assert "# Identity" in instruction
  assert "# Testing And Verification" in instruction
  assert "# Agent Config" not in instruction
  assert instruction.endswith("Always write a concise report.")
