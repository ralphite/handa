from __future__ import annotations

import asyncio
import json

from src.agents.ralph import runner as ralph
from src.agents.ralph.loader import MAIN_CONFIG_PATH
from src.agents.ralph.loop import DEFAULT_RALPH_MAX_ROUNDS
from src.agents.ralph.loop import RalphLoopResult
from src.agents.ralph.loop import VERIFIER_INDEPENDENCE_GUARD
from src.agents.ralph.runner import run
from src.runner import APP_NAME
from src.storage import HandaArtifactService
from src.storage import HandaSessionService


def _planner_plan(
    *,
    task_prompt: str,
    verification_prompt: str = "LLM acceptance: inspect real files and run focused verification.",
    builder_output_contract: str = "LLM contract: list real changes, commands, artifacts, and risk.",
    max_rounds: int = DEFAULT_RALPH_MAX_ROUNDS,
) -> dict[str, object]:
  return {
      "goal": task_prompt,
      "original_user_text": "Original user goal",
      "task_prompt": task_prompt,
      "verification_prompt": verification_prompt,
      "builder_output_contract": builder_output_contract,
      "builder_config_ref": {"source": "system", "name": "ralph_builder"},
      "verifier_config_ref": {"source": "system", "name": "ralph_verifier"},
      "max_rounds": max_rounds,
      "requires_user_confirmation": True,
  }


def test_native_ralph_plans_first_and_runs_after_confirmation(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    session_service = HandaSessionService()
    artifact_service = HandaArtifactService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-ralph-native",
        state={},
    )

    class FakePlanner:
      def __init__(self, **kwargs):
        self.kwargs = kwargs

      async def create_or_update_plan(self, **kwargs):
        planner_calls.append({"init": self.kwargs, "call": kwargs})
        return _planner_plan(
            task_prompt=(
                "Build a WYSIWYG diagram editor with nodes, dragging, links, "
                "deletion, auto layout, save/restore, and export."
            ),
            verification_prompt="LLM Verifier acceptance: verify the real UI and storage loop.",
        )

    planner_calls = []
    seen_loop = {}

    async def fake_run_confirmed_plan(**kwargs):
      seen_loop.update(kwargs)
      return RalphLoopResult(
          goal=str(kwargs["plan"]["goal"]),
          original_user_text=str(kwargs["plan"]["original_user_text"]),
          task_prompt=str(kwargs["plan"]["task_prompt"]),
          verification_prompt=str(kwargs["plan"]["verification_prompt"]),
          builder_output_contract=str(kwargs["plan"]["builder_output_contract"]),
          done=True,
          status="completed",
          rounds=[],
          final_text="Native Ralph loop completed.",
      )

    monkeypatch.setattr(ralph, "NativeRalphPlanPlanner", FakePlanner)
    monkeypatch.setattr(ralph, "_run_confirmed_plan", fake_run_confirmed_plan)
    events = []

    async def emit_event(event):
      events.append(event)

    planned = await run(
        prompt=(
            "Build a WYSIWYG diagram editor. first round only creates the "
            "Ralph plan, wait for confirmation, do not start Builder/Verifier, "
            "and do not modify project files."
        ),
        project_root=str(project),
        session_id="session-ralph-native",
        user_id="user",
        emit_event=emit_event,
        model_config_id="gemini-3.5-flash",
    )

    assert "Ralph Loop Plan" in planned.final_text
    assert "Please confirm" in planned.final_text
    assert seen_loop == {}
    assert planner_calls[0]["init"]["model_config_id"] == "gemini-3.5-flash"
    saved_plan = await artifact_service.load_artifact(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-ralph-native",
        filename="ralph_loop.plan.json",
    )
    assert saved_plan is not None and saved_plan.text is not None
    payload = json.loads(saved_plan.text)
    assert "WYSIWYG diagram editor" in payload["task_prompt"]
    assert "first round only" not in payload["task_prompt"]
    assert "wait for confirmation" not in payload["task_prompt"]
    assert "do not start Builder" not in payload["task_prompt"]
    assert "do not modify project" not in payload["task_prompt"]
    assert payload["max_rounds"] == DEFAULT_RALPH_MAX_ROUNDS
    assert VERIFIER_INDEPENDENCE_GUARD in payload["verification_prompt"]
    state = session_service.read_state_sync("session-ralph-native")
    assert state["ralph:plan_status"] == "pending_confirmation"
    assert state["ralph:pending_plan_artifact"] == "ralph_loop.plan.json"
    assert state["ralph:planner_session_id"]

    confirmed = await run(
        prompt="confirm",
        project_root=str(project),
        session_id="session-ralph-native",
        user_id="user",
        emit_event=emit_event,
    )

    assert "Ralph Loop Report" in confirmed.final_text
    assert seen_loop["session_id"] == "session-ralph-native"
    assert seen_loop["user_id"] == "user"
    assert seen_loop["plan"]["task_prompt"] == payload["task_prompt"]
    state_after_confirm = session_service.read_state_sync("session-ralph-native")
    assert state_after_confirm["ralph:plan_status"] == "confirmed"
    assert state_after_confirm["ralph:confirmed_plan_artifact"] == "ralph_loop.plan.json"
    assert [event["kind"] for event in events].count("ralph.started") == 2
    assert [event["kind"] for event in events].count("agent_text") == 2

  asyncio.run(run_test())


