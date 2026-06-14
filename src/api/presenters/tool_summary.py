from __future__ import annotations

import re
from typing import Any

_ERROR_STATUS_LINE = re.compile(r"\b\d{3}\b\s+[A-Z_]{4,}")
_ERROR_SUMMARY_MAX_CHARS = 140


def summarize_tool_call(name: str, args: dict[str, Any]) -> str:
  if name == "files_list":
    return f"Listed files in {args.get('path') or '.'}"
  if name == "files_search":
    return f"Searched code for {args.get('query') or ''}".strip()
  if name == "files_read":
    return f"Read {args.get('path') or 'file'}"
  if name == "files_write":
    return f"Wrote {args.get('path') or 'file'}"
  if name == "files_replace":
    return f"Edited {args.get('path') or 'file'}"
  if name == "commands_run":
    return f"Ran {_short_command(str(args.get('command') or 'command'))}"
  if name == "browser_open":
    return f"Opened browser {args.get('url') or ''}".strip()
  if name == "browser_snapshot":
    return "Captured browser snapshot"
  if name == "browser_click":
    return f"Clicked browser {args.get('target') or 'target'}"
  if name == "browser_type":
    return f"Typed in browser {args.get('target') or 'target'}"
  if name == "browser_keys":
    return f"Pressed browser keys {args.get('keys') or ''}".strip()
  if name == "browser_scroll":
    return f"Scrolled browser {args.get('direction') or 'down'}"
  if name == "browser_wait":
    return "Waited in browser"
  if name == "browser_screenshot":
    return "Captured browser screenshot"
  if name == "browser_close":
    return "Closed browser"
  if name == "artifacts_save_text":
    return f"Saved artifact {args.get('filename') or ''}".strip()
  if name == "artifacts_list":
    return "Listed artifacts"
  if name == "artifacts_read":
    return f"Read artifact {args.get('filename') or ''}".strip()
  if name == "agents_save_config":
    return f"Saved agent config {args.get('name') or ''}".strip()
  if name == "agents_start_run":
    return f"Started agent run {args.get('name') or ''}".strip()
  if name.startswith("agents_"):
    return f"Used {name}"
  if name.startswith("tasks_"):
    return "Checked background task" if name != "tasks_start_background" else "Started background task"
  return f"Called {name}"


def summarize_tool_response(name: str, response: Any) -> str:
  if name == "commands_run" and isinstance(response, dict):
    command = _short_command(str(response.get("command") or "command"))
    return f"Command passed: {command}" if response.get("success") else f"Command failed: {command}"
  if name.startswith("browser_") and isinstance(response, dict):
    action = str(response.get("last_action") or name).strip()
    return action if response.get("success", True) else f"Browser failed: {action}"
  if response_indicates_failed_outcome(response):
    return f"Failed {name}"
  if name == "artifacts_save_text" and isinstance(response, dict):
    return f"Saved artifact {response.get('filename') or ''}".strip()
  return f"Finished {name}"


def summarize_error(
    code: Any,
    message: Any,
    *,
    fallback: str = "Runtime error",
) -> str:
  """One-line summary for an error event; the full message stays in the payload.

  Provider errors arrive as multi-line walls (mitigation links, raw JSON). Pick
  the line that states the actual error — `429 RESOURCE_EXHAUSTED. {...}` style
  status lines win over lead-in prose — and cut before any inline JSON blob.
  """
  text = str(message or "").strip()
  line = ""
  if text:
    lines = [item.strip() for item in text.splitlines() if item.strip()]
    line = next(
        (item for item in lines if _ERROR_STATUS_LINE.search(item)),
        lines[0] if lines else "",
    )
    brace = line.find("{")
    if brace > 0:
      line = line[:brace]
  if not line:
    line = str(code or "").strip()
  line = " ".join(line.split())
  if not line:
    return fallback
  if len(line) <= _ERROR_SUMMARY_MAX_CHARS:
    return line
  return f"{line[:_ERROR_SUMMARY_MAX_CHARS - 3]}..."


def _short_command(command: str) -> str:
  command = " ".join(command.split())
  if len(command) <= 48:
    return command
  return f"{command[:45]}..."


def response_indicates_failed_outcome(response: Any) -> bool:
  """Whether the tool *call* itself failed.

  Only the top-level envelope decides this. A status/result reader that
  successfully reports on a *failed* child task is still a successful tool call:
  the nested `task`/`result` record's `status: failed` is data it read, not a
  failure of the read. Descending into it conflates `tool_status` with
  `task_status` and mislabels healthy reads as failures (and double-counts
  failures in activity stats). The child outcome is surfaced separately via the
  tool's explicit `task_status` field.
  """
  if not isinstance(response, dict):
    return False
  return _record_indicates_failure(response)


def _record_indicates_failure(record: dict[str, Any]) -> bool:
  if record.get("ok") is False or record.get("success") is False:
    return True
  if record.get("found") is False or record.get("error"):
    return True
  status = str(record.get("status") or "").strip().lower()
  if status in {"failed", "cancelled", "error"}:
    return True
  returncode = record.get("returncode")
  if isinstance(returncode, bool):
    return False
  if isinstance(returncode, (int, float)):
    return returncode != 0
  return False
