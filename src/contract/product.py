"""Read-only product metadata surface: agents, model configs, prompt previews.

Everything here is configuration and template rendering — listing agent
definitions, validating model config ids, rendering instruction text for
context previews. Importing this must never load agent implementations,
tools, or ADK/LangGraph runtime code (loaders enumerate agents statically).
"""
from __future__ import annotations

from ..agent_runtime import AgentDefinition as AgentDefinition
from ..agent_runtime import DEFAULT_WEB_AGENT_ID as DEFAULT_WEB_AGENT_ID
from ..agent_runtime import get_agent_definition as get_agent_definition
from ..agent_runtime import list_agent_definitions as list_agent_definitions
from ..agents.handa_adk.loader import AGENTS_DIR as ADK_AGENTS_DIR
from ..agents.handa_adk.loader import DEFAULT_AGENT_ID as DEFAULT_AGENT_ID
from ..agents.handa_langgraph.loader import MAIN_CONFIG_PATH as LANGGRAPH_MAIN_CONFIG_PATH
from ..agents.skill_prompt import render_skill_instructions as render_skill_instructions
from ..config import AgentConfig as AgentConfig
from ..config import agent_config_artifact_filename as agent_config_artifact_filename
from ..config import load_agent_config_from_path as load_agent_config_from_path
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
