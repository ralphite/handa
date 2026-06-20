from __future__ import annotations

import json
import time

import pytest

from src.agent_manager import AgentManagerError
from src.agent_manager import Store
from src.agent_manager import create_run
from src.agent_manager import execute_run
from src.agent_manager import main
from src.agent_manager import resolve_home
from src.agent_manager import start_run
from src.agent_manager import stop_run


@pytest.fixture()
def home(tmp_path, monkeypatch):
  root = tmp_path / "am"
  monkeypatch.setenv("HANDA_AGENT_MANAGER_HOME", str(root))
  return root


@pytest.fixture()
def store(home):
  return Store(home)


def _add_agent(store, name="echoer", command="printf '%s' {prompt}", **kwargs):
  from src.agent_manager import Agent
  from src.agent_manager import now_iso

  agent = Agent(name=name, command=command, created_at=now_iso(), updated_at=now_iso(), **kwargs)
  store.save_agent(agent)
  return agent


# --------------------------------------------------------------------------- #
# Storage / persistence
# --------------------------------------------------------------------------- #
def test_resolve_home_prefers_env(home):
  assert resolve_home() == home.resolve()


def test_resolve_home_explicit_overrides_env(tmp_path, home):
  explicit = tmp_path / "other"
  assert resolve_home(explicit) == explicit.resolve()


def test_agent_round_trips_through_disk(store):
  _add_agent(store, env={"FOO": "bar"}, description="hi", tags=["t1"])
  loaded = store.load_agent("echoer")
  assert loaded.command == "printf '%s' {prompt}"
  assert loaded.env == {"FOO": "bar"}
  assert loaded.description == "hi"
  assert loaded.tags == ["t1"]
  # The definition is a real file on disk.
  assert store.agent_file("echoer").exists()


def test_load_missing_agent_raises(store):
  with pytest.raises(AgentManagerError):
    store.load_agent("nope")


def test_list_agents_sorted_and_skips_garbage(store):
  _add_agent(store, name="bbb")
  _add_agent(store, name="aaa")
  (store.agents_dir / "broken.json").write_text("{not json", encoding="utf-8")
  names = [a.name for a in store.list_agents()]
  assert names == ["aaa", "bbb"]


def test_invalid_agent_name_rejected():
  from src.agent_manager import Agent

  with pytest.raises(Exception):
    Agent(name="bad name!", command="echo hi")


# --------------------------------------------------------------------------- #
# Run execution + state persistence
# --------------------------------------------------------------------------- #
def test_execute_success_persists_state_and_log(store):
  _add_agent(store, command="printf 'hello %s' {prompt}")
  run = create_run(store, "echoer", prompt="world")
  assert run.status == "queued"
  assert store.run_file(run.id).exists()

  done = execute_run(store, run.id)
  assert done.status == "succeeded"
  assert done.returncode == 0
  assert done.started_at and done.finished_at

  # State survives a fresh load (it is on disk, not just in memory).
  reloaded = Store(store.home).load_run(run.id)
  assert reloaded.status == "succeeded"
  assert "hello world" in store.run_log(run.id).read_text()


def test_execute_failure_records_returncode(store):
  _add_agent(store, command="exit 3")
  run = create_run(store, "echoer")
  done = execute_run(store, run.id)
  assert done.status == "failed"
  assert done.returncode == 3


def test_prompt_is_shell_quoted(store):
  # A prompt with shell metacharacters must reach the agent verbatim, not be
  # interpreted by the shell.
  _add_agent(store, command="printf '%s' {prompt}")
  payload = "a; rm -rf $HOME && echo pwned"
  run = create_run(store, "echoer", prompt=payload)
  execute_run(store, run.id)
  assert store.run_log(run.id).read_text() == payload


def test_run_env_exposed_to_command(store):
  _add_agent(store, command='printf "%s|%s" "$AGENT_PROMPT" "$AGENT_NAME"')
  run = create_run(store, "echoer", prompt="hey")
  execute_run(store, run.id)
  assert store.run_log(run.id).read_text() == "hey|echoer"


def test_extra_run_env_merged(store):
  _add_agent(store, command='printf "%s" "$EXTRA"')
  run = create_run(store, "echoer", env={"EXTRA": "xyz"})
  execute_run(store, run.id)
  assert store.run_log(run.id).read_text() == "xyz"


def test_cannot_reexecute_terminal_run(store):
  _add_agent(store, command="true")
  run = create_run(store, "echoer")
  execute_run(store, run.id)
  with pytest.raises(AgentManagerError):
    execute_run(store, run.id)


def test_resolve_run_id_prefix(store):
  _add_agent(store, command="true")
  run = create_run(store, "echoer")
  assert store.resolve_run_id(run.id[:12]) == run.id


# --------------------------------------------------------------------------- #
# Listing / filtering
# --------------------------------------------------------------------------- #
def test_list_runs_newest_first(store):
  _add_agent(store, command="true")
  first = create_run(store, "echoer")
  time.sleep(0.01)
  second = create_run(store, "echoer")
  ids = [r.id for r in store.list_runs()]
  assert ids[0] == second.id and ids[1] == first.id


# --------------------------------------------------------------------------- #
# Stop / cancel
# --------------------------------------------------------------------------- #
def test_stop_queued_run_marks_cancelled(store):
  _add_agent(store, command="true")
  run = create_run(store, "echoer")
  stopped = stop_run(store, run.id)
  assert stopped.status == "cancelled"
  assert stopped.cancel_requested_at


def test_stop_detached_run(store):
  _add_agent(store, command="sleep 30")
  run = create_run(store, "echoer")
  started = start_run(store, run.id, detach=True)
  assert started.detached and started.pid
  # Give the worker a moment to start the child.
  deadline = time.time() + 5
  while time.time() < deadline and store.load_run(run.id).status != "running":
    time.sleep(0.1)
  stop_run(store, run.id)
  deadline = time.time() + 5
  while time.time() < deadline and store.load_run(run.id).status not in {
      "cancelled",
      "failed",
  }:
    time.sleep(0.1)
  assert store.load_run(run.id).status in {"cancelled", "failed"}


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #
def test_cli_add_run_and_inspect(home, capsys):
  assert main(["agent", "add", "greet", "--command", "printf 'hi %s' {prompt}"]) == 0
  assert main(["run", "greet", "--prompt", "ada"]) == 0
  out = capsys.readouterr().out
  assert "hi ada" in out

  assert main(["--json", "runs", "--all"]) == 0
  rows = json.loads(capsys.readouterr().out)
  assert len(rows) == 1
  assert rows[0]["agent"] == "greet"
  assert rows[0]["status"] == "succeeded"


def test_cli_run_failure_returns_nonzero(home):
  main(["agent", "add", "boom", "--command", "exit 7"])
  assert main(["run", "boom", "--prompt", ""]) == 7


def test_cli_agent_add_no_overwrite_without_force(home):
  main(["agent", "add", "a", "--command", "true"])
  assert main(["agent", "add", "a", "--command", "false"]) == 1
  assert main(["agent", "add", "a", "--command", "false", "--force"]) == 0


def test_cli_unknown_agent_error(home, capsys):
  assert main(["run", "ghost", "--prompt", ""]) == 1
  assert "ghost" in capsys.readouterr().err
