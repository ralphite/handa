from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from ....runtime import cancel_task_view
from ....runtime import list_tasks as list_tasks_runtime
from ....runtime import read_task_log as read_task_log_runtime
from ....runtime import start_background_task
from ....runtime import task_status_view
from ....runtime import task_tool_view


def _session_id(tool_context: ToolContext) -> str:
  return tool_context.session.id


def start_background(
    command: str,
    tool_context: ToolContext,
    cwd: str = ".",
    summary: str | None = None,
) -> dict[str, Any]:
  """Start a long-running shell command in the background."""
  task = start_background_task(
      command=command,
      cwd=cwd,
      summary=summary,
      session_id=_session_id(tool_context),
  )
  return task_tool_view(task)


def get_status(task_id: str, tool_context: ToolContext) -> dict[str, Any]:
  """Get the current status for one background task."""
  return task_status_view(
      task_id,
      session_id=_session_id(tool_context),
  )


def list(tool_context: ToolContext) -> dict[str, Any]:
  """List recent background tasks."""
  return {
      "tasks": [
          task_tool_view(task)
          for task in list_tasks_runtime(session_id=_session_id(tool_context))
      ]
  }


def read_log(
    task_id: str,
    tool_context: ToolContext,
    tail_lines: int = 200,
) -> dict[str, Any]:
  """Read recent log lines for a background task."""
  return read_task_log_runtime(
      task_id=task_id,
      tail_lines=tail_lines,
      session_id=_session_id(tool_context),
  )


def cancel(task_id: str, tool_context: ToolContext) -> dict[str, Any]:
  """Cancel a running background task."""
  return cancel_task_view(
      task_id,
      session_id=_session_id(tool_context),
  )
