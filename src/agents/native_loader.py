from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable

from ..agent_runtime import AgentDefinition
from ..run_outcome import RunOutcome


NativeAgentRunner = Callable[..., Awaitable[RunOutcome]]


def list_agent_definitions() -> list[AgentDefinition]:
  from .browser.loader import list_agent_definitions as list_browser_definitions
  from .orca.loader import list_agent_definitions as list_orca_definitions

  return [
      *list_orca_definitions(),
      *list_browser_definitions(),
  ]


def load_agent(agent_id: str) -> NativeAgentRunner:
  normalized = agent_id.strip()
  if normalized == "orca":
    from .orca.loader import load_agent

    return load_agent(normalized)
  if normalized == "browser":
    from .browser.loader import load_agent

    return load_agent(normalized)
  available = ", ".join(sorted(definition.id for definition in list_agent_definitions()))
  raise ValueError(f"Unknown native agent_id: {normalized}. Available: {available}.")
