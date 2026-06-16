from __future__ import annotations

from google.genai import types

from src.model_configs import DEFAULT_MODEL_RETRY_ATTEMPTS
from src.model_configs import DEFAULT_MODEL_RETRY_INITIAL_DELAY_SEC
from src.model_configs import DEFAULT_MODEL_RETRY_MAX_DELAY_SEC
from src.model_configs import resolve_model_config
from src.model_configs import with_default_model_retry_options


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


def test_with_default_model_retry_options_preserves_existing_config():
  config = with_default_model_retry_options(
      types.GenerateContentConfig(
          thinking_config=types.ThinkingConfig(
              thinking_level=types.ThinkingLevel.HIGH
          )
      )
  )

  assert config.thinking_config.thinking_level == types.ThinkingLevel.HIGH
  _assert_default_retry_options(config)
