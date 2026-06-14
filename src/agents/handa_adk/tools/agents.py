from __future__ import annotations

import json
from typing import Any

from google.adk.tools import ToolContext
from google.genai import types

from ....config import AgentConfig
from ....config import agent_config_artifact_filename
from ....config import agent_config_warnings
from ....config import resolve_generated_agent_model_config_id
from ....model_configs import validate_model_config_id
from ....runtime import read_task_log
from ....runtime import start_agent_run_task
from ....runtime import start_run_agent_task
from ....runtime import task_log_file
from ....tools.text_window import window_text
from ....runner import DEFAULT_USER_ID
from ....runner import APP_NAME
from ....runtime import task_result_view
from ....runtime import task_status_view
from ....storage import HandaArtifactService
from ....storage.artifact_service import artifact_display_filename
from ....storage.artifact_service import artifact_stored_filename
from ....agent_runtime import validate_agent_id


async def save_config(
    name: str,
    description: str,
    tools: list[str],
    skills: list[str],
    instruction_sections: list[str],
    tool_context: ToolContext,
    custom_instruction: str | None = None,
    model_config_id: str | None = None,
) -> dict[str, Any]:
  """Save an agent config artifact.

  `name` must be a valid ADK agent identifier: letters, digits, and underscores,
  starting with a letter or underscore. Valid `instruction_sections` options are:
  identity, task_execution, tool_usage, file_editing, testing, storage,
  agent_config, subagents, html_output, communication. Use `custom_instruction`
  for extra
  free-form instructions; it is appended after the selected built-in sections.
  `model_config_id` is optional: set it to a supported model config id to pin
  this agent's model; omit it to inherit the model selected in the session.
  """
  normalized_model_config_id = None
  if model_config_id and model_config_id.strip():
    normalized_model_config_id = validate_model_config_id(model_config_id)
  config = AgentConfig(
      name=name,
      description=description,
      tools=tools,
      skills=skills,
      instruction_sections=instruction_sections,
      custom_instruction=custom_instruction,
      model_config_id=normalized_model_config_id,
  )
  _reject_unknown_tools(config.tools)
  warnings = agent_config_warnings(config)
  payload = json.dumps(
      config.model_dump(exclude_none=True),
      indent=2,
      ensure_ascii=True,
  )
  filename = agent_config_artifact_filename(name)
  version = await tool_context.save_artifact(
      filename,
      types.Part.from_text(text=payload),
  )
  return {
      "success": True,
      "filename": artifact_display_filename(filename),
      "stored_filename": artifact_stored_filename(filename, version),
      "version": version,
      "display_version": version + 1,
      "model_config_id": normalized_model_config_id or _parent_model_config_id(tool_context),
      "warnings": warnings,
  }


def _reject_unknown_tools(tools: list[str]) -> None:
  """Fail fast on tool names that no runtime can resolve (typos).

  The run-time toolset builders raise the same error, but checking at save time
  surfaces the mistake while the config is being authored. Imported lazily to
  avoid a registry<->agents import cycle.
  """
  from .registry import known_agent_tool_names

  unknown = [name for name in tools if name not in known_agent_tool_names()]
  if unknown:
    raise ValueError(f"Unknown agent tools: {', '.join(unknown)}")


async def read_config(
    name: str,
    tool_context: ToolContext,
    version: int | None = None,
) -> dict[str, Any]:
  """Read an agent config artifact."""
  filename = agent_config_artifact_filename(name)
  artifact = await tool_context.load_artifact(filename, version=version)
  if artifact is None or artifact.text is None:
    return {"found": False, "filename": filename, "version": version}
  return {
      "found": True,
      "filename": filename,
      "version": version,
      "config": json.loads(artifact.text),
  }


async def list_configs(tool_context: ToolContext) -> dict[str, Any]:
  """List agent config artifacts."""
  artifacts = await tool_context.list_artifacts()
  configs = [name for name in artifacts if name.endswith(".agent.json")]
  return {"configs": configs, "count": len(configs)}


def _session_id(tool_context: ToolContext) -> str:
  return tool_context.session.id


