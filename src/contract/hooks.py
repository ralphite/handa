"""Runtime hook contract shared by Web and worker processes.

Hooks are deliberately small and process-neutral: a hook is a bounded command
that receives a JSON context on stdin and may emit JSON on stdout. Worker
processes use this module for invocation/tool hooks; Web-side cancel paths use
the same module for stop-request hooks without importing runtime agents.
"""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any
from typing import Callable
from typing import Literal
import uuid


HookTrigger = Literal[
    "pre_invocation",
    "post_invocation",
    "stop_requested",
    "pre_tool",
    "post_tool",
]

HOOK_TRIGGERS = {
    "pre_invocation",
    "post_invocation",
    "stop_requested",
    "pre_tool",
    "post_tool",
}

DEFAULT_HOOK_TIMEOUT_SEC = 10
MAX_HOOK_OUTPUT_CHARS = 12000

_BLOCKED_COMMAND_WORDS = frozenset({
    "sudo",
    "doas",
    "shutdown",
    "reboot",
    "poweroff",
    "killall",
})
_COMMAND_WRAPPERS = frozenset({
    "command",
    "builtin",
    "exec",
    "nohup",
    "env",
    "time",
    "nice",
    "xargs",
})
_ENV_ASSIGNMENT = re.compile(r"^[a-z_][a-z0-9_]*=")


@dataclass(frozen=True)
class HookSpec:
  id: str
  trigger: str
  command: str
  cwd: str | None = None
  timeout_sec: int = DEFAULT_HOOK_TIMEOUT_SEC
  block_on_failure: bool = False
  enabled: bool = True

  def model_dump(self) -> dict[str, Any]:
    return {
        "id": self.id,
        "trigger": self.trigger,
        "command": self.command,
        "cwd": self.cwd,
        "timeout_sec": self.timeout_sec,
        "block_on_failure": self.block_on_failure,
        "enabled": self.enabled,
    }


class HookBlockedError(RuntimeError):
  def __init__(self, *, trigger: str, hook_id: str, result: dict[str, Any]):
    message = _block_reason(result) or f"Hook {hook_id} blocked {trigger}"
    super().__init__(message)
    self.trigger = trigger
    self.hook_id = hook_id
    self.result = result


SyncHookEventSink = Callable[[dict[str, Any]], None]
AsyncHookEventSink = Callable[[dict[str, Any]], Any]


def normalize_hooks(raw_hooks: Any) -> list[dict[str, Any]]:
  return [hook.model_dump() for hook in _parse_hooks(raw_hooks)]


def hooks_for_trigger(raw_hooks: Any, trigger: str) -> list[dict[str, Any]]:
  return [
      hook.model_dump()
      for hook in _parse_hooks(raw_hooks)
      if hook.trigger == trigger and hook.enabled
  ]


async def run_hooks(
    raw_hooks: Any,
    *,
    trigger: str,
    context: dict[str, Any],
    project_root: str | Path | None = None,
    emit_event: AsyncHookEventSink | None = None,
) -> list[dict[str, Any]]:
  results: list[dict[str, Any]] = []
  for hook in _parse_hooks(raw_hooks):
    if not hook.enabled or hook.trigger != trigger:
      continue
    await _emit_async(emit_event, _hook_event("hook.started", hook, context=context))
    result = await asyncio.to_thread(
        _run_hook_command,
        hook,
        trigger=trigger,
        context=context,
        project_root=project_root,
    )
    results.append(result)
    await _emit_async(emit_event, _hook_event(_result_event_kind(result), hook, result=result))
    if _result_blocks(result, hook):
      raise HookBlockedError(trigger=trigger, hook_id=hook.id, result=result)
  return results


def run_hooks_sync(
    raw_hooks: Any,
    *,
    trigger: str,
    context: dict[str, Any],
    project_root: str | Path | None = None,
    emit_event: SyncHookEventSink | None = None,
) -> list[dict[str, Any]]:
  results: list[dict[str, Any]] = []
  for hook in _parse_hooks(raw_hooks):
    if not hook.enabled or hook.trigger != trigger:
      continue
    _emit_sync(emit_event, _hook_event("hook.started", hook, context=context))
    result = _run_hook_command(
        hook,
        trigger=trigger,
        context=context,
        project_root=project_root,
    )
    results.append(result)
    _emit_sync(emit_event, _hook_event(_result_event_kind(result), hook, result=result))
    if _result_blocks(result, hook):
      raise HookBlockedError(trigger=trigger, hook_id=hook.id, result=result)
  return results


