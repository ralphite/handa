from __future__ import annotations

import re
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config import load_agent_config_from_path
from ...agent_runtime import AgentDefinition
from ...run_outcome import RunOutcome


AgentEventEmitter = Callable[[dict[str, Any]], Awaitable[None]]
LangGraphAgentRunner = Callable[..., Awaitable[RunOutcome]]
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAIN_CONFIG_PATH = Path(__file__).resolve().with_name("orca.agent.json")


def list_agent_definitions() -> list[AgentDefinition]:
  config = load_agent_config_from_path(MAIN_CONFIG_PATH)
  return [
      AgentDefinition(
          id="orca_langgraph",
          runtime="langgraph",
          entrypoint="src.agents.handa_langgraph.orca:run",
          label="Orca LangGraph",
          description=config.description,
      )
  ]


def load_agent(agent_id: str) -> LangGraphAgentRunner:
  normalized = _normalize_legacy_agent_id(agent_id)
  if normalized == "orca_langgraph":
    from .orca import run

    return run
  raise ValueError(f"Unknown LangGraph agent_id: {normalized}")


def validate_agent_id(agent_id: str) -> str:
  normalized = agent_id.strip()
  if not normalized:
    raise ValueError("agent_id must not be empty.")
  if not AGENT_ID_PATTERN.fullmatch(normalized):
    raise ValueError(f"Invalid agent_id: {agent_id!r}.")
  normalized = _normalize_legacy_agent_id(normalized)
  known = {definition.id for definition in list_agent_definitions()}
  if normalized not in known:
    available = ", ".join(sorted(known)) or "(none)"
    raise ValueError(f"Unknown LangGraph agent_id: {normalized}. Available: {available}.")
  return normalized


def _normalize_legacy_agent_id(agent_id: str) -> str:
  normalized = agent_id.strip()
  if normalized == "orca":
    return "orca_langgraph"
  return normalized
