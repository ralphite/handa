from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from google.genai import types

import src.agents.handa_adk.ralph.agent as ralph_agent_module
from src.agents.handa_adk.loader import load_agent
from src.agents.handa_adk.ralph import build_agent
from src.agents.handa_adk.ralph import RalphAgent
from src.agents.handa_adk.ralph.agent import LlmRalphPlanPlanner
from src.agents.handa_adk.ralph.loop import AgentVerifierNode
from src.agents.handa_adk.ralph.loop import AgentConfigNode
from src.agents.handa_adk.ralph.loop import DEFAULT_RALPH_MAX_ROUNDS
from src.agents.handa_adk.ralph.loop import VerificationResult
from src.agents.handa_adk.ralph.loop import NodeResult
from src.agents.handa_adk.ralph.loop import RalphLoopResult
from src.agents.handa_adk.ralph.loop import RalphLoopRunner
from src.agents.handa_adk.ralph.loop import VERIFIER_INDEPENDENCE_GUARD
from src.agents.handa_adk.ralph.loop import parse_verification_result
from agent_test_helpers import run_agent_once
from src.runner import APP_NAME
from src.runner import DEFAULT_USER_ID
from src.storage import HandaArtifactService


class TestBuilderNode:
  async def run(self, context, prompt):
    if context.round_number == 1:
      text = "Round 1 result is still missing the required marker."
    else:
      text = "Verifier feedback has been applied; RALPH_DONE marker added."
    return NodeResult(text=text, metadata={"prompt": prompt})


class TestVerifierNode:
  async def run(self, context, prompt, builder_result):
    done = "RALPH_DONE" in builder_result.text
    return VerificationResult(
        done=done,
        reason=(
            "Builder output contains the RALPH_DONE marker."
            if done
            else "The required marker is missing."
        ),
        feedback="" if done else "Add the RALPH_DONE marker in the next round.",
        metadata={"prompt": prompt},
    )


class FakePlanner:
  def __init__(self, plans):
    self.plans = plans
    self.calls = []

  async def create_or_update_plan(self, **kwargs):
    self.calls.append(kwargs)
    index = min(len(self.calls) - 1, len(self.plans) - 1)
    plan = self.plans[index]
    if callable(plan):
      return plan(kwargs)
    return plan


