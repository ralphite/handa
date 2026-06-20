from __future__ import annotations

import asyncio
import inspect
import os
from types import SimpleNamespace

import pytest
from google.genai import types

from src.storage import HandaArtifactService
from src.storage import HandaSessionService
from src.agents.orca import tools as agent_tools
from src.model_configs import DEFAULT_MODEL_CONFIG_ID


class FakeToolContext:
  def __init__(self, service: HandaArtifactService):
    self.service = service
    self.app_name = "handa"
    self.user_id = "user"
    self.session_id = "session-1"
    self.session = SimpleNamespace(id=self.session_id, state={})

  async def save_artifact(
      self,
      filename: str,
      artifact: types.Part,
      custom_metadata: dict | None = None,
  ) -> int:
    return await self.service.save_artifact(
        app_name=self.app_name,
        user_id=self.user_id,
        session_id=self.session_id,
        filename=filename,
        artifact=artifact,
        custom_metadata=custom_metadata,
    )

  async def list_artifacts(self) -> list[str]:
    return await self.service.list_artifact_keys(
        app_name=self.app_name,
        user_id=self.user_id,
        session_id=self.session_id,
    )

  async def load_artifact(
      self,
      filename: str,
      version: int | None = None,
  ) -> types.Part | None:
    return await self.service.load_artifact(
        app_name=self.app_name,
        user_id=self.user_id,
        session_id=self.session_id,
        filename=filename,
        version=version,
    )


def _session_context(tool_context: FakeToolContext) -> agent_tools.SessionContext:
  state = getattr(getattr(tool_context, "session", None), "state", {}) or {}
  return agent_tools.SessionContext(
      session_id=tool_context.session_id,
      user_id=tool_context.user_id,
      app_name=tool_context.app_name,
      model_config_id=state.get("handa:model_config_id") or DEFAULT_MODEL_CONFIG_ID,
      agent_run_depth=int(state.get("handa:agent_run_depth") or 0),
      project_root=os.environ.get("HANDA_PROJECT_ROOT"),
  )


async def _dispatch(
    tool_context: FakeToolContext,
    name: str,
    args: dict,
) -> dict:
  previous_root = os.environ.get("HANDA_STORAGE_ROOT")
  os.environ["HANDA_STORAGE_ROOT"] = tool_context.service.root
  try:
    toolset = agent_tools.build_toolset([name], _session_context(tool_context))
    result = toolset.callables[name](**args)
    if inspect.isawaitable(result):
      result = await result
    return result
  finally:
    if previous_root is None:
      os.environ.pop("HANDA_STORAGE_ROOT", None)
    else:
      os.environ["HANDA_STORAGE_ROOT"] = previous_root


class artifacts:
  @staticmethod
  async def save_text(*, filename: str, content: str, tool_context: FakeToolContext):
    return await _dispatch(
        tool_context,
        "artifacts_save_text",
        {"filename": filename, "content": content},
    )

  @staticmethod
  async def list(*, tool_context: FakeToolContext):
    return await _dispatch(tool_context, "artifacts_list", {})

  @staticmethod
  async def read(
      *,
      filename: str,
      tool_context: FakeToolContext,
      version: int | None = None,
      offset: int = 0,
      max_chars: int | None = None,
      metadata_only: bool = False,
  ):
    return await _dispatch(
        tool_context,
        "artifacts_read",
        {
            "filename": filename,
            "version": version,
            "offset": offset,
            "max_chars": max_chars,
            "metadata_only": metadata_only,
        },
    )


class agents:
  @staticmethod
  async def save_config(**kwargs):
    tool_context = kwargs.pop("tool_context")
    return await _dispatch(tool_context, "agents_save_config", kwargs)

  @staticmethod
  async def read_config(**kwargs):
    tool_context = kwargs.pop("tool_context")
    return await _dispatch(tool_context, "agents_read_config", kwargs)

  @staticmethod
  async def list_configs(*, tool_context: FakeToolContext):
    return await _dispatch(tool_context, "agents_list_configs", {})

  @staticmethod
  async def start_run(**kwargs):
    tool_context = kwargs.pop("tool_context")
    return await _dispatch(tool_context, "agents_start_run", kwargs)

  @staticmethod
  def get_run_status(**kwargs):
    tool_context = kwargs.pop("tool_context")
    previous_root = os.environ.get("HANDA_STORAGE_ROOT")
    os.environ["HANDA_STORAGE_ROOT"] = tool_context.service.root
    try:
      toolset = agent_tools.build_toolset(
          ["agents_get_run_status"],
          _session_context(tool_context),
      )
      return toolset.callables["agents_get_run_status"](**kwargs)
    finally:
      if previous_root is None:
        os.environ.pop("HANDA_STORAGE_ROOT", None)
      else:
        os.environ["HANDA_STORAGE_ROOT"] = previous_root

  @staticmethod
  async def list_run_artifacts(**kwargs):
    tool_context = kwargs.pop("tool_context")
    return await _dispatch(tool_context, "agents_list_run_artifacts", kwargs)

  @staticmethod
  async def read_run_artifact(**kwargs):
    tool_context = kwargs.pop("tool_context")
    return await _dispatch(tool_context, "agents_read_run_artifact", kwargs)

  @staticmethod
  async def run_agent(**kwargs):
    tool_context = kwargs.pop("tool_context")
    return await _dispatch(tool_context, "run_agent", kwargs)


