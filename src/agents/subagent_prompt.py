from __future__ import annotations

from html import escape

from ..agent_runtime import list_agent_definitions


SELF_SUBAGENT = "self"


def render_subagent_instructions(subagent_names: list[str]) -> str:
  """Render the shared <subagents> prompt block for the given subagent names.

  Mirrors render_skill_instructions: a declarative list that tells the parent
  which sub-agents are available for delegation. Unlike skills, unknown names
  are rendered as saved configs rather than raising, because user-generated
  configs are not resolvable at build time.
  """
  if not subagent_names:
    return ""

  rendered = [
      "<subagents>",
      "You can delegate to the following sub-agents. Each runs in an isolated "
      "child session, so its verbose output stays out of your context.",
  ]
  for name in subagent_names:
    rendered.extend(_render_subagent(name))
  rendered.extend(
      [
          "</subagents>",
          "",
          "<subagent_usage>",
          "Delegate to a predefined sub-agent with run_agent(agent_id=...), and "
          "to a saved agent config with agents_start_run(name=...). Put the task "
          "goal, the steps to take, and the result to return into the prompt. "
          "After launching a delegation, if later steps depend on its result, "
          "stop the turn and wait for the system task notification — do not poll.",
          "</subagent_usage>",
      ]
  )
  return "\n".join(rendered)


def _render_subagent(name: str) -> list[str]:
  if name == SELF_SUBAGENT:
    description = (
        "Re-run this same agent in a fresh isolated child session to handle a "
        "focused or parallelizable subtask; invoke via run_agent with your own "
        "agent id."
    )
    kind = "self"
  else:
    config_description = _predefined_agent_description(name)
    if config_description is not None:
      description = config_description
      kind = "predefined"
    else:
      description = "A saved agent config; run it via agents_start_run."
      kind = "config"
  return [
      "<subagent>",
      f"  <name>{_xml_text(name)}</name>",
      f"  <kind>{_xml_text(kind)}</kind>",
      f"  <description>{_xml_text(description)}</description>",
      "</subagent>",
  ]


def _predefined_agent_description(agent_id: str) -> str | None:
  """Best-effort description for a built-in native agent."""
  for definition in list_agent_definitions():
    if definition.id == agent_id:
      return definition.description or agent_id
  return None


def _xml_text(value: str) -> str:
  return escape(value, quote=False)
