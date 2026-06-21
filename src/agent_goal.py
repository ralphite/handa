"""Goal-driven runs layered on the agent manager.

A *goal* is a higher-level objective ("make the tests pass", "draft the
README") that one shot of an agent rarely satisfies. This module turns a goal
into a loop on top of the agent manager's `Run` primitive:

    1. wrap the goal in Handa's standard goal instructions (contract/goals.py),
    2. execute one agent run (a normal, fully-tracked Run),
    3. judge the result — achieved / continue / blocked,
    4. if "continue", feed the judge's feedback (plus the prior output, since
       each run is a stateless subprocess) into the next attempt,
    5. stop on achieved/blocked or when max_attempts is exhausted.

Every attempt is an ordinary run, so `runs`, `logs`, `show`, and `stop` all
work on it unchanged; a goal just links the attempts together and records the
verdict trail. Goal state persists as local JSON next to runs:

    goals/<goal_id>/goal.json

The judge is pluggable, which is what keeps this flexible and dependency-free:

    marker   deterministic — achieved when the run succeeds and its output
             contains a success marker (default "GOAL_ACHIEVED"). No API key.
    command  run any command/agent as the judge; it prints a verdict JSON
             {"status", "reason", "next_request"}. Handa's LLM goal judge can
             be wired in this way, but so can a linter, a test runner, etc.

Slash entry point: a prompt of the form `/goal [--max N] <text>` routes a
normal `run` into this loop, mirroring the web composer's `/goal` command.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Callable

from pydantic import BaseModel
from pydantic import Field

from .contract.goals import GOAL_STATUS_ACHIEVED
from .contract.goals import GOAL_STATUS_BLOCKED
from .contract.goals import GOAL_STATUS_CANCELLED
from .contract.goals import GOAL_STATUS_MAX_ATTEMPTS
from .contract.goals import apply_goal_to_prompt
from . import agent_manager as am


GOAL_SLASH_NAMES = frozenset({"goal", "goals", "objective"})
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_MARKER = "GOAL_ACHIEVED"
# How much of an attempt's output to carry into the next attempt's prompt and
# into the command judge. Each run is a stateless subprocess, so this tail is
# the only "memory" the loop has.
ATTEMPT_OUTPUT_TAIL = 400


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class GoalAttempt(BaseModel):
  attempt: int
  run_id: str
  verdict: str = ""
  reason: str = ""
  next_request: str = ""


class Goal(BaseModel):
  id: str
  agent: str
  text: str
  status: str = "active"
  max_attempts: int = DEFAULT_MAX_ATTEMPTS
  judge: dict[str, Any] = Field(default_factory=dict)
  attempts: list[GoalAttempt] = Field(default_factory=list)
  reason: str | None = None
  created_at: str = ""
  updated_at: str = ""


@dataclass
class Verdict:
  """A judge's call on one attempt. Mirrors Handa's goal-judge vocabulary."""

  status: str  # "achieved" | "continue" | "blocked"
  reason: str = ""
  next_request: str = ""


Judge = Callable[[Goal, "am.Run", str, am.Store], Verdict]


# --------------------------------------------------------------------------- #
# Goal store helpers (parallel to agent_manager.Store run helpers)
# --------------------------------------------------------------------------- #
def _goals_dir(store: am.Store) -> Path:
  return store.home / "goals"


def _goal_dir(store: am.Store, goal_id: str) -> Path:
  return _goals_dir(store) / am._safe_name(goal_id)


def _goal_file(store: am.Store, goal_id: str) -> Path:
  return _goal_dir(store, goal_id) / "goal.json"


def save_goal(store: am.Store, goal: Goal) -> None:
  am.atomic_write_text(_goal_file(store, goal.id), goal.model_dump_json(indent=2))


def load_goal(store: am.Store, goal_id: str) -> Goal:
  path = _goal_file(store, goal_id)
  if not path.exists():
    raise am.AgentManagerError(f"No goal with id {goal_id!r}.")
  return Goal.model_validate_json(path.read_text(encoding="utf-8"))


def list_goals(store: am.Store) -> list[Goal]:
  directory = _goals_dir(store)
  if not directory.exists():
    return []
  goals: list[Goal] = []
  for path in directory.glob("*/goal.json"):
    try:
      goals.append(Goal.model_validate_json(path.read_text(encoding="utf-8")))
    except Exception:
      continue
  return sorted(goals, key=lambda g: g.created_at, reverse=True)


def resolve_goal_id(store: am.Store, ref: str) -> str:
  if _goal_file(store, ref).exists():
    return ref
  matches = [g.id for g in list_goals(store) if g.id.startswith(ref)]
  if len(matches) == 1:
    return matches[0]
  if not matches:
    raise am.AgentManagerError(f"No goal matching {ref!r}.")
  raise am.AgentManagerError(f"{ref!r} is ambiguous; matches {len(matches)} goals.")


def _new_goal_id() -> str:
  import uuid

  return f"goal_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Judges
# --------------------------------------------------------------------------- #
def marker_judge(marker: str = DEFAULT_MARKER, *, require_success: bool = True) -> Judge:
  """Deterministic judge: achieved when the run succeeded and printed `marker`.

  No model, no API key — useful for goals whose agent can emit a clear "done"
  signal, and a sane default for the prototype.
  """

  def judge(goal: Goal, run: "am.Run", output: str, store: am.Store) -> Verdict:
    if require_success and run.status != "succeeded":
      return Verdict(
          "continue",
          f"Attempt exited with status {run.status} (rc={run.returncode}).",
          "The previous attempt failed. Fix the failure, then continue.",
      )
    if marker in output:
      return Verdict("achieved", f"Output contained the success marker {marker!r}.")
    return Verdict(
        "continue",
        f"Success marker {marker!r} not found in the output.",
        f"Keep working until the goal is truly met, then print {marker!r} on its own line.",
    )

  return judge


def command_judge(command_template: str) -> Judge:
  """Run an arbitrary command/agent as the judge.

  The command receives the goal and the attempt via env vars and must print a
  verdict JSON object on stdout: {"status", "reason", "next_request"} where
  status is one of achieved/continue/blocked. This is how a real LLM judge (or
  a test runner, linter, ...) plugs in without this module depending on it.
  """

  def judge(goal: Goal, run: "am.Run", output: str, store: am.Store) -> Verdict:
    env = os.environ.copy()
    env["AGENT_GOAL"] = goal.text
    env["AGENT_GOAL_ID"] = goal.id
    env["AGENT_ATTEMPT"] = str(run.attempt or 0)
    env["AGENT_ATTEMPT_RC"] = "" if run.returncode is None else str(run.returncode)
    env["AGENT_ATTEMPT_STATUS"] = run.status
    env["AGENT_ATTEMPT_OUTPUT"] = output
    env["AGENT_RUN_DIR"] = str(store.run_dir(run.id))
    command = command_template.replace("{goal}", shlex.quote(goal.text)).replace(
        "{run_dir}", str(store.run_dir(run.id))
    )
    try:
      completed = subprocess.run(
          command,
          shell=True,
          cwd=str(store.home),
          env=env,
          capture_output=True,
          text=True,
          timeout=300,
      )
    except subprocess.SubprocessError as exc:
      return Verdict("continue", f"Judge command failed to run: {exc}", "Retry the goal.")
    return _parse_verdict(completed.stdout)

  return judge


def _parse_verdict(text: str) -> Verdict:
  """Tolerant verdict parser (mirrors goal_judge.parse_goal_judge_response)."""
  import re

  raw: Any = None
  try:
    raw = json.loads(text)
  except json.JSONDecodeError:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
      try:
        raw = json.loads(match.group(0))
      except json.JSONDecodeError:
        raw = None
  if not isinstance(raw, dict):
    return Verdict(
        "continue",
        "Judge did not return a JSON verdict.",
        "Continue working on the same goal and provide clearer proof.",
    )
  status = str(raw.get("status") or "").strip().lower()
  if status not in {"achieved", "continue", "blocked"}:
    status = "continue"
  return Verdict(
      status=status,
      reason=str(raw.get("reason") or "").strip() or "(no reason given)",
      next_request=str(raw.get("next_request") or "").strip(),
  )


def build_judge(spec: dict[str, Any]) -> Judge:
  kind = str(spec.get("kind") or "marker")
  if kind == "marker":
    return marker_judge(
        str(spec.get("marker") or DEFAULT_MARKER),
        require_success=bool(spec.get("require_success", True)),
    )
  if kind == "command":
    command = str(spec.get("command") or "").strip()
    if not command:
      raise am.AgentManagerError("command judge requires a non-empty 'command'.")
    return command_judge(command)
  raise am.AgentManagerError(f"Unknown judge kind: {kind!r}.")


# --------------------------------------------------------------------------- #
# The goal loop
# --------------------------------------------------------------------------- #
def _attempt_prompt(goal: Goal, attempt: int, next_request: str, last_output: str) -> str:
  """Build one attempt's prompt: goal instructions + the iteration context.

  Attempt 1 just opens the goal. Later attempts must re-establish context the
  stateless subprocess has lost, so they carry the judge's feedback and a tail
  of the previous attempt's output.
  """
  if attempt == 1:
    user_message = "Begin working on the goal."
  else:
    user_message = (
        f"This is attempt {attempt}. A reviewer judged the previous attempt "
        "incomplete.\n\n"
        f"Reviewer feedback:\n{next_request or '(none)'}\n\n"
        f"Previous attempt output (for context):\n{last_output or '(empty)'}"
    )
  return apply_goal_to_prompt(user_message, {"text": goal.text})


def run_goal(
    store: am.Store,
    agent_name: str,
    text: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    judge: Judge | None = None,
    judge_spec: dict[str, Any] | None = None,
    on_attempt: Callable[[Goal, "am.Run", Verdict], None] | None = None,
) -> Goal:
  """Drive an agent toward a goal, attempt by attempt, until judged done."""
  store.load_agent(agent_name)  # fail fast on an unknown agent
  normalized = text.strip()
  if not normalized:
    raise am.AgentManagerError("Goal text must not be empty.")
  max_attempts = max(1, int(max_attempts))
  spec = judge_spec or {"kind": "marker", "marker": DEFAULT_MARKER}
  judge = judge or build_judge(spec)

  goal = Goal(
      id=_new_goal_id(),
      agent=agent_name,
      text=normalized,
      status="active",
      max_attempts=max_attempts,
      judge=spec,
      created_at=am.now_iso(),
      updated_at=am.now_iso(),
  )
  save_goal(store, goal)

  last_output = ""
  next_request = ""
  for attempt in range(1, max_attempts + 1):
    run = am.create_run(
        store,
        agent_name,
        prompt=_attempt_prompt(goal, attempt, next_request, last_output),
        label=f"goal {goal.id} attempt {attempt}/{max_attempts}",
    )
    am.Store(store.home).update_run(run.id, goal_id=goal.id, attempt=attempt)
    run = am.execute_run(store, run.id)
    last_output = am.read_log_tail(store, run.id, ATTEMPT_OUTPUT_TAIL)
    verdict = judge(goal, run, last_output, store)

    goal.attempts.append(
        GoalAttempt(
            attempt=attempt,
            run_id=run.id,
            verdict=verdict.status,
            reason=verdict.reason,
            next_request=verdict.next_request,
        )
    )
    goal.updated_at = am.now_iso()
    save_goal(store, goal)
    if on_attempt is not None:
      on_attempt(goal, run, verdict)

    if verdict.status == "achieved":
      return _finalize(store, goal, GOAL_STATUS_ACHIEVED, verdict.reason)
    if verdict.status == "blocked":
      return _finalize(store, goal, GOAL_STATUS_BLOCKED, verdict.reason)
    next_request = verdict.next_request

  return _finalize(
      store,
      goal,
      GOAL_STATUS_MAX_ATTEMPTS,
      f"Goal not achieved after {max_attempts} attempts.",
  )


def _finalize(store: am.Store, goal: Goal, status: str, reason: str) -> Goal:
  goal.status = status
  goal.reason = reason
  goal.updated_at = am.now_iso()
  save_goal(store, goal)
  return goal


def cancel_goal(store: am.Store, goal_id: str) -> Goal:
  goal = load_goal(store, goal_id)
  if goal.status != "active":
    return goal
  return _finalize(store, goal, GOAL_STATUS_CANCELLED, "Cancelled by user.")


# --------------------------------------------------------------------------- #
# Slash parsing — `/goal [--max N] <text>`
# --------------------------------------------------------------------------- #
@dataclass
class GoalSlash:
  text: str
  max_attempts: int = DEFAULT_MAX_ATTEMPTS
  extra: dict[str, str] = field(default_factory=dict)


def parse_slash(prompt: str) -> tuple[str, str] | None:
  """Return (command_name, remainder) if `prompt` opens with a slash command."""
  stripped = prompt.strip()
  if not stripped.startswith("/"):
    return None
  head, _, rest = stripped[1:].partition(" ")
  name = head.strip().lower()
  if not name:
    return None
  return name, rest.strip()


def parse_goal_slash(remainder: str) -> GoalSlash:
  """Parse the text after `/goal`: optional `--max N`, then free-form goal text."""
  tokens = remainder.split()
  max_attempts = DEFAULT_MAX_ATTEMPTS
  index = 0
  while index < len(tokens) and tokens[index].startswith("--"):
    flag = tokens[index]
    if flag in {"--max", "--max-attempts"} and index + 1 < len(tokens):
      try:
        max_attempts = max(1, int(tokens[index + 1]))
      except ValueError:
        break
      index += 2
      continue
    break
  text = " ".join(tokens[index:]).strip()
  return GoalSlash(text=text, max_attempts=max_attempts)


def maybe_handle_goal_slash(store: am.Store, agent: str, prompt: str, *, as_json: bool) -> int | None:
  """If `prompt` is a `/goal …` slash, run the goal loop and return an exit code.

  Returns None when the prompt is not a goal slash, so the caller falls through
  to a normal one-shot run.
  """
  parsed = parse_slash(prompt)
  if parsed is None or parsed[0] not in GOAL_SLASH_NAMES:
    return None
  slash = parse_goal_slash(parsed[1])
  if not slash.text:
    raise am.AgentManagerError("`/goal` needs goal text, e.g. `/goal make the tests pass`.")
  goal = run_goal(
      store,
      agent,
      slash.text,
      max_attempts=slash.max_attempts,
      on_attempt=None if as_json else _print_attempt,
  )
  _emit_goal(goal, as_json=as_json)
  return 0 if goal.status == GOAL_STATUS_ACHIEVED else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_attempt(goal: Goal, run: "am.Run", verdict: Verdict) -> None:
  import sys

  print(
      f"[attempt {run.attempt}/{goal.max_attempts}] {run.id} -> {verdict.status}: {verdict.reason}",
      file=sys.stderr,
  )


def _goal_text(goal: Goal) -> str:
  lines = [
      f"goal:    {goal.id}",
      f"agent:   {goal.agent}",
      f"status:  {goal.status}",
      f"text:    {goal.text}",
      f"judge:   {goal.judge.get('kind', 'marker')}",
      f"attempts: {len(goal.attempts)}/{goal.max_attempts}",
  ]
  if goal.reason:
    lines.append(f"reason:  {goal.reason}")
  for attempt in goal.attempts:
    lines.append(f"  #{attempt.attempt} {attempt.run_id} -> {attempt.verdict}: {attempt.reason}")
  return "\n".join(lines)


def _emit_goal(goal: Goal, *, as_json: bool) -> None:
  if as_json:
    print(json.dumps(goal.model_dump(), indent=2, default=str))
  else:
    print(_goal_text(goal))


def _cmd_goal_run(store: am.Store, args: argparse.Namespace) -> int:
  text = args.text
  if not text and not __import__("sys").stdin.isatty():
    text = __import__("sys").stdin.read()
  if not (text or "").strip():
    raise am.AgentManagerError("Provide goal text as an argument or via stdin.")
  spec: dict[str, Any] = {"kind": args.judge}
  if args.judge == "marker":
    spec["marker"] = args.marker
  elif args.judge == "command":
    if not args.judge_command:
      raise am.AgentManagerError("--judge command requires --judge-command.")
    spec["command"] = args.judge_command
  goal = run_goal(
      store,
      args.agent,
      text,
      max_attempts=args.max,
      judge_spec=spec,
      on_attempt=None if args.json else _print_attempt,
  )
  _emit_goal(goal, as_json=args.json)
  return 0 if goal.status == GOAL_STATUS_ACHIEVED else 1


def _cmd_goal_list(store: am.Store, args: argparse.Namespace) -> int:
  goals = list_goals(store)
  if args.json:
    print(json.dumps([g.model_dump() for g in goals], indent=2, default=str))
    return 0
  if not goals:
    print("No goals.")
    return 0
  for goal in goals:
    print(
        f"{goal.id}  {goal.status:<12}  {goal.agent:<16}  "
        f"{len(goal.attempts)}/{goal.max_attempts}  {goal.text[:60]}"
    )
  return 0


def _cmd_goal_show(store: am.Store, args: argparse.Namespace) -> int:
  goal = load_goal(store, resolve_goal_id(store, args.goal))
  _emit_goal(goal, as_json=args.json)
  return 0


def _cmd_goal_cancel(store: am.Store, args: argparse.Namespace) -> int:
  goal = cancel_goal(store, resolve_goal_id(store, args.goal))
  _emit_goal(goal, as_json=args.json)
  return 0


def register(sub: argparse._SubParsersAction) -> None:
  """Attach the `goal` command tree to the agent-manager parser."""
  goal = sub.add_parser("goal", help="Run and inspect goal-driven loops.")
  goal_sub = goal.add_subparsers(dest="goal_command", required=True)

  run = goal_sub.add_parser("run", help="Drive an agent toward a goal until achieved.")
  run.add_argument("agent")
  run.add_argument("text", nargs="?", help="Goal text (or piped via stdin).")
  run.add_argument("--max", type=int, default=DEFAULT_MAX_ATTEMPTS, help="Max attempts.")
  run.add_argument(
      "--judge", choices=["marker", "command"], default="marker", help="Judge strategy."
  )
  run.add_argument("--marker", default=DEFAULT_MARKER, help="Marker judge success token.")
  run.add_argument("--judge-command", help="Command judge: prints a verdict JSON.")
  run.set_defaults(func=_cmd_goal_run)

  listing = goal_sub.add_parser("list", aliases=["ls"], help="List goals.")
  listing.set_defaults(func=_cmd_goal_list)

  show = goal_sub.add_parser("show", help="Show one goal and its attempts.")
  show.add_argument("goal")
  show.set_defaults(func=_cmd_goal_show)

  cancel = goal_sub.add_parser("cancel", help="Mark an active goal cancelled.")
  cancel.add_argument("goal")
  cancel.set_defaults(func=_cmd_goal_cancel)
