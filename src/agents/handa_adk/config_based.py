from __future__ import annotations

from google.adk.agents import Agent

from ...config import AgentConfig
from ...config import resolve_agent_config_model_config_id
from ...instructions import render_instruction
from ...model_configs import resolve_model_config
from ...project_instructions import append_project_agents_instruction
from ..skill_prompt import render_skill_instructions
from ..subagent_prompt import render_subagent_instructions
from .tools import select_agent_tools


def build_llm_agent_from_config(
    config: AgentConfig,
    *,
    project_name: str = "handa",
    project_root: str | None = None,
    model_config_id: str | None = None,
    allow_config_model: bool = True,
) -> Agent:
  resolved_model_config_id = resolve_agent_config_model_config_id(
      config,
      inherited_model_config_id=model_config_id,
      allow_config_model=allow_config_model,
  )
  runtime_model_config = resolve_model_config(resolved_model_config_id)
  instruction = render_instruction(
      section_names=config.instruction_sections,
      params={
          "agent_name": config.name.upper(),
          "project_name": project_name,
      },
  )
  skill_instruction = render_skill_instructions(config.skills)
  if skill_instruction:
    instruction = f"{instruction}\n\n{skill_instruction}"
  subagent_instruction = render_subagent_instructions(config.subagents)
  if subagent_instruction:
    instruction = f"{instruction}\n\n{subagent_instruction}"
  if config.custom_instruction and config.custom_instruction.strip():
    instruction = f"{instruction}\n\n{config.custom_instruction.strip()}"
  instruction = append_project_agents_instruction(instruction, project_root)

  return Agent(
      model=runtime_model_config.model,
      name=config.name,
      description=config.description,
      instruction=instruction,
      generate_content_config=runtime_model_config.generate_content_config,
      tools=select_agent_tools(config.tools),  # type: ignore[arg-type]
  )