def _parse_hooks(raw_hooks: Any) -> list[HookSpec]:
  if not isinstance(raw_hooks, list):
    return []
  hooks: list[HookSpec] = []
  for index, raw in enumerate(raw_hooks):
    if not isinstance(raw, dict):
      continue
    trigger = str(raw.get("trigger") or "").strip()
    command = str(raw.get("command") or "").strip()
    if trigger not in HOOK_TRIGGERS or not command:
      continue
    hooks.append(
        HookSpec(
            id=str(raw.get("id") or f"{trigger}_{index + 1}").strip(),
            trigger=trigger,
            command=command,
            cwd=_optional_str(raw.get("cwd")),
            timeout_sec=_bounded_timeout(raw.get("timeout_sec")),
            block_on_failure=bool(raw.get("block_on_failure") or False),
            enabled=bool(raw.get("enabled", True)),
        )
    )
  return hooks


def _run_hook_command(
    hook: HookSpec,
    *,
    trigger: str,
    context: dict[str, Any],
    project_root: str | Path | None,
) -> dict[str, Any]:
  started = time.time()
  payload = _jsonable({
      "trigger": trigger,
      "hook_id": hook.id,
      "context": context,
  })
  cwd = _resolve_cwd(hook.cwd, project_root)
  env = os.environ.copy()
  env["HANDA_HOOK_TRIGGER"] = trigger
  env["HANDA_HOOK_ID"] = hook.id
  env["HANDA_HOOK_CONTEXT_JSON"] = json.dumps(payload, ensure_ascii=True)
  try:
    _validate_command(hook.command)
    completed = subprocess.run(
        ["/bin/zsh", "-fc", hook.command],
        cwd=str(cwd) if cwd is not None else None,
        input=json.dumps(payload, ensure_ascii=True),
        capture_output=True,
        text=True,
        timeout=hook.timeout_sec,
        env=env,
    )
    stdout = _clip(completed.stdout or "")
    stderr = _clip(completed.stderr or "")
    parsed = _parse_json(stdout)
    return {
        "hook_id": hook.id,
        "trigger": trigger,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "duration_ms": round((time.time() - started) * 1000),
        "stdout": stdout,
        "stderr": stderr,
        "json": parsed,
        "block": _json_blocked(parsed),
    }
  except subprocess.TimeoutExpired as exc:
    return {
        "hook_id": hook.id,
        "trigger": trigger,
        "ok": False,
        "returncode": None,
        "duration_ms": round((time.time() - started) * 1000),
        "stdout": _clip(exc.stdout or ""),
        "stderr": _clip(exc.stderr or ""),
        "timed_out": True,
        "block": False,
    }
  except Exception as exc:  # noqa: BLE001 - hook failures are reported as data.
    return {
        "hook_id": hook.id,
        "trigger": trigger,
        "ok": False,
        "returncode": None,
        "duration_ms": round((time.time() - started) * 1000),
        "error": {"type": type(exc).__name__, "message": str(exc)},
        "block": False,
    }


