export interface ProjectNavItem {
  id: string
  name: string
  path: string
  sessions: SessionNavSummary[]
}

export interface SessionNavSummary {
  id: string
  title: string
  createdAt: string
  lastActivityAt?: string | null
  automatedTaskId?: string | null
  forkedFromSessionId?: string | null
  status: AgentSessionStatus
  /** A user-input form is pending, even if a queued follow-up turn owns the session status. */
  waitingInput?: boolean
  attention?: SessionAttention
  starred?: boolean
  starredAt?: string | null
  archivedAt?: string | null
  unread?: boolean
  unreadAt?: string | null
}

export interface MessageAttachment {
  id: string
  turnId?: string
  filename: string
  mimeType: string
  kind: string
  byteCount: number
  url: string
  isImage: boolean
}

export interface PendingUserMessage {
  id: string
  prompt: string
  files: File[]
  createdAt: string
  modelConfigId?: string
  /** Server-side attachments for queued turns; files holds local not-yet-uploaded ones. */
  attachments?: MessageAttachment[]
}

export interface SessionGoal {
  goalId?: string | null
  text: string
  status: string
  createdTurnId?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  maxAttempts?: number | null
  reason?: string | null
}

export interface AgentMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  body: string
  createdAt: string
  turnId?: string
  meta?: string
  invocationId?: string
  triggerKind?: string
  systemRunLabel?: string
  elapsed?: string
  /** Agent working time in seconds, excluding time paused on user input. */
  activeSeconds?: number
  status?: AgentSessionStatus
  tokenUsage?: InvocationTokenUsage
  detailEvents?: InvocationDetailEvent[]
  timelineItems?: InvocationTimelineItem[]
  attachments?: MessageAttachment[]
  pendingUserInput?: PendingUserInputRequest
}

export interface UserInputOption {
  label: string
  description?: string
}

export interface UserInputQuestion {
  id: string
  prompt: string
  options: UserInputOption[]
  multiSelect: boolean
  allowFreeText: boolean
}

export interface PendingUserInputRequest {
  requestId: string
  turnId: string
  questions: UserInputQuestion[]
}

export interface UserInputAnswer {
  id: string
  selected: string[]
  free_text?: string
}

export interface UserInputSubmissionPayload {
  turnId: string
  requestId: string
  answers?: UserInputAnswer[]
  cancelled?: boolean
}

export interface EditMessagePayload {
  sourceTurnId: string
  prompt: string
  files: File[]
  existingAttachmentIds: string[]
}

export interface SendPromptPayload {
  prompt: string
  files: File[]
  /** Ids of existing server-side attachments to clone onto the new turn (e.g. when forking). */
  existingAttachmentIds: string[]
  /** Marks this user message as the session goal while still creating a normal turn. */
  goal?: boolean
}

export interface InvocationTokenUsage {
  input: number
  output: number
  total: number
}

export interface ContextUsageBreakdownItem {
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
  tokenCount: number
  tokenText?: string
  percent?: number
  children?: ContextUsageBreakdownItem[]
}

export interface ContextUsageSummary {
  contextTokens: string
  contextLimit: string
  contextPercent: number
  contextTokenCount?: number
  contextLimitCount?: number
  breakdown?: ContextUsageBreakdownItem[]
  outputTokens?: string
  outputTokenCount?: number
  totalTokens?: string
  totalTokenCount?: number
  toolCalls?: number
  agentTime?: string
  modelName?: string
}

export interface InvocationDetailEvent {
  seq: number
  kind: string
  summary: string
  payload: Record<string, unknown>
  rawEvent?: Record<string, unknown>
  createdAt: string
  expanded?: boolean
}

export interface InvocationTimelineItem {
  seq: number
  kind: 'process_text' | 'tool' | 'artifact' | 'error' | 'cancelled' | 'raw_event'
  summary: string
  createdAt: string
  text?: string
  status?: 'running' | 'done' | 'failed'
  toolCallId?: string
  toolName?: string
  responseSummary?: string
  payload?: Record<string, unknown>
  rawEvent?: Record<string, unknown>
}

export interface InvocationStep {
  id: string
  title: string
  status: 'done' | 'running' | 'pending' | 'failed'
  detail: string
}

export interface AgentBackgroundRun {
  id: string
  kind: 'sub-agent' | 'command' | 'test' | 'index' | 'sync' | 'custom'
  title: string
  status: 'queued' | 'running' | 'waiting' | 'pending' | 'done' | 'failed' | 'cancelled'
  childSessionId?: string
  currentStep?: string
  artifactCount?: number
}

export interface AgentProgressItem {
  id: string
  title: string
  status: 'pending' | 'running' | 'done' | 'failed'
  detail?: string
  updatedAt?: string | null
  sourceTurnId?: string | null
}

export interface AgentBrowserEnvironment {
  success: boolean
  status: 'idle' | 'open' | 'running' | 'closed' | 'error' | string
  sessionId?: string | null
  url?: string | null
  title?: string | null
  lastAction?: string | null
  lastError?: string | null
  updatedAt?: string | null
  screenshotUrl?: string | null
  streamUrl?: string | null
  viewport?: { width?: number; height?: number } | null
}

export type BrowserInteraction =
  | { action: 'click'; x: number; y: number; button?: 'left' | 'right' | 'middle' }
  | { action: 'type'; text: string }
  | { action: 'key'; key: string }
  | { action: 'scroll'; delta_x?: number; delta_y?: number }
  | { action: 'resize'; width: number; height: number }

export interface FileChange {
  path: string
  additions: number
  deletions: number
}

export interface Artifact {
  id: string
  title: string
  subtitle: string
  filename?: string
  kind: 'markdown' | 'report' | 'plan' | string
  filetype?: string
  version?: number | null
  displayVersion?: number | null
  mimeType?: string | null
  content?: string
  loading?: boolean
  error?: string
  blocks?: ArtifactBlock[]
}

export type ArtifactBlock =
  | {
      type: 'heading'
      text: string
    }
  | {
      type: 'paragraph'
      text: string
    }
  | {
      type: 'code'
      language: string
      text: string
    }
  | {
      type: 'list'
      items: string[]
    }

export interface AgentSession {
  id: string
  agentId?: string
  agentRuntime?: string
  latestInvocationId?: string
  latestModelConfigId?: string
  title: string
  createdAt: string
  lastActivityAt?: string | null
  automatedTaskId?: string | null
  projectId: string
  projectRoot: string
  branch: string
  status: AgentSessionStatus
  attention?: SessionAttention
  starred?: boolean
  starredAt?: string | null
  archivedAt?: string | null
  unread?: boolean
  unreadAt?: string | null
  parentSessionId?: string | null
  parentTaskId?: string | null
  rootSessionId?: string | null
  forkedFromSessionId?: string | null
  forkedFromTurnId?: string | null
  forkedAt?: string | null
  breadcrumbs?: { id: string; label: string; title?: string | null }[]
  backgroundRuns?: AgentBackgroundRun[]
  progressItems?: AgentProgressItem[]
  browserEnvironment?: AgentBrowserEnvironment | null
  goal?: SessionGoal | null
  readOnly?: boolean
  contextUsageBreakdown?: ContextUsageBreakdownItem[]
  elapsed: string
  messages: AgentMessage[]
  detailEvents?: InvocationDetailEvent[]
  invocationSteps: InvocationStep[]
  artifacts: Artifact[]
  fileChanges: FileChange[]
}

export type AgentSessionStatus = 'idle' | 'queued' | 'running' | 'waiting_input' | 'done' | 'failed' | 'cancelled'
export type SessionAttention = 'success' | 'error'
