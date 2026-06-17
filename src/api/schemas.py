from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

from ..contract.product import DEFAULT_WEB_AGENT_ID


class SessionCreateRequest(BaseModel):
  agent_id: str = DEFAULT_WEB_AGENT_ID
  project_id: str = Field(min_length=1)


class AgentDefinitionSummary(BaseModel):
  id: str
  runtime: str
  label: str
  description: str = ""


class AgentCatalogTool(BaseModel):
  name: str
  namespace: str = ""
  definition: str = ""


class AgentCatalogSection(BaseModel):
  name: str
  title: str
  template: str


class AgentCatalogSkill(BaseModel):
  name: str
  skill_name: str
  description: str = ""
  source: str = ""


class AgentCatalogModelConfig(BaseModel):
  id: str
  label: str
  description: str = ""
  context_window: int


class AgentCatalog(BaseModel):
  tools: list[AgentCatalogTool]
  instruction_sections: list[AgentCatalogSection]
  skills: list[AgentCatalogSkill]
  agents: list[AgentDefinitionSummary]
  model_configs: list[AgentCatalogModelConfig]


class SessionSummary(BaseModel):
  id: str
  session_id: str | None = None
  title: str
  agent_id: str
  agent_runtime: str = "native"
  automated_task_id: str | None = None
  project_id: str | None = None
  status: str = "idle"
  created_at: str
  updated_at: str
  parent_session_id: str | None = None
  forked_from_session_id: str | None = None
  forked_from_turn_id: str | None = None
  forked_at: str | None = None
  starred: bool = False
  starred_at: str | None = None
  archived_at: str | None = None
  unread: bool = False
  unread_at: str | None = None


class BreadcrumbSummary(BaseModel):
  id: str
  label: str
  title: str | None = None


class BackgroundRunSummary(BaseModel):
  id: str
  kind: str
  title: str
  status: str
  child_session_id: str | None = None
  current_step: str | None = None
  artifact_count: int | None = None


class ProgressItemSummary(BaseModel):
  id: str
  title: str
  status: str
  detail: str | None = None
  updated_at: str | None = None
  source_turn_id: str | None = None


class BrowserEnvironmentSummary(BaseModel):
  success: bool = True
  status: str = "idle"
  session_id: str | None = None
  url: str | None = None
  title: str | None = None
  last_action: str | None = None
  last_error: str | None = None
  updated_at: str | None = None
  screenshot_url: str | None = None
  stream_url: str | None = None
  viewport: dict[str, int] | None = None


class BrowserInteractionRequest(BaseModel):
  action: Literal["click", "type", "key", "scroll", "resize"]
  x: float | None = Field(default=None, ge=0, le=1)
  y: float | None = Field(default=None, ge=0, le=1)
  button: Literal["left", "right", "middle"] = "left"
  text: str | None = None
  key: str | None = None
  delta_x: int = 0
  delta_y: int = 0
  width: int | None = Field(default=None, ge=1)
  height: int | None = Field(default=None, ge=1)


class ContextUsageBreakdownItem(BaseModel):
  id: Literal[
      "instruction",
      "system_instruction",
      "system_tools",
      "user_messages",
      "tool_call_responses",
      "llm_responses",
      "llm_response_thought",
      "llm_response_text",
      "llm_response_tool_call_request",
      "skills",
      "project_config",
  ]
  label: str
  token_count: int = 0
  percent: float = 0.0
  children: list["ContextUsageBreakdownItem"] = Field(default_factory=list)


class AgentContextUsageSummary(BaseModel):
  agent_id: str
  agent_runtime: str
  project_id: str | None = None
  total_token_count: int = 0
  breakdown: list[ContextUsageBreakdownItem] = Field(default_factory=list)


class SessionDetail(BaseModel):
  id: str
  session_id: str | None = None
  title: str
  agent_id: str
  agent_runtime: str = "native"
  automated_task_id: str | None = None
  project_id: str | None = None
  project_root: str | None = None
  status: str
  created_at: str
  updated_at: str
  parent_session_id: str | None = None
  parent_task_id: str | None = None
  root_session_id: str | None = None
  forked_from_session_id: str | None = None
  forked_from_turn_id: str | None = None
  forked_at: str | None = None
  prompt: str | None = None
  input_token_count: int = 0
  output_token_count: int = 0
  total_token_count: int = 0
  context_usage_breakdown: list[ContextUsageBreakdownItem] = Field(default_factory=list)
  breadcrumbs: list[BreadcrumbSummary] = Field(default_factory=list)
  progress_items: list[ProgressItemSummary] = Field(default_factory=list)
  browser_environment: BrowserEnvironmentSummary | None = None
  background_runs: list[BackgroundRunSummary] = Field(default_factory=list)
  steps: list["StepSummary"] = Field(default_factory=list)


class AttachmentSummary(BaseModel):
  id: str
  turn_id: str
  filename: str
  mime_type: str
  kind: str
  byte_count: int = 0


class TurnSummary(BaseModel):
  id: str
  turn_id: str | None = None
  session_id: str
  model_config_id: str | None = None
  title: str | None = None
  input_text: str
  trigger_kind: str = "user_message"
  status: str
  created_at: str
  updated_at: str
  started_at: str | None = None
  finished_at: str | None = None
  cancel_requested_at: str | None = None
  input_token_count: int = 0
  output_token_count: int = 0
  total_token_count: int = 0
  # Tool-call and file-change counters accumulated as the turn's events are
  # ingested (parallel to the token counts above). Default 0 for turns that ran
  # before instrumentation or that touched no tools/files.
  tool_call_count: int = 0
  tool_success_count: int = 0
  tool_fail_count: int = 0
  tool_duration_ms: int = 0
  file_lines_added: int = 0
  file_lines_removed: int = 0
  # Wall-clock elapsed minus time paused on user input — the agent's actual
  # working time. Frozen once the turn reaches a terminal state.
  active_seconds: float = 0.0
  final_text: str | None = None
  error_type: str | None = None
  error_message: str | None = None
  system_run_label: str | None = None
  attachments: list[AttachmentSummary] = Field(default_factory=list)


