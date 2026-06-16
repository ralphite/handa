from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Literal


AgentRuntime = Literal["native"]
DEFAULT_WEB_AGENT_ID = "orca"


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
  return sorted(_native_agent_definitions(), key=lambda definition: definition.id)


def get_agent_definition(agent_id: str) -> AgentDefinition:
  normalized = _normalize_agent_id(agent_id)
  for definition in list_agent_definitions():
    if definition.id == normalized:
      return definition
  available = ", ".join(item.id for item in list_agent_definitions()) or "(none)"
  raise ValueError(f"Unknown agent_id: {normalized}. Available: {available}.")


def validate_agent_id(agent_id: str) -> str:
  return get_agent_definition(agent_id).id


def resolve_agent_id_for_runtime(agent_id: str, agent_runtime: str) -> str:
  runtime = agent_runtime.strip() or "native"
  if runtime != "native":
    raise ValueError(f"Unsupported agent runtime: {runtime!r}. Handa only supports native agents.")
  definition = get_agent_definition(agent_id)
  return definition.id


def agent_config_runtime_snapshot(
    *,
    config_name: str,
    config_version: int | None,
) -> dict[str, str]:
  _ = config_name, config_version
  return {"agent_runtime": "native"}


def system_agent_config_runtime_snapshot(config_name: str) -> dict[str, str]:
  _ = config_name
  return {"agent_runtime": "native"}


def _native_agent_definitions() -> list[AgentDefinition]:
  from .agents.native_loader import list_agent_definitions

  return list_agent_definitions()


def _normalize_agent_id(agent_id: str) -> str:
  normalized = agent_id.strip()
  if normalized in {"main", "orca_adk", "orca_langgraph"}:
    return "orca"
  return normalized
