from __future__ import annotations

import asyncio
import typing

import pytest

from src.agent import select_agent_tools
from src.agents.handa_adk.loader import list_agents
from src.agents.handa_adk.loader import load_agent
from src.agents.handa_adk.tools import get_tool_registry
from src.agents.browser.loader import MAIN_CONFIG_PATH as BROWSER_MAIN_CONFIG_PATH
from src.agents.orca.tools import SessionContext
from src.agents.orca.tools import build_toolset
from src.config import load_agent_config_from_path
from src.agents.handa_adk.tools.registry import ToolSpec
from src.agents.handa_adk.tools.registry import _wrap_tool_function
from src.agents.tool_catalog import known_agent_tool_names


def _tool_names(toolsets):
  return [tool.name for tool in toolsets]


def test_select_agent_tools_returns_empty_when_empty():
  toolsets = select_agent_tools([])

  assert toolsets == []


def test_select_agent_tools_filters_by_name():
  toolsets = select_agent_tools(["files_read", "commands_run"])

  assert _tool_names(toolsets) == ["files_read", "commands_run"]


def test_select_agent_tools_rejects_unknown_name():
  with pytest.raises(ValueError, match="Unknown agent tools"):
    select_agent_tools(["missing_tool"])


def test_tool_registry_uses_namespaced_names():
  registry = get_tool_registry()

  assert "run_agent" in registry
  assert registry["run_agent"].namespace == ""
  assert "agents_save_config" in registry
  assert registry["agents_save_config"].namespace == "agents"
  assert registry["agents_save_config"].name == "save_config"
  assert "vcs_jj_status" not in registry
  assert "vcs_jj_log" not in registry


def test_shared_tool_catalog_matches_adk_registry():
  assert known_agent_tool_names() == frozenset(get_tool_registry())


def test_agent_loader_lists_and_builds_agents():
  assert {"orca_adk", "ralph"}.issubset(set(list_agents()))
  assert load_agent("orca_adk").name == "orca_adk"
  assert load_agent("ralph").name == "ralph_agent"


def test_agent_loader_adds_project_agents_to_system_instruction(tmp_path):
  (tmp_path / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")

  agent = load_agent("orca_adk", project_root=str(tmp_path))

  assert "Project Instructions (project_root/AGENTS.md)" in agent.instruction
  assert "Answer in Chinese." in agent.instruction


def test_main_agent_config_selects_explicit_tools():
  from src.config import load_agent_config

  config = load_agent_config()
  toolsets = select_agent_tools(config.tools)

  assert config.skills == []
  tool_names = set(_tool_names(toolsets))
  assert "files_read" in tool_names
  assert "commands_run" in tool_names
  assert "vcs_jj_status" not in tool_names
  assert "vcs_jj_log" not in tool_names
  assert "agent_get_config" not in tool_names
  assert "run_agent" in tool_names
  assert "agents_save_config" in tool_names
  assert "agents_read_config" in tool_names
  assert "agents_list_configs" in tool_names
  assert "agents_start_run" in tool_names
  assert "agents_read_run_result" in tool_names
  assert "artifacts_save_text" in tool_names
  assert "artifacts_list" in tool_names
  # Browser automation is delegated to the dedicated `browser` sub-agent, so the
  # main agent must not carry the browser_* tools itself.
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


def test_function_tool_wrapper_adds_ok_to_dict_result():
  def sample() -> dict:
    return {"value": 1}

  wrapped = _wrap_tool_function(ToolSpec("sample", "read", sample))

  assert wrapped() == {"ok": True, "value": 1}


def test_function_tool_wrapper_wraps_non_dict_result():
  def sample() -> str:
    return "done"

  wrapped = _wrap_tool_function(ToolSpec("sample", "read", sample))

  assert wrapped() == {"ok": True, "result": "done"}


def test_function_tool_wrapper_returns_error_result_for_exception():
  def sample() -> dict:
    raise ValueError("bad input")

  wrapped = _wrap_tool_function(ToolSpec("sample", "read", sample))

  assert wrapped() == {
      "ok": False,
      "error": {
          "type": "ValueError",
          "message": "bad input",
          "tool": "sample_read",
      },
  }


def test_function_tool_wrapper_returns_error_result_for_async_exception():
  async def sample() -> dict:
    raise RuntimeError("async failed")

  wrapped = _wrap_tool_function(ToolSpec("sample", "read", sample))

  assert asyncio.run(wrapped()) == {
      "ok": False,
      "error": {
          "type": "RuntimeError",
          "message": "async failed",
          "tool": "sample_read",
      },
  }


def test_function_tool_wrapper_exposes_tool_context_type_hints():
  from google.adk.tools import ToolContext

  def sample(tool_context: ToolContext) -> dict:
    return {"session_id": getattr(tool_context, "_invocation_context", None)}

  wrapped = _wrap_tool_function(ToolSpec("sample", "read", sample))

  hints = typing.get_type_hints(wrapped)

  assert hints["tool_context"] is ToolContext
  assert not hasattr(wrapped, "__wrapped__")