class UserInputSubmission(BaseModel):
  request_id: str
  answers: list[dict[str, Any]] | None = None
  cancelled: bool = False


class SessionStarUpdateRequest(BaseModel):
  starred: bool


class SessionArchiveUpdateRequest(BaseModel):
  archived: bool


class SessionUnreadUpdateRequest(BaseModel):
  unread: bool


class SessionRenameRequest(BaseModel):
  title: str = Field(min_length=1, max_length=200)


class SessionForkRequest(BaseModel):
  source_turn_id: str | None = None
  include_source_turn: bool = True


class SessionStarSummary(BaseModel):
  session_id: str
  starred: bool
  starred_at: str | None = None


class SessionDeleteSummary(BaseModel):
  session_id: str
  deleted: bool
  deleted_at: str | None = None


class StepSummary(BaseModel):
  id: str
  turn_id: str
  seq: int
  session_seq: int | None = None
  kind: str
  summary: str
  payload: dict[str, Any]
  created_at: str


class ArtifactSummary(BaseModel):
  id: str
  filename: str
  title: str
  kind: str
  filetype: str
  version: int | None = None
  display_version: int | None = None
  mime_type: str | None = None


class ArtifactContent(BaseModel):
  filename: str
  found: bool
  text: str | None = None
  mime_type: str | None = None
  byte_count: int | None = None


class ProjectCreateRequest(BaseModel):
  root_path: str = Field(min_length=1)
  name: str | None = None


class ProjectUpdateRequest(BaseModel):
  name: str = Field(min_length=1)


class ProjectDeleteSummary(BaseModel):
  project_id: str
  root_path: str
  removed: bool = True


class ProjectLauncherRequest(BaseModel):
  target: Literal["finder", "vscode"]


class ProjectLauncherSummary(BaseModel):
  project_id: str
  target: Literal["finder", "vscode"]
  opened: bool = True


class ModelConfigOptionSummary(BaseModel):
  id: str
  label: str
  description: str
  context_window: int = 0


class WebSettingsSummary(BaseModel):
  theme_id: str = "dark"
  model_config_id: str
  model_configs: list[ModelConfigOptionSummary]
  streaming_mode_enabled: bool = True
  folded_project_ids: list[str] = Field(default_factory=list)
  gemini_api_key_set: bool = False
  gemini_api_key_preview: str = ""


class WebSettingsUpdateRequest(BaseModel):
  theme_id: str | None = Field(default=None, min_length=1, max_length=64)
  model_config_id: str | None = Field(default=None, min_length=1, max_length=80)
  streaming_mode_enabled: bool | None = None
  folded_project_ids: list[str] | None = None
  gemini_api_key: str | None = Field(default=None, max_length=512)


class DictateResponse(BaseModel):
  transcript: str


class OptimizePromptRequest(BaseModel):
  prompt: str = Field(min_length=1)
  session_id: str | None = None
  project_id: str | None = None


class OptimizePromptResponse(BaseModel):
  optimized: str


class ProjectSummary(BaseModel):
  id: str
  name: str
  root_path: str
  created_at: str
  updated_at: str
  last_opened_at: str


class AutomatedTaskTriggerInput(BaseModel):
  type: Literal["time", "event"]
  config: dict[str, Any] = Field(default_factory=dict)
  enabled: bool = True


class AutomatedTaskCreateRequest(BaseModel):
  name: str | None = None
  project_id: str = Field(min_length=1)
  prompt: str = Field(min_length=1)
  agent_id: str = DEFAULT_WEB_AGENT_ID
  model_config_id: str | None = None
  description: str | None = None
  enabled: bool = False
  triggers: list[AutomatedTaskTriggerInput] = Field(default_factory=list)


class AutomatedTaskUpdateRequest(BaseModel):
  name: str | None = None
  prompt: str | None = None
  agent_id: str | None = None
  model_config_id: str | None = None
  description: str | None = None
  triggers: list[AutomatedTaskTriggerInput] | None = None


class AutomatedTaskTriggerSummary(BaseModel):
  id: str
  type: str
  enabled: bool
  config: dict[str, Any] = Field(default_factory=dict)
  next_fire_at: str | None = None
  last_fired_at: str | None = None


class AutomatedTaskRunSummary(BaseModel):
  id: str
  automated_task_id: str
  trigger_kind: str
  trigger_id: str | None = None
  status: str
  session_id: str | None = None
  turn_id: str | None = None
  error_message: str | None = None
  created_at: str
  updated_at: str


class AutomatedTaskSummary(BaseModel):
  id: str
  project_id: str
  name: str
  description: str | None = None
  enabled: bool
  agent_id: str
  model_config_id: str | None = None
  prompt: str
  last_triggered_at: str | None = None
  last_run_session_id: str | None = None
  last_run_status: str | None = None
  created_at: str
  updated_at: str
  triggers: list[AutomatedTaskTriggerSummary] = Field(default_factory=list)


class AutomatedTaskDetail(AutomatedTaskSummary):
  runs: list[AutomatedTaskRunSummary] = Field(default_factory=list)


class AutomatedTaskDeleteSummary(BaseModel):
  id: str
  removed: bool
