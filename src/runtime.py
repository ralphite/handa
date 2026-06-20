from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import difflib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any
import uuid

from .agent_runtime import get_agent_definition
from .config import AgentConfig
from .contract.hooks import normalize_hooks
from .contract.product import hooks_for_agent
from .model_configs import validate_model_config_id
from .storage.paths import resolve_storage_root
from .storage.paths import session_dir
from .storage.paths import sessions_dir
from .storage.session_service import create_child_session_id
from .storage.session_service import HandaSessionService
from .storage.file_io import atomic_write_text
from .storage.file_io import file_lock



# Task-store and run-record primitives moved to the contract package; they are
# re-exported here because runtime-side workers and tools import them from
# this module. New code (and everything under src/web) should import
# src.contract.task_store directly.
from .contract.task_store import AGENT_RUN_TASK_KINDS as AGENT_RUN_TASK_KINDS
from .contract.task_store import LIVE_TASK_STATUSES as LIVE_TASK_STATUSES
from .contract.task_store import _is_process_alive as _is_process_alive
from .contract.task_store import _read_task_notifications_unlocked as _read_task_notifications_unlocked
from .contract.task_store import _task_events_lock_path as _task_events_lock_path
from .contract.task_store import _task_lock_path as _task_lock_path
from .contract.task_store import _task_notifications_lock_path as _task_notifications_lock_path
from .contract.task_store import _write_task_notifications_unlocked as _write_task_notifications_unlocked
from .contract.task_store import agent_run_report_label as agent_run_report_label
from .contract.task_store import append_task_event as append_task_event
from .contract.task_store import build_agent_run_report as build_agent_run_report
from .contract.task_store import cancel_descendant_runs as cancel_descendant_runs
from .contract.task_store import cancel_stale_live_tasks as cancel_stale_live_tasks
from .contract.task_store import cancel_task as cancel_task
from .contract.task_store import create_task_notification as create_task_notification
from .contract.task_store import create_web_turn_task as create_web_turn_task
from .contract.task_store import ensure_task_dirs as ensure_task_dirs
from .contract.task_store import get_product_root as get_product_root
from .contract.task_store import get_session_storage_dir as get_session_storage_dir
from .contract.task_store import get_storage_root as get_storage_root
from .contract.task_store import get_task_events_path as get_task_events_path
from .contract.task_store import get_task_notifications_path as get_task_notifications_path
from .contract.task_store import get_tasks_dir as get_tasks_dir
from .contract.task_store import is_process_alive as is_process_alive
from .contract.task_store import list_task_events as list_task_events
from .contract.task_store import list_task_notifications as list_task_notifications
from .contract.task_store import list_tasks as list_tasks
from .contract.task_store import load_task as load_task
from .contract.task_store import now_iso as now_iso
from .contract.task_store import now_ts as now_ts
from .contract.task_store import read_task_result as read_task_result
from .contract.task_store import resume_web_turn_task as resume_web_turn_task
from .contract.task_store import save_task as save_task
from .contract.task_store import save_task_notifications as save_task_notifications
from .contract.task_store import spawn_web_turn_worker as spawn_web_turn_worker
from .contract.task_store import start_web_turn_task as start_web_turn_task
from .contract.task_store import task_dir as task_dir
from .contract.task_store import task_file as task_file
from .contract.task_store import task_log_file as task_log_file
from .contract.task_store import task_result_file as task_result_file
from .contract.task_store import update_task_notification as update_task_notification


# Command words the agent must never invoke. validate_command is a guardrail
# against the model reaching for destructive one-liners, not a security
# boundary: anything smuggled through quoting or an interpreter (`zsh -c "..."`)
# is out of scope. Matching command-position tokens instead of substrings keeps
# legitimate work like `grep -rn shutdown src` from being rejected.
BLOCKED_COMMAND_WORDS = frozenset({
    "sudo", "doas", "shutdown", "reboot", "poweroff", "killall",
})

# Wrappers that execute their argument as a command: the word after them is
# still in command position (`nohup reboot`, `env -i sudo ls`).
_COMMAND_WRAPPERS = frozenset({
    "command", "builtin", "exec", "nohup", "env", "time", "nice", "xargs",
})

