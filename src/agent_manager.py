"""A small, very flexible agent manager.

This is a self-contained prototype: a single command (`handa-agents`) that
manages *agent runs* and persists both the agent definitions and the per-run
task state as plain local JSON files. It deliberately has no dependency on the
Handa web API, storage service, or session machinery — an agent here is just a
named shell command template, so the manager can drive *anything*: the Handa
CLI, `claude`, a Python script, a shell pipeline, a remote API caller, etc.

Flexibility comes from the command template. An agent is defined by a single
command string that may reference placeholders substituted at run time:

    {prompt}    the run's prompt, shell-quoted for safe interpolation
    {run_id}    the run identifier
    {run_dir}   the run's state directory (a good place to drop artifacts)
    {home}      the manager's storage home

The same values are also exported as environment variables (AGENT_PROMPT,
AGENT_RUN_ID, AGENT_RUN_DIR, AGENT_NAME) so templates that prefer env over
interpolation work too.

On-disk layout (under $HANDA_AGENT_MANAGER_HOME, default ~/.handa/agent-manager):

    agents/<name>.json          one agent definition per file
    runs/<run_id>/run.json      one run's full lifecycle state
    runs/<run_id>/output.log    that run's captured stdout+stderr

State files are written atomically and guarded by a per-record file lock, so a
detached background worker updating a run never races a `handa-agents` command
reading it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Iterable

from pydantic import BaseModel
from pydantic import Field

from .storage.file_io import atomic_write_text
from .storage.file_io import file_lock


HOME_ENV = "HANDA_AGENT_MANAGER_HOME"
AGENT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"

# Run lifecycle. A run is "live" while queued or running; the rest are terminal.
LIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# Hard ceiling on the log tail we read back into memory for `logs`/`show`, so a
# runaway agent that writes gigabytes cannot OOM the CLI.
MAX_TAIL_LINES = 5000

# Repo root, so a spawned `python -m src.agent_manager` worker can import `src`.
_REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class Agent(BaseModel):
  """A named, reusable run template.

  `command` is the only thing that makes an agent do work; everything else is
  presentation or environment. Keeping the command a free-form shell string is
  what makes the manager flexible — it never assumes the agent is any one tool.
  """

  name: str = Field(pattern=AGENT_NAME_PATTERN)
  command: str
  description: str = ""
  cwd: str | None = None
  env: dict[str, str] = Field(default_factory=dict)
  tags: list[str] = Field(default_factory=list)
  created_at: str = ""
  updated_at: str = ""


class Run(BaseModel):
  """One execution of an agent, with its full persisted lifecycle."""

  id: str
  agent: str
  label: str = ""
  prompt: str = ""
  command: str = ""
  cwd: str | None = None
  env: dict[str, str] = Field(default_factory=dict)
  status: str = "queued"
  returncode: int | None = None
  pid: int | None = None
  detached: bool = False
  error: str | None = None
  created_at: str = ""
  created_ts: float = 0.0
  started_at: str | None = None
  finished_at: str | None = None
  cancel_requested_at: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
  return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ts() -> float:
  return time.time()


def _new_run_id() -> str:
  # Timestamp prefix keeps run directories naturally sorted on disk; the random
  # suffix keeps ids unique even within the same second.
  stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
  return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


class AgentManagerError(Exception):
  """A user-facing error: the CLI prints its message and exits non-zero."""


# --------------------------------------------------------------------------- #
# Store: where state lives on disk
# --------------------------------------------------------------------------- #
class Store:
  """Filesystem-backed store for agent definitions and run state.

  All paths derive from a single `home` directory so a test (or a user who
  wants throwaway state) can point the whole manager at a tmp dir via the
  `--home` flag or the HANDA_AGENT_MANAGER_HOME env var.
  """

  def __init__(self, home: Path | str | None = None) -> None:
    self.home = resolve_home(home)

  # -- directories ---------------------------------------------------------- #
  @property
  def agents_dir(self) -> Path:
    return self.home / "agents"

  @property
  def runs_dir(self) -> Path:
    return self.home / "runs"

  def agent_file(self, name: str) -> Path:
    return self.agents_dir / f"{_safe_name(name)}.json"

  def run_dir(self, run_id: str) -> Path:
    return self.runs_dir / _safe_name(run_id)

  def run_file(self, run_id: str) -> Path:
    return self.run_dir(run_id) / "run.json"

  def run_log(self, run_id: str) -> Path:
    return self.run_dir(run_id) / "output.log"

  # -- agents --------------------------------------------------------------- #
  def save_agent(self, agent: Agent) -> None:
    atomic_write_text(self.agent_file(agent.name), agent.model_dump_json(indent=2))

  def load_agent(self, name: str) -> Agent:
    path = self.agent_file(name)
    if not path.exists():
      raise AgentManagerError(f"No agent named {name!r}. Add it with `agent add`.")
    return Agent.model_validate_json(path.read_text(encoding="utf-8"))

  def has_agent(self, name: str) -> bool:
    return self.agent_file(name).exists()

  def list_agents(self) -> list[Agent]:
    if not self.agents_dir.exists():
      return []
    agents: list[Agent] = []
    for path in sorted(self.agents_dir.glob("*.json")):
      try:
        agents.append(Agent.model_validate_json(path.read_text(encoding="utf-8")))
      except Exception:
        # A malformed file should not blind the whole listing; skip it.
        continue
    return sorted(agents, key=lambda a: a.name)

  def delete_agent(self, name: str) -> None:
    path = self.agent_file(name)
    if not path.exists():
      raise AgentManagerError(f"No agent named {name!r}.")
    path.unlink()

  # -- runs ----------------------------------------------------------------- #
  def save_run(self, run: Run) -> None:
    atomic_write_text(self.run_file(run.id), run.model_dump_json(indent=2))

  def load_run(self, run_id: str) -> Run:
    path = self.run_file(run_id)
    if not path.exists():
      raise AgentManagerError(f"No run with id {run_id!r}.")
    return Run.model_validate_json(path.read_text(encoding="utf-8"))

  def update_run(self, run_id: str, **changes: Any) -> Run:
    """Read-modify-write a run under its lock, so concurrent writers serialize.

    The detached worker and a `stop` command can both touch the same run; the
    lock plus read-inside-lock makes the last writer build on the latest state
    instead of clobbering it.
    """
    with file_lock(self.run_dir(run_id) / ".lock"):
      run = self.load_run(run_id)
      updated = run.model_copy(update=changes)
      self.save_run(updated)
      return updated

  def list_runs(self) -> list[Run]:
    if not self.runs_dir.exists():
      return []
    runs: list[Run] = []
    for path in self.runs_dir.glob("*/run.json"):
      try:
        runs.append(Run.model_validate_json(path.read_text(encoding="utf-8")))
      except Exception:
        continue
    # Newest first — the most recent run is almost always the one in question.
    return sorted(runs, key=lambda r: r.created_ts, reverse=True)

  def delete_run(self, run_id: str) -> None:
    import shutil

    directory = self.run_dir(run_id)
    if not directory.exists():
      raise AgentManagerError(f"No run with id {run_id!r}.")
    shutil.rmtree(directory)

  def resolve_run_id(self, ref: str) -> str:
    """Accept a full run id or an unambiguous id prefix (like git short SHAs)."""
    if self.run_file(ref).exists():
      return ref
    matches = [r.id for r in self.list_runs() if r.id.startswith(ref)]
    if len(matches) == 1:
      return matches[0]
    if not matches:
      raise AgentManagerError(f"No run matching {ref!r}.")
    raise AgentManagerError(
        f"{ref!r} is ambiguous; matches {len(matches)} runs: {', '.join(matches[:5])}…"
    )


def resolve_home(home: Path | str | None = None) -> Path:
  if home is not None:
    return Path(home).expanduser().resolve()
  configured = os.getenv(HOME_ENV)
  if configured:
    return Path(configured).expanduser().resolve()
  return (Path.home() / ".handa" / "agent-manager").resolve()


def _safe_name(value: str) -> str:
  if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
    raise AgentManagerError(f"invalid name: {value!r}")
  return value


# --------------------------------------------------------------------------- #
# Run execution
# --------------------------------------------------------------------------- #
def _resolve_command(agent: Agent, run: Run, store: Store) -> str:
  """Substitute placeholders in the agent's command template for this run."""
  replacements = {
      "{prompt}": shlex.quote(run.prompt),
      "{run_id}": run.id,
      "{run_dir}": str(store.run_dir(run.id)),
      "{home}": str(store.home),
  }
  command = agent.command
  for token, value in replacements.items():
    command = command.replace(token, value)
  return command


