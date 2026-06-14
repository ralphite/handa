from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from ....runtime import get_project_root
from ....tools import browser as browser_tools


def _session_id(tool_context: ToolContext) -> str:
  return tool_context.session.id


def _project_root(tool_context: ToolContext) -> str | None:
  value = tool_context.state.get("handa:project_root")
  if value:
    return str(value)
  try:
    return str(get_project_root())
  except RuntimeError:
    return None


async def open(
    url: str,
    tool_context: ToolContext,
    wait_until: str = "domcontentloaded",
) -> dict[str, Any]:
  """Open a URL in this session's Browser Environment."""
  return await browser_tools.open(
      session_id=_session_id(tool_context),
      url=url,
      wait_until=wait_until,
      project_root=_project_root(tool_context),
  )


async def snapshot(
    tool_context: ToolContext,
    max_elements: int = 80,
) -> dict[str, Any]:
  """Capture visible clickable/input elements from the current browser page."""
  return await browser_tools.snapshot(
      session_id=_session_id(tool_context),
      max_elements=max_elements,
  )


async def click(target: str, tool_context: ToolContext) -> dict[str, Any]:
  """Click a browser element by snapshot id such as e12, or by CSS selector."""
  return await browser_tools.click(session_id=_session_id(tool_context), target=target)


async def type(
    target: str,
    text: str,
    tool_context: ToolContext,
    clear: bool = True,
) -> dict[str, Any]:
  """Type into a browser element by snapshot id such as e12, or by CSS selector."""
  return await browser_tools.type(
      session_id=_session_id(tool_context),
      target=target,
      text=text,
      clear=clear,
  )


async def keys(keys: str, tool_context: ToolContext) -> dict[str, Any]:
  """Press keyboard keys in the current browser page."""
  return await browser_tools.keys(session_id=_session_id(tool_context), keys=keys)


async def scroll(
    tool_context: ToolContext,
    direction: str = "down",
    amount: int = 600,
) -> dict[str, Any]:
  """Scroll the current browser page up or down."""
  return await browser_tools.scroll(
      session_id=_session_id(tool_context),
      direction=direction,
      amount=amount,
  )


async def wait(
    tool_context: ToolContext,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
  """Wait for a selector, text, or a fixed timeout in the current browser page."""
  return await browser_tools.wait(
      session_id=_session_id(tool_context),
      selector=selector,
      text=text,
      timeout_ms=timeout_ms,
  )


async def screenshot(
    tool_context: ToolContext,
    full_page: bool = False,
) -> dict[str, Any]:
  """Save the current browser screenshot for Web UI preview."""
  return await browser_tools.screenshot(
      session_id=_session_id(tool_context),
      full_page=full_page,
  )


async def close(tool_context: ToolContext) -> dict[str, Any]:
  """Close this session's Browser Environment."""
  return await browser_tools.close(session_id=_session_id(tool_context))
