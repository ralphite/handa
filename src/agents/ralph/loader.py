from __future__ import annotations

import re
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...agent_runtime import AgentDefinition
from ...config import load_agent_config_from_path
from ...run_outcome import RunOutcome


RalphAgentRunner = Callable[..., Awaitable[RunOutcome]]
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAIN_CONFIG_PATH = Path(__file__).resolve().with_name("ralph.agent.json")


def list_agent_definitions() -> list[AgentDefinition]:
  config = load_agent_config_from_path(MAIN_CONFIG_PATH)
  return [
      AgentDefinition(
          id="ralph",
          runtime="native",
          entrypoint="src.agents.ralph.runner:run",
          label="Ralph",
          description=config.description,
      )
  ]


def load_agent(agent_id: str) -> RalphAgentRunner:
  normalized = validate_agent_id(agent_id)
  if normalized == "ralph":
    from .runner import run

    return run
  raise ValueError(f"Unknown Ralph agent_id: {normalized}")


def validate_agent_id(agent_id: str) -> str:
  normalized = agent_id.strip()
  if not normalized:
    raise ValueError("agent_id must not be empty.")
  if not AGENT_ID_PATTERN.fullmatch(normalized):
    raise ValueError(f"Invalid agent_id: {agent_id!r}.")
  if normalized != "ralph":
    raise ValueError(f"Unknown Ralph agent_id: {normalized}. Available: ralph.")
  return normalized
