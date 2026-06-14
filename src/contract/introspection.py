"""Reader side of the runtime introspection export.

Tool definition texts are produced by `python -m src.agent_introspection`
(which loads the tool implementations) and consumed here as plain JSON, so
Web-side context previews never import tool code. `refresh_tool_definitions`
spawns the exporter out of process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..storage.paths import resolve_storage_root
from .task_store import get_product_root


def tool_definitions_path(root: Path | str | None = None) -> Path:
  return resolve_storage_root(root) / "introspection" / "tool_definitions.json"


def read_tool_definitions(root: Path | str | None = None) -> dict[str, str]:
  """Tool name -> definition text; empty until the exporter has run."""
  path = tool_definitions_path(root)
  if not path.is_file():
    return {}
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}
  tools = data.get("tools") if isinstance(data, dict) else None
  if not isinstance(tools, list):
    return {}
  result: dict[str, str] = {}
  for item in tools:
    if isinstance(item, dict) and item.get("name"):
      result[str(item["name"])] = str(item.get("text") or "")
  return result


def read_tool_catalog(root: Path | str | None = None) -> list[dict[str, str]]:
  """Tool entries as {name, namespace, text}; empty until the exporter has run.

  Exports written before the namespace field existed yield namespace "", which
  consumers must treat as ungrouped.
  """
  path = tool_definitions_path(root)
  if not path.is_file():
    return []
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return []
  tools = data.get("tools") if isinstance(data, dict) else None
  if not isinstance(tools, list):
    return []
  result: list[dict[str, str]] = []
  for item in tools:
    if isinstance(item, dict) and item.get("name"):
      result.append(
          {
              "name": str(item["name"]),
              "namespace": str(item.get("namespace") or ""),
              "text": str(item.get("text") or ""),
          }
      )
  return result


REFRESH_MAX_AGE_SECONDS = 3600.0


def refresh_tool_definitions(root: Path | str | None = None) -> Any | None:
  """Spawn the runtime-side exporter unless the export is still fresh.

  Definition texts only feed context-size previews, so staleness within the
  max age is fine; the throttle keeps app startups (and test suites, which
  also set the disable env) from forking a heavyweight exporter each time.
  """
  if os.environ.get("HANDA_DISABLE_INTROSPECTION_REFRESH"):
    return None
  path = tool_definitions_path(root)
  try:
    if time.time() - path.stat().st_mtime < REFRESH_MAX_AGE_SECONDS:
      return None
  except OSError:
    pass
  env = os.environ.copy()
  env["HANDA_STORAGE_ROOT"] = str(resolve_storage_root(root))
  return subprocess.Popen(
      [sys.executable, "-m", "src.agent_introspection"],
      cwd=str(get_product_root()),
      env=env,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
  )
