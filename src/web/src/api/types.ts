export interface BackendAgentDefinition {
  id: string
  runtime: string
  label: string
  description: string
}

export interface BackendAgentCatalogTool {
  name: string
  namespace: string
  definition: string
}

export interface BackendAgentCatalogSection {
  name: string
  title: string
  template: string
}

export interface BackendAgentCatalogSkill {
  name: string
  skill_name: string
  description: string
  source: string
}

export interface BackendAgentCatalog {
  tools: BackendAgentCatalogTool[]
  instruction_sections: BackendAgentCatalogSection[]
  skills: BackendAgentCatalogSkill[]
  agents: BackendAgentDefinition[]
  model_configs: BackendModelConfigOption[]
}

export interface BackendTurn {
  id: string
  turn_id?: string | null
  session_id: string
  model_config_id?: string | null
  title?: string | null
  input_text: string
  trigger_kind: string
  status: string
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  cancel_requested_at?: string | null
  input_token_count: number
  output_token_count: number
  total_token_count?: number
  /** Agent working time (seconds), excluding time paused on user input. */
  active_seconds?: number
  final_text?: string | null
  error_type?: string | null
  error_message?: string | null
  system_run_label?: string | null
  attachments?: BackendTurnAttachment[]
}

export interface BackendSession {
  id: string
  session_id?: string | null
  title: string
  agent_id: string
  agent_runtime: string
  automated_task_id?: string | null
  project_id?: string | null
  status: string
  created_at: string
  updated_at: string
  parent_session_id?: string | null
  forked_from_session_id?: string | null
  forked_from_turn_id?: string | null
  forked_at?: string | null
  starred: boolean
  starred_at?: string | null
  archived_at?: string | null
  unread: boolean
  unread_at?: string | null
}

export interface BackendTurnAttachment {
  id: string
  turn_id: string
  filename: string
  mime_type: string
  kind: string
  byte_count: number
}

export interface BackendStep {
  id: string
  turn_id: string
  seq: number
  session_seq?: number | null
  kind: string
  summary: string
  payload: Record<string, unknown>
  raw_event?: Record<string, unknown>
  created_at: string
}

export interface BackendBreadcrumb {
  id: string
  label: string
  title?: string | null
}

export interface BackendBackgroundRun {
  id: string
  kind: string
  title: string
  status: string
  child_session_id?: string | null
  current_step?: string | null
  artifact_count?: number | null
}

export interface BackendProgressItem {
  id: string
  title: string
  status: string
  detail?: string | null
  updated_at?: string | null
  source_turn_id?: string | null
}

export interface BackendBrowserEnvironment {
  success?: boolean
  status: string
  session_id?: string | null
  url?: string | null
  title?: string | null
  last_action?: string | null
  last_error?: string | null
  updated_at?: string | null
  screenshot_url?: string | null
  stream_url?: string | null
  viewport?: { width?: number; height?: number } | null
}

export type BackendBrowserInteraction =
  | { action: 'click'; x: number; y: number; button?: 'left' | 'right' | 'middle' }
  | { action: 'type'; text: string }
  | { action: 'key'; key: string }
  | { action: 'scroll'; delta_x?: number; delta_y?: number }
  | { action: 'resize'; width: number; height: number }

export interface BackendContextUsageBreakdownItem {
  id:
    | 'instruction'
    | 'system_instruction'
    | 'system_tools'
    | 'user_messages'
    | 'tool_call_responses'
    | 'llm_responses'
    | 'llm_response_thought'
    | 'llm_response_text'
    | 'llm_response_tool_call_request'
    | 'skills'
    | 'project_config'
  label: string
  token_count: number
  percent: number
  children?: BackendContextUsageBreakdownItem[]
}

export interface BackendAgentContextUsage {
  agent_id: string
  agent_runtime: string
  project_id?: string | null
  total_token_count: number
  breakdown: BackendContextUsageBreakdownItem[]
}

