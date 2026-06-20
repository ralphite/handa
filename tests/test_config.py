from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.config import AgentConfig
from src.config import agent_config_artifact_filename
from src.config import load_agent_config


def sample_config() -> AgentConfig:
  return AgentConfig(
      name="handa",
      description="Test agent.",
      tools=["files_read", "commands_run"],
      skills=["testing"],
      subagents=["self", "browser"],
      instruction_sections=["identity", "testing"],
      hooks=[{"trigger": "pre_invocation", "command": "echo ok"}],
  )


def test_config_roundtrip(tmp_path, monkeypatch):
  config_path = tmp_path / "main.agent.json"
  monkeypatch.setenv("HANDA_AGENT_CONFIG_PATH", str(config_path))

  config_path.write_text(
      json.dumps(sample_config().model_dump(), ensure_ascii=True),
      encoding="utf-8",
  )
  loaded = load_agent_config()

  assert loaded.name == "handa"
  assert loaded.tools == ["files_read", "commands_run"]
  assert loaded.skills == ["testing"]
  assert loaded.subagents == ["self", "browser"]
  assert loaded.instruction_sections == ["identity", "testing"]
  assert loaded.hooks == [{"trigger": "pre_invocation", "command": "echo ok"}]


def test_agent_config_subagents_default_empty():
  config = AgentConfig.model_validate_json('{"name":"no_subagents"}')

  assert config.subagents == []
  assert config.hooks == []


def test_agent_config_defaults_missing_description():
  config = AgentConfig.model_validate_json(
      '{"name":"qa_static_runner"}'
  )

  assert config.description == ""


def test_agent_config_artifact_filename_accepts_base_name_or_filename():
  assert agent_config_artifact_filename("qa_static_runner") == (
      "qa_static_runner.agent.json"
  )
  assert agent_config_artifact_filename("qa_static_runner.agent.json") == (
      "qa_static_runner.agent.json"
  )


def test_agent_config_rejects_invalid_agent_name():
  with pytest.raises(ValidationError, match="String should match pattern"):
    AgentConfig(
        name="qa-static-runner",
        description="Invalid name.",
    )


def test_agent_config_rejects_unknown_instruction_section():
  with pytest.raises(ValidationError, match="Allowed instruction_sections"):
    AgentConfig(
        name="qa_static_runner",
        description="Invalid section.",
        instruction_sections=["You are a QA runner."],
    )


def test_agent_config_ignores_legacy_and_stale_model_keys():
  # Agent configs no longer carry a model; runs inherit the session model.
  # Artifacts persisted before the field was removed may still carry a `model`
  # or `model_config_id` key — both must load without error and be dropped.
  config = AgentConfig.model_validate_json(
      '{"name":"legacy","model":"gpt-4o","model_config_id":"gemini-3.1-pro-low"}'
  )

  assert config.name == "legacy"
  assert "model" not in config.model_dump()
  assert "model_config_id" not in config.model_dump()
