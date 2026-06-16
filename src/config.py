from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator


APP_DIR = Path(__file__).resolve().parent
AGENT_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
AGENT_CONFIG_SUFFIX = ".agent.json"

# Tools that let a generated agent persist output to disk or session storage. A
# config whose instructions ask for a written report while granting none of
# these fails at run time when the child reaches for a write tool; we surface it
# as a save-time warning instead.
WRITE_CAPABLE_TOOLS = frozenset({"files_write", "files_replace", "artifacts_save_text"})

_WRITE_INTENT_PATTERN = re.compile(
    r"\b(write|save|create|generate|produce|output|emit|store)\b[\s\S]{0,40}?"
    r"\b(report|file|files|artifact|artifacts|document|markdown|results?|summary|analysis)\b",
    re.IGNORECASE,
)


class AgentConfig(BaseModel):
  name: str = Field(pattern=AGENT_NAME_PATTERN)
  model_config_id: str | None = None
  description: str = ""
  tools: list[str] = Field(default_factory=list)
  skills: list[str] = Field(default_factory=list)
  subagents: list[str] = Field(default_factory=list)
  instruction_sections: list[str] = Field(default_factory=list)
  custom_instruction: str | None = None

  @model_validator(mode="before")
  @classmethod
  def _migrate_legacy_model_field(cls, data: object) -> object:
    """Fold the pre-rename `model` key into `model_config_id`.

    `model` was this field's original name before it became `model_config_id`;
    `*.agent.json` artifacts persisted before the rename may still carry it.
    Accept it as a read-time alias (the new name wins when both are present) so
    those configs keep resolving their model. Nothing writes `model` back, so
    re-saving a migrated config drops the legacy key.
    """
    if not isinstance(data, dict) or "model" not in data:
      return data
    legacy = data["model"]
    migrated = {key: value for key, value in data.items() if key != "model"}
    if legacy and not migrated.get("model_config_id"):
      migrated["model_config_id"] = legacy
    return migrated

  @field_validator("instruction_sections")
  @classmethod
  def validate_instruction_sections(cls, value: list[str]) -> list[str]:
    from .instructions import SECTIONS

    unknown_sections = [name for name in value if name not in SECTIONS]
    if unknown_sections:
      allowed = ", ".join(sorted(SECTIONS))
      unknown = ", ".join(unknown_sections)
      raise ValueError(
          f"Unknown instruction section(s): {unknown}. "
          f"Allowed instruction_sections: {allowed}."
      )
    return value


def agent_config_warnings(config: AgentConfig) -> list[str]:
  """Non-blocking authoring warnings for a generated agent config.

  Flags a write-intent/tool mismatch: instructions that ask the agent to write
  or save output while no write-capable tool is granted. Returned as warnings
  rather than errors because returning an inline answer is a valid alternative.
  """
  warnings: list[str] = []
  if not set(config.tools) & WRITE_CAPABLE_TOOLS:
    text = " ".join(
        part for part in (config.description, config.custom_instruction) if part
    )
    if _WRITE_INTENT_PATTERN.search(text):
      warnings.append(
          "Instructions ask the agent to write or save output, but no "
          "write-capable tool is granted. Add one of "
          f"{', '.join(sorted(WRITE_CAPABLE_TOOLS))}, or require an inline answer."
      )
  return warnings


def resolve_agent_config_model_config_id(
    config: AgentConfig,
    *,
    inherited_model_config_id: str | None = None,
    allow_config_model: bool = True,
) -> str:
  """Resolve the model config id for a runnable AgentConfig.

  When `allow_config_model=False` the config's model is ignored and the
  inherited model is used. Otherwise the config's `model_config_id` is used and
  must name a supported model config; this is the strict path for
  predefined/system configs where an unknown model is a config error worth
  surfacing.
  """
  from .model_configs import validate_model_config_id

  configured = config.model_config_id if allow_config_model else None
  return validate_model_config_id(configured or inherited_model_config_id)


def resolve_generated_agent_model_config_id(
    config: AgentConfig,
    *,
    inherited_model_config_id: str | None = None,
) -> str:
  """Resolve a generated Agent Config run's model.

  The model field is optional: when the config names a supported model config
  it wins; otherwise the run inherits the session-selected model. Unlike
  `resolve_agent_config_model_config_id`, an unsupported config model falls
  back to the inherited model instead of raising — generated configs may carry
  stale or user-entered model values, and the session model is a safe default.
  """
  from .model_configs import is_supported_model_config_id
  from .model_configs import validate_model_config_id

  configured = config.model_config_id
  if is_supported_model_config_id(configured):
    return validate_model_config_id(configured)
  return validate_model_config_id(inherited_model_config_id)


def get_config_path() -> Path:
  configured = os.getenv("HANDA_AGENT_CONFIG_PATH")
  if configured:
    return Path(configured).expanduser().resolve()
  return APP_DIR / "agents" / "orca" / "orca.agent.json"


def load_agent_config() -> AgentConfig:
  path = get_config_path()
  return load_agent_config_from_path(path)


def load_agent_config_from_path(path: Path | str) -> AgentConfig:
  path = Path(path).expanduser().resolve()
  return AgentConfig.model_validate_json(path.read_text(encoding="utf-8"))


def agent_config_artifact_filename(name: str) -> str:
  normalized = name.strip()
  if not normalized:
    raise ValueError("Agent Config name must not be empty.")
  if normalized.endswith(AGENT_CONFIG_SUFFIX):
    return normalized
  return f"{normalized}{AGENT_CONFIG_SUFFIX}"