export interface BackendSessionDetail {
  id: string
  session_id?: string | null
  title: string
  agent_id: string
  agent_runtime: string
  automated_task_id?: string | null
  project_id?: string | null
  project_root?: string | null
  status: string
  created_at: string
  updated_at: string
  parent_session_id?: string | null
  parent_task_id?: string | null
  root_session_id?: string | null
  forked_from_session_id?: string | null
  forked_from_turn_id?: string | null
  forked_at?: string | null
  prompt?: string | null
  input_token_count?: number
  output_token_count?: number
  total_token_count?: number
  context_usage_breakdown?: BackendContextUsageBreakdownItem[]
  breadcrumbs: BackendBreadcrumb[]
  progress_items: BackendProgressItem[]
  browser_environment?: BackendBrowserEnvironment | null
  background_runs: BackendBackgroundRun[]
  steps: BackendStep[]
}

export interface BackendArtifact {
  id: string
  filename: string
  title: string
  kind: string
  filetype: string
  version?: number | null
  display_version?: number | null
  mime_type?: string | null
}

export interface BackendArtifactContent {
  filename: string
  found: boolean
  text?: string | null
  mime_type?: string | null
  byte_count?: number | null
}

export interface BackendProject {
  id: string
  name: string
  root_path: string
  created_at: string
  updated_at: string
  last_opened_at: string
}

export interface BackendProjectDelete {
  project_id: string
  root_path: string
  removed: boolean
}

export type BackendProjectLauncherTarget = 'finder' | 'vscode'

export interface BackendProjectLauncherResult {
  project_id: string
  target: BackendProjectLauncherTarget
  opened: boolean
}

export interface BackendSettings {
  theme_id: string
  model_config_id: string
  model_configs: BackendModelConfigOption[]
  streaming_mode_enabled: boolean
  folded_project_ids: string[]
  gemini_api_key_set: boolean
  gemini_api_key_preview: string
}

export interface BackendSettingsUpdate {
  theme_id?: string
  model_config_id?: string
  streaming_mode_enabled?: boolean
  folded_project_ids?: string[]
  gemini_api_key?: string
}

export interface BackendModelConfigOption {
  id: string
  label: string
  description: string
  context_window: number
}

export interface BackendSessionStar {
  session_id: string
  starred: boolean
  starred_at?: string | null
}

export interface BackendSessionDelete {
  session_id: string
  deleted: boolean
  deleted_at?: string | null
}

export interface BackendAutomatedTaskTrigger {
  id: string
  type: 'time' | 'event' | string
  enabled: boolean
  config: Record<string, unknown>
  next_fire_at?: string | null
  last_fired_at?: string | null
}

export interface BackendAutomatedTaskTriggerInput {
  type: 'time' | 'event'
  config: Record<string, unknown>
  enabled?: boolean
}

export interface BackendAutomatedTaskRun {
  id: string
  automated_task_id: string
  trigger_kind: string
  trigger_id?: string | null
  status: string
  session_id?: string | null
  turn_id?: string | null
  error_message?: string | null
  created_at: string
  updated_at: string
}

export interface BackendAutomatedTask {
  id: string
  project_id: string
  name: string
  description?: string | null
  enabled: boolean
  agent_id: string
  model_config_id?: string | null
  prompt: string
  last_triggered_at?: string | null
  last_run_session_id?: string | null
  last_run_status?: string | null
  created_at: string
  updated_at: string
  triggers: BackendAutomatedTaskTrigger[]
}

export interface BackendAutomatedTaskDetail extends BackendAutomatedTask {
  runs: BackendAutomatedTaskRun[]
}

export interface BackendAutomatedTaskCreate {
  name?: string
  project_id: string
  prompt: string
  agent_id?: string
  model_config_id?: string | null
  description?: string | null
  enabled?: boolean
  triggers?: BackendAutomatedTaskTriggerInput[]
}

export interface BackendAutomatedTaskUpdate {
  name?: string
  prompt?: string
  agent_id?: string
  model_config_id?: string | null
  description?: string | null
  triggers?: BackendAutomatedTaskTriggerInput[]
}

export interface BackendAutomatedTaskDelete {
  id: string
  removed: boolean
}
