from __future__ import annotations

from google.adk.agents import Agent
from google.genai import types

from src.agents.handa_adk.config_based import build_llm_agent_from_config
from src.config import AgentConfig
from src.model_configs import DEFAULT_MODEL_RETRY_ATTEMPTS
from src.model_configs import DEFAULT_MODEL_RETRY_INITIAL_DELAY_SEC
from src.model_configs import DEFAULT_MODEL_RETRY_MAX_DELAY_SEC
from src.model_configs import resolve_model_config
from src.run_manager import _apply_model_config


def _assert_default_retry_options(config: types.GenerateContentConfig) -> None:
  assert config.http_options is not None
  assert config.http_options.retry_options is not None
  retry_options = config.http_options.retry_options
  assert retry_options.attempts == DEFAULT_MODEL_RETRY_ATTEMPTS
  assert retry_options.initial_delay == DEFAULT_MODEL_RETRY_INITIAL_DELAY_SEC
  assert retry_options.max_delay == DEFAULT_MODEL_RETRY_MAX_DELAY_SEC


def test_resolve_model_config_maps_id_to_runtime_config():
  config = resolve_model_config("gemini-3.1-pro-high")

  assert config.model == "gemini-3.1-pro-preview"
  assert config.generate_content_config is not None
  assert (
      config.generate_content_config.thinking_config.thinking_level
      == types.ThinkingLevel.HIGH
  )
  _assert_default_retry_options(config.generate_content_config)


def test_resolve_model_config_maps_available_thinking_levels():
  cases = {
      "gemini-3.5-flash": ("gemini-3.5-flash", types.ThinkingLevel.MEDIUM),
      "gemini-3.5-flash-high": ("gemini-3.5-flash", types.ThinkingLevel.HIGH),
      "gemini-3.1-pro-low": (
          "gemini-3.1-pro-preview",
          types.ThinkingLevel.LOW,
      ),
      "gemini-3.1-pro-high": (
          "gemini-3.1-pro-preview",
          types.ThinkingLevel.HIGH,
      ),
  }

  for config_id, (model, thinking_level) in cases.items():
    config = resolve_model_config(config_id)

    assert config.model == model
    assert config.generate_content_config is not None
    assert (
        config.generate_content_config.thinking_config.thinking_level
        == thinking_level
    )
    _assert_default_retry_options(config.generate_content_config)


def test_apply_model_config_updates_adk_agent_runtime_fields():
  agent = Agent(name="test_agent", model="old-model")

  _apply_model_config(agent, "gemini-3.5-flash")

  assert agent.model == "gemini-3.5-flash"
  assert agent.generate_content_config is not None
  assert (
      agent.generate_content_config.thinking_config.thinking_level
      == types.ThinkingLevel.MEDIUM
  )
  _assert_default_retry_options(agent.generate_content_config)


def test_build_llm_agent_from_config_sets_default_retry_options():
  agent = build_llm_agent_from_config(
      AgentConfig(
          name="test_agent",
          model_config_id="gemini-3.5-flash",
      )
  )

  assert agent.model == "gemini-3.5-flash"
  assert agent.generate_content_config is not None
  _assert_default_retry_options(agent.generate_content_config)


def test_build_generated_agent_from_config_inherits_parent_model_config():
  agent = build_llm_agent_from_config(
      AgentConfig(
          name="test_agent",
          model="gpt-4o",
      ),
      model_config_id="gemini-3.5-flash-high",
      allow_config_model=False,
  )

  assert agent.model == "gemini-3.5-flash"
  assert agent.generate_content_config is not None
  assert (
      agent.generate_content_config.thinking_config.thinking_level
      == types.ThinkingLevel.HIGH
  )
  _assert_default_retry_options(agent.generate_content_config)
