from __future__ import annotations

from src.agent_runtime import agent_config_runtime_snapshot
from src.agent_runtime import get_agent_definition
from src.agent_runtime import list_agent_definitions


def test_agent_registry_lists_orca_variants():
  definitions = {definition.id: definition for definition in list_agent_definitions()}

  assert definitions["browser"].runtime == "native"
  assert definitions["browser"].entrypoint == "src.agents.browser.runner:run"
  assert definitions["browser"].label == "browser"
  assert definitions["orca"].runtime == "native"
  assert definitions["orca"].entrypoint == "src.agents.orca.runner:run"
  assert definitions["orca"].label == "Orca"
  assert definitions["ralph"].runtime == "native"
  assert definitions["ralph"].entrypoint == "src.agents.ralph.runner:run"
  assert definitions["ralph"].label == "ralph"
  assert definitions["orca_adk"].runtime == "adk"
  assert definitions["orca_adk"].entrypoint == "src.agents.handa_adk.orca_adk:build_agent"
  assert definitions["orca_adk"].label == "Orca ADK"
  assert definitions["orca_langgraph"].runtime == "langgraph"
  assert definitions["orca_langgraph"].entrypoint == "src.agents.handa_langgraph.orca:run"
  assert definitions["orca_langgraph"].label == "Orca LangGraph"
  assert len(definitions) == len(list_agent_definitions())


def test_agent_definition_runtime_snapshot_is_persistable():
  snapshot = get_agent_definition("orca").runtime_snapshot()

  assert snapshot == {"agent_runtime": "native"}
  assert "entrypoint" not in get_agent_definition("orca").model_dump()
  assert get_agent_definition("browser").runtime_snapshot() == {"agent_runtime": "native"}
  assert get_agent_definition("ralph").runtime_snapshot() == {"agent_runtime": "native"}


def test_agent_config_snapshot_keeps_runtime_internal_to_adk():
  snapshot = agent_config_runtime_snapshot(
      config_name="builder",
      config_version=2,
  )

  assert snapshot == {"agent_runtime": "adk"}