def _planner_plan(
    *,
    task_prompt: str,
    verification_prompt: str = "LLM acceptance: read real files and run the minimal necessary verification.",
    builder_output_contract: str = "LLM contract: list real changes, real verification, artifacts, and remaining risk.",
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


def test_ralph_loop_saves_parent_artifacts_with_test_nodes(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  artifact_service = HandaArtifactService(root=str(storage_root))
  runner = RalphLoopRunner(
      builder_node=TestBuilderNode(),
      verifier_node=TestVerifierNode(),
      artifact_service=artifact_service,
  )

  result = asyncio.run(
      runner.run(
          goal="Produce the accepted test result.",
          parent_session_id="session-1",
      )
  )
  artifacts = asyncio.run(
      artifact_service.list_artifact_keys(
          app_name=APP_NAME,
          user_id=DEFAULT_USER_ID,
          session_id="session-1",
      )
  )

  assert result.done is True
  assert result.status == "completed"
  assert len(result.rounds) == 2
  assert runner.max_rounds == DEFAULT_RALPH_MAX_ROUNDS
  first_verifier_prompt = result.rounds[0].verification.metadata["prompt"]
  assert "Original user request" in first_verifier_prompt
  assert "User-confirmed Ralph plan" in first_verifier_prompt
  assert VERIFIER_INDEPENDENCE_GUARD in first_verifier_prompt
  assert any(name.endswith(".result.json") for name in artifacts)
  assert any(name.endswith(".report.md") for name in artifacts)


def test_ralph_agent_is_custom_parent_agent():
  agent = load_agent("ralph")

  assert agent.name == "ralph_agent"
  assert not hasattr(agent, "model")
  assert agent.builder_config_name == "ralph_builder"
  assert agent.verifier_config_name == "ralph_verifier"
  assert agent.max_rounds == DEFAULT_RALPH_MAX_ROUNDS


def test_ralph_defaults_to_ten_rounds_and_guards_verifier_prompt(tmp_path):
  service = HandaArtifactService(root=str(tmp_path / ".handa"))
  runner = RalphLoopRunner(
      builder_node=TestBuilderNode(),
      verifier_node=TestVerifierNode(),
      artifact_service=service,
  )
  plan = ralph_agent_module._normalize_plan_payload(
      {
          "goal": "Implement the user-confirmed feature.",
          "task_prompt": "Implement the user-confirmed feature in the real project.",
          "verification_prompt": "Run real verification.",
          "builder_output_contract": "List changes and verification.",
          "max_rounds": 3,
      },
      user_text="Original user request.",
      current_plan=None,
      max_rounds=DEFAULT_RALPH_MAX_ROUNDS,
  )

  assert runner.max_rounds == DEFAULT_RALPH_MAX_ROUNDS
  assert plan["max_rounds"] == DEFAULT_RALPH_MAX_ROUNDS
  assert VERIFIER_INDEPENDENCE_GUARD in plan["verification_prompt"]


def test_ralph_has_no_dedicated_cli_entrypoint():
  assert not (Path("src") / "agents" / "ralph" / "cli.py").exists()


def test_ralph_agent_plans_first_and_runs_after_confirmation(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  planner = FakePlanner(
      [
          _planner_plan(
              task_prompt=(
                  "LLM Builder task: build a WYSIWYG diagram editor with nodes, "
                  "dragging, linking, deletion, auto layout, save/restore, and export."
              ),
              verification_prompt=(
                  "LLM Verifier acceptance: verify the core diagram editor loop "
                  "from real UI and storage state."
              ),
              builder_output_contract=(
                  "LLM Builder contract: state real changed files, verification "
                  "commands run, saved artifacts, and remaining risk."
              ),
          )
      ]
  )
  agent = RalphAgent(name="ralph_agent", planner=planner)
  seen = {}

  async def fake_run(self, *, goal, parent_session_id, **kwargs):
    seen["goal"] = goal
    seen["original_user_text"] = str(kwargs["original_user_text"])
    seen["task_prompt"] = str(kwargs["task_prompt"])
    seen["verification_prompt"] = str(kwargs["verification_prompt"])
    seen["builder_output_contract"] = str(kwargs["builder_output_contract"])
    seen["builder_config"] = self.builder_node.config_name
    seen["verifier_config"] = self.verifier_node.agent_node.config_name
    seen["parent_session_id"] = parent_session_id
    return RalphLoopResult(
        goal=goal,
        task_prompt=str(kwargs["task_prompt"]),
        verification_prompt=str(kwargs["verification_prompt"]),
        builder_output_contract=str(kwargs["builder_output_contract"]),
        done=True,
        status="completed",
        rounds=[],
        final_text="Test loop completed.",
    )

  monkeypatch.setattr(RalphLoopRunner, "run", fake_run)

  result = asyncio.run(
      run_agent_once(
          project=project,
          prompt=(
              "Build a WYSIWYG diagram editor in the project. "
              "Product requirements: create nodes, edit nodes, drag nodes, link nodes, "
              "delete nodes, auto layout, save/restore after refresh, and export. "
              "Note: first round only creates the Ralph plan, wait for confirmation, "
              "do not start Builder/Verifier, and do not modify project files."
          ),
          agent=agent,
      )
  )
  plan_artifact = asyncio.run(
      HandaArtifactService(root=str(storage_root)).load_artifact(
          app_name=APP_NAME,
          user_id=DEFAULT_USER_ID,
          session_id=result.session_id or "",
          filename="ralph_loop.plan.json",
      )
  )
  artifacts = asyncio.run(
      HandaArtifactService(root=str(storage_root)).list_artifact_keys(
          app_name=APP_NAME,
          user_id=DEFAULT_USER_ID,
          session_id=result.session_id or "",
      )
  )

  assert result.ok is True
  assert "Ralph Loop Plan" in result.response
  assert "Please confirm" in result.response
  assert "LLM Verifier acceptance" in result.response
  assert seen == {}
  assert len(planner.calls) == 1
  assert planner.calls[0]["current_plan"] is None
  assert plan_artifact is not None and plan_artifact.text is not None
  saved_plan = json.loads(plan_artifact.text)
  assert saved_plan["verification_prompt"].startswith("LLM Verifier acceptance")
  assert "ralph_loop.v1.plan.json" in artifacts
  assert "ralph_loop.v1.plan.md" in artifacts
  assert "ralph_builder.v1.agent.json" not in artifacts
  assert "ralph_verifier.v1.agent.json" not in artifacts

  confirmed = asyncio.run(
      run_agent_once(
          project=project,
          session_id=result.session_id,
          prompt="confirm",
          agent=agent,
      )
  )
  artifacts_after_confirm = asyncio.run(
      HandaArtifactService(root=str(storage_root)).list_artifact_keys(
          app_name=APP_NAME,
          user_id=DEFAULT_USER_ID,
          session_id=result.session_id or "",
      )
  )

  assert confirmed.ok is True
  assert "Ralph Loop Report" in confirmed.response
  assert seen["goal"] == seen["task_prompt"]
  assert "WYSIWYG diagram editor" in seen["original_user_text"]
  assert "WYSIWYG diagram editor" in seen["task_prompt"]
  assert "auto layout" in seen["task_prompt"]
  assert seen["verification_prompt"].startswith("LLM Verifier acceptance")
  assert seen["builder_output_contract"].startswith("LLM Builder contract")
  assert "first round only" not in seen["task_prompt"]
  assert "wait for confirmation" not in seen["task_prompt"]
  assert "do not start Builder" not in seen["task_prompt"]
  assert "do not modify project" not in seen["task_prompt"]
  assert seen["builder_config"] == "ralph_builder"
  assert seen["verifier_config"] == "ralph_verifier"
  assert seen["parent_session_id"] == result.session_id
  assert "ralph_builder.v1.agent.json" not in artifacts_after_confirm
  assert "ralph_verifier.v1.agent.json" not in artifacts_after_confirm


def test_ralph_agent_updates_pending_plan_before_confirmation(
    tmp_path,
    monkeypatch,
):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  seen = {}

  planner = FakePlanner(
      [
          _planner_plan(
              task_prompt="LLM Builder task v1: implement the base task.",
              verification_prompt="LLM Verifier acceptance v1: run minimal verification.",
          ),
          lambda kwargs: _planner_plan(
              task_prompt=str(kwargs["current_plan"]["task_prompt"]),
              verification_prompt="LLM Verifier acceptance v2: must run pytest.",
              max_rounds=2,
          ),
      ]
  )
  agent = RalphAgent(name="ralph_agent", planner=planner)

  async def fake_run(self, *, goal, parent_session_id, **kwargs):
    seen["goal"] = goal
    seen["verification_prompt"] = str(kwargs["verification_prompt"])
    seen["max_rounds"] = self.max_rounds
    return RalphLoopResult(
        goal=goal,
        task_prompt=str(kwargs["task_prompt"]),
        verification_prompt=str(kwargs["verification_prompt"]),
        builder_output_contract=str(kwargs["builder_output_contract"]),
        done=True,
        status="completed",
        rounds=[],
        final_text="Test loop completed.",
    )

  monkeypatch.setattr(RalphLoopRunner, "run", fake_run)

  planned = asyncio.run(
      run_agent_once(
          project=project,
          prompt="Implement a small verifiable feature.",
          agent=agent,
      )
  )
  updated = asyncio.run(
      run_agent_once(
          project=project,
          session_id=planned.session_id,
          prompt="Change the acceptance method to require pytest and set max_rounds to 2.",
          agent=agent,
      )
  )
  versions = asyncio.run(
      HandaArtifactService(root=str(storage_root)).list_versions(
          app_name=APP_NAME,
          user_id=DEFAULT_USER_ID,
          session_id=planned.session_id or "",
          filename="ralph_loop.plan.json",
      )
  )

  assert planned.ok is True
  assert updated.ok is True
  assert "must run pytest" in updated.response
  assert len(planner.calls) == 2
  assert planner.calls[1]["current_plan"]["task_prompt"] == (
      "LLM Builder task v1: implement the base task."
  )
  assert versions == [0, 1]

  confirmed = asyncio.run(
      run_agent_once(
          project=project,
          session_id=planned.session_id,
          prompt="confirm",
          agent=agent,
      )
  )

  assert confirmed.ok is True
  assert seen["verification_prompt"].startswith(
      "LLM Verifier acceptance v2: must run pytest."
  )
  assert VERIFIER_INDEPENDENCE_GUARD in seen["verification_prompt"]
  assert seen["max_rounds"] == 2


def test_llm_ralph_planner_parses_runner_final_json(monkeypatch):
  plan = _planner_plan(task_prompt="LLM Builder task: generate a real plan.")
  seen_agent_kwargs = {}

  class FakeEvent:
    content = types.Content(
        role="model",
        parts=[types.Part(text=json.dumps(plan, ensure_ascii=False))],
    )

    def is_final_response(self):
      return True

  class FakeAgent:
    def __init__(self, **kwargs):
      seen_agent_kwargs.update(kwargs)

  class FakeRunner:
    def __init__(self, **kwargs):
      self.kwargs = kwargs

    async def run_async(self, **kwargs):
      yield FakeEvent()

  async def fake_ensure_planner_session(ctx, user_id):
    return "planner-session"

  monkeypatch.setattr(ralph_agent_module, "Runner", FakeRunner)
  monkeypatch.setattr(
      ralph_agent_module,
      "_ensure_planner_session",
      fake_ensure_planner_session,
  )
  monkeypatch.setattr(ralph_agent_module, "Agent", FakeAgent)
  ctx = type(
      "FakeContext",
      (),
      {
          "session": type("FakeSession", (), {"app_name": APP_NAME})(),
          "artifact_service": None,
          "session_service": None,
          "memory_service": None,
          "credential_service": None,
      },
  )()

  result = asyncio.run(
      LlmRalphPlanPlanner().create_or_update_plan(
          ctx=ctx,
          user_id=DEFAULT_USER_ID,
          user_text="Generate a plan.",
          current_plan=None,
          max_rounds=DEFAULT_RALPH_MAX_ROUNDS,
      )
  )

  assert result["task_prompt"] == "LLM Builder task: generate a real plan."
  assert "output_schema" not in seen_agent_kwargs


def test_ralph_build_agent_adds_project_agents_to_planner_instruction(
    tmp_path,
    monkeypatch,
):
  (tmp_path / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
  plan = _planner_plan(task_prompt="LLM Builder task: generate a real plan.")
  seen_agent_kwargs = {}

  class FakeEvent:
    content = types.Content(
        role="model",
        parts=[types.Part(text=json.dumps(plan, ensure_ascii=False))],
    )

    def is_final_response(self):
      return True

  class FakeAgent:
    def __init__(self, **kwargs):
      seen_agent_kwargs.update(kwargs)

  class FakeRunner:
    def __init__(self, **kwargs):
      self.kwargs = kwargs

    async def run_async(self, **kwargs):
      yield FakeEvent()

  async def fake_ensure_planner_session(ctx, user_id):
    return "planner-session"

  monkeypatch.setattr(ralph_agent_module, "Runner", FakeRunner)
  monkeypatch.setattr(
      ralph_agent_module,
      "_ensure_planner_session",
      fake_ensure_planner_session,
  )
  monkeypatch.setattr(ralph_agent_module, "Agent", FakeAgent)
  ctx = type(
      "FakeContext",
      (),
      {
          "agent": build_agent(project_root=str(tmp_path)),
          "session": type("FakeSession", (), {"app_name": APP_NAME})(),
          "artifact_service": None,
          "session_service": None,
          "memory_service": None,
          "credential_service": None,
      },
  )()

  result = asyncio.run(
      ralph_agent_module._create_or_update_plan(
          ctx=ctx,
          planner=None,
          user_id=DEFAULT_USER_ID,
          user_text="Generate a plan.",
          current_plan=None,
          max_rounds=DEFAULT_RALPH_MAX_ROUNDS,
      )
  )

  assert result["task_prompt"] == "LLM Builder task: generate a real plan."
  assert "Project Instructions (project_root/AGENTS.md)" in seen_agent_kwargs[
      "instruction"
  ]
  assert "Answer in Chinese." in seen_agent_kwargs["instruction"]


def test_verifier_string_false_is_not_done():
  result = parse_verification_result(
      json.dumps(
          {
              "done": "false",
              "reason": "Builder only produced a plan.",
              "feedback": "Run the real implementation next.",
              "resources": [],
          }
      )
  )

  assert result.done is False
  assert result.reason == "Builder only produced a plan."


def test_agent_config_mode_requires_parent_config_artifact(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  service = HandaArtifactService(root=str(storage_root))
  context = type(
      "FakeContext",
      (),
      {
          "artifact_service": service,
          "app_name": APP_NAME,
          "user_id": DEFAULT_USER_ID,
          "parent_session_id": "session-1",
      },
  )()

  with pytest.raises(ValueError, match="Agent Config artifact not found"):
    asyncio.run(AgentConfigNode("missing_builder")._validate_config_exists(context))


def test_agent_config_node_accepts_config_artifact_filename(tmp_path):
  async def run():
    service = HandaArtifactService(root=str(tmp_path / ".handa"))
    await service.save_artifact(
        app_name=APP_NAME,
        user_id=DEFAULT_USER_ID,
        session_id="session-1",
        filename="builder.agent.json",
        artifact=types.Part.from_text(
            text='{"name":"builder","model_config_id":"gemini-3.1-pro-high"}'
        ),
    )
    context = type(
        "FakeContext",
        (),
        {
            "artifact_service": service,
            "app_name": APP_NAME,
            "user_id": DEFAULT_USER_ID,
            "parent_session_id": "session-1",
        },
    )()

    await AgentConfigNode("builder.agent.json")._validate_config_exists(context)

  asyncio.run(run())


def test_agent_verifier_node_parses_agent_result(monkeypatch):
  async def fake_run(self, context, prompt):
    return type(
        "FakeNodeResult",
        (),
        {
            "text": json.dumps(
                {
                    "done": True,
                    "reason": "ok",
                    "feedback": "",
                    "resources": [{"kind": "artifact", "name": "x"}],
                }
            ),
            "resources": [{"kind": "agent_run", "task_id": "task-1"}],
            "metadata": {"child_session_id": "child-1"},
        },
    )()

  monkeypatch.setattr(AgentConfigNode, "run", fake_run)
  node = AgentVerifierNode(AgentConfigNode("verifier"))
  context = type(
      "FakeContext",
      (),
      {
          "goal": "goal",
          "parent_session_id": "parent",
          "round_number": 1,
          "history": [],
          "app_name": APP_NAME,
          "user_id": DEFAULT_USER_ID,
      },
  )()

  result = asyncio.run(node.run(context, "prompt", NodeResult(text="builder done")))

  assert result.done is True
  assert result.reason == "ok"
  assert result.resources[-1]["task_id"] == "task-1"
  assert result.metadata["child_session_id"] == "child-1"


@pytest.mark.skipif(
    os.getenv("HANDA_RUN_REAL_LLM_TESTS") != "1",
    reason="real model smoke tests are opt-in",
)
def test_ralph_runs_through_handa_cli_with_real_model(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project = tmp_path / "project"
  project.mkdir()
  (project / "README.md").write_text("# Real Ralph Smoke\n", encoding="utf-8")
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  planned = asyncio.run(
      run_agent_once(
          project=project,
          prompt=(
              "Read this project, save a short Ralph report artifact, "
              "explain what you found, and do not modify source code."
          ),
          agent_id="ralph",
      )
  )

  result = asyncio.run(
      run_agent_once(
          project=project,
          session_id=planned.session_id,
          prompt="confirm",
          agent_id="ralph",
      )
  )
  artifacts = asyncio.run(
      HandaArtifactService(root=str(storage_root)).list_artifact_keys(
          app_name=APP_NAME,
          user_id=DEFAULT_USER_ID,
          session_id=planned.session_id or "",
      )
  )

  assert planned.ok is True
  assert "Ralph Loop Plan" in planned.response
  assert result.ok is True
  assert result.response
  assert any(name.endswith(".report.md") for name in artifacts)
