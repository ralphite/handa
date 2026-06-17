"""Read-only product metadata surface: agents, model configs, prompt previews.

Everything here is configuration and template rendering — listing native agent
definitions, validating model config ids, and rendering instruction text for
context previews. Importing this must never load agent implementations.
"""
from __future__ import annotations

from ..agent_runtime import AgentDefinition as AgentDefinition
from ..agent_runtime import DEFAULT_WEB_AGENT_ID as DEFAULT_WEB_AGENT_ID
from ..agent_runtime import get_agent_definition as get_agent_definition
from ..agent_runtime import list_agent_definitions as list_agent_definitions
from ..agent_runtime import resolve_agent_id_for_runtime as resolve_agent_id_for_runtime
from ..agent_runtime import DEFAULT_WEB_AGENT_ID as DEFAULT_AGENT_ID
from ..agents.browser.loader import MAIN_CONFIG_PATH as BROWSER_MAIN_CONFIG_PATH
from ..agents.orca.loader import MAIN_CONFIG_PATH as ORCA_MAIN_CONFIG_PATH
from ..agents.ralph.loader import MAIN_CONFIG_PATH as RALPH_MAIN_CONFIG_PATH
from ..agents.skill_prompt import render_skill_instructions as render_skill_instructions
from ..config import AgentConfig as AgentConfig
from ..config import agent_config_artifact_filename as agent_config_artifact_filename
from ..config import load_agent_config_from_path as load_agent_config_from_path
from .hooks import normalize_hooks as normalize_hooks
from ..instructions import render_instruction as render_instruction
from ..instructions import SECTIONS as INSTRUCTION_SECTIONS
from ..model_configs import DEFAULT_MODEL_CONFIG_ID as DEFAULT_MODEL_CONFIG_ID
from ..model_configs import list_model_config_options as list_model_config_options
from ..model_configs import validate_model_config_id as validate_model_config_id
from ..model_configs import with_default_model_retry_options as with_default_model_retry_options
from ..observability import setup_phoenix_tracing as setup_phoenix_tracing
from ..progress import normalize_progress_items as normalize_progress_items
from ..progress import PROGRESS_STATE_KEY as PROGRESS_STATE_KEY
from ..project_instructions import render_project_agents_instruction as render_project_agents_instruction
from ..tools.skills import list as list_skills


def load_agent_config_for_agent(agent_id: str) -> AgentConfig | None:
  normalized = get_agent_definition(agent_id).id
  path = {
      "orca": ORCA_MAIN_CONFIG_PATH,
      "browser": BROWSER_MAIN_CONFIG_PATH,
      "ralph": RALPH_MAIN_CONFIG_PATH,
  }.get(normalized)
  return load_agent_config_from_path(path) if path is not None else None


def hooks_for_agent(agent_id: str) -> list[dict]:
  config = load_agent_config_for_agent(agent_id)
  return normalize_hooks(config.hooks if config is not None else [])