def test_artifacts_text_tool_roundtrip(tmp_path):
  asyncio.run(_assert_artifacts_text_tool_roundtrip(tmp_path))


async def _assert_artifacts_text_tool_roundtrip(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))

  saved = await artifacts.save_text(
      filename="testing_quality.plan.md",
      content="# Plan\n\nRun pytest.",
      tool_context=context,
  )
  listed = await artifacts.list(tool_context=context)
  loaded = await artifacts.read(
      filename="testing_quality.plan.md",
      tool_context=context,
  )

  assert saved["display_version"] == 1
  assert saved["filename"] == "testing_quality.plan.md"
  assert saved["stored_filename"] == "testing_quality.v1.plan.md"
  assert listed["artifacts"] == ["testing_quality.v1.plan.md"]
  assert loaded["content"] == "# Plan\n\nRun pytest."


def test_artifacts_read_bounds_large_artifact(tmp_path):
  asyncio.run(_assert_artifacts_read_bounds_large_artifact(tmp_path))


async def _assert_artifacts_read_bounds_large_artifact(tmp_path):
  from src.tools.text_window import DEFAULT_READ_MAX_CHARS

  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))
  big = "Z" * (DEFAULT_READ_MAX_CHARS + 1000)
  await artifacts.save_text(
      filename="huge.report.md",
      content=big,
      tool_context=context,
  )

  # Default read is bounded and tells the caller how to resume.
  loaded = await artifacts.read(filename="huge.report.md", tool_context=context)
  assert len(loaded["content"]) == DEFAULT_READ_MAX_CHARS
  assert loaded["char_count"] == DEFAULT_READ_MAX_CHARS + 1000
  assert loaded["truncated"] is True
  assert loaded["next_offset"] == DEFAULT_READ_MAX_CHARS

  # metadata_only skips the content entirely.
  meta = await artifacts.read(
      filename="huge.report.md", tool_context=context, metadata_only=True
  )
  assert "content" not in meta
  assert meta["char_count"] == DEFAULT_READ_MAX_CHARS + 1000

  # Paging from next_offset returns the remainder.
  tail = await artifacts.read(
      filename="huge.report.md",
      tool_context=context,
      offset=DEFAULT_READ_MAX_CHARS,
  )
  assert len(tail["content"]) == 1000
  assert "truncated" not in tail


def test_agents_config_tool_versions_session_artifacts(tmp_path):
  asyncio.run(_assert_agents_config_tool_versions_session_artifacts(tmp_path))


def test_agents_save_config_tool_omits_model_parameters():
  signature = inspect.signature(
      agent_tools.build_toolset(
          ["agents_save_config"],
          agent_tools.SessionContext(session_id="session-1", user_id="user"),
      ).callables["agents_save_config"]
  )

  # Agent configs no longer pin a model; the run inherits the session model, so
  # neither the canonical `model_config_id` nor the legacy `model` is a param.
  assert "model_config_id" not in signature.parameters
  assert "model" not in signature.parameters


async def _assert_agents_config_tool_versions_session_artifacts(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))

  first = await agents.save_config(
      name="testing_quality",
      description="Run focused quality checks for a coding task.",
      tools=["files_read", "commands_run", "artifacts_save_text"],
      skills=["testing"],
      instruction_sections=["identity", "testing"],
      custom_instruction="Save a report at the end.",
      tool_context=context,
  )
  second = await agents.save_config(
      name="testing_quality",
      description="Run focused quality checks for a coding task.",
      tools=["files_read", "commands_run", "artifacts_save_text"],
      skills=["testing"],
      instruction_sections=["identity", "testing"],
      custom_instruction="Save a report at the end.",
      tool_context=context,
  )
  listed = await agents.list_configs(tool_context=context)
  loaded_latest = await agents.read_config(
      name="testing_quality",
      tool_context=context,
  )
  loaded_latest_by_filename = await agents.read_config(
      name="testing_quality.agent.json",
      tool_context=context,
  )
  loaded_first = await agents.read_config(
      name="testing_quality",
      version=0,
      tool_context=context,
  )

  artifact_dir = root / "sessions" / "session-1" / "artifacts"
  assert first["display_version"] == 1
  assert second["display_version"] == 2
  assert first["filename"] == "testing_quality.agent.json"
  assert first["stored_filename"] == "testing_quality.v1.agent.json"
  assert listed["configs"] == [
      "testing_quality.v1.agent.json",
      "testing_quality.v2.agent.json",
  ]
  assert first["model_config_id"] == "gemini-3.1-pro-high"
  assert loaded_latest["config"]["name"] == "testing_quality"
  assert "model" not in loaded_latest["config"]
  assert "model_config_id" not in loaded_latest["config"]
  assert loaded_latest_by_filename["config"]["name"] == "testing_quality"
  assert loaded_latest["config"]["custom_instruction"] == "Save a report at the end."
  assert loaded_first["config"]["tools"] == [
      "files_read",
      "commands_run",
      "artifacts_save_text",
  ]
  assert (artifact_dir / "testing_quality.v1.agent.json").exists()
  assert (artifact_dir / "testing_quality.v2.agent.json").exists()


