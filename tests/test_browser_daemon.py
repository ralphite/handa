from __future__ import annotations

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from threading import Thread

from src.browser_client import BrowserDaemonClient
from src.browser_daemon import read_daemon_endpoint
from src.browser_environment import read_browser_summary
from src.runtime import is_process_alive


def _serve_directory(directory) -> tuple[ThreadingHTTPServer, str]:
  handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
  server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
  thread = Thread(target=server.serve_forever, daemon=True)
  thread.start()
  return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_browser_daemon_roundtrip_survives_client_processes(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  page = tmp_path / "index.html"
  page.write_text(
      "<!doctype html><html><head><title>Daemon Test</title></head>"
      "<body><button id='go'>Go</button></body></html>",
      encoding="utf-8",
  )
  server, base_url = _serve_directory(tmp_path)
  session_id = "daemon-session"
  client = BrowserDaemonClient(storage_root)

  async def scenario() -> None:
    summary = await client.open(session_id=session_id, url=f"{base_url}/index.html")
    assert summary["status"] == "open"
    assert summary["title"] == "Daemon Test"

    endpoint = read_daemon_endpoint(storage_root, session_id)
    assert endpoint is not None
    assert is_process_alive(endpoint["pid"])

    snapshot = await client.snapshot(session_id=session_id)
    elements = snapshot.get("elements") or []
    assert any("Go" in str(element.get("text") or "") for element in elements)

    # A second client (as another worker process would create) reuses the
    # same daemon instead of spawning a new browser.
    other = BrowserDaemonClient(storage_root)
    again = await other.screenshot(session_id=session_id)
    assert again["status"] == "open"
    assert read_daemon_endpoint(storage_root, session_id) == endpoint

    persisted = read_browser_summary(storage_root, session_id)
    assert persisted is not None
    assert persisted["status"] == "open"

    closed = await client.close(session_id=session_id)
    assert closed["status"] == "closed"

  try:
    asyncio.run(scenario())
  finally:
    server.shutdown()


def test_browser_daemon_exits_after_close(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  page = tmp_path / "index.html"
  page.write_text("<!doctype html><title>Bye</title>", encoding="utf-8")
  server, base_url = _serve_directory(tmp_path)
  session_id = "daemon-close"
  client = BrowserDaemonClient(storage_root)

  async def scenario() -> int:
    await client.open(session_id=session_id, url=f"{base_url}/index.html")
    endpoint = read_daemon_endpoint(storage_root, session_id)
    assert endpoint is not None
    await client.close(session_id=session_id)
    return int(endpoint["pid"])

  try:
    pid = asyncio.run(scenario())
  finally:
    server.shutdown()

  import time

  for _ in range(100):
    if not is_process_alive(pid):
      break
    time.sleep(0.1)
  assert not is_process_alive(pid)
  assert read_daemon_endpoint(storage_root, session_id) is None
