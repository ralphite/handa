from __future__ import annotations

from src.agent_runtime import get_agent_definition
from src.agent_runtime import list_agent_definitions


def test_agent_registry_lists_native_agents_only():
  listed = list_agent_definitions()
  definitions = {definition.id: definition for definition in listed}

  assert [definition.id for definition in listed] == ["orca", "browser"]
  assert set(definitions) == {"browser", "orca"}
  assert definitions["browser"].runtime == "native"
  assert definitions["browser"].entrypoint == "src.agents.browser.runner:run"
  assert definitions["browser"].label == "Browser"
  assert definitions["orca"].runtime == "native"
  assert definitions["orca"].entrypoint == "src.agents.orca.runner:run"
  assert definitions["orca"].label == "Orca"


def test_agent_definition_excludes_entrypoint_from_model_dump():
  definition = get_agent_definition("orca")

  assert definition.runtime == "native"
  assert "entrypoint" not in definition.model_dump()
  assert get_agent_definition("browser").runtime == "native"
