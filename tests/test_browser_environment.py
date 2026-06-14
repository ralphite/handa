from __future__ import annotations

import asyncio
import json

from src.browser_environment import BrowserEnvironmentManager
from src.browser_environment import read_browser_summary
from src.storage.paths import browser_dir
from src.storage.paths import browser_screenshot_path
from src.storage.paths import browser_state_path
from src.storage.paths import session_dir


def test_browser_summary_reads_session_scoped_state(tmp_path):
  root = tmp_path / ".handa"
  session_id = "sess-browser"
  browser_dir(root, session_id).mkdir(parents=True)
  browser_screenshot_path(root, session_id).write_bytes(b"\x89PNG\r\n\x1a\n")
  browser_state_path(root, session_id).write_text(
      json.dumps(
          {
              "success": True,
              "status": "open",
              "url": "https://example.com",
              "title": "Example",
              "last_action": "Captured screenshot",
              "last_error": None,
              "updated_at": "2026-06-08T18:00:00Z",
              "viewport": {"width": 1280, "height": 720},
              "last_snapshot": [{"id": "e1", "selector": "button"}],
          }
      ),
      encoding="utf-8",
  )

  summary = read_browser_summary(root, session_id)

  assert browser_state_path(root, session_id).is_relative_to(session_dir(root, session_id))
  assert summary == {
      "success": True,
      "status": "open",
      "session_id": session_id,
      "url": "https://example.com",
      "title": "Example",
      "last_action": "Captured screenshot",
      "last_error": None,
      "updated_at": "2026-06-08T18:00:00Z",
      "viewport": {"width": 1280, "height": 720},
      "screenshot_url": f"/api/sessions/{session_id}/browser/screenshot",
      "stream_url": f"/api/sessions/{session_id}/browser/stream",
  }


def test_browser_summary_is_empty_without_state(tmp_path):
  assert read_browser_summary(tmp_path / ".handa", "missing-session") is None


def test_browser_refresh_closed_state_does_not_launch_browser(tmp_path):
  root = tmp_path / ".handa"
  session_id = "sess-closed"
  browser_dir(root, session_id).mkdir(parents=True)
  browser_screenshot_path(root, session_id).write_bytes(b"\x89PNG\r\n\x1a\n")
  browser_state_path(root, session_id).write_text(
      json.dumps(
          {
              "success": True,
              "status": "closed",
              "url": "https://example.com",
              "title": "Example",
              "last_action": "Closed browser",
              "last_error": None,
              "updated_at": "2026-06-08T18:00:00Z",
              "viewport": {"width": 1280, "height": 720},
          }
      ),
      encoding="utf-8",
  )
  manager = BrowserEnvironmentManager(root)

  summary = asyncio.run(manager.refresh(session_id=session_id))

  assert summary["status"] == "closed"
  assert summary["last_action"] == "Closed browser"
  assert session_id not in manager._sessions


class _FakePage:
  def __init__(self):
    self.viewport_size = {"width": 1280, "height": 720}
    self.url = "https://example.com"

  async def title(self):
    return "Example"

  async def set_viewport_size(self, size):
    self.viewport_size = {"width": int(size["width"]), "height": int(size["height"])}


def test_browser_set_viewport_resizes_live_page(tmp_path):
  root = tmp_path / ".handa"
  session_id = "sess-resize"
  browser_dir(root, session_id).mkdir(parents=True)
  manager = BrowserEnvironmentManager(root)
  page = _FakePage()
  manager._sessions[session_id] = {"context": object(), "page": page}

  assert manager.has_live_session(session_id) is True
  assert manager.has_live_session("missing") is False

  summary = asyncio.run(manager.set_viewport(session_id=session_id, width=800, height=900))

  assert page.viewport_size == {"width": 800, "height": 900}
  assert summary["viewport"] == {"width": 800, "height": 900}
  assert summary["last_action"] == "Resized browser to 800x900"

  # Out-of-range sizes are clamped to the streamable maximum.
  asyncio.run(manager.set_viewport(session_id=session_id, width=99999, height=99999))

  assert page.viewport_size == {"width": 2560, "height": 1600}
