from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from ..config_based import build_llm_agent_from_config
from ....config import load_agent_config_from_path


load_dotenv()
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
  os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

CONFIG = load_agent_config_from_path(Path(__file__).with_name("orca_adk.agent.json"))


def build_agent(*, project_root: str | None = None):
  return build_llm_agent_from_config(CONFIG, project_root=project_root)
