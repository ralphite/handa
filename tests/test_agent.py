from __future__ import annotations

import pytest

from src.agents.browser.loader import MAIN_CONFIG_PATH as BROWSER_MAIN_CONFIG_PATH
from src.agents.native_loader import list_agent_definitions
from src.agents.orca.tools import SessionContext
from src.agents.orca.tools import build_toolset
from src.agents.ralph.loader import MAIN_CONFIG_PATH as RALPH_MAIN_CONFIG_PATH
from src.agents.tool_catalog import known_agent_tool_names
from src.config import load_agent_config
from src.config import load_agent_config_from_path


def test_native_agent_loader_lists_built_ins():
  listed = list_agent_definitions()
  definitions = {definition.id: definition for definition in listed}

  assert [definition.id for definition in listed] == ["orca", "browser", "ralph"]
  assert set(definitions) == {"browser", "orca", "ralph"}
  assert definitions["orca"].runtime == "native"
  assert definitions["browser"].runtime == "native"
  assert definitions["ralph"].runtime == "native"


def test_native_tool_catalog_matches_buildable_toolset():
  toolset = build_toolset(
      sorted(known_agent_tool_names()),
      SessionContext(session_id="session-tools", user_id="user"),
  )

  assert set(toolset.callables) == set(known_agent_tool_names())


def test_build_toolset_rejects_unknown_name():
  with pytest.raises(ValueError, match="Unknown agent tools"):
    build_toolset(
        ["files_read", "missing_tool"],
        SessionContext(session_id="session-tools", user_id="user"),
    )


def test_main_agent_config_selects_explicit_tools():
  config = load_agent_config()
  toolset = build_toolset(
      config.tools,
      SessionContext(session_id="session-main", user_id="user"),
  )

  assert config.skills == ["chat-session-analysis", "qa", "vcs-jj"]
  tool_names = set(toolset.callables)
  assert "files_read" in tool_names
  assert "commands_run" in tool_names
  assert "run_agent" in tool_names
  assert "agents_save_config" in tool_names
  assert "agents_read_config" in tool_names
  assert "agents_list_configs" in tool_names
  assert "agents_start_run" in tool_names
  assert "agents_read_run_result" in tool_names
  assert "artifacts_save_text" in tool_names
  assert "artifacts_list" in tool_names
  for name in {
      "browser_open",
      "browser_snapshot",
      "browser_click",
      "browser_type",
      "browser_keys",
      "browser_scroll",
      "browser_wait",
      "browser_screenshot",
      "browser_close",
  }:
    assert name not in tool_names


def test_browser_sub_agent_owns_browser_tools():
  config = load_agent_config_from_path(BROWSER_MAIN_CONFIG_PATH)
  assert config.name == "browser"
  assert config.skills == []
  toolset = build_toolset(
      config.tools,
      SessionContext(session_id="session-browser", user_id="user"),
  )
  tool_names = set(toolset.callables)
  for name in {
      "browser_open",
      "browser_snapshot",
      "browser_click",
      "browser_type",
      "browser_keys",
      "browser_scroll",
      "browser_wait",
      "browser_screenshot",
      "browser_close",
  }:
    assert name in tool_names
  assert "skills_read" not in tool_names
  assert "commands_run" not in tool_names


def test_ralph_internal_agents_keep_vcs_skill():
  builder_config = load_agent_config_from_path(
      RALPH_MAIN_CONFIG_PATH.parent / "ralph_builder.agent.json"
  )
  verifier_config = load_agent_config_from_path(
      RALPH_MAIN_CONFIG_PATH.parent / "ralph_verifier.agent.json"
  )
  planner_config = load_agent_config_from_path(
      RALPH_MAIN_CONFIG_PATH.parent / "ralph_planner.agent.json"
  )

  assert builder_config.skills == ["vcs-jj"]
  assert verifier_config.skills == ["vcs-jj"]
  assert planner_config.skills == []