def _hook_event(
    kind: str,
    hook: HookSpec,
    *,
    context: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
  payload: dict[str, Any] = {
      "hook_id": hook.id,
      "trigger": hook.trigger,
  }
  if context is not None:
    payload["context"] = _event_context(context)
  if result is not None:
    payload["result"] = result
  return {
      "id": f"hook_{uuid.uuid4().hex[:12]}",
      "kind": kind,
      "summary": f"{hook.trigger} hook {hook.id}",
      "payload": payload,
  }


def _event_context(context: dict[str, Any]) -> dict[str, Any]:
  allowed = {
      "session_id",
      "turn_id",
      "task_id",
      "agent_id",
      "agent_runtime",
      "trigger",
      "tool_name",
      "status",
      "reason",
  }
  return {key: value for key, value in context.items() if key in allowed}


async def _emit_async(sink: AsyncHookEventSink | None, event: dict[str, Any]) -> None:
  if sink is None:
    return
  result = sink(event)
  if asyncio.iscoroutine(result):
    await result


def _emit_sync(sink: SyncHookEventSink | None, event: dict[str, Any]) -> None:
  if sink is not None:
    sink(event)


def _result_event_kind(result: dict[str, Any]) -> str:
  if result.get("block"):
    return "hook.blocked"
  if result.get("ok"):
    return "hook.completed"
  return "hook.failed"


def _result_blocks(result: dict[str, Any], hook: HookSpec) -> bool:
  return bool(result.get("block")) or (hook.block_on_failure and not result.get("ok"))


def _block_reason(result: dict[str, Any]) -> str:
  parsed = result.get("json")
  if isinstance(parsed, dict):
    reason = str(parsed.get("reason") or parsed.get("message") or "").strip()
    if reason:
      return reason
  error = result.get("error")
  if isinstance(error, dict):
    return str(error.get("message") or "").strip()
  return str(result.get("stderr") or "").strip()


def _json_blocked(value: Any) -> bool:
  return isinstance(value, dict) and bool(value.get("block"))


def _parse_json(text: str) -> Any:
  stripped = text.strip()
  if not stripped:
    return None
  try:
    return json.loads(stripped)
  except json.JSONDecodeError:
    return None


def _resolve_cwd(cwd: str | None, project_root: str | Path | None) -> Path | None:
  if project_root is None or not str(project_root).strip():
    return Path(cwd).expanduser().resolve() if cwd else None
  root = Path(project_root).expanduser().resolve()
  if not cwd:
    return root
  target = (root / cwd).resolve() if not Path(cwd).is_absolute() else Path(cwd).expanduser().resolve()
  if target != root and root not in target.parents:
    raise ValueError(f"hook cwd escapes project root: {cwd}")
  return target


def _bounded_timeout(value: Any) -> int:
  try:
    timeout = int(value)
  except (TypeError, ValueError):
    timeout = DEFAULT_HOOK_TIMEOUT_SEC
  return max(1, min(timeout, 120))


def _optional_str(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _clip(text: str) -> str:
  if len(text) <= MAX_HOOK_OUTPUT_CHARS:
    return text
  omitted = len(text) - MAX_HOOK_OUTPUT_CHARS
  return f"... first {omitted} chars truncated ...\n{text[-MAX_HOOK_OUTPUT_CHARS:]}"


def _validate_command(command: str) -> None:
  lowered = command.lower()
  if ":(){" in lowered.replace(" ", ""):
    raise ValueError("blocked command: fork bomb")
  for tokens in _command_position_tokens(lowered):
    head, args = tokens[0], tokens[1:]
    if head in _BLOCKED_COMMAND_WORDS:
      raise ValueError(f"blocked command: {head}")
    if head.startswith("mkfs"):
      raise ValueError("blocked command: mkfs")
    if head == "dd" and any(arg.startswith("if=") for arg in args):
      raise ValueError("blocked command: dd if=")
    if head == "diskutil" and any(arg.startswith("erase") for arg in args):
      raise ValueError("blocked command: diskutil erase")
    if head == "rm" and _rm_targets_filesystem_root(args):
      raise ValueError("blocked command: rm -rf /")


def _command_position_tokens(lowered: str) -> list[list[str]]:
  groups: list[list[str]] = []
  for segment in re.split(r"[;&|\n]+|\$\(|`", lowered):
    try:
      tokens = shlex.split(segment)
    except ValueError:
      tokens = segment.split()
    tokens = [token.strip("(){}") for token in tokens]
    tokens = [token for token in tokens if token]
    index = 0
    while index < len(tokens):
      token = tokens[index]
      if (
          token in _COMMAND_WRAPPERS
          or token.startswith("-")
          or _ENV_ASSIGNMENT.match(token)
      ):
        index += 1
        continue
      break
    if index < len(tokens):
      groups.append(tokens[index:])
  return groups


def _rm_targets_filesystem_root(args: list[str]) -> bool:
  flags = "".join(arg.lstrip("-") for arg in args if arg.startswith("-"))
  if not ("r" in flags and "f" in flags):
    return False
  return any(arg in {"/", "/*"} for arg in args if not arg.startswith("-"))


def _jsonable(value: Any) -> Any:
  if value is None or isinstance(value, (str, int, float, bool)):
    return value
  if isinstance(value, dict):
    return {str(key): _jsonable(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_jsonable(item) for item in value]
  return str(value)