_ENV_ASSIGNMENT = re.compile(r"^[a-z_][a-z0-9_]*=")

_project_root_context: ContextVar[Path | None] = ContextVar(
    "handa_project_root",
    default=None,
)




def get_project_root() -> Path:
  contextual = _project_root_context.get()
  if contextual is not None:
    return contextual
  configured = os.getenv("HANDA_PROJECT_ROOT")
  if not configured:
    raise RuntimeError("HANDA_PROJECT_ROOT must be set.")
  return Path(configured).expanduser().resolve()


@contextmanager
def project_context(project_root: Path | str):
  token = _project_root_context.set(Path(project_root).expanduser().resolve())
  try:
    yield
  finally:
    _project_root_context.reset(token)






def _session_model_config_id(session_id: str) -> str | None:
  session = HandaSessionService(root=str(get_storage_root()))._read_session(session_id)
  if session is None:
    return None
  value = (session.state or {}).get("handa:model_config_id")
  if value is None:
    return None
  text = str(value).strip()
  return text or None














def resolve_repo_path(path: str) -> Path:
  root = get_project_root()
  target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
  if root not in target.parents and target != root:
    raise ValueError(f"path escapes project root: {path}")
  return target


def validate_command(command: str) -> None:
  lowered = command.lower()
  if ":(){" in lowered.replace(" ", ""):
    raise ValueError("blocked command: fork bomb")
  for tokens in _command_position_tokens(lowered):
    head, args = tokens[0], tokens[1:]
    if head in BLOCKED_COMMAND_WORDS:
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
  """Token groups whose first word is in command position.

  Splitting on shell operators and command-substitution openers means each
  group's head is about to be executed: `echo $(reboot)` yields a group
  starting with `reboot`, while `git log --grep reboot` keeps `reboot` in
  argument position where it is harmless.
  """
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
  # Only recursive+force `rm` aimed at `/` itself is blocked; `rm -rf` on a
  # project path is routine agent work.
  flags = "".join(arg.lstrip("-") for arg in args if arg.startswith("-"))
  if not ("r" in flags and "f" in flags):
    return False
  return any(arg in {"/", "/*"} for arg in args if not arg.startswith("-"))


# Hard ceiling for captured command output. The tail is kept because build and
# test failures report at the end; the marker line plus the *_truncated flags
# make the cut visible to the model.
MAX_COMMAND_OUTPUT_CHARS = 16000


def _clip_output(value: Any) -> tuple[str, bool]:
  if isinstance(value, bytes):
    text = value.decode("utf-8", errors="replace")
  else:
    text = value or ""
  if len(text) <= MAX_COMMAND_OUTPUT_CHARS:
    return text, False
  omitted = len(text) - MAX_COMMAND_OUTPUT_CHARS
  return (
      f"... first {omitted} chars truncated ...\n{text[-MAX_COMMAND_OUTPUT_CHARS:]}",
      True,
  )


def run_command(command: str, cwd: str = ".", timeout_sec: int = 60) -> dict[str, Any]:
  validate_command(command)
  timeout_sec = max(1, min(timeout_sec, 300))
  working_dir = resolve_repo_path(cwd)
  started = now_ts()
  try:
    completed = subprocess.run(
        ["/bin/zsh", "-fc", command],
        cwd=str(working_dir),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
  except subprocess.TimeoutExpired as exc:
    # Return the partial output instead of raising: the model needs to see how
    # far the command got to decide between retrying and raising the timeout.
    result = _command_result(
        command=command,
        working_dir=working_dir,
        returncode=None,
        stdout=exc.stdout,
        stderr=exc.stderr,
        started=started,
    )
    result["timed_out"] = True
    result["error"] = f"command timed out after {timeout_sec}s"
    return result
  return _command_result(
      command=command,
      working_dir=working_dir,
      returncode=completed.returncode,
      stdout=completed.stdout,
      stderr=completed.stderr,
      started=started,
  )


def _command_result(
    *,
    command: str,
    working_dir: Path,
    returncode: int | None,
    stdout: Any,
    stderr: Any,
    started: float,
) -> dict[str, Any]:
  stdout_text, stdout_truncated = _clip_output(stdout)
  stderr_text, stderr_truncated = _clip_output(stderr)
  result: dict[str, Any] = {
      "success": returncode == 0,
      "command": command,
      "cwd": str(working_dir),
      "returncode": returncode,
      "stdout": stdout_text,
      "stderr": stderr_text,
      "duration_sec": round(now_ts() - started, 3),
  }
  if stdout_truncated:
    result["stdout_truncated"] = True
  if stderr_truncated:
    result["stderr_truncated"] = True
  return result


# Directory names that never carry useful source signal. Skipping them keeps
# `list_files` from drowning the model in bytecode caches, build artifacts, and
# vendored dependencies. ripgrep and `git ls-files` already honour `.gitignore`,
# but we still prune these explicitly because some (e.g. `.claude`, `.idea`)
# routinely live outside any ignore file, and the non-git fallback walk has no
# ignore handling at all.
EXCLUDED_DIRS = frozenset({
    ".git", ".jj", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", ".turbo", ".cache", "storybook-static",
    ".kilo", ".claude", ".idea", ".vscode",
})

# Compiled / binary / vendored file suffixes that bloat listings without helping
# the model reason about the code.
EXCLUDED_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib", ".dll", ".class",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
    ".mp4", ".mov", ".mp3", ".wav", ".zip", ".gz", ".tar", ".whl",
    ".map", ".lock",
})

