from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import inspect
from typing import Any

from google.adk.tools import ToolContext  # noqa: F401 - resolves wrapper annotations.
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.long_running_tool import LongRunningFunctionTool

from ....tools import commands
from ....tools import files
from ....tools import skills
from ....tools.user_input import UserInputQuestion  # noqa: F401 - resolves wrapper annotations.
from . import agents
from . import artifacts
from . import browser
from . import notes
from . import notifications
from . import progress
from . import tasks
from . import user_input
from .types import FunctionToolCallable
from .types import FunctionToolPayload


@dataclass(frozen=True)
class ToolSpec:
  namespace: str
  name: str
  function: FunctionToolCallable

  @property
  def exposed_name(self) -> str:
    if not self.namespace:
      return self.name
    return f"{self.namespace}_{self.name}"


TOOL_CATEGORIES: dict[str, list[Callable]] = {
    "": [
        agents.run_agent,
        user_input.request_user_input,
    ],
    "agents": [
        agents.save_config,
        agents.read_config,
        agents.list_configs,
        agents.start_run,
        agents.get_run_status,
        agents.read_run_result,
        agents.read_run_log,
        agents.list_run_artifacts,
        agents.read_run_artifact,
    ],
    "artifacts": [
        artifacts.save_text,
        artifacts.list,
        artifacts.read,
    ],
    "browser": [
        browser.open,
        browser.snapshot,
        browser.click,
        browser.type,
        browser.keys,
        browser.scroll,
        browser.wait,
        browser.screenshot,
        browser.close,
    ],
    "skills": [
        skills.list,
        skills.read,
    ],
    "files": [
        files.list,
        files.search,
        files.read,
        files.write,
        files.replace,
    ],
    "commands": [
        commands.run,
    ],
    "tasks": [
        tasks.start_background,
        tasks.get_status,
        tasks.list,
        tasks.read_log,
        tasks.cancel,
    ],
    "notifications": [
        notifications.get,
    ],
    "progress": [
        progress.update,
    ],
    "notes": [
        notes.add,
    ],
}


# Long-running tools pause the ADK invocation: the function returns None, no
# function response is built, and the answer arrives later via session
# injection (see ADK LongRunningFunctionTool).
LONG_RUNNING_TOOL_NAMES = {"request_user_input"}


def known_agent_tool_names() -> frozenset[str]:
  """Every tool name a generated agent config may grant.

  This is the full catalog across runtimes (the ADK registry is a superset of
  the LangGraph toolset), so it validates config tool lists without rejecting a
  tool that is valid for whichever runtime ends up running the config.
  """
  return frozenset(get_tool_registry())


def get_tool_registry() -> dict[str, ToolSpec]:
  registry = {}
  for namespace, functions in TOOL_CATEGORIES.items():
    for function in functions:
      spec = ToolSpec(
          namespace=namespace,
          name=function.__name__,
          function=function,
      )
      registry[spec.exposed_name] = spec
  return registry


def create_agent_tools(tool_names: list[str]) -> list[FunctionTool]:
  if not tool_names:
    return []

  registry = get_tool_registry()
  unknown_names = [name for name in tool_names if name not in registry]
  if unknown_names:
    raise ValueError(f"Unknown agent tools: {', '.join(unknown_names)}")

  return [_create_prefixed_tool(registry[tool_name]) for tool_name in tool_names]


def _create_prefixed_tool(spec: ToolSpec) -> FunctionTool:
  function = _wrap_tool_function(spec)
  if spec.exposed_name in LONG_RUNNING_TOOL_NAMES:
    return LongRunningFunctionTool(function)
  return FunctionTool(function)


def _wrap_tool_function(spec: ToolSpec) -> Callable:
  function = spec.function
  tool_name = spec.exposed_name
  # Long-running tools must return their bare None so ADK skips the function
  # response and pauses the invocation; normalizing it into a payload would
  # resume the model immediately.
  preserve_none = tool_name in LONG_RUNNING_TOOL_NAMES

  if asyncio.iscoroutinefunction(function):

    async def _async_wrapped(*args: Any, **kwargs: Any) -> FunctionToolPayload | None:
      try:
        result = await function(*args, **kwargs)
      except Exception as exc:  # noqa: BLE001 - tool errors must not break agent loop.
        return _tool_error_result(tool_name=tool_name, exc=exc)
      if preserve_none and result is None:
        return None
      return _normalize_tool_result(result)

    _copy_tool_metadata(_async_wrapped, function, name=tool_name)
    return _async_wrapped

  def _wrapped(*args: Any, **kwargs: Any) -> FunctionToolPayload | None:
    try:
      result = function(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - tool errors must not break agent loop.
      return _tool_error_result(tool_name=tool_name, exc=exc)
    if preserve_none and result is None:
      return None
    return _normalize_tool_result(result)

  _copy_tool_metadata(_wrapped, function, name=tool_name)
  return _wrapped


def _copy_tool_metadata(wrapper: Callable, function: Callable, name: str | None = None) -> None:
  wrapper.__name__ = name or function.__name__
  wrapper.__qualname__ = name or function.__qualname__
  wrapper.__doc__ = function.__doc__
  wrapper.__annotations__ = dict(getattr(function, "__annotations__", {}))
  wrapper.__signature__ = inspect.signature(function)  # type: ignore[attr-defined]


def _normalize_tool_result(result: Any) -> FunctionToolPayload:
  if isinstance(result, dict):
    return {"ok": True, **result} if "ok" not in result else result
  return {
      "ok": True,
      "result": result,
  }


def _tool_error_result(*, tool_name: str, exc: Exception) -> FunctionToolPayload:
  return {
      "ok": False,
      "error": {
          "type": type(exc).__name__,
          "message": str(exc),
          "tool": tool_name,
      },
  }


def select_agent_tools(tool_names: list[str]) -> list[FunctionTool]:
  return create_agent_tools(tool_names)
