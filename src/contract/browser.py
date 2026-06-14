"""Browser surface of the contract: daemon client and read-only state files.

The daemon client speaks loopback HTTP/WS to the per-session browser daemon;
the summary/screenshot readers consume the state files the daemon persists.
Importing this never loads Playwright.
"""
from __future__ import annotations

from ..browser_client import BrowserDaemonClient as BrowserDaemonClient
from ..browser_client import default_browser_client as default_browser_client
from ..browser_environment import browser_screenshot_file as browser_screenshot_file
from ..browser_environment import DEFAULT_VIEWPORT as DEFAULT_VIEWPORT
from ..browser_environment import read_browser_summary as read_browser_summary