# Hard ceilings for search output. `list_files` has no default item ceiling:
# callers can opt into truncation with `max_files` when they need a preview.
MAX_SEARCH_MATCHES = 100
MAX_SEARCH_LINE_CHARS = 200
MAX_SEARCH_TOTAL_CHARS = 16000

# Cap the per-directory breakdown appended to truncated listings so the
# omission note itself cannot balloon past the budget `max_files` bought.
MAX_OMITTED_DIRS_IN_NOTE = 10


def _is_excluded_file(name: str) -> bool:
  lowered = name.lower()
  if lowered.endswith(".min.js") or lowered.endswith(".min.css"):
    return True
  return any(lowered.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _collect_files_via_git(
    base: Path,
    root: Path,
    bounded_depth: int | None,
) -> tuple[list[tuple[int, str]], bool] | None:
  """Collect (depth, path) entries from git, or None when git cannot answer.

  `git ls-files --cached --others --exclude-standard` yields tracked files plus
  untracked-but-not-ignored ones, so listings honour the project's own
  `.gitignore` — something the walk fallback cannot do. Returns None outside a
  git work tree (or when git is missing/failing) so the caller falls back.
  """
  try:
    # `-z` keeps unusual filenames parseable: without it git quotes and escapes
    # non-ASCII paths.
    completed = subprocess.run(
        ["git", "-C", str(base), "ls-files",
         "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
        timeout=30,
    )
  except (OSError, subprocess.SubprocessError):
    return None
  if completed.returncode != 0:
    return None
  rel_paths = [Path(entry) for entry in completed.stdout.split("\0") if entry]
  # Match the walk's emission order — files before subdirectories, each level
  # sorted — so truncation keeps the same entries either way. Comparing
  # (directory parts, filename) tuples reproduces exactly that order.
  rel_paths.sort(key=lambda rel: (rel.parts[:-1], rel.parts[-1]))
  collected: list[tuple[int, str]] = []
  depth_limited = False
  for rel in rel_paths:
    # EXCLUDED_DIRS still applies as a backstop: some of those directories are
    # routinely absent from ignore files.
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
      continue
    if _is_excluded_file(rel.parts[-1]):
      continue
    # `--cached` also reports files deleted from the worktree but still in the
    # index; the walk would never see those, so drop them here too.
    if not (base / rel).is_file():
      continue
    depth = len(rel.parts) - 1
    if bounded_depth is not None and depth > bounded_depth:
      # Unlike the walk, git only reveals a too-deep subtree when it actually
      # contains listable files — which is when the note matters anyway.
      depth_limited = True
      continue
    collected.append((depth, str((base / rel).relative_to(root))))
  return collected, depth_limited


def _collect_files_via_walk(
    base: Path,
    root: Path,
    bounded_depth: int | None,
) -> tuple[list[tuple[int, str]], bool]:
  """Collect (depth, path) entries by walking the tree; no ignore handling."""
  collected: list[tuple[int, str]] = []
  depth_limited = False
  for current_dir, dirnames, filenames in os.walk(base):
    # Prune excluded directories in place so os.walk never descends into them.
    dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
    current = Path(current_dir)
    depth = len(current.relative_to(base).parts)
    if bounded_depth is not None and depth >= bounded_depth:
      # Already at the depth limit; do not recurse further but still list files
      # at this level.
      if dirnames:
        depth_limited = True
      dirnames[:] = []
    for filename in sorted(filenames):
      if _is_excluded_file(filename):
        continue
      collected.append((depth, str((current / filename).relative_to(root))))
  return collected, depth_limited


def list_files(
    path: str = ".",
    max_depth: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
  base = resolve_repo_path(path)
  root = get_project_root()
  bounded_depth = None if max_depth is None else max(0, int(max_depth))
  bounded_files = None if max_files is None else max(0, int(max_files))
  via_git = _collect_files_via_git(base, root, bounded_depth)
  if via_git is not None:
    collected, depth_limited = via_git
  else:
    collected, depth_limited = _collect_files_via_walk(base, root, bounded_depth)
  total_count = len(collected)
  truncated = bounded_files is not None and total_count > bounded_files
  if truncated:
    # Keep the shallowest files so a truncated listing still maps the overall
    # repo shape instead of stopping wherever the walk happened to be; the
    # stable sort preserves walk order between files at the same depth.
    kept = {
        file_path
        for _, file_path in sorted(collected, key=lambda entry: entry[0])[:bounded_files]
    }
    shown_files = [file_path for _, file_path in collected if file_path in kept]
    omitted_files = [file_path for _, file_path in collected if file_path not in kept]
  else:
    shown_files = [file_path for _, file_path in collected]
    omitted_files = []
  # Truncation must be visible in the listing itself: the model reads the text,
  # not the side-channel counters.
  notes: list[str] = []
  if truncated:
    notes.append(_omission_note(omitted_files, total_count))
  if depth_limited:
    notes.append(
        f"... subdirectories below depth {bounded_depth} not listed"
        " (raise max_depth for more)"
    )
  listing = "\n".join(
      part for part in [_format_grouped_file_listing(shown_files), *notes] if part
  )
  result: dict[str, Any] = {
      "path": str(base.relative_to(root)),
      "format": "directory_groups",
      "listing": listing,
      "file_count": total_count,
      "shown_count": len(shown_files),
  }
  if truncated:
    result["truncated"] = True
    result["omitted_count"] = total_count - len(shown_files)
  if depth_limited:
    result["depth_limited"] = True
  if bounded_depth is not None:
    result["max_depth"] = bounded_depth
  if bounded_files is not None:
    result["max_files"] = bounded_files
  return result


def _omission_note(omitted_files: list[str], total_count: int) -> str:
  by_dir: dict[str, int] = {}
  for file_path in omitted_files:
    parent = str(Path(file_path).parent)
    directory = "./" if parent == "." else f"{parent}/"
    by_dir[directory] = by_dir.get(directory, 0) + 1
  ranked = sorted(by_dir.items(), key=lambda item: (-item[1], item[0]))
  breakdown = ", ".join(
      f"{directory} +{count}"
      for directory, count in ranked[:MAX_OMITTED_DIRS_IN_NOTE]
  )
  if len(ranked) > MAX_OMITTED_DIRS_IN_NOTE:
    breakdown = f"{breakdown}, +{len(ranked) - MAX_OMITTED_DIRS_IN_NOTE} more directories"
  return (
      f"... {len(omitted_files)} of {total_count} files omitted: {breakdown}."
      " List a subdirectory or raise max_files for the rest."
  )


def _format_grouped_file_listing(files: list[str]) -> str:
  groups: dict[str, list[str]] = {}
  for file_path in files:
    path = Path(file_path)
    directory = str(path.parent)
    if directory == ".":
      directory = "./"
    else:
      directory = f"{directory}/"
    groups.setdefault(directory, []).append(path.name)

  blocks: list[str] = []
  for directory in sorted(groups):
    blocks.append(directory)
    blocks.extend(f"  {filename}" for filename in sorted(groups[directory]))
  return "\n".join(blocks)


def search_code(query: str, path: str = ".") -> dict[str, Any]:
  base = resolve_repo_path(path)
  root = get_project_root()
  rel_base = str(base.relative_to(root)) or "."
  # Run from the project root with a relative target so match lines carry short
  # relative paths instead of long absolute ones. `-M` caps single-line width so
  # a minified/vendored line cannot blow up the payload, and ripgrep's default
  # .gitignore handling keeps build artifacts out.
  command = (
      f"rg -n --no-heading -M {MAX_SEARCH_LINE_CHARS} "
      f"--glob '!.git' --glob '!**/*.min.*' "
      f"{shlex.quote(query)} {shlex.quote(rel_base)}"
  )
  result = subprocess.run(
      ["/bin/zsh", "-fc", command],
      cwd=str(root),
      capture_output=True,
      text=True,
      timeout=60,
  )
  # ripgrep exits 0 with matches and 1 on a clean no-match run; anything else
  # (bad regex, missing binary) must surface as an error — a silent empty
  # result reads as "the symbol does not exist".
  if result.returncode not in (0, 1):
    return {
        "query": query,
        "path": rel_base,
        "matches": [],
        "match_count": 0,
        "truncated": False,
        "error": (result.stderr.strip() or f"rg exited with {result.returncode}")[:1000],
    }
  raw = [line for line in result.stdout.splitlines() if line.strip()]
  matches: list[str] = []
  total_chars = 0
  for line in raw[:MAX_SEARCH_MATCHES]:
    total_chars += len(line)
    if total_chars > MAX_SEARCH_TOTAL_CHARS:
      break
    matches.append(line)
  return {
      "query": query,
      "path": rel_base,
      "matches": matches,
      "match_count": len(raw),
      "truncated": len(matches) < len(raw),
  }


def read_file(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
  target = resolve_repo_path(path)
  lines = target.read_text(encoding="utf-8").splitlines()
  start = max(1, start_line)
  end = max(start, min(end_line, len(lines) or start))
  excerpt = [
      f"{index + 1}: {line}"
      for index, line in enumerate(lines[start - 1 : end], start=start - 1)
  ]
  return {
      "path": str(target.relative_to(get_project_root())),
      "start_line": start,
      "end_line": end,
      "total_lines": len(lines),
      "content": "\n".join(excerpt),
  }


def _diff_line_counts(old_text: str, new_text: str) -> tuple[int, int]:
  """Lines added/removed between two file contents (diff-stat semantics).

  Used to surface per-edit change size on file-tool responses so the web turn
  can accumulate file-change stats. A new file reports every line as added; an
  unchanged rewrite reports (0, 0). Counting from SequenceMatcher opcodes
  avoids the prefix collision a unified-diff scan would hit on source lines
  that themselves begin with "+" or "-".
  """
  matcher = difflib.SequenceMatcher(
      a=old_text.splitlines(),
      b=new_text.splitlines(),
      autojunk=False,
  )
  added = removed = 0
  for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag in ("replace", "delete"):
      removed += i2 - i1
    if tag in ("replace", "insert"):
      added += j2 - j1
  return added, removed


def write_file(path: str, content: str) -> dict[str, Any]:
  target = resolve_repo_path(path)
  target.parent.mkdir(parents=True, exist_ok=True)
  previous = target.read_text(encoding="utf-8") if target.exists() else ""
  target.write_text(content, encoding="utf-8")
  lines_added, lines_removed = _diff_line_counts(previous, content)
  return {
      "success": True,
      "path": str(target.relative_to(get_project_root())),
      "lines_added": lines_added,
      "lines_removed": lines_removed,
  }


def replace_in_file(
    path: str,
    old_text: str,
    new_text: str,
    expected_replacements: int | None = None,
) -> dict[str, Any]:
  target = resolve_repo_path(path)
  original = target.read_text(encoding="utf-8")
  occurrences = original.count(old_text)
  if occurrences == 0:
    return {
        "success": False,
        "path": str(target.relative_to(get_project_root())),
        "error": "old_text not found",
    }
  if expected_replacements is not None and occurrences != expected_replacements:
    return {
        "success": False,
        "path": str(target.relative_to(get_project_root())),
        "occurrences": occurrences,
        "error": (
            f"old_text occurs {occurrences} times, expected"
            f" {expected_replacements}; nothing was replaced"
        ),
    }
  updated = original.replace(old_text, new_text)
  target.write_text(updated, encoding="utf-8")
  lines_added, lines_removed = _diff_line_counts(original, updated)
  return {
      "success": True,
      "path": str(target.relative_to(get_project_root())),
      "replacements": occurrences,
      "lines_added": lines_added,
      "lines_removed": lines_removed,
  }




























def start_background_task(
    command: str,
    cwd: str = ".",
    summary: str | None = None,
    *,
    session_id: str,
) -> dict[str, Any]:
  ensure_task_dirs(session_id)
  validate_command(command)
  working_dir = resolve_repo_path(cwd)
  task_id = f"task_{uuid.uuid4().hex[:10]}"
  task = {
      "id": task_id,
      "session_id": session_id,
      "kind": "command",
      "command": command,
      "project_root": str(get_project_root()),
      "cwd": str(working_dir.relative_to(get_project_root())),
      "summary": summary or command,
      "status": "queued",
      "created_at": now_iso(),
      "created_ts": now_ts(),
      "started_at": None,
      "finished_at": None,
      "returncode": None,
      "worker_pid": None,
      "command_pid": None,
      "cancel_requested_at": None,
      "log_path": str(task_log_file(task_id, session_id=session_id)),
  }
  save_task(task)
  append_task_event(
      "task.created",
      f"Task {task_id} created",
      session_id=session_id,
      task_id=task_id,
  )
  product_root = get_product_root()
  env = os.environ.copy()
  env["HANDA_STORAGE_ROOT"] = str(get_storage_root())
  process = subprocess.Popen(
      [sys.executable, "-m", "src.task_worker", session_id, task_id],
      cwd=str(product_root),
      env=env,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
  )
  task["worker_pid"] = process.pid
  save_task(task)
  return task


def _allocate_child_session_id(parent_session_id: str) -> str:
  for _ in range(100):
    child_session_id = create_child_session_id(parent_session_id)
    child_session_file = (
        session_dir(get_storage_root(), child_session_id) / "session.json"
    )
    if not child_session_file.exists():
      return child_session_id
  raise RuntimeError("Could not create a unique child session id.")


def _base_run_task(
    *,
    task_id: str,
    session_id: str,
    kind: str,
    child_session_id: str,
    user_id: str,
    summary: str,
) -> dict[str, Any]:
  return {
      "id": task_id,
      "session_id": session_id,
      "kind": kind,
      "project_root": str(get_project_root()),
      "agent_runtime": "native",
      "child_session_id": child_session_id,
      "user_id": user_id,
      "summary": summary,
      "status": "queued",
      "created_at": now_iso(),
      "created_ts": now_ts(),
      "started_at": None,
      "finished_at": None,
      "returncode": None,
      "worker_pid": None,
      "command_pid": None,
      "cancel_requested_at": None,
      "log_path": str(task_log_file(task_id, session_id=session_id)),
      "result_path": str(task_dir(task_id, session_id=session_id) / "result.json"),
      "summary_artifact": None,
      "hooks": [],
  }


def _launch_agent_run_worker(
    task: dict[str, Any], *, session_id: str, task_id: str
) -> dict[str, Any]:
  product_root = get_product_root()
  env = os.environ.copy()
  env["HANDA_STORAGE_ROOT"] = str(get_storage_root())
  process = subprocess.Popen(
      [sys.executable, "-m", "src.agent_run_worker", session_id, task_id],
      cwd=str(product_root),
      env=env,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
  )
  task["worker_pid"] = process.pid
  save_task(task)
  return task


def start_agent_run_task(
    config_name: str,
    prompt: str,
    context: str | None = None,
    summary: str | None = None,
    config_version: int | None = None,
    model_config_id: str | None = None,
    *,
    session_id: str,
    user_id: str,
    app_name: str,
    depth: int = 0,
    suppress_task_notification: bool = False,
) -> dict[str, Any]:
  resolved_model_config_id = validate_model_config_id(
      model_config_id or _session_model_config_id(session_id)
  )
  ensure_task_dirs(session_id)
  task_id = f"task_{uuid.uuid4().hex[:10]}"
  child_session_id = _allocate_child_session_id(session_id)
  task = {
      **_base_run_task(
          task_id=task_id,
          session_id=session_id,
          kind="agent_run",
          child_session_id=child_session_id,
          user_id=user_id,
          summary=summary or f"Run agent config {config_name}",
      ),
      "config_name": config_name,
      "config_version": config_version,
      "model_config_id": resolved_model_config_id,
      "prompt": prompt,
      "context": context or "",
      "depth": depth,
      "suppress_task_notification": bool(suppress_task_notification),
  }
  save_task(task)
  append_task_event(
      "task.created",
      f"Agent run {task_id} created",
      session_id=session_id,
      task_id=task_id,
      payload={
          "kind": "agent_run",
          "config_name": config_name,
          "agent_runtime": task["agent_runtime"],
          "model_config_id": resolved_model_config_id,
          "child_session_id": child_session_id,
          "depth": depth,
      },
  )

  child_state = {
      "handa:session_kind": "agent_run_child",
      "handa:parent_session_id": session_id,
      "handa:parent_task_id": task_id,
      "handa:agent_run_config_name": config_name,
      "handa:agent_run_config_version": config_version,
      "handa:agent_runtime": task["agent_runtime"],
      "handa:agent_run_prompt": prompt,
      "handa:model_config_id": resolved_model_config_id,
      "handa:agent_run_depth": depth + 1,
  }
  HandaSessionService(root=str(get_storage_root()))._create_session_sync(
      app_name,
      user_id,
      child_state,
      child_session_id,
  )

  return _launch_agent_run_worker(task, session_id=session_id, task_id=task_id)


def start_system_agent_run_task(
    config: dict[str, Any],
    prompt: str,
    context: str | None = None,
    summary: str | None = None,
    model_config_id: str | None = None,
    *,
    session_id: str,
    user_id: str,
    app_name: str,
    suppress_task_notification: bool = False,
) -> dict[str, Any]:
  agent_config = AgentConfig.model_validate(config)
  resolved_model_config_id = validate_model_config_id(
      model_config_id or _session_model_config_id(session_id)
  )
  normalized_config = agent_config.model_dump(exclude_none=True)

  ensure_task_dirs(session_id)
  config_name = str(normalized_config.get("name") or "").strip()
  if not config_name:
    raise ValueError("System agent config must include a name.")

  task_id = f"task_{uuid.uuid4().hex[:10]}"
  child_session_id = _allocate_child_session_id(session_id)

  task = {
      **_base_run_task(
          task_id=task_id,
          session_id=session_id,
          kind="system_agent_run",
          child_session_id=child_session_id,
          user_id=user_id,
          summary=summary or f"Run system agent {config_name}",
      ),
      "config_name": config_name,
      "config": normalized_config,
      "model_config_id": resolved_model_config_id,
      "prompt": prompt,
      "context": context or "",
      "save_parent_summary": False,
      "suppress_task_notification": bool(suppress_task_notification),
      "hooks": normalize_hooks(normalized_config.get("hooks") or []),
  }
  save_task(task)
  append_task_event(
      "task.created",
      f"System agent run {task_id} created",
      session_id=session_id,
      task_id=task_id,
      payload={
          "kind": "system_agent_run",
          "config_name": config_name,
          "agent_runtime": task["agent_runtime"],
          "model_config_id": resolved_model_config_id,
          "child_session_id": child_session_id,
      },
  )

  child_state = {
      "handa:session_kind": "system_agent_run_child",
      "handa:parent_session_id": session_id,
      "handa:parent_task_id": task_id,
      "handa:system_agent_config_name": config_name,
      "handa:agent_runtime": task["agent_runtime"],
      "handa:system_agent_run_prompt": prompt,
      "handa:model_config_id": resolved_model_config_id,
  }
  HandaSessionService(root=str(get_storage_root()))._create_session_sync(
      app_name,
      user_id,
      child_state,
      child_session_id,
  )

  return _launch_agent_run_worker(task, session_id=session_id, task_id=task_id)


def start_run_agent_task(
    agent_id: str,
    prompt: str,
    context: str | None = None,
    summary: str | None = None,
    *,
    session_id: str,
    user_id: str,
    app_name: str,
    depth: int = 0,
) -> dict[str, Any]:
  ensure_task_dirs(session_id)
  get_agent_definition(agent_id)  # validate the agent id exists
  task_id = f"task_{uuid.uuid4().hex[:10]}"
  child_session_id = _allocate_child_session_id(session_id)
  task = {
      **_base_run_task(
          task_id=task_id,
          session_id=session_id,
          kind="run_agent",
          child_session_id=child_session_id,
          user_id=user_id,
          summary=summary or f"Run agent {agent_id}",
      ),
      "agent_id": agent_id,
      "prompt": prompt,
      "context": context or "",
      "depth": depth,
      "hooks": hooks_for_agent(agent_id),
  }
  save_task(task)
  append_task_event(
      "task.created",
      f"Run agent {task_id} created",
      session_id=session_id,
      task_id=task_id,
      payload={
          "kind": "run_agent",
          "agent_id": agent_id,
          "agent_runtime": task["agent_runtime"],
          "child_session_id": child_session_id,
          "depth": depth,
      },
  )

  child_state = {
      "handa:session_kind": "run_agent_child",
      "handa:parent_session_id": session_id,
      "handa:parent_task_id": task_id,
      "handa:target_agent_id": agent_id,
      "handa:agent_runtime": task["agent_runtime"],
      "handa:run_agent_prompt": prompt,
      "handa:agent_run_depth": depth + 1,
  }
  HandaSessionService(root=str(get_storage_root()))._create_session_sync(
      app_name,
      user_id,
      child_state,
      child_session_id,
  )

  return _launch_agent_run_worker(task, session_id=session_id, task_id=task_id)


def get_task_status(
    task_id: str,
    *,
    session_id: str,
) -> dict[str, Any]:
  task = load_task(task_id, session_id=session_id)
  return task


# The full task record carries worker bookkeeping (pids, absolute paths) and
# the complete prompt/context the parent already has in its own history;
# echoing all of that back through tool results bloats the loop. Tool
# responses send this slim view instead.
_TASK_TOOL_VIEW_KEYS = (
    "id", "kind", "status", "summary", "command", "cwd",
    "agent_id", "config_name", "config_version", "child_session_id", "depth",
    "created_at", "started_at", "finished_at", "returncode",
    "cancel_requested_at", "timed_out",
)


def task_tool_view(task: dict[str, Any]) -> dict[str, Any]:
  return {key: task[key] for key in _TASK_TOOL_VIEW_KEYS if task.get(key) is not None}


def _unknown_task_response(task_id: str) -> dict[str, Any]:
  return {"found": False, "task_id": task_id, "error": f"unknown task_id: {task_id}"}


def task_status_view(task_id: str, *, session_id: str) -> dict[str, Any]:
  """Slim task lookup for tool responses; unknown ids return found=False."""
  try:
    task = load_task(task_id, session_id=session_id)
  except FileNotFoundError:
    return _unknown_task_response(task_id)
  return {"found": True, "task": task_tool_view(task)}


def task_result_view(task_id: str, *, session_id: str) -> dict[str, Any]:
  """read_task_result with the embedded task slimmed for tool responses."""
  try:
    result = read_task_result(task_id, session_id=session_id)
  except FileNotFoundError:
    return _unknown_task_response(task_id)
  task = result.get("task")
  if isinstance(task, dict):
    result = {**result, "task": task_tool_view(task)}
  return result


def cancel_task_view(task_id: str, *, session_id: str) -> dict[str, Any]:
  """cancel_task that reports unknown ids instead of raising."""
  try:
    return cancel_task(task_id, session_id=session_id)
  except FileNotFoundError:
    return _unknown_task_response(task_id)


def read_task_log(
    task_id: str,
    tail_lines: int = 200,
    *,
    session_id: str,
) -> dict[str, Any]:
  try:
    load_task(task_id, session_id=session_id)
  except FileNotFoundError:
    return _unknown_task_response(task_id)
  log_path = task_log_file(task_id, session_id=session_id)
  if not log_path.exists():
    return {"task_id": task_id, "log": "", "line_count": 0}
  lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
  tail = lines[-max(1, min(tail_lines, 500)) :]
  return {
      "task_id": task_id,
      "log": "\n".join(tail),
      "line_count": len(lines),
  }
