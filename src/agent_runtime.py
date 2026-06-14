from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Literal

from .agents.handa_adk.loader import AGENTS_DIR as ADK_AGENTS_DIR
from .agents.handa_adk.loader import list_agents as list_adk_agents
from .config import load_agent_config_from_path


AgentRuntime = Literal["adk", "langgraph"]
DEFAULT_WEB_AGENT_ID = "orca"
MAIN_AGENT_LABELS = {
    "orca_adk": "Orca ADK",
    "orca": "Orca",
}


@dataclass(frozen=True)
class AgentDefinition:
  id: str
  runtime: AgentRuntime
  entrypoint: str
  label: str
  description: str = ""

  def runtime_snapshot(self) -> dict[str, str]:
    return {"agent_runtime": self.runtime}

  def model_dump(self) -> dict[str, str]:
    data = asdict(self)
    data.pop("entrypoint", None)
    return data


def list_agent_definitions() -> list[AgentDefinition]:
  definitions = [
      AgentDefinition(
          id=agent_id,
          runtime="adk",
          entrypoint=f"src.agents.handa_adk.{agent_id}:build_agent",
          label=_adk_agent_label(agent_id),
          description="Handa ADK agent",
      )
      for agent_id in list_adk_agents()
  ]
  definitions.extend(_langgraph_agent_definitions())
  return sorted(definitions, key=lambda item: (item.runtime, item.id))


def get_agent_definition(agent_id: str) -> AgentDefinition:
  normalized = agent_id.strip()
  for definition in list_agent_definitions():
    if definition.id == normalized:
      return definition
  available = ", ".join(item.id for item in list_agent_definitions()) or "(none)"
  raise ValueError(f"Unknown agent_id: {normalized}. Available: {available}.")


def validate_agent_id(agent_id: str) -> str:
  return get_agent_definition(agent_id).id


def agent_config_runtime_snapshot(
    *,
    config_name: str,
    config_version: int | None,
) -> dict[str, str]:
  _ = config_name, config_version
  return {"agent_runtime": "adk"}


def system_agent_config_runtime_snapshot(config_name: str) -> dict[str, str]:
  _ = config_name
  return {"agent_runtime": "adk"}


def _adk_agent_label(agent_id: str) -> str:
  if agent_id in MAIN_AGENT_LABELS:
    return MAIN_AGENT_LABELS[agent_id]
  config_path = ADK_AGENTS_DIR / agent_id / f"{agent_id}.agent.json"
  if not config_path.exists():
    return agent_id
  return load_agent_config_from_path(config_path).name


def _langgraph_agent_definitions() -> list[AgentDefinition]:
  from .agents.handa_langgraph.loader import list_agent_definitions

  return list_agent_definitions()