def _user_id(tool_context: ToolContext) -> str:
  return getattr(tool_context, "user_id", None) or DEFAULT_USER_ID


def _parent_model_config_id(tool_context: ToolContext) -> str:
  state = getattr(tool_context.session, "state", {}) or {}
  return validate_model_config_id(state.get("handa:model_config_id"))


def _run_agent_depth(tool_context: ToolContext) -> int:
  state = getattr(tool_context.session, "state", {}) or {}
  value = state.get("handa:agent_run_depth", 0)
  try:
    return int(value)
  except (TypeError, ValueError):
    return 0


def _is_agent_task(task: dict[str, Any]) -> bool:
  return task.get("kind") in {"agent_run", "run_agent", "system_agent_run"}


async def run_agent(
    agent_id: str,
    prompt: str,
    tool_context: ToolContext,
    context: str | None = None,
    summary: str | None = None,
    max_depth: int = 3,
) -> dict[str, Any]:
  """Run a registered Handa agent as a child session task.

  `agent_id` must be one of Handa's registered agent definitions, such as the
  ADK agents `main`/`ralph` or the LangGraph agent `orca`. This
  is Handa's agent-as-tool boundary: the target agent runs in a child session and
  can be inspected through task status/result/artifacts.
  """
  normalized_agent_id = validate_agent_id(agent_id)
  depth = _run_agent_depth(tool_context)
  max_depth = max(1, min(max_depth, 8))
  if depth >= max_depth:
    raise ValueError(
        f"run_agent max depth reached: depth={depth}, max_depth={max_depth}"
    )
  task = start_run_agent_task(
      agent_id=normalized_agent_id,
      prompt=prompt,
      context=context,
      summary=summary,
      session_id=_session_id(tool_context),
      user_id=_user_id(tool_context),
      app_name=APP_NAME,
      depth=depth,
  )
  return {
      "success": True,
      "task_id": task["id"],
      "status": task["status"],
      "agent_id": task["agent_id"],
      "child_session_id": task["child_session_id"],
      "delivery": "The child agent result will be sent back as a system task notification when the task reaches completed, failed, or cancelled. Do not poll task status or logs to wait for completion.",
  }


async def start_run(
    name: str,
    prompt: str,
    tool_context: ToolContext,
    context: str | None = None,
    summary: str | None = None,
    version: int | None = None,
    max_depth: int = 3,
) -> dict[str, Any]:
  """Start an Agent Config run as a parent task with a child session."""
  depth = _run_agent_depth(tool_context)
  max_depth = max(1, min(max_depth, 8))
  if depth >= max_depth:
    raise ValueError(
        f"Agent Config run max depth reached: depth={depth}, max_depth={max_depth}"
    )
  filename = agent_config_artifact_filename(name)
  artifact = await tool_context.load_artifact(filename, version=version)
  if artifact is None or artifact.text is None:
    raise ValueError(f"Agent Config artifact not found: {filename}")
  config = AgentConfig.model_validate_json(artifact.text)
  task = start_agent_run_task(
      config_name=config.name,
      prompt=prompt,
      context=context,
      summary=summary,
      config_version=version,
      # The config's model wins when it names a supported model config;
      # otherwise the run inherits the session-selected model.
      model_config_id=resolve_generated_agent_model_config_id(
          config,
          inherited_model_config_id=_parent_model_config_id(tool_context),
      ),
      session_id=_session_id(tool_context),
      user_id=_user_id(tool_context),
      app_name=APP_NAME,
      depth=depth,
  )
  return {
      "success": True,
      "task_id": task["id"],
      "status": task["status"],
      "child_session_id": task["child_session_id"],
      "config_name": task["config_name"],
  }


def _agent_task_view(task_id: str, tool_context: ToolContext) -> dict[str, Any]:
  view = task_status_view(task_id, session_id=_session_id(tool_context))
  if not view.get("found"):
    return view
  if not _is_agent_task(view["task"]):
    return {"found": False, "task_id": task_id, "error": "task is not an agent task"}
  return view


