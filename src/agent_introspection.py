"""Runtime introspection export.

Tool definition texts come from inspecting live tool functions, which means
importing every tool implementation — something the Web process must not do.
This module runs on the runtime side (spawned as `python -m
src.agent_introspection`) and exports the definitions as plain data under the
storage root; readers consume the JSON via src.contract.introspection.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

from .contract.introspection import tool_definitions_path
from .storage.file_io import atomic_write_text
from .storage.paths import resolve_storage_root


def export_tool_definitions(root: Path | str | None = None) -> Path:
  from .agents.orca.tools import build_toolset
  from .agents.orca.tools import SessionContext
  from .agents.tool_catalog import known_agent_tool_names

  ctx = SessionContext(session_id="introspection", user_id="user")
  toolset = build_toolset(sorted(known_agent_tool_names()), ctx)
  definitions = []
  for tool_name, function in sorted(toolset.callables.items()):
    definitions.append(
        {
            "name": tool_name,
            "namespace": _tool_namespace(tool_name),
            "text": _tool_definition_text(tool_name, function),
        }
    )
  path = tool_definitions_path(resolve_storage_root(root))
  path.parent.mkdir(parents=True, exist_ok=True)
  atomic_write_text(
      path,
      json.dumps({"tools": definitions}, ensure_ascii=True, indent=2) + "\n",
  )
  return path


def _tool_namespace(tool_name: str) -> str:
  prefix = tool_name.split("_", 1)[0]
  if prefix in {
      "agents",
      "artifacts",
      "browser",
      "commands",
      "files",
      "notifications",
      "notes",
      "progress",
      "skills",
      "tasks",
  }:
    return prefix
  return ""


def _tool_definition_text(tool_name: str, function) -> str:
  try:
    signature = str(inspect.signature(function))
  except (TypeError, ValueError):
    signature = "()"
  doc = inspect.getdoc(function) or ""
  return f"{tool_name}{signature}\n{doc}".strip()


def main() -> int:
  export_tool_definitions()
  return 0


if __name__ == "__main__":
  sys.exit(main())
