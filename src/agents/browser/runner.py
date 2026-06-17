from __future__ import annotations

from pathlib import Path

from ..native_runner import make_native_agent_run
from ..orca.tools import build_session_context
from ..orca.tools import build_toolset


run = make_native_agent_run(
    config_path=Path(__file__).with_name("browser.agent.json"),
    prefix="browser",
    display_name="Browser",
    build_session_context=build_session_context,
    build_toolset=build_toolset,
)