def test_native_ralph_updates_pending_plan_before_confirmation(tmp_path, monkeypatch):
  async def run_test():
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HANDA_STORAGE_ROOT", str(tmp_path / ".handa"))
    session_service = HandaSessionService()
    artifact_service = HandaArtifactService()
    await session_service.create_session(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-ralph-update",
        state={},
    )
    calls = []

    class FakePlanner:
      def __init__(self, **kwargs):
        pass

      async def create_or_update_plan(self, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
          return _planner_plan(task_prompt="Implement the base task.")
        return _planner_plan(
            task_prompt=str(kwargs["current_plan"]["task_prompt"]),
            verification_prompt="Updated acceptance: must run pytest.",
            max_rounds=2,
        )

    monkeypatch.setattr(ralph, "NativeRalphPlanPlanner", FakePlanner)

    async def emit_event(event):
      pass

    first = await run(
        prompt="Implement a small verifiable feature.",
        project_root=str(project),
        session_id="session-ralph-update",
        user_id="user",
        emit_event=emit_event,
    )
    second = await run(
        prompt="Change the acceptance method to require pytest and set max_rounds to 2.",
        project_root=str(project),
        session_id="session-ralph-update",
        user_id="user",
        emit_event=emit_event,
    )
    versions = await artifact_service.list_versions(
        app_name=APP_NAME,
        user_id="user",
        session_id="session-ralph-update",
        filename="ralph_loop.plan.json",
    )

    assert "Implement the base task." in first.final_text
    assert "must run pytest" in second.final_text
    assert calls[0]["current_plan"] is None
    assert calls[1]["current_plan"]["task_prompt"] == "Implement the base task."
    assert versions == [0, 1]

  asyncio.run(run_test())


def test_native_ralph_internal_configs_are_present():
  for filename in (
      "ralph_builder.agent.json",
      "ralph_planner.agent.json",
      "ralph_verifier.agent.json",
  ):
    native_config = json.loads(
        (MAIN_CONFIG_PATH.parent / filename).read_text(encoding="utf-8")
    )

    assert native_config["name"]
    assert native_config["model_config_id"]


def test_native_ralph_package_has_no_framework_dependency():
  for path in MAIN_CONFIG_PATH.parent.glob("*.py"):
    text = path.read_text(encoding="utf-8")
    assert "google.adk" not in text
    assert "langgraph" not in text
  assert "google.adk" not in MAIN_CONFIG_PATH.read_text(encoding="utf-8")
