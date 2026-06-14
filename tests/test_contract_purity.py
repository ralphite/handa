from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


# Modules whose presence in sys.modules means runtime implementation code
# leaked into the Web process. google.adk itself is absent on purpose:
# HandaSessionService subclasses ADK's BaseSessionService (the runner demands
# it), which drags in ADK's data-model layer — accepted as the Python client
# convenience; the execution surface is covered by the src.* entries below.
FORBIDDEN_MODULE_PREFIXES = (
    "playwright",
    "langgraph",
    "src.run_manager",
    "src.turn_worker",
    "src.agent_run_worker",
    "src.task_worker",
    "src.agents.handa_adk.orca_adk",
    "src.agents.handa_adk.ralph",
    "src.agents.handa_adk.browser",
    "src.agents.handa_adk.tools",
    "src.tools.commands",
    "src.tools.files",
    "src.tools.browser",
)

_PROBE = """
import json
import sys

import src.contract.browser
import src.contract.introspection
import src.contract.parent_runs
import src.contract.product
import src.contract.run_events
import src.contract.services
import src.contract.storage
import src.contract.task_store
import src.contract.turn_trace
import src.contract.user_input
from src.api.app import create_app

create_app()
print(json.dumps(sorted(sys.modules)))
"""


def test_contract_and_web_app_do_not_load_runtime_code(tmp_path):
  result = subprocess.run(
      [sys.executable, "-c", _PROBE],
      capture_output=True,
      text=True,
      cwd=str(Path(__file__).resolve().parents[1]),
      env={
          "PATH": "/usr/bin:/bin",
          "HANDA_STORAGE_ROOT": str(tmp_path / ".handa"),
          "HOME": str(tmp_path),
      },
      timeout=120,
  )
  assert result.returncode == 0, result.stderr
  loaded = json.loads(result.stdout.splitlines()[-1])
  violations = [
      module
      for module in loaded
      if module.startswith(FORBIDDEN_MODULE_PREFIXES)
  ]
  assert violations == [], violations


def test_introspection_export_roundtrip(tmp_path, monkeypatch):
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
  from src.agent_introspection import export_tool_definitions
  from src.contract.introspection import read_tool_catalog
  from src.contract.introspection import read_tool_definitions

  path = export_tool_definitions(tmp_path / ".handa")
  assert path.is_file()
  definitions = read_tool_definitions(tmp_path / ".handa")
  assert definitions, "exporter produced no tool definitions"
  sample_name, sample_text = next(iter(definitions.items()))
  assert sample_name in sample_text

  catalog = read_tool_catalog(tmp_path / ".handa")
  by_name = {item["name"]: item for item in catalog}
  assert by_name.keys() == definitions.keys()
  assert by_name["files_read"]["namespace"] == "files"
  assert by_name["run_agent"]["namespace"] == ""
