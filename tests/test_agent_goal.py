from __future__ import annotations

import json

import pytest

from src import agent_goal
from src.agent_goal import Verdict
from src.agent_goal import load_goal
from src.agent_goal import parse_goal_slash
from src.agent_goal import parse_slash
from src.agent_goal import run_goal
from src.agent_manager import AgentManagerError
from src.agent_manager import Store
from src.agent_manager import main
from src.contract.goals import GOAL_STATUS_ACHIEVED
from src.contract.goals import GOAL_STATUS_BLOCKED
from src.contract.goals import GOAL_STATUS_MAX_ATTEMPTS


@pytest.fixture()
def home(tmp_path, monkeypatch):
  root = tmp_path / "am"
  monkeypatch.setenv("HANDA_AGENT_MANAGER_HOME", str(root))
  return root


@pytest.fixture()
def store(home):
  return Store(home)


def _add_agent(store, name, command):
  from src.agent_manager import Agent
  from src.agent_manager import now_iso

  store.save_agent(Agent(name=name, command=command, created_at=now_iso(), updated_at=now_iso()))


# --------------------------------------------------------------------------- #
# Slash parsing
# --------------------------------------------------------------------------- #
def test_parse_slash_detects_goal():
  assert parse_slash("/goal make tests pass") == ("goal", "make tests pass")
  assert parse_slash("no slash here") is None
  assert parse_slash("/model gpt") == ("model", "gpt")


def test_parse_goal_slash_reads_max():
  parsed = parse_goal_slash("--max 3 ship the feature")
  assert parsed.max_attempts == 3
  assert parsed.text == "ship the feature"


def test_parse_goal_slash_default_max():
  parsed = parse_goal_slash("just do it")
  assert parsed.max_attempts == agent_goal.DEFAULT_MAX_ATTEMPTS
  assert parsed.text == "just do it"


# --------------------------------------------------------------------------- #
# Marker judge loop
# --------------------------------------------------------------------------- #
def test_goal_achieved_when_marker_printed(store):
  # Agent always prints the success marker -> achieved on attempt 1.
  _add_agent(store, "winner", "echo GOAL_ACHIEVED")
  goal = run_goal(store, "winner", "do the thing", max_attempts=3)
  assert goal.status == GOAL_STATUS_ACHIEVED
  assert len(goal.attempts) == 1
  assert goal.attempts[0].verdict == "achieved"
  # The attempt is a real, linked run.
  run = store.load_run(goal.attempts[0].run_id)
  assert run.goal_id == goal.id and run.attempt == 1


def test_goal_exhausts_attempts_without_marker(store):
  _add_agent(store, "noop", "echo working")
  goal = run_goal(store, "noop", "never done", max_attempts=3)
  assert goal.status == GOAL_STATUS_MAX_ATTEMPTS
  assert len(goal.attempts) == 3
  assert all(a.verdict == "continue" for a in goal.attempts)


def test_failed_attempt_is_continue(store):
  _add_agent(store, "boom", "exit 1")
  goal = run_goal(store, "boom", "x", max_attempts=2)
  assert goal.status == GOAL_STATUS_MAX_ATTEMPTS
  assert goal.attempts[0].verdict == "continue"


def test_goal_persists_to_disk(store):
  _add_agent(store, "winner", "echo GOAL_ACHIEVED")
  goal = run_goal(store, "winner", "persist me", max_attempts=1)
  reloaded = load_goal(Store(store.home), goal.id)
  assert reloaded.status == GOAL_STATUS_ACHIEVED
  assert reloaded.text == "persist me"


def test_unknown_agent_rejected(store):
  with pytest.raises(AgentManagerError):
    run_goal(store, "ghost", "x")


# --------------------------------------------------------------------------- #
# Attempt prompt carries goal + feedback
# --------------------------------------------------------------------------- #
def test_attempt_prompt_includes_goal_instructions(store):
  _add_agent(store, "noop", "echo working")
  goal = run_goal(store, "noop", "fix the bug", max_attempts=2)
  # The second attempt's run prompt should carry the goal framing and the
  # reviewer feedback from attempt 1.
  second = store.load_run(goal.attempts[1].run_id)
  assert "# Goal" in second.prompt
  assert "fix the bug" in second.prompt
  assert "attempt 2" in second.prompt.lower()


# --------------------------------------------------------------------------- #
# Pluggable command judge
# --------------------------------------------------------------------------- #
def test_command_judge_drives_verdict(store):
  _add_agent(store, "noop", "echo hello")
  # The judge agent says achieved iff the attempt output mentions "hello".
  judge_cmd = (
      'python -c "import os,json;'
      "ok='hello' in os.environ['AGENT_ATTEMPT_OUTPUT'];"
      'print(json.dumps({\'status\': \'achieved\' if ok else \'continue\', '
      "'reason': 'checked output', 'next_request': 'try again'}))\""
  )
  goal = run_goal(
      store,
      "noop",
      "say hello",
      max_attempts=2,
      judge_spec={"kind": "command", "command": judge_cmd},
  )
  assert goal.status == GOAL_STATUS_ACHIEVED
  assert goal.attempts[0].verdict == "achieved"


def test_command_judge_can_block(store):
  _add_agent(store, "noop", "echo hi")
  judge_cmd = (
      "python -c \"import json;print(json.dumps({'status':'blocked',"
      "'reason':'cannot proceed'}))\""
  )
  goal = run_goal(
      store, "noop", "x", max_attempts=5, judge_spec={"kind": "command", "command": judge_cmd}
  )
  assert goal.status == GOAL_STATUS_BLOCKED
  assert len(goal.attempts) == 1


def test_verdict_parser_tolerant():
  v = agent_goal._parse_verdict("noise before {\"status\": \"achieved\", \"reason\": \"ok\"} after")
  assert v.status == "achieved"
  bad = agent_goal._parse_verdict("not json at all")
  assert bad.status == "continue"


# --------------------------------------------------------------------------- #
# CLI: slash routing + goal subcommands
# --------------------------------------------------------------------------- #
def test_cli_slash_goal_routes_into_loop(home, capsys):
  main(["agent", "add", "winner", "--command", "echo GOAL_ACHIEVED"])
  capsys.readouterr()
  rc = main(["--json", "run", "winner", "--prompt", "/goal --max 2 do it"])
  out = json.loads(capsys.readouterr().out)
  assert rc == 0
  assert out["status"] == GOAL_STATUS_ACHIEVED
  assert out["max_attempts"] == 2


def test_cli_goal_run_and_list(home, capsys):
  main(["agent", "add", "noop", "--command", "echo working"])
  rc = main(["goal", "run", "noop", "unreachable", "--max", "2"])
  assert rc == 1  # not achieved
  capsys.readouterr()
  assert main(["--json", "goal", "list"]) == 0
  goals = json.loads(capsys.readouterr().out)
  assert len(goals) == 1
  assert goals[0]["status"] == GOAL_STATUS_MAX_ATTEMPTS


def test_cli_goal_cancel_noop_after_finish(home, capsys):
  main(["agent", "add", "winner", "--command", "echo GOAL_ACHIEVED"])
  main(["goal", "run", "winner", "done quick", "--max", "1"])
  capsys.readouterr()
  main(["--json", "goal", "list"])
  goal_id = json.loads(capsys.readouterr().out)[0]["id"]
  # Cancelling an already-achieved goal leaves it achieved.
  assert main(["goal", "cancel", goal_id]) == 0
  out = capsys.readouterr().out
  assert "achieved" in out
