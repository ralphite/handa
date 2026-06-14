import type {
  BackendArtifact,
  BackendArtifactContent,
  BackendAgentCatalog,
  BackendAgentContextUsage,
  BackendAgentDefinition,
  BackendStep,
  BackendTurn,
  BackendProject,
  BackendProjectDelete,
  BackendProjectLauncherResult,
  BackendProjectLauncherTarget,
  BackendBackgroundRun,
  BackendSession,
  BackendSessionDelete,
  BackendSessionStar,
  BackendSessionDetail,
  BackendSettings,
  BackendSettingsUpdate,
  BackendBrowserEnvironment,
  BackendBrowserInteraction,
  BackendAutomatedTask,
  BackendAutomatedTaskDetail,
  BackendAutomatedTaskCreate,
  BackendAutomatedTaskUpdate,
  BackendAutomatedTaskDelete,
  BackendAutomatedTaskRun,
} from './types'
import { DEFAULT_AGENT_ID } from '../agentDefaults'

const API_BASE = import.meta.env.VITE_HANDA_API_BASE ?? '/api'

export class ApiRequestError extends Error {
  readonly status: number
  readonly body: string

  constructor(status: number, body: string, fallbackMessage: string) {
    super(readableErrorMessage(body) || fallbackMessage)
    this.name = 'ApiRequestError'
    this.status = status
    this.body = body
  }
}

export async function listTurns(sessionId?: string): Promise<BackendTurn[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return request(`/turns${query}`)
}

export async function listAgents(): Promise<BackendAgentDefinition[]> {
  return request('/agents')
}

export async function getAgentCatalog(): Promise<BackendAgentCatalog> {
  return request('/agents/catalog')
}

export async function getAgentContextUsage(
  agentId: string,
  projectId?: string,
): Promise<BackendAgentContextUsage> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return request(`/agents/${encodeURIComponent(agentId)}/context-usage${query}`)
}

export async function listSessions(
  projectId?: string,
  options: { includeArchived?: boolean; archived?: boolean } = {},
): Promise<BackendSession[]> {
  const params = new URLSearchParams()
  if (projectId) params.set('project_id', projectId)
  if (options.includeArchived) params.set('include_archived', 'true')
  if (options.archived !== undefined) params.set('archived', String(options.archived))
  const query = params.toString() ? `?${params.toString()}` : ''
  return request(`/sessions${query}`)
}

export async function renameSession(sessionId: string, title: string): Promise<BackendSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export async function forkSession(
  sessionId: string,
  sourceTurnId?: string,
  options: { includeSourceTurn?: boolean } = {},
): Promise<BackendSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/fork`, {
    method: 'POST',
    body: JSON.stringify({
      source_turn_id: sourceTurnId ?? null,
      include_source_turn: options.includeSourceTurn ?? true,
    }),
  })
}

export async function listProjects(): Promise<BackendProject[]> {
  return request('/projects')
}

export async function createProject(rootPath: string, name?: string): Promise<BackendProject> {
  return request('/projects', {
    method: 'POST',
    body: JSON.stringify({ root_path: rootPath, name }),
  })
}

export async function renameProject(projectId: string, name: string): Promise<BackendProject> {
  return request(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

export async function deleteProject(projectId: string): Promise<BackendProjectDelete> {
  return request(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
  })
}

export async function openProject(projectId: string): Promise<BackendProject> {
  return request(`/projects/${encodeURIComponent(projectId)}/open`, {
    method: 'POST',
  })
}

export async function launchProjectApp(
  projectId: string,
  target: BackendProjectLauncherTarget,
): Promise<BackendProjectLauncherResult> {
  return request(`/projects/${encodeURIComponent(projectId)}/launcher`, {
    method: 'POST',
    body: JSON.stringify({ target }),
  })
}

export function projectLauncherIconUrl(target: BackendProjectLauncherTarget): string {
  return `${API_BASE}/projects/launcher-icons/${encodeURIComponent(target)}`
}

export async function updateSessionStar(
  sessionId: string,
  starred: boolean,
): Promise<BackendSessionStar> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/star`, {
    method: 'PUT',
    body: JSON.stringify({ starred }),
  })
}