def _run_environment(agent: Agent, run: Run, store: Store) -> dict[str, str]:
  env = os.environ.copy()
  env.update(agent.env)
  env.update(run.env)
  # Run-scoped context for templates that read env instead of interpolating.
  env["AGENT_NAME"] = agent.name
  env["AGENT_RUN_ID"] = run.id
  env["AGENT_RUN_DIR"] = str(store.run_dir(run.id))
  env["AGENT_PROMPT"] = run.prompt
  return env


def create_run(
    store: Store,
    agent_name: str,
    *,
    prompt: str = "",
    label: str = "",
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> Run:
  """Materialize a queued run for an agent and persist it. Does not start it."""
  agent = store.load_agent(agent_name)
  run_id = _new_run_id()
  run = Run(
      id=run_id,
      agent=agent.name,
      label=label,
      prompt=prompt,
      env=dict(env or {}),
      cwd=cwd if cwd is not None else agent.cwd,
      status="queued",
      created_at=now_iso(),
      created_ts=now_ts(),
  )
  # Resolve the command now so the run record is a faithful, replayable snapshot
  # even if the agent definition is later edited or deleted.
  run.command = _resolve_command(agent, run, store)
  store.run_dir(run_id).mkdir(parents=True, exist_ok=True)
  store.save_run(run)
  return run


def execute_run(store: Store, run_id: str) -> Run:
  """Run a queued run to completion in *this* process, updating state and log.

  This is the single execution path; both the foreground CLI and the detached
  worker call it. It streams the child's stdout+stderr into the run's log file
  and records the lifecycle (running -> succeeded/failed/cancelled) atomically.
  """
  run = store.load_run(run_id)
  if run.status != "queued":
    raise AgentManagerError(
        f"Run {run_id} is {run.status!r}, not queued; refusing to re-execute."
    )
  agent = store.load_agent(run.agent)
  working_dir = Path(run.cwd).expanduser() if run.cwd else Path.cwd()

  run = store.update_run(
      run_id, status="running", started_at=now_iso(), pid=os.getpid()
  )

  log_path = store.run_log(run_id)
  log_path.parent.mkdir(parents=True, exist_ok=True)
  try:
    with log_path.open("w", encoding="utf-8") as log_handle:
      process = subprocess.Popen(
          run.command,
          shell=True,
          cwd=str(working_dir),
          env=_run_environment(agent, run, store),
          stdout=log_handle,
          stderr=subprocess.STDOUT,
          text=True,
          # New session/group so `stop` can signal the whole process tree, not
          # just the shell that spawned the real work.
          start_new_session=True,
      )
      # Record the live child pid so `stop` from another process can find it.
      run = store.update_run(run_id, pid=process.pid)
      returncode = process.wait()
  except Exception as exc:  # noqa: BLE001 - persist failure rather than crash.
    return store.update_run(
        run_id,
        status="failed",
        returncode=None,
        error=f"{type(exc).__name__}: {exc}",
        finished_at=now_iso(),
        pid=None,
    )

  final = store.load_run(run_id)
  cancelled = final.cancel_requested_at is not None and returncode != 0
  status = "cancelled" if cancelled else ("succeeded" if returncode == 0 else "failed")
  return store.update_run(
      run_id,
      status=status,
      returncode=returncode,
      finished_at=now_iso(),
      pid=None,
  )


def start_run(store: Store, run_id: str, *, detach: bool) -> Run:
  """Start a queued run, either inline (blocking) or as a detached worker."""
  if not detach:
    return execute_run(store, run_id)

  env = os.environ.copy()
  env[HOME_ENV] = str(store.home)
  process = subprocess.Popen(
      [sys.executable, "-m", "src.agent_manager", "_worker", run_id],
      cwd=str(_REPO_ROOT),
      env=env,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True,
  )
  return store.update_run(run_id, detached=True, pid=process.pid)


def stop_run(store: Store, run_id: str) -> Run:
  """Request cancellation of a live run and signal its process group."""
  run = store.load_run(run_id)
  if run.status in TERMINAL_STATUSES:
    return run
  run = store.update_run(run_id, cancel_requested_at=now_iso())
  if run.pid:
    _terminate_process_group(run.pid)
  # A queued-but-never-started run (or a worker that died before exec) has no
  # live process to flip the status, so mark it cancelled here.
  if run.status == "queued" or not _pid_alive(run.pid):
    run = store.update_run(run_id, status="cancelled", finished_at=now_iso())
  return run


def _terminate_process_group(pid: int) -> None:
  try:
    if os.name == "posix":
      os.killpg(os.getpgid(pid), signal.SIGTERM)
    else:  # Windows has no process groups; best-effort single-process kill.
      os.kill(pid, signal.SIGTERM)
  except (ProcessLookupError, PermissionError, OSError):
    pass


def _pid_alive(pid: int | None) -> bool:
  if not pid:
    return False
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  return True


def read_log_tail(store: Store, run_id: str, tail: int) -> str:
  log_path = store.run_log(run_id)
  if not log_path.exists():
    return ""
  lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
  tail = max(1, min(tail, MAX_TAIL_LINES))
  return "\n".join(lines[-tail:])


# --------------------------------------------------------------------------- #
# CLI rendering
# --------------------------------------------------------------------------- #
def _print(payload: Any, *, as_json: bool, text: str) -> None:
  if as_json:
    print(json.dumps(payload, indent=2, default=str))
  else:
    print(text)


def _agent_text(agent: Agent) -> str:
  lines = [f"{agent.name}: {agent.description or '(no description)'}", f"  command: {agent.command}"]
  if agent.cwd:
    lines.append(f"  cwd: {agent.cwd}")
  if agent.env:
    lines.append(f"  env: {', '.join(f'{k}={v}' for k, v in agent.env.items())}")
  if agent.tags:
    lines.append(f"  tags: {', '.join(agent.tags)}")
  return "\n".join(lines)


def _run_row(run: Run) -> str:
  rc = "-" if run.returncode is None else str(run.returncode)
  label = f"  {run.label}" if run.label else ""
  return f"{run.id}  {run.status:<9}  {run.agent:<16}  rc={rc:<4}  {run.created_at}{label}"


def _run_text(run: Run) -> str:
  lines = [
      f"run:      {run.id}",
      f"agent:    {run.agent}",
      f"status:   {run.status}",
      f"command:  {run.command}",
  ]
  if run.label:
    lines.insert(1, f"label:    {run.label}")
  if run.prompt:
    lines.append(f"prompt:   {run.prompt}")
  if run.cwd:
    lines.append(f"cwd:      {run.cwd}")
  if run.returncode is not None:
    lines.append(f"returncode: {run.returncode}")
  if run.error:
    lines.append(f"error:    {run.error}")
  lines.append(f"created:  {run.created_at}")
  if run.started_at:
    lines.append(f"started:  {run.started_at}")
  if run.finished_at:
    lines.append(f"finished: {run.finished_at}")
  if run.detached and run.pid:
    lines.append(f"pid:      {run.pid}")
  return "\n".join(lines)


def _parse_env_pairs(pairs: Iterable[str] | None) -> dict[str, str]:
  env: dict[str, str] = {}
  for pair in pairs or []:
    if "=" not in pair:
      raise AgentManagerError(f"--env expects KEY=VALUE, got {pair!r}.")
    key, value = pair.split("=", 1)
    if not key:
      raise AgentManagerError(f"--env key must be non-empty in {pair!r}.")
    env[key] = value
  return env


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def _cmd_agent_add(store: Store, args: argparse.Namespace) -> int:
  if store.has_agent(args.name) and not args.force:
    raise AgentManagerError(f"Agent {args.name!r} already exists; pass --force to overwrite.")
  existing = store.load_agent(args.name) if store.has_agent(args.name) else None
  agent = Agent(
      name=args.name,
      command=args.command,
      description=args.description or "",
      cwd=args.cwd,
      env=_parse_env_pairs(args.env),
      tags=list(args.tag or []),
      created_at=existing.created_at if existing else now_iso(),
      updated_at=now_iso(),
  )
  store.save_agent(agent)
  _print(agent.model_dump(), as_json=args.json, text=f"Saved agent {agent.name!r}.")
  return 0


def _cmd_agent_list(store: Store, args: argparse.Namespace) -> int:
  agents = store.list_agents()
  if args.json:
    _print([a.model_dump() for a in agents], as_json=True, text="")
    return 0
  if not agents:
    print("No agents defined. Add one with `handa-agents agent add`.")
    return 0
  print("\n".join(_agent_text(a) for a in agents))
  return 0


def _cmd_agent_show(store: Store, args: argparse.Namespace) -> int:
  agent = store.load_agent(args.name)
  _print(agent.model_dump(), as_json=args.json, text=_agent_text(agent))
  return 0


def _cmd_agent_remove(store: Store, args: argparse.Namespace) -> int:
  store.delete_agent(args.name)
  _print({"removed": args.name}, as_json=args.json, text=f"Removed agent {args.name!r}.")
  return 0


def _read_prompt(prompt_arg: str | None) -> str:
  if prompt_arg is not None:
    return prompt_arg
  if not sys.stdin.isatty():
    piped = sys.stdin.read()
    if piped.strip():
      return piped
  return ""


def _cmd_run(store: Store, args: argparse.Namespace) -> int:
  run = create_run(
      store,
      args.agent,
      prompt=_read_prompt(args.prompt),
      label=args.label or "",
      env=_parse_env_pairs(args.env),
      cwd=args.cwd,
  )
  run = start_run(store, run.id, detach=args.detach)
  if args.json:
    _print(run.model_dump(), as_json=True, text="")
  elif args.detach:
    print(f"Started {run.id} (detached, pid {run.pid}).")
  else:
    log = read_log_tail(store, run.id, args.tail)
    if log:
      print(log)
    print(
        f"\n[{run.id}] {run.status}"
        + (f" (rc={run.returncode})" if run.returncode is not None else ""),
        file=sys.stderr,
    )
  # A non-detached run's exit code mirrors the agent's, so scripts can branch.
  if not args.detach and run.status != "succeeded":
    return run.returncode if run.returncode not in (None, 0) else 1
  return 0


def _cmd_runs(store: Store, args: argparse.Namespace) -> int:
  runs = store.list_runs()
  if args.agent:
    runs = [r for r in runs if r.agent == args.agent]
  if args.status:
    runs = [r for r in runs if r.status == args.status]
  if not args.all and not args.status:
    # Default view leans on the live runs; pass --all for the full history.
    live = [r for r in runs if r.status in LIVE_STATUSES]
    runs = live or runs[: args.limit]
  else:
    runs = runs[: args.limit]
  if args.json:
    _print([r.model_dump() for r in runs], as_json=True, text="")
    return 0
  if not runs:
    print("No runs.")
    return 0
  print("\n".join(_run_row(r) for r in runs))
  return 0


def _cmd_show(store: Store, args: argparse.Namespace) -> int:
  run = store.load_run(store.resolve_run_id(args.run))
  _print(run.model_dump(), as_json=args.json, text=_run_text(run))
  return 0


def _cmd_logs(store: Store, args: argparse.Namespace) -> int:
  run_id = store.resolve_run_id(args.run)
  if args.follow:
    return _follow_log(store, run_id, args.tail)
  print(read_log_tail(store, run_id, args.tail))
  return 0


def _follow_log(store: Store, run_id: str, tail: int) -> int:
  log_path = store.run_log(run_id)
  printed = 0
  while True:
    if log_path.exists():
      lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
      if printed == 0 and tail:
        start = max(0, len(lines) - tail)
        printed = start
      for line in lines[printed:]:
        print(line)
      printed = len(lines)
    run = store.load_run(run_id)
    if run.status in TERMINAL_STATUSES:
      return 0
    time.sleep(0.4)


def _cmd_stop(store: Store, args: argparse.Namespace) -> int:
  run = stop_run(store, store.resolve_run_id(args.run))
  _print(run.model_dump(), as_json=args.json, text=f"{run.id}: {run.status}")
  return 0


def _cmd_remove(store: Store, args: argparse.Namespace) -> int:
  run_id = store.resolve_run_id(args.run)
  run = store.load_run(run_id)
  if run.status in LIVE_STATUSES and not args.force:
    raise AgentManagerError(f"Run {run_id} is {run.status}; stop it first or pass --force.")
  store.delete_run(run_id)
  _print({"removed": run_id}, as_json=args.json, text=f"Removed run {run_id}.")
  return 0


def _cmd_retry(store: Store, args: argparse.Namespace) -> int:
  source = store.load_run(store.resolve_run_id(args.run))
  run = create_run(
      store,
      source.agent,
      prompt=source.prompt,
      label=source.label,
      env=source.env,
      cwd=source.cwd,
  )
  run = start_run(store, run.id, detach=args.detach)
  _print(
      run.model_dump(),
      as_json=args.json,
      text=f"Retried {source.id} as {run.id} ({run.status}).",
  )
  if not args.detach and run.status != "succeeded":
    return run.returncode if run.returncode not in (None, 0) else 1
  return 0


def _cmd_worker(store: Store, args: argparse.Namespace) -> int:
  """Hidden entry point: the detached background worker for one run."""
  execute_run(store, args.run)
  return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      prog="handa-agents",
      description="Manage agent runs with local-file state persistence.",
  )
  parser.add_argument(
      "--home",
      help="State directory (default $HANDA_AGENT_MANAGER_HOME or ~/.handa/agent-manager).",
  )
  parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
  sub = parser.add_subparsers(dest="command", required=True)

  # agent <subcommand>
  agent = sub.add_parser("agent", help="Manage agent definitions.")
  agent_sub = agent.add_subparsers(dest="agent_command", required=True)

  add = agent_sub.add_parser("add", help="Create or update an agent.")
  add.add_argument("name")
  add.add_argument("--command", required=True, help="Shell command template (supports {prompt}).")
  add.add_argument("--description", default="")
  add.add_argument("--cwd", help="Working directory for runs of this agent.")
  add.add_argument("--env", action="append", metavar="KEY=VALUE", help="Repeatable.")
  add.add_argument("--tag", action="append", metavar="TAG", help="Repeatable.")
  add.add_argument("--force", action="store_true", help="Overwrite an existing agent.")
  add.set_defaults(func=_cmd_agent_add)

  agent_ls = agent_sub.add_parser("list", aliases=["ls"], help="List agents.")
  agent_ls.set_defaults(func=_cmd_agent_list)

  agent_show = agent_sub.add_parser("show", help="Show one agent.")
  agent_show.add_argument("name")
  agent_show.set_defaults(func=_cmd_agent_show)

  agent_rm = agent_sub.add_parser("remove", aliases=["rm"], help="Delete an agent.")
  agent_rm.add_argument("name")
  agent_rm.set_defaults(func=_cmd_agent_remove)

  # run
  run = sub.add_parser("run", help="Start a run of an agent.")
  run.add_argument("agent")
  run.add_argument("--prompt", help="Prompt text (or piped via stdin).")
  run.add_argument("--label", help="Human label for this run.")
  run.add_argument("--env", action="append", metavar="KEY=VALUE", help="Repeatable run env.")
  run.add_argument("--cwd", help="Override the agent's working directory.")
  run.add_argument("--detach", action="store_true", help="Run in the background.")
  run.add_argument("--tail", type=int, default=200, help="Log lines to show (foreground).")
  run.set_defaults(func=_cmd_run)

  # runs / ps
  runs = sub.add_parser("runs", aliases=["ps"], help="List runs.")
  runs.add_argument("--agent", help="Filter by agent name.")
  runs.add_argument("--status", help="Filter by status.")
  runs.add_argument("--all", action="store_true", help="Show terminal runs too.")
  runs.add_argument("--limit", type=int, default=50)
  runs.set_defaults(func=_cmd_runs)

  show = sub.add_parser("show", help="Show one run's state.")
  show.add_argument("run")
  show.set_defaults(func=_cmd_show)

  logs = sub.add_parser("logs", help="Print a run's captured output.")
  logs.add_argument("run")
  logs.add_argument("--tail", type=int, default=200)
  logs.add_argument("-f", "--follow", action="store_true", help="Stream until the run ends.")
  logs.set_defaults(func=_cmd_logs)

  stop = sub.add_parser("stop", aliases=["cancel"], help="Stop a live run.")
  stop.add_argument("run")
  stop.set_defaults(func=_cmd_stop)

  remove = sub.add_parser("rm", help="Delete a run's record.")
  remove.add_argument("run")
  remove.add_argument("--force", action="store_true", help="Delete even if live.")
  remove.set_defaults(func=_cmd_remove)

  retry = sub.add_parser("retry", help="Re-run an existing run with the same inputs.")
  retry.add_argument("run")
  retry.add_argument("--detach", action="store_true")
  retry.set_defaults(func=_cmd_retry)

  worker = sub.add_parser("_worker")  # hidden: detached background executor
  worker.add_argument("run")
  worker.set_defaults(func=_cmd_worker)

  return parser


def main(argv: list[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  store = Store(args.home)
  try:
    return args.func(store, args)
  except AgentManagerError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
