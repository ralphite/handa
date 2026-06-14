from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from google.adk.agents import BaseAgent


DEFAULT_AGENT_ID: Final = "orca_adk"
AGENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AGENTS_DIR: Final = Path(__file__).resolve().parent


class AgentLoadError(ValueError):
  pass


def list_agents() -> list[str]:
  agent_ids: list[str] = []
  for path in sorted(AGENTS_DIR.iterdir()):
    if not path.is_dir() or path.name.startswith("_"):
      continue
    if _has_agent_builder(path):
      agent_ids.append(path.name)
  return agent_ids


def _has_agent_builder(package_dir: Path) -> bool:
  # Static check on purpose: listing agents must not import agent modules
  # (each pulls in the full ADK/tool stack), so metadata consumers like the
  # Web API can enumerate agents without loading runtime code.
  init_file = package_dir / "__init__.py"
  if not init_file.is_file():
    return False
  try:
    text = init_file.read_text(encoding="utf-8")
  except OSError:
    return False
  if "build_agent" in text:
    return True
  module_file = package_dir / f"{package_dir.name}.py"
  if module_file.is_file():
    try:
      return "def build_agent" in module_file.read_text(encoding="utf-8")
    except OSError:
      return False
  return False


def load_agent(
    agent_id: str = DEFAULT_AGENT_ID,
    *,
    project_root: str | None = None,
) -> BaseAgent:
  from google.adk.agents import BaseAgent

  normalized = validate_agent_id(agent_id)
  module = importlib.import_module(f"src.agents.handa_adk.{normalized}")
  build_agent = getattr(module, "build_agent", None)
  if not isinstance(build_agent, Callable):
    raise AgentLoadError(
        f"Agent {normalized!r} must expose build_agent(project_root=...)."
    )
  agent = build_agent(project_root=project_root)
  if not isinstance(agent, BaseAgent):
    raise AgentLoadError(
        f"Agent {normalized!r} build_agent() must return an ADK BaseAgent."
    )
  _record_handa_origin(agent, normalized)
  return agent


def validate_agent_id(agent_id: str) -> str:
  normalized = agent_id.strip()
  if not normalized:
    raise AgentLoadError("agent_id must not be empty.")
  if not AGENT_ID_PATTERN.fullmatch(normalized):
    raise AgentLoadError(f"Invalid agent_id: {agent_id!r}.")
  if normalized not in list_agents():
    available = ", ".join(list_agents()) or "(none)"
    raise AgentLoadError(f"Unknown agent_id: {normalized}. Available: {available}.")
  return normalized


def _record_handa_origin(agent: BaseAgent, agent_id: str) -> None:
  # ADK uses these optional attributes for app-name diagnostics. Handa has one
  # product app name and multiple agent ids, so the origin app is always handa.
  setattr(agent, "_adk_origin_app_name", "handa")
  setattr(agent, "_adk_origin_path", AGENTS_DIR / agent_id)
