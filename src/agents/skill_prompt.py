from __future__ import annotations

from html import escape

from ..tools import skills


def render_skill_instructions(skill_names: list[str]) -> str:
  """Render the shared <skills> prompt block for the given skill names.

  Used by every native agent so skill exposure stays identical across agents.
  """
  if not skill_names:
    return ""

  rendered = ["<skills>", "You have the following skills."]
  for skill_name in skill_names:
    result = skills.describe(skill_name)
    if not result.get("success"):
      raise ValueError(f"Unknown skill in agent config: {skill_name}")
    rendered.extend(
        [
            "<skill>",
            f"  <name>{_xml_text(str(result.get('skill_name') or skill_name))}</name>",
            (
                "  <description>"
                f"{_xml_text(str(result.get('description') or ''))}"
                "</description>"
            ),
            f"  <source>{_xml_text(str(result.get('source') or 'unknown'))}</source>",
            f"  <path>{_xml_text(str(result['path']))}</path>",
            "</skill>",
        ]
    )
  rendered.extend(
      [
          "</skills>",
          "",
          "<skill_usage>",
          "To use a skill, read the SKILL.md file at its <path> directly before applying it. "
          "The SKILL.md file is the source of truth for that skill. If the skill points to "
          "related files such as references, examples, templates, scripts, or assets, read "
          "only the relevant files you need. Do not preload unrelated skill files. Follow the "
          "skill's own design and instructions.",
          "</skill_usage>",
      ]
  )
  return "\n".join(rendered)


def _xml_text(value: str) -> str:
  return escape(value, quote=False)
