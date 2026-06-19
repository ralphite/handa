#!/usr/bin/env python3
"""Import the worker/agent-runtime entrypoints to catch platform-specific landmines.

The API server boots one import graph; the workers it spawns (`python -m
src.turn_worker`, etc.) pull in additional modules. On Windows a single Unix-only
import (fcntl, termios, ...) anywhere in that graph crashes the worker at startup.
Importing every spawned entrypoint here surfaces such issues without needing an
LLM key or a live task. Run with the bundled interpreter and PYTHONPATH set to the
bundle's app dir + vendor.
"""

from __future__ import annotations

import importlib
import sys

# Every module the app launches via `sys.executable -m <module>`.
ENTRYPOINTS = [
    "src.turn_worker",
    "src.task_worker",
    "src.agent_run_worker",
    "src.browser_daemon",
    "src.agent_introspection",
]


def main() -> int:
    for module in ENTRYPOINTS:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report which entrypoint failed
            print(f"import FAILED: {module}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    print("worker/runtime imports ok: " + ", ".join(ENTRYPOINTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