def test_agents_save_config_rejects_unrunnable_config(tmp_path):
  asyncio.run(_assert_agents_save_config_rejects_unrunnable_config(tmp_path))


async def _assert_agents_save_config_rejects_unrunnable_config(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))

  with pytest.raises(ValueError, match="Allowed instruction_sections"):
    await agents.save_config(
        name="qa_static_runner",
        description="Invalid section.",
        tools=["files_read"],
        skills=[],
        instruction_sections=["Use natural language here."],
        tool_context=context,
    )

  with pytest.raises(ValueError, match="String should match pattern"):
    await agents.save_config(
        name="qa-static-runner",
        description="Invalid name.",
        tools=["files_read"],
        skills=[],
        instruction_sections=["identity"],
        tool_context=context,
    )

  # An unknown tool name (typo) is rejected at save time, not deferred to run.
  with pytest.raises(ValueError, match="Unknown agent tools: file_write"):
    await agents.save_config(
        name="typo_tool_agent",
        description="Has a typo.",
        tools=["files_read", "file_write"],
        skills=[],
        instruction_sections=["identity"],
        tool_context=context,
    )

  listed = await agents.list_configs(tool_context=context)

  assert listed["configs"] == []


def test_agents_save_config_warns_on_write_intent_without_write_tool(tmp_path):
  asyncio.run(_assert_agents_save_config_warns_on_write_intent(tmp_path))


async def _assert_agents_save_config_warns_on_write_intent(tmp_path):
  root = tmp_path / ".handa"
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))

  # Asks for a written report but only grants read tools -> non-blocking warning.
  warned = await agents.save_config(
      name="reader_that_should_write",
      description="Analyze the code.",
      tools=["files_read", "files_search"],
      skills=[],
      instruction_sections=["identity"],
      custom_instruction="Save a report.md with your findings.",
      tool_context=context,
  )
  assert warned["success"] is True
  assert any("write-capable tool" in w for w in warned["warnings"])

  # Granting a write tool clears the warning.
  ok = await agents.save_config(
      name="reader_that_can_write",
      description="Analyze the code.",
      tools=["files_read", "artifacts_save_text"],
      skills=[],
      instruction_sections=["identity"],
      custom_instruction="Save a report.md with your findings.",
      tool_context=context,
  )
  assert ok["warnings"] == []


def test_agent_run_child_artifact_tools(tmp_path, monkeypatch):
  asyncio.run(_assert_agent_run_child_artifact_tools(tmp_path, monkeypatch))


def test_agent_start_run_validates_existing_config_artifact(tmp_path, monkeypatch):
  asyncio.run(_assert_agent_start_run_validates_existing_config_artifact(tmp_path, monkeypatch))


def test_run_agent_tool_creates_agent_task(tmp_path, monkeypatch):
  asyncio.run(_assert_run_agent_tool_creates_agent_task(tmp_path, monkeypatch))


def test_agent_start_run_inherits_session_model(tmp_path, monkeypatch):
  asyncio.run(_assert_agent_start_run_inherits_session_model(tmp_path, monkeypatch))


