#!/usr/bin/env python3
"""Real-browser smoke test: launch bundled Chromium via Playwright and load a URL.

Run with the *bundled* interpreter so it exercises the vendored Playwright and the
Chromium that `playwright install` placed on this machine. Proves the browser
stack actually works on the host OS, not just that the package imports.

Usage: python browser_smoke.py <url>
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: browser_smoke.py <url>", file=sys.stderr)
        return 2
    url = sys.argv[1]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            title = page.title()
            html = page.content()
        finally:
            browser.close()

    if not html:
        print("error: loaded page was empty", file=sys.stderr)
        return 1

    print(f"browser ok: title={title!r} bytes={len(html)} url={url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