export async function updateSessionArchive(
  sessionId: string,
  archived: boolean,
): Promise<BackendSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/archive`, {
    method: 'PUT',
    body: JSON.stringify({ archived }),
  })
}

export async function updateSessionUnread(
  sessionId: string,
  unread: boolean,
): Promise<BackendSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/unread`, {
    method: 'PUT',
    body: JSON.stringify({ unread }),
  })
}

export async function deleteSession(sessionId: string): Promise<BackendSessionDelete> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
}

export async function getSettings(): Promise<BackendSettings> {
  return request('/settings')
}

export async function updateSettings(payload: BackendSettingsUpdate): Promise<BackendSettings> {
  return request('/settings', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function createTurn(
  inputText: string,
  projectId: string,
  agentId = DEFAULT_AGENT_ID,
  sessionId?: string,
  modelConfigId?: string,
  files: File[] = [],
  existingAttachmentIds: string[] = [],
): Promise<BackendTurn> {
  const form = new FormData()
  form.append('input_text', inputText)
  form.append('project_id', projectId)
  form.append('agent_id', agentId)
  if (modelConfigId) form.append('model_config_id', modelConfigId)
  if (sessionId) form.append('session_id', sessionId)
  if (existingAttachmentIds.length) {
    form.append('existing_attachment_ids', JSON.stringify(existingAttachmentIds))
  }
  for (const file of files) form.append('files', file, file.name)

  const response = await fetch(`${API_BASE}/turns`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw await requestError(response, `Request failed: ${response.status}`)
  }
  return response.json() as Promise<BackendTurn>
}

export async function rewriteSessionFromTurn(
  sessionId: string,
  sourceTurnId: string,
  inputText: string,
  modelConfigId?: string,
  files: File[] = [],
  existingAttachmentIds: string[] = [],
): Promise<BackendTurn> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('source_turn_id', sourceTurnId)
  form.append('input_text', inputText)
  if (modelConfigId) form.append('model_config_id', modelConfigId)
  if (existingAttachmentIds.length) {
    form.append('existing_attachment_ids', JSON.stringify(existingAttachmentIds))
  }
  for (const file of files) form.append('files', file, file.name)

  const response = await fetch(`${API_BASE}/turns/rewrite`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw await requestError(response, `Request failed: ${response.status}`)
  }
  return response.json() as Promise<BackendTurn>
}

export async function getTurn(turnId: string): Promise<BackendTurn> {
  return request(`/turns/${encodeURIComponent(turnId)}`)
}

export async function getSessionDetail(
  sessionId: string,
  options: { includeEvents?: boolean } = {},
): Promise<BackendSessionDetail> {
  const query = options.includeEvents === false ? '?include_events=false' : ''
  return request(`/sessions/${encodeURIComponent(sessionId)}/detail${query}`)
}

export async function refreshBrowserEnvironment(sessionId: string): Promise<BackendBrowserEnvironment> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/browser/refresh`, {
    method: 'POST',
  })
}

export async function sendBrowserInteraction(
  sessionId: string,
  payload: BackendBrowserInteraction,
): Promise<BackendBrowserEnvironment> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/browser/interactions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function terminateSessionTask(
  sessionId: string,
  taskId: string,
): Promise<BackendBackgroundRun> {
  return request(
    `/sessions/${encodeURIComponent(sessionId)}/tasks/${encodeURIComponent(taskId)}/terminate`,
    { method: 'POST' },
  )
}

export async function terminateTurn(turnId: string): Promise<BackendTurn> {
  return request(`/turns/${encodeURIComponent(turnId)}/terminate`, {
    method: 'POST',
  })
}

export async function retryTurn(turnId: string): Promise<BackendTurn> {
  return request(`/turns/${encodeURIComponent(turnId)}/retry`, {
    method: 'POST',
  })
}

export async function submitTurnUserInput(
  turnId: string,
  body: {
    request_id: string
    answers?: { id: string; selected: string[]; free_text?: string }[]
    cancelled?: boolean
  },
): Promise<BackendTurn> {
  return request(`/turns/${encodeURIComponent(turnId)}/user-input`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function listTurnSteps(
  turnId: string,
  afterSeq = 0,
): Promise<BackendStep[]> {
  return request(`/turns/${encodeURIComponent(turnId)}/steps?after_seq=${afterSeq}`)
}

export async function listSessionSteps(
  sessionId: string,
  afterSeq = 0,
): Promise<BackendStep[]> {
  return request(
    `/sessions/${encodeURIComponent(sessionId)}/steps?after_seq=${afterSeq}`,
  )
}

export async function listArtifacts(sessionId: string): Promise<BackendArtifact[]> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/artifacts`)
}