async def _assert_agent_start_run_inherits_session_model(tmp_path, monkeypatch):
  root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(root))
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  service = HandaArtifactService(root=str(root))
  context = FakeToolContext(service)
  context.session.state["handa:model_config_id"] = "gemini-3.5-flash-high"

  captured: dict[str, object] = {}

  def fake_start_agent_run_task(**kwargs):
    captured.update(kwargs)
    return {
        "id": "task_fake",
        "status": "queued",
        "child_session_id": "child-1",
        "config_name": kwargs["config_name"],
    }

  # start_agent_run_task spawns a worker subprocess; capture its kwargs instead.
  monkeypatch.setattr(agent_tools, "start_agent_run_task", fake_start_agent_run_task)

  # A stale `model_config_id` key on the artifact is ignored; the run always
  # inherits the session model.
  await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="stale_model_agent.agent.json",
      artifact=types.Part.from_text(
          text='{"name":"stale_model_agent","model_config_id":"gemini-3.1-pro-low"}'
      ),
  )
  await agents.start_run(name="stale_model_agent", prompt="Go.", tool_context=context)
  assert captured["model_config_id"] == "gemini-3.5-flash-high"

  captured.clear()
  await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="plain_agent.agent.json",
      artifact=types.Part.from_text(text='{"name":"plain_agent"}'),
  )
  await agents.start_run(name="plain_agent", prompt="Go.", tool_context=context)
  assert captured["model_config_id"] == "gemini-3.5-flash-high"


async def _assert_agent_start_run_validates_existing_config_artifact(
    tmp_path,
    monkeypatch,
):
  root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(root))
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  service = HandaArtifactService(root=str(root))
  context = FakeToolContext(service)
  await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id="session-1",
      filename="qa-static-runner.agent.json",
      artifact=types.Part.from_text(
          text='{"name":"qa-static-runner","model":"gemini-test","description":"bad"}'
      ),
  )

  with pytest.raises(ValueError, match="String should match pattern"):
    await agents.start_run(
        name="qa-static-runner",
        prompt="Run pytest.",
        tool_context=context,
    )

  assert not (root / "sessions" / "session-1" / "tasks").exists()


async def _assert_agent_run_child_artifact_tools(tmp_path, monkeypatch):
  root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(root))
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.runtime.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  await agents.save_config(
      name="testing_quality",
      description="Run focused quality checks for a coding task.",
      tools=["files_read", "commands_run", "artifacts_save_text"],
      skills=["testing"],
      instruction_sections=["identity", "testing"],
      tool_context=context,
  )
  run = await agents.start_run(
      name="testing_quality.agent.json",
      prompt="Run pytest.",
      tool_context=context,
  )
  child_session_id = run["child_session_id"]
  service = HandaArtifactService(root=str(root))
  await service.save_artifact(
      app_name="handa",
      user_id="user",
      session_id=child_session_id,
      filename="pytest_result.verification.md",
      artifact=types.Part(text="passed"),
  )

  listed = await agents.list_run_artifacts(
      task_id=run["task_id"],
      tool_context=context,
  )
  loaded = await agents.read_run_artifact(
      task_id=run["task_id"],
      filename="pytest_result.verification.md",
      tool_context=context,
  )
  status = agents.get_run_status(
      task_id=run["task_id"],
      tool_context=context,
  )
  child = HandaSessionService(root=str(root))._read_session(child_session_id)

  assert run["status"] == "queued"
  assert child is not None
  assert child.state["handa:agent_run_depth"] == 1
  assert listed["child_session_id"] == child_session_id
  assert listed["artifacts"] == ["pytest_result.v1.verification.md"]
  assert loaded["content"] == "passed"
  assert status["task"]["kind"] == "agent_run"

  context.session.state["handa:agent_run_depth"] = 3
  with pytest.raises(ValueError, match="max depth"):
    await agents.start_run(
        name="testing_quality.agent.json",
        prompt="Recurse.",
        tool_context=context,
        max_depth=3,
    )


async def _assert_run_agent_tool_creates_agent_task(tmp_path, monkeypatch):
  root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_PROJECT_ROOT", str(tmp_path))
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(root))
  await HandaSessionService(root=str(root)).create_session(
      app_name="handa",
      user_id="user",
      session_id="session-1",
  )
  context = FakeToolContext(HandaArtifactService(root=str(root)))

  class FakeProcess:
    pid = 12345

  monkeypatch.setattr(
      "src.runtime.subprocess.Popen",
      lambda *args, **kwargs: FakeProcess(),
  )

  run = await agents.run_agent(
      agent_id="browser",
      prompt="Run browser.",
      tool_context=context,
  )
  child = HandaSessionService(root=str(root))._read_session(run["child_session_id"])
  status = agents.get_run_status(
      task_id=run["task_id"],
      tool_context=context,
  )

  assert run["status"] == "queued"
  assert run["agent_id"] == "browser"
  assert child is not None
  assert child.state["handa:session_kind"] == "run_agent_child"
  assert child.state["handa:target_agent_id"] == "browser"
  assert status["task"]["kind"] == "run_agent"

  context.session.state["handa:agent_run_depth"] = 3
  with pytest.raises(ValueError, match="max depth"):
    await agents.run_agent(
        agent_id="orca",
        prompt="Recurse.",
        tool_context=context,
        max_depth=3,
    )