def get_run_status(task_id: str, tool_context: ToolContext) -> dict[str, Any]:
  """Get the parent task status for one Agent Config run.

  The reply separates `tool_status` (this read succeeded) from `task_status`
  (how the child run itself ended): reading a *failed* run is still a successful
  tool call, so a failed child must not read as a tool failure.
  """
  view = _agent_task_view(task_id, tool_context)
  return _with_task_status(view)


def read_run_result(task_id: str, tool_context: ToolContext) -> dict[str, Any]:
  """Read the final structured result for one Agent Config run.

  Like `get_run_status`, the reply separates `tool_status` from `task_status` so
  a successful read of a failed run is not mistaken for a failed read.
  """
  result = task_result_view(task_id, session_id=_session_id(tool_context))
  task = result.get("task")
  if not isinstance(task, dict):
    return result  # unknown task id
  if not _is_agent_task(task):
    return {"found": False, "task_id": task_id, "error": "task is not an agent task"}
  return _with_task_status(result)


def _with_task_status(view: dict[str, Any]) -> dict[str, Any]:
  """Tag a successful agent-task read with explicit tool/task status fields."""
  if not view.get("found"):
    return view
  task = view.get("task")
  task_status = task.get("status") if isinstance(task, dict) else None
  return {**view, "tool_status": "ok", "task_status": task_status}


def read_run_log(
    task_id: str,
    tool_context: ToolContext,
    tail_lines: int = 200,
    metadata_only: bool = False,
) -> dict[str, Any]:
  """Read recent parent task log lines for one Agent Config run.

  Returns the tail plus the total `line_count` and the on-disk `log_path`. Pass
  `metadata_only=True` to get just the line count and path without the log text.
  """
  session_id = _session_id(tool_context)
  view = _agent_task_view(task_id, tool_context)
  if not view.get("found"):
    return view
  result = read_task_log(task_id, tail_lines=tail_lines, session_id=session_id)
  result["log_path"] = str(task_log_file(task_id, session_id=session_id))
  if metadata_only:
    result.pop("log", None)
  return result


async def list_run_artifacts(
    task_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
  """List artifacts from the child session for an Agent Config run."""
  view = _agent_task_view(task_id, tool_context)
  if not view.get("found"):
    return view
  task = view["task"]
  service = HandaArtifactService()
  artifacts = await service.list_artifact_keys(
      app_name=APP_NAME,
      user_id=_user_id(tool_context),
      session_id=task["child_session_id"],
  )
  return {
      "found": True,
      "task_id": task_id,
      "child_session_id": task["child_session_id"],
      "artifacts": artifacts,
      "count": len(artifacts),
  }


async def read_run_artifact(
    task_id: str,
    filename: str,
    tool_context: ToolContext,
    version: int | None = None,
    offset: int = 0,
    max_chars: int | None = None,
    metadata_only: bool = False,
) -> dict[str, Any]:
  """Read a text artifact from the child session for an Agent Config run.

  Child reports can be large, so the reply is bounded: it carries
  `char_count`/`line_count` plus a windowed `content` slice. Pass
  `metadata_only=True` for just the size, or page a big artifact with `offset`
  (start char) and `max_chars`.
  """
  view = _agent_task_view(task_id, tool_context)
  if not view.get("found"):
    return view
  task = view["task"]
  service = HandaArtifactService()
  artifact = await service.load_artifact(
      app_name=APP_NAME,
      user_id=_user_id(tool_context),
      session_id=task["child_session_id"],
      filename=filename,
      version=version,
  )
  if artifact is None:
    return {"found": False, "task_id": task_id, "filename": filename}
  if artifact.text is None:
    return {
        "found": True,
        "task_id": task_id,
        "filename": filename,
        "text": None,
        "mime_type": artifact.inline_data.mime_type if artifact.inline_data else None,
        "size": len(artifact.inline_data.data or b"") if artifact.inline_data else 0,
    }
  return {
      "found": True,
      "task_id": task_id,
      "filename": filename,
      **window_text(
          artifact.text,
          offset=offset,
          max_chars=max_chars,
          metadata_only=metadata_only,
      ),
  }
