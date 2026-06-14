from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.config import AgentConfig
from src.config import agent_config_artifact_filename
from src.config import load_agent_config
from src.config import resolve_agent_config_model_config_id
from src.config import resolve_generated_agent_model_config_id


def sample_config() -> AgentConfig:
  return AgentConfig(
      name="handa",
      model_config_id="gemini-3.1-pro-high",
      description="Test agent.",
      tools=["files_read", "commands_run"],
      skills=["testing"],
      subagents=["self", "browser"],
      instruction_sections=["identity", "testing"],
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
  assert loaded.model_config_id == "gemini-3.1-pro-high"
  assert loaded.tools == ["files_read", "commands_run"]
  assert loaded.skills == ["testing"]
  assert loaded.subagents == ["self", "browser"]
  assert loaded.instruction_sections == ["identity", "testing"]


def test_agent_config_subagents_default_empty():
  config = AgentConfig.model_validate_json('{"name":"no_subagents"}')

  assert config.subagents == []


def test_agent_config_defaults_missing_description():
  config = AgentConfig.model_validate_json(
      '{"name":"qa_static_runner"}'
  )

  assert config.description == ""
  assert config.model_config_id is None


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
        model_config_id="gemini-3.1-pro-high",
        description="Invalid name.",
    )


def test_agent_config_rejects_unknown_instruction_section():
  with pytest.raises(ValidationError, match="Allowed instruction_sections"):
    AgentConfig(
        name="qa_static_runner",
        model_config_id="gemini-3.1-pro-high",
        description="Invalid section.",
        instruction_sections=["You are a QA runner."],
    )


def test_generated_agent_config_uses_supported_model_config_id():
  config = AgentConfig(name="pinned", model_config_id="gemini-3.1-pro-low")

  assert (
      resolve_generated_agent_model_config_id(
          config,
          inherited_model_config_id="gemini-3.5-flash-high",
      )
      == "gemini-3.1-pro-low"
  )


def test_generated_agent_config_inherits_session_model_when_unset():
  config = AgentConfig(name="unpinned")

  assert (
      resolve_generated_agent_model_config_id(
          config,
          inherited_model_config_id="gemini-3.5-flash-high",
      )
      == "gemini-3.5-flash-high"
  )


def test_generated_agent_config_falls_back_for_unsupported_model():
  # Legacy or user-entered model values that aren't supported model configs
  # fall back to the session model instead of raising.
  config = AgentConfig(name="legacy", model="gpt-4o")

  assert (
      resolve_generated_agent_model_config_id(
          config,
          inherited_model_config_id="gemini-3.5-flash-high",
      )
      == "gemini-3.5-flash-high"
  )


def test_generated_agent_config_defaults_when_nothing_set():
  config = AgentConfig(name="bare")

  assert resolve_generated_agent_model_config_id(config) == "gemini-3.1-pro-high"


def test_predefined_agent_config_resolves_supported_model_config_id():
  config = AgentConfig(name="research_agent", model_config_id="gemini-3.5-flash")

  assert resolve_agent_config_model_config_id(config) == "gemini-3.5-flash"


def test_predefined_agent_config_rejects_unknown_model_config_id():
  config = AgentConfig(name="research_agent", model_config_id="gpt-4o")

  with pytest.raises(ValueError, match="Unknown model_config_id"):
    resolve_agent_config_model_config_id(config)


def test_generated_agent_config_ignores_legacy_model_when_inheriting():
  config = AgentConfig(name="legacy_generated", model="gpt-4o")

  assert (
      resolve_agent_config_model_config_id(
          config,
          inherited_model_config_id="gemini-3.5-flash-high",
          allow_config_model=False,
      )
      == "gemini-3.5-flash-high"
  )
