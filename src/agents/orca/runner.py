from __future__ import annotations

from pathlib import Path

from ..native_runner import make_native_agent_run
from .tools import build_session_context
from .tools import build_toolset


run = make_native_agent_run(
    config_path=Path(__file__).with_name("orca.agent.json"),
    prefix="orca",
    display_name="Orca",
    build_session_context=build_session_context,
    build_toolset=build_toolset,
)
