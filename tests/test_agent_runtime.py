from __future__ import annotations

from src.agent_runtime import agent_config_runtime_snapshot
from src.agent_runtime import get_agent_definition
from src.agent_runtime import list_agent_definitions


def test_agent_registry_lists_adk_and_langgraph_agents():
  definitions = {definition.id: definition for definition in list_agent_definitions()}

  assert definitions["orca_adk"].runtime == "adk"
  assert definitions["orca_adk"].entrypoint == "src.agents.handa_adk.orca_adk:build_agent"
  assert definitions["orca_adk"].label == "Orca ADK"
  assert definitions["orca"].runtime == "langgraph"
  assert definitions["orca"].entrypoint == "src.agents.handa_langgraph.orca:run"
  assert definitions["orca"].label == "Orca"


def test_agent_definition_runtime_snapshot_is_persistable():
  snapshot = get_agent_definition("orca").runtime_snapshot()

  assert snapshot == {"agent_runtime": "langgraph"}
  assert "entrypoint" not in get_agent_definition("orca").model_dump()


def test_agent_config_snapshot_keeps_runtime_internal_to_adk():
  snapshot = agent_config_runtime_snapshot(
      config_name="builder",
      config_version=2,
  )

  assert snapshot == {"agent_runtime": "adk"}