export async function readArtifact(
  sessionId: string,
  filename: string,
): Promise<BackendArtifactContent> {
  return request(
    `/sessions/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(filename)}`,
  )
}

export async function dictate(
  audio: Blob,
  options: { sessionId?: string; projectId?: string } = {},
): Promise<{ transcript: string }> {
  const form = new FormData()
  const ext = audio.type.includes('mp4') || audio.type.includes('m4a') ? 'm4a' : 'webm'
  form.append('audio', audio, `dictation.${ext}`)
  if (options.sessionId) form.append('session_id', options.sessionId)
  if (options.projectId) form.append('project_id', options.projectId)
  const response = await fetch(`${API_BASE}/dictate`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw await requestError(response, `Dictation failed: ${response.status}`)
  }
  return response.json() as Promise<{ transcript: string }>
}

export async function optimizePrompt(
  prompt: string,
  options: { sessionId?: string; projectId?: string } = {},
): Promise<{ optimized: string }> {
  return request('/optimize_prompt', {
    method: 'POST',
    body: JSON.stringify({
      prompt,
      session_id: options.sessionId,
      project_id: options.projectId,
    }),
  })
}

export async function listAutomatedTasks(projectId?: string): Promise<BackendAutomatedTask[]> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return request(`/automated-tasks${query}`)
}

export async function getAutomatedTask(taskId: string): Promise<BackendAutomatedTaskDetail> {
  return request(`/automated-tasks/${encodeURIComponent(taskId)}`)
}

export async function createAutomatedTask(
  payload: BackendAutomatedTaskCreate,
): Promise<BackendAutomatedTaskDetail> {
  return request('/automated-tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateAutomatedTask(
  taskId: string,
  payload: BackendAutomatedTaskUpdate,
): Promise<BackendAutomatedTaskDetail> {
  return request(`/automated-tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function setAutomatedTaskEnabled(
  taskId: string,
  enabled: boolean,
): Promise<BackendAutomatedTaskDetail> {
  const action = enabled ? 'enable' : 'disable'
  return request(`/automated-tasks/${encodeURIComponent(taskId)}/${action}`, {
    method: 'POST',
  })
}

export async function deleteAutomatedTask(taskId: string): Promise<BackendAutomatedTaskDelete> {
  return request(`/automated-tasks/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  })
}

export async function runAutomatedTaskNow(taskId: string): Promise<BackendAutomatedTaskRun> {
  return request(`/automated-tasks/${encodeURIComponent(taskId)}/run`, {
    method: 'POST',
  })
}

export async function listAutomatedTaskRuns(
  taskId: string,
  limit = 50,
): Promise<BackendAutomatedTaskRun[]> {
  return request(`/automated-tasks/${encodeURIComponent(taskId)}/runs?limit=${limit}`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'content-type': 'application/json',
    },
    ...init,
  })
  if (!response.ok) {
    throw await requestError(response, `Request failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function requestError(response: Response, fallbackMessage: string): Promise<ApiRequestError> {
  const message = await response.text()
  return new ApiRequestError(response.status, message, fallbackMessage)
}

function readableErrorMessage(body: string): string {
  const trimmed = body.trim()
  if (!trimmed) return ''
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown; message?: unknown; error?: unknown } | null
    if (!parsed || typeof parsed !== 'object') return trimmed
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map((item) => {
          if (item && typeof item === 'object' && 'msg' in item) return String(item.msg)
          return String(item)
        })
        .join('; ')
    }
    if (typeof parsed.message === 'string') return parsed.message
    if (typeof parsed.error === 'string') return parsed.error
  } catch {
    // Plain-text response bodies are already useful.
  }
  return trimmed
}
