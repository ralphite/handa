from __future__ import annotations

from pathlib import Path
from typing import Any

from ..browser_client import default_browser_client


def _project_root(value: str | None) -> Path | None:
  if not value:
    return None
  return Path(value).expanduser().resolve()


async def open(
    session_id: str,
    url: str,
    wait_until: str = "domcontentloaded",
    project_root: str | None = None,
) -> dict[str, Any]:
  """Open a URL in the current session browser."""
  return await default_browser_client().open(
      session_id=session_id,
      url=url,
      wait_until=wait_until,
      project_root=_project_root(project_root),
  )


async def snapshot(
    session_id: str,
    max_elements: int = 80,
) -> dict[str, Any]:
  """Capture visible clickable/input elements from the current browser page."""
  return await default_browser_client().snapshot(
      session_id=session_id,
      max_elements=max_elements,
  )


async def click(session_id: str, target: str) -> dict[str, Any]:
  """Click a browser element by snapshot id such as e12, or by CSS selector."""
  return await default_browser_client().click(session_id=session_id, target=target)


async def type(
    session_id: str,
    target: str,
    text: str,
    clear: bool = True,
) -> dict[str, Any]:
  """Type into a browser element by snapshot id such as e12, or by CSS selector."""
  return await default_browser_client().type(
      session_id=session_id,
      target=target,
      text=text,
      clear=clear,
  )


async def keys(session_id: str, keys: str) -> dict[str, Any]:
  """Press keyboard keys in the current browser page."""
  return await default_browser_client().keys(session_id=session_id, keys=keys)


async def scroll(
    session_id: str,
    direction: str = "down",
    amount: int = 600,
) -> dict[str, Any]:
  """Scroll the current browser page up or down."""
  return await default_browser_client().scroll(
      session_id=session_id,
      direction=direction,
      amount=amount,
  )


async def wait(
    session_id: str,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
  """Wait for a selector, text, or a fixed timeout in the current browser page."""
  return await default_browser_client().wait(
      session_id=session_id,
      selector=selector,
      text=text,
      timeout_ms=timeout_ms,
  )


async def screenshot(session_id: str, full_page: bool = False) -> dict[str, Any]:
  """Save the current browser screenshot for Web UI preview."""
  return await default_browser_client().screenshot(
      session_id=session_id,
      full_page=full_page,
  )


async def close(session_id: str) -> dict[str, Any]:
  """Close the current session browser context."""
  return await default_browser_client().close(session_id=session_id)
