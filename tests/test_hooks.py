from __future__ import annotations

import asyncio

import pytest

from src.contract.hooks import HookBlockedError
from src.contract.hooks import hooks_for_trigger
from src.contract.hooks import run_hooks
from src.contract.hooks import run_hooks_sync


def test_hooks_for_trigger_normalizes_valid_hooks():
  hooks = hooks_for_trigger(
      [
          {"trigger": "pre_invocation", "command": "python3 -c 'print(1)'"},
          {"trigger": "missing", "command": "echo no"},
          {"trigger": "pre_invocation", "command": ""},
      ],
      "pre_invocation",
  )

  assert len(hooks) == 1
  assert hooks[0]["trigger"] == "pre_invocation"
  assert hooks[0]["timeout_sec"] == 10


def test_run_hooks_sync_blocks_when_hook_requests_block(tmp_path):
  events = []
  hook = {
      "id": "gate",
      "trigger": "pre_tool",
      "command": "python3 -c 'print(\"{\\\"block\\\": true, \\\"reason\\\": \\\"deny\\\"}\")'",
  }

  with pytest.raises(HookBlockedError, match="deny"):
    run_hooks_sync(
        [hook],
        trigger="pre_tool",
        context={"tool_name": "files_write"},
        project_root=tmp_path,
        emit_event=events.append,
    )

  assert [event["kind"] for event in events] == ["hook.started", "hook.blocked"]


def test_run_hooks_async_passes_context_on_stdin(tmp_path):
  async def run():
    hook = {
        "id": "reader",
        "trigger": "pre_invocation",
        "command": (
            "python3 -c 'import json,sys; "
            "print(json.load(sys.stdin)[\"context\"][\"session_id\"])'"
        ),
    }
    results = await run_hooks(
        [hook],
        trigger="pre_invocation",
        context={"session_id": "session-1"},
        project_root=tmp_path,
    )
    assert results[0]["ok"] is True
    assert results[0]["stdout"].strip() == "session-1"

  asyncio.run(run())
