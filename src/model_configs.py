from __future__ import annotations

from dataclasses import dataclass

from google.genai import types


DEFAULT_MODEL_CONFIG_ID = "gemini-3.1-pro-high"
DEFAULT_MODEL_RETRY_ATTEMPTS = 5
DEFAULT_MODEL_RETRY_INITIAL_DELAY_SEC = 1.0
DEFAULT_MODEL_RETRY_MAX_DELAY_SEC = 60.0
# Bound how long a single buffered generate_content may hold the connection
# open. Without it a slow/stalled response can sit waiting for response headers
# for many minutes before the peer resets it (surfacing as an opaque
# httpx.ReadError); with it a stuck call fails as a ReadTimeout fast enough to
# be retried at the model-call layer.
DEFAULT_MODEL_REQUEST_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class ModelConfigOption:
  id: str
  label: str
  description: str
  context_window: int


@dataclass(frozen=True)
class RuntimeModelConfig:
  model: str
  generate_content_config: types.GenerateContentConfig | None = None


@dataclass(frozen=True)
class ModelConfig:
  option: ModelConfigOption
  runtime: RuntimeModelConfig


def default_model_retry_options() -> types.HttpRetryOptions:
  return types.HttpRetryOptions(
      attempts=DEFAULT_MODEL_RETRY_ATTEMPTS,
      initial_delay=DEFAULT_MODEL_RETRY_INITIAL_DELAY_SEC,
      max_delay=DEFAULT_MODEL_RETRY_MAX_DELAY_SEC,
  )


def with_default_model_retry_options(
    config: types.GenerateContentConfig | None,
) -> types.GenerateContentConfig:
  configured = (
      config.model_copy(deep=True) if config else types.GenerateContentConfig()
  )
  http_options = (
      configured.http_options.model_copy(deep=True)
      if configured.http_options
      else types.HttpOptions()
  )
  if http_options.retry_options is None:
    http_options.retry_options = default_model_retry_options()
  if http_options.timeout is None:
    http_options.timeout = DEFAULT_MODEL_REQUEST_TIMEOUT_MS
  configured.http_options = http_options
  return configured


MODEL_CONFIGS: dict[str, ModelConfig] = {
    "gemini-3.5-flash": ModelConfig(
        option=ModelConfigOption(
            id="gemini-3.5-flash",
            label="Gemini 3.5 Flash Medium",
            description="Fast option for everyday edits with balanced thinking.",
            context_window=1048576,
        ),
        runtime=RuntimeModelConfig(
            model="gemini-3.5-flash",
            generate_content_config=with_default_model_retry_options(
                types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.MEDIUM
                    )
                )
            ),
        ),
    ),
    "gemini-3.5-flash-high": ModelConfig(
        option=ModelConfigOption(
            id="gemini-3.5-flash-high",
            label="Gemini 3.5 Flash High",
            description="Fast model with deeper thinking for heavier edits.",
            context_window=1048576,
        ),
        runtime=RuntimeModelConfig(
            model="gemini-3.5-flash",
            generate_content_config=with_default_model_retry_options(
                types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.HIGH
                    )
                )
            ),
        ),
    ),
    "gemini-3.1-pro-low": ModelConfig(
        option=ModelConfigOption(
            id="gemini-3.1-pro-low",
            label="Gemini 3.1 Pro Low",
            description="Pro model with lower latency for straightforward tasks.",
            context_window=1048576,
        ),
        runtime=RuntimeModelConfig(
            model="gemini-3.1-pro-preview",
            generate_content_config=with_default_model_retry_options(
                types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.LOW
                    )
                )
            ),
        ),
    ),
    "gemini-3.1-pro-high": ModelConfig(
        option=ModelConfigOption(
            id="gemini-3.1-pro-high",
            label="Gemini 3.1 Pro High",
            description="Best for complex coding tasks with high thinking.",
            context_window=1048576,
        ),
        runtime=RuntimeModelConfig(
            model="gemini-3.1-pro-preview",
            generate_content_config=with_default_model_retry_options(
                types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.HIGH
                    )
                )
            ),
        ),
    ),
}


def list_model_config_options() -> list[ModelConfigOption]:
  return [config.option for config in MODEL_CONFIGS.values()]


def validate_model_config_id(model_config_id: str | None) -> str:
  normalized = (model_config_id or DEFAULT_MODEL_CONFIG_ID).strip()
  if normalized not in MODEL_CONFIGS:
    available = ", ".join(MODEL_CONFIGS)
    raise ValueError(
        f"Unknown model_config_id: {normalized}. Available: {available}."
    )
  return normalized


def is_supported_model_config_id(model_config_id: str | None) -> bool:
  """Whether the value names a supported model config, without raising."""
  return bool(model_config_id) and model_config_id.strip() in MODEL_CONFIGS


def resolve_model_config(model_config_id: str | None) -> RuntimeModelConfig:
  return MODEL_CONFIGS[validate_model_config_id(model_config_id)].runtime
