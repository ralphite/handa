from __future__ import annotations

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from src.browser_environment import BrowserEnvironmentError
from src.browser_environment import BrowserEnvironmentManager
from src.browser_environment import read_browser_summary


def test_playwright_browser_environment_roundtrip(tmp_path):
  page = tmp_path / "index.html"
  page.write_text(
      """
<!doctype html>
<html>
  <head><title>Browser Test</title></head>
  <body>
    <label>Name <input id="name" placeholder="name"></label>
    <button id="save">Save</button>
    <p id="out"></p>
    <label>Coord <input id="coord" placeholder="coord"></label>
    <button id="coord-save">Coord Save</button>
    <p id="coord-out"></p>
    <div id="pad" tabindex="0" style="width:300px;height:120px;background:#eef">pad</div>
    <p id="drag-out"></p>
    <script>
      const out = document.querySelector('#out')
      const input = document.querySelector('#name')
      const saved = localStorage.getItem('name') || ''
      input.value = saved
      out.textContent = saved
      document.querySelector('#save').addEventListener('click', () => {
        localStorage.setItem('name', input.value)
        out.textContent = input.value
      })
      const coordOut = document.querySelector('#coord-out')
      const coordInput = document.querySelector('#coord')
      const coordSaved = localStorage.getItem('coord') || ''
      coordInput.value = coordSaved
      coordOut.textContent = coordSaved
      document.querySelector('#coord-save').addEventListener('click', () => {
        localStorage.setItem('coord', coordInput.value)
        coordOut.textContent = coordInput.value
      })
      const pad = document.querySelector('#pad')
      const dragOut = document.querySelector('#drag-out')
      let dragging = false
      let moves = 0
      pad.addEventListener('mousedown', () => { dragging = true; moves = 0 })
      window.addEventListener('mousemove', () => { if (dragging) moves += 1 })
      window.addEventListener('mouseup', () => {
        if (!dragging) return
        dragging = false
        dragOut.textContent = 'dragged:' + moves
      })
    </script>
  </body>
</html>
""",
      encoding="utf-8",
  )
  handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
  server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
  thread = Thread(target=server.serve_forever, daemon=True)
  thread.start()

  async def go():
    root = tmp_path / ".handa"
    session_id = "sess-playwright"
    url = f"http://127.0.0.1:{server.server_port}/index.html"
    manager = BrowserEnvironmentManager(root)
    reopened = BrowserEnvironmentManager(root)
    try:
      await manager.open(session_id=session_id, url=url)
      snapshot = await manager.snapshot(session_id=session_id, max_elements=10)
      name_target = next(item["id"] for item in snapshot["elements"] if item["selector"] == "#name")
      save_target = next(item["id"] for item in snapshot["elements"] if item["selector"] == "#save")
      await manager.type(session_id=session_id, target=name_target, text="Ada")
      await manager.click(session_id=session_id, target=save_target)
      await manager.wait(session_id=session_id, text="Ada", timeout_ms=5000)
      snapshot = await manager.snapshot(session_id=session_id, max_elements=10)
      coord_box = next(item["bbox"] for item in snapshot["elements"] if item["selector"] == "#coord")
      coord_save_box = next(item["bbox"] for item in snapshot["elements"] if item["selector"] == "#coord-save")
      pad_box = next(item["bbox"] for item in snapshot["elements"] if item["selector"] == "#pad")
      await manager.click_at(
          session_id=session_id,
          x=(coord_box["x"] + coord_box["width"] / 2) / 1280,
          y=(coord_box["y"] + coord_box["height"] / 2) / 720,
      )
      await manager.type_text(session_id=session_id, text="Grace")
      await manager.click_at(
          session_id=session_id,
          x=(coord_save_box["x"] + coord_save_box["width"] / 2) / 1280,
          y=(coord_save_box["y"] + coord_save_box["height"] / 2) / 720,
      )
      await manager.wait(session_id=session_id, text="Grace", timeout_ms=5000)
      # A real drag must reach the page as mousedown -> mousemove(s) -> mouseup,
      # not a teleport, so the page records the gesture.
      await manager.drag(
          session_id=session_id,
          x=(pad_box["x"] + pad_box["width"] * 0.25) / 1280,
          y=(pad_box["y"] + pad_box["height"] / 2) / 720,
          x2=(pad_box["x"] + pad_box["width"] * 0.75) / 1280,
          y2=(pad_box["y"] + pad_box["height"] / 2) / 720,
      )
      await manager.wait(session_id=session_id, text="dragged:", timeout_ms=5000)
      screenshot = await manager.screenshot(session_id=session_id)
      assert screenshot["screenshot_url"] == f"/api/sessions/{session_id}/browser/screenshot"
      await manager.close(session_id=session_id)

      await reopened.open(session_id=session_id, url=url)
      await reopened.wait(session_id=session_id, text="Ada", timeout_ms=5000)
      await reopened.wait(session_id=session_id, text="Grace", timeout_ms=5000)
      summary = read_browser_summary(root, session_id)
      assert summary is not None
      assert summary["status"] == "open"
      assert summary["url"].endswith("/index.html")
    finally:
      await manager.stop()
      await reopened.stop()

  try:
    asyncio.run(go())
  except BrowserEnvironmentError as exc:
    pytest.skip(f"{exc} Run `uv run playwright install chromium`.")
  finally:
    server.shutdown()
    thread.join(timeout=2)


def test_browser_snapshot_includes_visible_shadow_root_text(tmp_path):
  page = tmp_path / "shadow.html"
  page.write_text(
      """
<!doctype html>
<html>
  <head><title>Shadow Overlay Test</title></head>
  <body>
    <vite-error-overlay></vite-error-overlay>
    <script>
      const overlay = document.querySelector('vite-error-overlay')
      const root = overlay.attachShadow({ mode: 'open' })
      root.innerHTML = `
        <style>
          :host {
            display: block;
            position: fixed;
            inset: 0;
            background: rgb(20, 20, 20);
            color: rgb(255, 90, 90);
            font: 16px monospace;
          }
        </style>
        <div>[plugin:vite:esbuild] Transform failed with 3 errors</div>
        <pre>/src/App.tsx:464:57 ERROR: The character "&gt;" is not valid inside a JSX element</pre>
      `
    </script>
  </body>
</html>
""",
      encoding="utf-8",
  )
  handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
  server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
  thread = Thread(target=server.serve_forever, daemon=True)
  thread.start()

  async def go():
    root = tmp_path / ".handa"
    session_id = "sess-shadow"
    manager = BrowserEnvironmentManager(root)
    try:
      await manager.open(
          session_id=session_id,
          url=f"http://127.0.0.1:{server.server_port}/shadow.html",
      )
      snapshot = await manager.snapshot(session_id=session_id, max_elements=10)
      assert "Transform failed with 3 errors" in snapshot["text"]
      assert "not valid inside a JSX element" in snapshot["text"]
    finally:
      await manager.stop()

  try:
    asyncio.run(go())
  except BrowserEnvironmentError as exc:
    pytest.skip(f"{exc} Run `uv run playwright install chromium`.")
  finally:
    server.shutdown()
    thread.join(timeout=2)
