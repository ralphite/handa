import { computed, ref } from 'vue'
import {
  ApiRequestError,
  createProject,
  createTurn,
  deleteProject,
  deleteSession,
  forkSession,
  getTurn,
  getSessionDetail,
  clearSessionGoal as clearSessionGoalRequest,
  launchProjectApp,
  listAgents,
  listArtifacts,
  listProjects,
  listTurnSteps,
  listTurns,
  listSessionSteps,
  listSessions,
  readArtifact,
  renameProject,
  renameSession,
  refreshBrowserEnvironment as refreshBrowserEnvironmentRequest,
  retryTurn as retryTurnRequest,
  rewriteSessionFromTurn,
  sendBrowserInteraction as sendBrowserInteractionRequest,
  submitTurnUserInput,
  terminateTurn,
  terminateSessionTask,
  updateSessionGoal as updateSessionGoalRequest,
  updateSessionArchive,
  updateSessionStar,
  updateSessionUnread,
} from '../api/client'
import type {
  BackendArtifact,
  BackendAgentDefinition,
  BackendBrowserEnvironment,
  BackendBrowserInteraction,
  BackendContextUsageBreakdownItem,
  BackendTurnAttachment,
  BackendBackgroundRun,
  BackendProgressItem,
  BackendProject,
  BackendTurn,
  BackendStep,
  BackendSession,
  BackendSessionDetail,
} from '../api/types'
import type {
  AgentBackgroundRun,
  AgentBrowserEnvironment,
  AgentMessage,
  AgentProgressItem,
  AgentSession,
  EditMessagePayload,
  Artifact,
  MessageAttachment,
  ProjectNavItem,
  InvocationDetailEvent,
  InvocationTimelineItem,
  InvocationTokenUsage,
  ContextUsageBreakdownItem,
  PendingUserInputRequest,
  UserInputOption,
  UserInputQuestion,
  UserInputSubmissionPayload,
} from '../types'
import { DEFAULT_AGENT_ID } from '../agentDefaults'
import { formatWorkDuration } from '../presenters/duration'
import { goalFromBackend } from '../presenters/sessionGoal'
import { toolResponsePayloadIndicatesFailedOutcome } from '../presenters/toolDisplay'
import { removeDuplicateFinalProcessText } from '../presenters/timelineDisplay'

const EMPTY_SESSION_ID = 'empty'
const SESSION_QUERY_PARAM = 'session_id'
const LEGACY_SESSION_QUERY_PARAM = 'thread_id'
const NEW_CHAT_QUERY_PARAM = 'new_chat'
const PROJECT_QUERY_PARAM = 'project_id'
const ARTIFACT_QUERY_PARAM = 'artifact'
const INITIAL_LOAD_RETRY_BASE_MS = 350
const INITIAL_LOAD_RETRY_MAX_MS = 2500
const INITIAL_LOAD_TRANSIENT_RETRY_COUNT = 3
const BACKEND_UNAVAILABLE_MESSAGE = 'Backend unavailable. Start or restart the Handa backend server, then retry.'
const DETAIL_POLL_SETTLE_MS = 3500
const INVOCATION_POLL_MS = 900
const DETAIL_POLL_MS = 1800
// Sessions created outside this page (handacli, another tab or device) only
// become visible through the list endpoints, so the sidebar re-pulls them on
// a slow cadence; the per-session pollers above cover already-known sessions.
const SESSION_LIST_POLL_MS = 5000

export function useChatSessions(options: { onActionError?: (message: string) => void } = {}) {
  const backendProjects = ref<BackendProject[]>([])
  const agentDefinitions = ref<BackendAgentDefinition[]>([])
  const sessions = ref<AgentSession[]>([])
  const activeSessionId = ref(EMPTY_SESSION_ID)
  const draftProjectId = ref('')
  const draftAgentId = ref(DEFAULT_AGENT_ID)
  const loading = ref(false)
  const error = ref('')
  const sendError = ref('')
  const retryingInitialLoad = ref(false)
  const userInputSubmitting = ref(false)
  const lastSeqByTurn = new Map<string, number>()
  const lastSeqBySession = new Map<string, number>()
  const timers = new Map<string, number>()
  const detailTimers = new Map<string, number>()
  const detailPollSettleUntil = new Map<string, number>()
  let sessionListTimer: number | null = null
  let sessionListRefreshInFlight = false
  const handledTerminalTurnIds = new Set<string>()
  let browserInteractionQueue: Promise<void> = Promise.resolve()
  let initialLoadRetryTimer: number | undefined
  let initialLoadRetryCount = 0
  let initialLoadRunId = 0

  const activeSessionRecord = computed(() => {
    return sessions.value.find((session) => session.id === activeSessionId.value) ?? null
  })

  const draftProject = computed(() => {
    return backendProjects.value.find((project) => project.id === draftProjectId.value) ?? null
  })

  const projectsLoading = computed(() => {
    return loading.value && backendProjects.value.length === 0 && sessions.value.length === 0
  })

  const emptySession = computed<AgentSession>(() => ({
    id: 'empty',
    agentId: draftAgentId.value,
    agentRuntime: agentDefinitionById(draftAgentId.value)?.runtime ?? 'native',
    title: draftProject.value
      ? 'New chat session'
      : projectsLoading.value || retryingInitialLoad.value
        ? 'Loading projects'
        : hasProjects.value
          ? 'Choose a project'
          : error.value
            ? 'Backend unavailable'
            : 'Add a project',
    createdAt: new Date().toISOString(),
    projectId: draftProject.value?.id ?? '',
    projectRoot: draftProject.value?.root_path ?? '',
    branch: 'main',
    status: 'idle',
    elapsed: '0s',
    messages: [
      {
        id: 'empty-help',
        role: 'assistant',
        createdAt: new Date().toISOString(),
        body: draftProject.value
          ? 'Enter a real development task, Handa will connect to the backend to run the native agent, and render the event stream here.'
          : projectsLoading.value || retryingInitialLoad.value
            ? 'Loading projects and recent chats. If the server just restarted, Handa will reconnect automatically.'
            : hasProjects.value
              ? 'Click the new button next to a project on the left to select it for this task.'
              : error.value
                ? 'Handa could not reach the backend. Start or restart the server, then use Retry to load projects and recent chats.'
                : 'First add a project on the left, so Handa knows where this task should run.',
      },
    ],
    detailEvents: [],
    invocationSteps: [],
    progressItems: [],
    browserEnvironment: null,
    goal: null,
    contextUsageBreakdown: [],
    artifacts: [],
    fileChanges: [],
  }))

  const activeSession = computed(() => {
    return activeSessionRecord.value ?? emptySession.value
  })

  const projects = computed<ProjectNavItem[]>(() =>
    backendProjects.value.map((project) => ({
      id: project.id,
      name: project.name,
      path: project.root_path,
      sessions: sessions.value
        .filter((session) =>
          session.projectId === project.id
          && !session.parentSessionId
          && !session.archivedAt,
        )
        .map((session) => ({
          id: session.id,
          title: session.title,
          createdAt: session.createdAt,
          lastActivityAt: session.lastActivityAt,
          automatedTaskId: session.automatedTaskId,
          forkedFromSessionId: session.forkedFromSessionId,
          status: session.status,
          waitingInput: sessionHasPendingInput(session),
          attention: session.attention,
          starred: session.starred,
          starredAt: session.starredAt,
          archivedAt: session.archivedAt,
          unread: session.unread,
          unreadAt: session.unreadAt,
        }))
        .sort(compareSessionSummary),
    })),
  )

  const archivedProjects = computed<ProjectNavItem[]>(() =>
    backendProjects.value
      .map((project) => ({
        id: project.id,
        name: project.name,
        path: project.root_path,
        sessions: sessions.value
          .filter((session) =>
            session.projectId === project.id
            && !session.parentSessionId
            && Boolean(session.archivedAt),
          )
          .map((session) => ({
            id: session.id,
            title: session.title,
            createdAt: session.createdAt,
            lastActivityAt: session.lastActivityAt,
            automatedTaskId: session.automatedTaskId,
            forkedFromSessionId: session.forkedFromSessionId,
            status: session.status,
            attention: session.attention,
            starred: session.starred,
            starredAt: session.starredAt,
            archivedAt: session.archivedAt,
            unread: session.unread,
            unreadAt: session.unreadAt,
          }))
          .sort(compareSessionSummary),
      }))
      .filter((project) => project.sessions.length > 0),
  )

  const archivedSessionCount = computed(() =>
    archivedProjects.value.reduce((total, project) => total + project.sessions.length, 0),
  )

  const hasProjects = computed(() => backendProjects.value.length > 0)

  function agentDefinitionById(agentId: string) {
    return agentDefinitions.value.find((agent) => agent.id === agentId)
  }

  async function loadInitial() {
    initialLoadRunId += 1
    const runId = initialLoadRunId
    initialLoadRetryCount = 0
    clearInitialLoadRetry()
    await loadInitialAttempt(runId)
  }

  async function loadInitialAttempt(runId: number) {
    loading.value = true
    error.value = ''
    try {
      await loadInitialOnce()
      if (runId !== initialLoadRunId) return
      retryingInitialLoad.value = false
      initialLoadRetryCount = 0
      loading.value = false
    } catch (exc) {
      if (runId !== initialLoadRunId) return
      if (isTransientInitialLoadError(exc) && initialLoadRetryCount < INITIAL_LOAD_TRANSIENT_RETRY_COUNT) {
        retryingInitialLoad.value = true
        error.value = ''
        scheduleInitialLoadRetry(runId)
        return
      }
      retryingInitialLoad.value = false
      error.value = initialLoadErrorMessage(exc)
      loading.value = false
    }
  }

  async function loadInitialOnce() {
    const [agents, projects] = await Promise.all([listAgents(), listProjects()])
    agentDefinitions.value = agents
    if (!agentDefinitions.value.some((agent) => agent.id === draftAgentId.value)) {
      draftAgentId.value = agentDefinitions.value[0]?.id ?? DEFAULT_AGENT_ID
    }
    backendProjects.value = projects
    draftProjectId.value = ''

    const projectById = new Map(backendProjects.value.map((project) => [project.id, project]))
    // Initial navigation only needs project and session summaries. Conversation
    // detail is loaded after the active session is known.
    const metas = await listSessions(undefined, { includeArchived: true })
    const ordered: AgentSession[] = []
    for (const meta of metas) {
      if (!meta.project_id) continue
      const project = projectById.get(meta.project_id)
      if (!project) continue
      const session = sessionFromMeta(meta, project)
      ordered.push(session)
    }
    sessions.value = ordered

    const requestedNewChat = readNewChatFromUrl()
    if (requestedNewChat.requested) {
      const projectId = resolveNewChatProjectId(requestedNewChat.projectId, backendProjects.value)
      activeSessionId.value = EMPTY_SESSION_ID
      draftProjectId.value = projectId
      if (projectId) writeNewChatToUrl(projectId, 'replace')
      return
    }

    const requestedSessionId = readSessionIdFromUrl()
    const requestedSession = requestedSessionId
      ? sessions.value.find((session) => session.id === requestedSessionId)
      : undefined
    if (requestedSession) {
      draftProjectId.value = ''
      setActiveSessionId(requestedSession.id, { history: 'replace' })
      await refreshSessionDetail(requestedSession.id)
    } else if (requestedSessionId) {
      const loaded = await loadSessionDetail(requestedSessionId, { includeArtifacts: true, includeEvents: true })
      if (loaded) {
        draftProjectId.value = ''
        setActiveSessionId(loaded.id, { history: 'replace' })
        syncDetailPolling(loaded)
      } else {
        const nextSessionId = sessions.value[0]?.id ?? EMPTY_SESSION_ID
        activeSessionId.value = nextSessionId
        if (nextSessionId !== EMPTY_SESSION_ID) draftProjectId.value = ''
        writeSessionIdToUrl(EMPTY_SESSION_ID, 'replace')
        if (nextSessionId !== EMPTY_SESSION_ID) await refreshSessionDetail(nextSessionId)
      }
    } else {
      const nextSessionId = sessions.value[0]?.id ?? EMPTY_SESSION_ID
      activeSessionId.value = nextSessionId
      if (nextSessionId !== EMPTY_SESSION_ID) draftProjectId.value = ''
      if (nextSessionId !== EMPTY_SESSION_ID) await refreshSessionDetail(nextSessionId)
    }
    syncLiveSessionPolling()
    startSessionListPolling()
  }

  function scheduleInitialLoadRetry(runId: number) {
    clearInitialLoadRetry()
    const delayMs = initialLoadRetryDelay(initialLoadRetryCount)
    initialLoadRetryCount += 1
    initialLoadRetryTimer = window.setTimeout(() => {
      initialLoadRetryTimer = undefined
      void loadInitialAttempt(runId)
    }, delayMs)
  }

  function clearInitialLoadRetry() {
    if (!initialLoadRetryTimer) return
    window.clearTimeout(initialLoadRetryTimer)
    initialLoadRetryTimer = undefined
  }

  async function addProject(rootPath: string, name?: string) {
    try {
      const project = await createProject(rootPath, name)
      backendProjects.value = [project, ...backendProjects.value.filter((item) => item.id !== project.id)]
      setActiveSessionId(EMPTY_SESSION_ID)
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  async function renameProjectDisplayName(projectId: string, name: string) {
    const trimmed = name.trim()
    if (!trimmed) return
    const project = backendProjects.value.find((item) => item.id === projectId)
    if (!project) return
    const previousName = project.name
    project.name = trimmed
    try {
      const result = await renameProject(projectId, trimmed)
      backendProjects.value = backendProjects.value.map((item) =>
        item.id === projectId ? result : item,
      )
    } catch (exc) {
      project.name = previousName
      notifyActionError(exc)
    }
  }

  async function removeProjectFromHanda(projectId: string) {
    const existing = backendProjects.value.find((item) => item.id === projectId)
    if (!existing) return

    const previousProjects = backendProjects.value
    const previousSessions = sessions.value
    const previousActiveSessionId = activeSessionId.value
    const previousDraftProjectId = draftProjectId.value

    backendProjects.value = backendProjects.value.filter((item) => item.id !== projectId)
    sessions.value = sessions.value.filter((item) => item.projectId !== projectId)

    const activeProjectRemoved =
      previousDraftProjectId === projectId ||
      previousSessions.some((item) => item.id === previousActiveSessionId && item.projectId === projectId)
    if (activeProjectRemoved) {
      const next = sessions.value.find((item) => !item.parentSessionId && !item.archivedAt)
      if (next) {
        draftProjectId.value = ''
        setActiveSessionId(next.id, { history: 'replace' })
      } else {
        activeSessionId.value = EMPTY_SESSION_ID
        draftProjectId.value = ''
        writeSessionIdToUrl(EMPTY_SESSION_ID, 'replace')
      }
    }

    try {
      await deleteProject(projectId)
    } catch (exc) {
      backendProjects.value = previousProjects
      sessions.value = previousSessions
      activeSessionId.value = previousActiveSessionId
      draftProjectId.value = previousDraftProjectId
      if (previousDraftProjectId) writeNewChatToUrl(previousDraftProjectId, 'replace')
      else writeSessionIdToUrl(previousActiveSessionId, 'replace')
      notifyActionError(exc)
    }
  }

  async function openProjectInFinder(projectId: string) {
    try {
      await launchProjectApp(projectId, 'finder')
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  function newSession(id?: string, options: { history?: 'push' | 'replace' } = {}) {
    const projectId = id || backendProjects.value[0]?.id || ''
    if (!projectId) {
      notifyActionError('Please add a project first.')
      return
    }
    activeSessionId.value = EMPTY_SESSION_ID
    draftProjectId.value = projectId
    writeNewChatToUrl(projectId, options.history ?? 'push')
  }

  function setDraftAgent(agentId: string) {
    if (!agentDefinitions.value.some((agent) => agent.id === agentId)) return
    draftAgentId.value = agentId
  }

  async function sendPrompt(
    payload: { prompt: string; files: File[]; existingAttachmentIds?: string[]; goal?: boolean },
    modelConfigId?: string,
  ) {
    const trimmed = payload.prompt.trim()
    const files = payload.files ?? []
    const existingAttachmentIds = payload.existingAttachmentIds ?? []
    if (!trimmed && files.length === 0 && existingAttachmentIds.length === 0) return
    const existing = activeSessionRecord.value
    const projectId = existing?.projectId ?? draftProjectId.value
    if (!projectId) {
      sendError.value = 'Please add a project first.'
      return
    }

    sendError.value = ''
    try {
      const reuseSessionId = existing?.id
      await startInvocation(
        { prompt: trimmed, files, existingAttachmentIds, goal: payload.goal },
        projectId,
        existing?.agentId ?? DEFAULT_AGENT_ID,
        reuseSessionId,
        modelConfigId,
        existing ?? undefined,
      )
    } catch (exc) {
      sendError.value = errorMessageFromUnknown(exc)
    }
  }

  async function sendNewChatPrompt(
    payload: { prompt: string; files: File[]; existingAttachmentIds?: string[]; goal?: boolean },
    projectId: string,
    modelConfigId?: string,
    agentId = draftAgentId.value,
  ) {
    const trimmed = payload.prompt.trim()
    const files = payload.files ?? []
    if (!trimmed && files.length === 0) return
    if (!projectId) {
      sendError.value = 'Please add a project first.'
      return
    }

    sendError.value = ''
    draftProjectId.value = projectId
    setDraftAgent(agentId)
    try {
      await startInvocation(
        {
          prompt: trimmed,
          files,
          existingAttachmentIds: payload.existingAttachmentIds ?? [],
          goal: payload.goal,
        },
        projectId,
        draftAgentId.value,
        undefined,
        modelConfigId,
      )
    } catch (exc) {
      sendError.value = errorMessageFromUnknown(exc)
    }
  }

  async function setActiveSessionGoal(text: string) {
    const session = activeSessionRecord.value
    if (!session || session.readOnly) return
    try {
      const goal = await updateSessionGoalRequest(session.id, text)
      session.goal = goalFromBackend(goal)
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  async function clearActiveSessionGoal() {
    const session = activeSessionRecord.value
    if (!session || session.readOnly) return
    try {
      const goal = await clearSessionGoalRequest(session.id)
      session.goal = goalFromBackend(goal)
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  async function startInvocation(
    payload: { prompt: string; files: File[]; existingAttachmentIds?: string[]; goal?: boolean },
    projectId: string,
    agentId: string,
    reuseSessionId?: string,
    modelConfigId?: string,
    existingSession?: AgentSession,
  ) {
    logInvocationStartMessages({
      payload,
      projectId,
      agentId,
      reuseSessionId,
      modelConfigId,
      existingSession,
    })
    const invocation = await createTurn(
      payload.prompt,
      projectId,
      agentId,
      reuseSessionId,
      modelConfigId,
      payload.files,
      payload.existingAttachmentIds ?? [],
      Boolean(payload.goal),
    )
    const project = backendProjects.value.find((item) => item.id === projectId)
    if (!project) return
    const existing = existingSession ?? sessions.value.find((item) => item.id === reuseSessionId)
    if (existing && reuseSessionId) {
      appendInvocationToSession(existing, invocation)
      if (payload.goal && payload.prompt.trim()) {
        existing.goal = {
          text: payload.prompt.trim(),
          status: 'active',
          createdAt: null,
          updatedAt: null,
        }
      }
    } else {
      const session = sessionFromInvocation(invocation, project, agentId)
      if (payload.goal && payload.prompt.trim()) {
        session.goal = {
          text: payload.prompt.trim(),
          status: 'active',
          createdAt: null,
          updatedAt: null,
        }
      }
      sessions.value = [session, ...sessions.value.filter((item) => item.id !== session.id)]
      backendProjects.value = [project, ...backendProjects.value.filter((item) => item.id !== project.id)]
      draftProjectId.value = ''
      setActiveSessionId(session.id)
    }
    pollInvocation(invocation.id)
  }

  function logInvocationStartMessages({
    payload,
    projectId,
    agentId,
    reuseSessionId,
    modelConfigId,
    existingSession,
  }: {
    payload: { prompt: string; files: File[]; existingAttachmentIds?: string[]; goal?: boolean }
    projectId: string
    agentId: string
    reuseSessionId?: string
    modelConfigId?: string
    existingSession?: AgentSession
  }) {
    const session = existingSession ?? sessions.value.find((item) => item.id === reuseSessionId) ?? null
    console.log('[Handa] invocation start messages', {
      sessionId: reuseSessionId ?? null,
      projectId,
      agentId,
      modelConfigId: modelConfigId ?? null,
      goal: Boolean(payload.goal),
      messages: (session?.messages ?? []).map((message, index) => ({
        index,
        id: message.id,
        role: message.role,
        turnId: message.turnId ?? null,
        invocationId: message.invocationId ?? null,
        status: message.status ?? null,
        body: message.body,
        attachments: (message.attachments ?? []).map((attachment) => ({
          id: attachment.id,
          filename: attachment.filename,
          mimeType: attachment.mimeType,
          byteCount: attachment.byteCount,
          kind: attachment.kind,
        })),
      })),
      outgoing: {
        role: 'user',
        body: payload.prompt,
        files: payload.files.map((file) => ({
          name: file.name,
          size: file.size,
          type: file.type,
        })),
        existingAttachmentIds: payload.existingAttachmentIds ?? [],
      },
    })
  }

  async function stopActiveInvocation() {
    const session = activeSession.value
    const invocationId = session.latestInvocationId
    if (!invocationId || !isLiveStatus(session.status)) return
    try {
      const invocation = await terminateTurn(invocationId)
      syncInvocation(session, invocation)
      await ingestEvents(invocationId, session)
      await refreshInvocation(invocationId)
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  async function cancelQueuedTurn(turnId: string) {
    const session = activeSessionRecord.value
    if (!session) return
    const assistant = session.messages.find(
      (message) => message.role === 'assistant' && message.turnId === turnId,
    )
    if (assistant && assistant.status !== 'queued') return
    try {
      const invocation = await terminateTurn(turnId)
      syncInvocation(session, invocation)
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  async function terminateBackgroundRun(sessionId: string, taskId: string) {
    try {
      await terminateSessionTask(sessionId, taskId)
      await refreshSessionDetail(sessionId)
    } catch (exc) {
      notifyActionError(exc)
    }
  }

  function appendInvocationToSession(session: AgentSession, invocation: BackendTurn) {
    const invocationStatus = statusFromInvocation(invocation.status)
    const deferStatusPromotion = session.status === 'running' && invocationStatus === 'queued'
    if (!deferStatusPromotion) {
      session.latestInvocationId = invocation.id
      session.status = invocationStatus
      session.elapsed = formatWorkDuration(invocationActiveSeconds(invocation))
      session.detailEvents = []
    }
    session.latestModelConfigId = invocation.model_config_id ?? session.latestModelConfigId
    session.createdAt = earliestIso(
      session.createdAt,
      createdAtFromSessionId(invocation.session_id, invocation.created_at),
    )
    session.lastActivityAt = invocation.updated_at ?? assistantCreatedAtFromInvocation(invocation)
    session.attention = undefined
    const invocationActive = invocationActiveSeconds(invocation)
    const invocationElapsed = formatWorkDuration(invocationActive)
    const messages: AgentMessage[] = []
    if (shouldShowInvocationInput(invocation)) {
      messages.push({
        id: `${invocation.id}-user`,
        role: 'user',
        turnId: invocation.id,
        body: invocation.input_text,
        createdAt: invocation.created_at,
        attachments: messageAttachments(invocation),
      })
    }
    messages.push({
      id: `${invocation.id}-assistant`,
      role: 'assistant',
      turnId: invocation.id,
      createdAt: assistantCreatedAtFromInvocation(invocation),
      invocationId: invocation.id,
      triggerKind: invocation.trigger_kind,
      systemRunLabel: invocation.system_run_label ?? undefined,
      elapsed: invocationElapsed,
      activeSeconds: invocationActive,
      status: invocationStatus,
      tokenUsage: tokenUsageFromInvocation(invocation),
      body: assistantBodyFromInvocation(invocation),
      detailEvents: [],
      timelineItems: [],
    })
    session.messages.push(...messages)
  }

  async function selectSession(id: string) {
    draftProjectId.value = ''
    const session = sessions.value.find((item) => item.id === id)
    if (session) {
      setActiveSessionId(id)
      session.attention = undefined
      if (session.unread) void setSessionUnread(session, false)
      void refreshSessionDetail(id)
      return
    }
    const loaded = await loadSessionDetail(id, { includeArtifacts: true, includeEvents: true })
    if (loaded) {
      setActiveSessionId(loaded.id)
      syncDetailPolling(loaded)
    }
  }

  async function toggleSessionStar(id: string) {
    const session = sessions.value.find((item) => item.id === id)
    if (!session) return

    const previousStarred = session.starred === true
    const previousStarredAt = session.starredAt ?? null
    const nextStarred = !previousStarred
    session.starred = nextStarred
    session.starredAt = nextStarred ? new Date().toISOString() : null

    try {
      const result = await updateSessionStar(session.id, nextStarred)
      session.starred = result.starred
      session.starredAt = result.starred_at ?? null
    } catch (exc) {
      session.starred = previousStarred
      session.starredAt = previousStarredAt
      notifyActionError(exc)
    }
  }

  async function renameSessionTitle(id: string, title: string) {
    const trimmed = title.trim()
    if (!trimmed) return
    const session = sessions.value.find((item) => item.id === id)
    if (!session) return
    const previousTitle = session.title
    session.title = trimmed
    try {
      const result = await renameSession(id, trimmed)
      session.title = result.title
    } catch (exc) {
      session.title = previousTitle
      notifyActionError(exc)
    }
  }

  async function markSessionUnread(id: string) {
    const session = sessions.value.find((item) => item.id === id)
    if (!session) return
    await setSessionUnread(session, true)
  }

  async function setSessionUnread(session: AgentSession, unread: boolean) {
    const previousUnread = session.unread === true
    const previousUnreadAt = session.unreadAt ?? null
    session.unread = unread
    session.unreadAt = unread ? new Date().toISOString() : null
    try {
      const result = await updateSessionUnread(session.id, unread)
      applySessionMeta(session, result)
    } catch (exc) {
      session.unread = previousUnread
      session.unreadAt = previousUnreadAt
      notifyActionError(exc)
    }
  }

  async function archiveSession(id: string) {
    const session = sessions.value.find((item) => item.id === id)
    if (!session) return
    await setSessionArchived(session, true)
  }

  async function unarchiveSession(id: string) {
    const session = sessions.value.find((item) => item.id === id)
    if (!session) return
    await setSessionArchived(session, false)
  }

  async function setSessionArchived(session: AgentSession, archived: boolean) {
    const previousArchivedAt = session.archivedAt ?? null
    session.archivedAt = archived ? new Date().toISOString() : null
    try {
      const result = await updateSessionArchive(session.id, archived)
      applySessionMeta(session, result)
    } catch (exc) {
      session.archivedAt = previousArchivedAt
      notifyActionError(exc)
    }
  }

  async function deleteSessionById(id: string) {
    const existing = sessions.value.find((item) => item.id === id)
    if (!existing) return
    const previousSessions = sessions.value
    const previousActiveSessionId = activeSessionId.value
    const previousDraftProjectId = draftProjectId.value
    sessions.value = sessions.value.filter((item) => item.id !== id)
    if (activeSessionId.value === id) {
      const next = sessions.value.find((item) => !item.parentSessionId && !item.archivedAt)
      activeSessionId.value = next?.id ?? EMPTY_SESSION_ID
      if (next) {
        draftProjectId.value = ''
        writeSessionIdToUrl(next.id, 'replace')
      } else {
        writeSessionIdToUrl(EMPTY_SESSION_ID, 'replace')
      }
    }
    try {
      await deleteSession(id)
    } catch (exc) {
      sessions.value = previousSessions
      activeSessionId.value = previousActiveSessionId
      draftProjectId.value = previousDraftProjectId
      writeSessionIdToUrl(previousActiveSessionId, 'replace')
      notifyActionError(exc)
    }
  }

  async function forkActiveSession(
    sourceTurnId?: string,
    options: { includeSourceTurn?: boolean } = {},
  ): Promise<string | undefined> {
    const source = activeSessionRecord.value
    if (!source || source.id === EMPTY_SESSION_ID) return undefined
    try {
      const meta = await forkSession(source.id, sourceTurnId, options)
      const project = backendProjects.value.find((item) => item.id === meta.project_id)
      if (project) {
        const session = sessionFromMeta(meta, project)
        sessions.value = [session, ...sessions.value.filter((item) => item.id !== session.id)]
        draftProjectId.value = ''
        setActiveSessionId(session.id)
        await refreshSessionDetail(session.id)
        await refreshArtifacts(session)
        return session.id
      }

      const loaded = await loadSessionDetail(meta.id, { includeArtifacts: true, includeEvents: true })
      if (loaded) {
        draftProjectId.value = ''
        setActiveSessionId(loaded.id)
        return loaded.id
      }
      return undefined
    } catch (exc) {
      notifyActionError(exc)
      return undefined
    }
  }

  function applyRewrittenInvocation(
    source: AgentSession,
    sourceTurnId: string,
    invocation: BackendTurn,
  ) {
    const sourceIndex = source.messages.findIndex((message) => messageSourceTurnId(message) === sourceTurnId)
    source.messages = sourceIndex >= 0 ? source.messages.slice(0, sourceIndex) : source.messages
    source.detailEvents = []
    source.invocationSteps = []
    source.progressItems = (source.progressItems ?? []).filter((item) =>
      !item.sourceTurnId || source.messages.some((message) => messageSourceTurnId(message) === item.sourceTurnId),
    )
    appendInvocationToSession(source, invocation)
  }

  async function editUserMessage(
    payload: EditMessagePayload,
    modelConfigId?: string,
  ) {
    const source = activeSessionRecord.value
    if (!source || source.id === EMPTY_SESSION_ID || source.readOnly) return
    if (isLiveStatus(source.status)) {
      sendError.value = ''
      notifyActionError('Stop the current run before editing a message.')
      return
    }
    const prompt = payload.prompt.trim()
    const files = payload.files ?? []
    const existingAttachmentIds = payload.existingAttachmentIds ?? []
    if (!prompt && files.length === 0 && existingAttachmentIds.length === 0) return

    sendError.value = ''
    try {
      const invocation = await rewriteSessionFromTurn(
        source.id,
        payload.sourceTurnId,
        prompt,
        modelConfigId,
        files,
        existingAttachmentIds,
      )
      applyRewrittenInvocation(source, payload.sourceTurnId, invocation)
      await refreshArtifacts(source).catch(() => undefined)
      pollInvocation(invocation.id)
    } catch (exc) {
      sendError.value = errorMessageFromUnknown(exc)
    }
  }

  async function retryTurn(turnId: string) {
    const source = activeSessionRecord.value
    if (!source || source.id === EMPTY_SESSION_ID || source.readOnly) return
    if (isLiveStatus(source.status)) {
      sendError.value = 'Stop the current run before retrying.'
      return
    }
    sendError.value = ''
    try {
      const invocation = await retryTurnRequest(turnId)
      applyRewrittenInvocation(source, turnId, invocation)
      await refreshArtifacts(source).catch(() => undefined)
      pollInvocation(invocation.id)
    } catch (exc) {
      sendError.value = errorMessageFromUnknown(exc)
    }
  }

  function syncSessionFromUrl() {
    void syncSessionFromUrlAsync()
  }

  async function syncSessionFromUrlAsync() {
    const requestedNewChat = readNewChatFromUrl()
    if (requestedNewChat.requested) {
      const projectId = resolveNewChatProjectId(requestedNewChat.projectId, backendProjects.value)
      activeSessionId.value = EMPTY_SESSION_ID
      draftProjectId.value = projectId
      if (projectId) writeNewChatToUrl(projectId, 'replace')
      return
    }

    const requestedSessionId = readSessionIdFromUrl()
    const session = requestedSessionId
      ? sessions.value.find((item) => item.id === requestedSessionId)
      : undefined
    if (session) {
      draftProjectId.value = ''
      activeSessionId.value = session.id
      session.attention = undefined
      void refreshSessionDetail(session.id)
      return
    }
    if (!requestedSessionId) {
      activeSessionId.value = EMPTY_SESSION_ID
      draftProjectId.value = ''
      return
    }
    const loaded = await loadSessionDetail(requestedSessionId, { includeArtifacts: true, includeEvents: true })
    if (loaded) {
      activeSessionId.value = loaded.id
      loaded.attention = undefined
      syncDetailPolling(loaded)
      return
    }
    writeSessionIdToUrl(activeSessionId.value, 'replace')
  }

  function setActiveSessionId(id: string, options: { history?: 'push' | 'replace' } = {}) {
    activeSessionId.value = id
    writeSessionIdToUrl(id, options.history ?? 'push')
  }

  function stopPolling() {
    initialLoadRunId += 1
    clearInitialLoadRetry()
    retryingInitialLoad.value = false
    loading.value = false
    for (const timer of timers.values()) {
      window.clearInterval(timer)
    }
    timers.clear()
    for (const timer of detailTimers.values()) {
      window.clearInterval(timer)
    }
    detailTimers.clear()
    detailPollSettleUntil.clear()
    if (sessionListTimer !== null) {
      window.clearInterval(sessionListTimer)
      sessionListTimer = null
    }
    document.removeEventListener('visibilitychange', handleSessionListVisibilityChange)
  }

  function pollInvocation(invocationId: string) {
    if (timers.has(invocationId)) return
    void refreshInvocation(invocationId)
    const timer = window.setInterval(() => {
      void refreshInvocation(invocationId)
    }, INVOCATION_POLL_MS)
    timers.set(invocationId, timer)
  }

  function pollSessionDetail(sessionId: string) {
    if (detailTimers.has(sessionId)) return
    const timer = window.setInterval(() => {
      void refreshSessionDetail(sessionId, { includeInvocations: false, includeArtifacts: false })
    }, DETAIL_POLL_MS)
    detailTimers.set(sessionId, timer)
  }

  function stopSessionDetailPolling(sessionId: string) {
    const timer = detailTimers.get(sessionId)
    if (!timer) return
    window.clearInterval(timer)
    detailTimers.delete(sessionId)
  }

  function syncLiveSessionPolling() {
    for (const session of sessions.value) {
      if (session.id === activeSessionId.value) continue
      if (!isLiveStatus(session.status)) continue
      void refreshSessionInvocations(session, { includeEvents: false })
    }
  }

  function startSessionListPolling() {
    if (sessionListTimer !== null) return
    sessionListTimer = window.setInterval(() => {
      if (document.hidden) return
      void refreshSessionList()
    }, SESSION_LIST_POLL_MS)
    document.addEventListener('visibilitychange', handleSessionListVisibilityChange)
  }

  function handleSessionListVisibilityChange() {
    if (!document.hidden) void refreshSessionList()
  }

  async function refreshSessionList() {
    if (sessionListRefreshInFlight) return
    sessionListRefreshInFlight = true
    try {
      const [projects, metas] = await Promise.all([
        listProjects(),
        listSessions(undefined, { includeArchived: true }),
      ])
      if (JSON.stringify(projects) !== JSON.stringify(backendProjects.value)) {
        backendProjects.value = projects
      }
      mergeSessionSummaries(metas)
      syncLiveSessionPolling()
    } catch {
      // Keep the last sidebar state; the next tick retries.
    } finally {
      sessionListRefreshInFlight = false
    }
  }

  function mergeSessionSummaries(metas: BackendSession[]) {
    const projectById = new Map(backendProjects.value.map((project) => [project.id, project]))
    const byId = new Map(sessions.value.map((session) => [session.id, session]))
    const added: AgentSession[] = []
    for (const meta of metas) {
      if (!meta.project_id) continue
      const project = projectById.get(meta.project_id)
      if (!project) continue
      const existing = byId.get(meta.id)
      if (existing) {
        applySessionListMeta(existing, meta, project)
      } else {
        added.push(sessionFromMeta(meta, project))
      }
    }
    // Sessions missing from the response are kept: optimistic local sessions
    // may not be listed yet, and stale leftovers resolve on the next reload.
    if (added.length) {
      sessions.value = [...added, ...sessions.value]
    }
  }

  function applySessionListMeta(session: AgentSession, meta: BackendSession, project: BackendProject) {
    session.title = meta.title
    session.lastActivityAt = meta.updated_at ?? meta.created_at
    session.projectId = project.id
    session.projectRoot = project.root_path
    session.automatedTaskId = meta.automated_task_id ?? null
    session.forkedFromSessionId = meta.forked_from_session_id ?? null
    session.starred = meta.starred
    session.starredAt = meta.starred_at ?? null
    session.archivedAt = meta.archived_at ?? null
    if (session.id === activeSessionId.value) return
    // The active session's status and read state are owned by its own faster
    // pollers and local read-marking; a stale list response must not flap them.
    session.status = statusFromSessionDetail(meta.status)
    session.unread = meta.unread
    session.unreadAt = meta.unread_at ?? null
  }

  async function loadSessionDetail(
    sessionId: string,
    options: { includeArtifacts?: boolean; includeEvents?: boolean } = {},
  ): Promise<AgentSession | null> {
    try {
      const detail = await getSessionDetail(sessionId, { includeEvents: shouldIncludeSessionDetailEvents(sessionId) })
      const session = await upsertSessionFromDetail(detail)
      await refreshSessionInvocations(session, { includeEvents: options.includeEvents }).catch(() => false)
      if (options.includeArtifacts ?? (session.id === activeSessionId.value)) {
        await refreshArtifacts(session)
      }
      syncDetailPolling(session)
      return session
    } catch {
      return null
    }
  }

  async function refreshSessionDetail(
    sessionId: string,
    options: { includeInvocations?: boolean; includeArtifacts?: boolean } = {},
  ) {
    const previous = sessions.value.find((item) => item.id === sessionId)
    const hadLiveBackgroundRun = hasLiveBackgroundRun(previous)
    try {
      const detail = await getSessionDetail(sessionId, { includeEvents: shouldIncludeSessionDetailEvents(sessionId) })
      const session = await upsertSessionFromDetail(detail)
      let artifactsChanged = false
      if (options.includeInvocations !== false) {
        artifactsChanged = await refreshSessionInvocations(session).catch(() => false)
      }
      if ((options.includeArtifacts ?? (session.id === activeSessionId.value)) || (options.includeArtifacts !== false && artifactsChanged)) {
        await refreshArtifacts(session)
      }
      if (hadLiveBackgroundRun && !hasLiveBackgroundRun(session)) {
        detailPollSettleUntil.set(session.id, Date.now() + DETAIL_POLL_SETTLE_MS)
      }
      syncDetailPolling(session)
    } catch {
      stopSessionDetailPolling(sessionId)
    }
  }

  async function refreshActiveBrowserEnvironment(options: { quiet?: boolean } = {}) {
    const session = activeSessionRecord.value
    if (!session?.browserEnvironment || session.readOnly) return
    const browserSessionId = session.browserEnvironment.sessionId ?? session.id
    try {
      const browser = await refreshBrowserEnvironmentRequest(browserSessionId)
      session.browserEnvironment = browserEnvironmentFromDetail(browser)
    } catch (exc) {
      if (!options.quiet) notifyActionError(exc)
    }
  }

  async function sendBrowserInteraction(payload: BackendBrowserInteraction) {
    const run = async () => {
      await sendBrowserInteractionNow(payload)
    }
    browserInteractionQueue = browserInteractionQueue.then(run, run)
    await browserInteractionQueue
  }

  async function sendBrowserInteractionNow(payload: BackendBrowserInteraction) {
    const session = activeSessionRecord.value
    if (!session?.browserEnvironment || session.readOnly) return
    const browserSessionId = session.browserEnvironment.sessionId ?? session.id
    try {
      const browser = await sendBrowserInteractionRequest(browserSessionId, payload)
      session.browserEnvironment = browserEnvironmentFromDetail(browser)
      detailPollSettleUntil.set(session.id, Date.now() + DETAIL_POLL_SETTLE_MS)
      syncDetailPolling(session)
    } catch (exc) {
      notifyActionError(exc)
      void refreshSessionDetail(session.id, { includeInvocations: false, includeArtifacts: false })
    }
  }

  function shouldIncludeSessionDetailEvents(sessionId: string) {
    const existing = sessions.value.find((item) => item.id === sessionId)
    return existing ? Boolean(existing.parentSessionId) : true
  }

  async function refreshInvocation(invocationId: string) {
    const invocation = await getTurn(invocationId)
    const session = sessionForInvocation(invocationId)
    if (!session) return
    syncInvocation(session, invocation)
    const isActiveSession = session.id === activeSessionId.value
    const artifactsChanged = isActiveSession ? await ingestEvents(invocationId, session) : false
    if (isActiveSession) {
      if (!detailTimers.has(session.id)) {
        await refreshSessionDetail(session.id, { includeInvocations: false, includeArtifacts: false })
      }
      if (artifactsChanged) await refreshArtifacts(session)
    }
    if (
      invocation.status === 'completed' ||
      invocation.status === 'failed' ||
      invocation.status === 'cancelled'
    ) {
      const timer = timers.get(invocationId)
      if (timer) window.clearInterval(timer)
      timers.delete(invocationId)
      if (handledTerminalTurnIds.has(invocationId)) return
      handledTerminalTurnIds.add(invocationId)
      if (isActiveSession) await refreshArtifacts(session)
    }
  }

  async function ingestEvents(invocationId: string, session: AgentSession): Promise<boolean> {
    const afterSeq = lastSeqByTurn.get(invocationId) ?? 0
    const events = await listTurnSteps(invocationId, afterSeq)
    return ingestEventList(events, session, { updateSessionCursor: false })
  }

  async function ingestSessionEvents(session: AgentSession): Promise<boolean> {
    const afterSeq = lastSeqBySession.get(session.id) ?? 0
    const events = await listSessionSteps(session.id, afterSeq)
    return ingestEventList(events, session, { updateSessionCursor: true })
  }

  function ingestEventList(
    events: BackendStep[],
    session: AgentSession,
    options: { updateSessionCursor: boolean },
  ): boolean {
    let artifactsChanged = false
    for (const event of events) {
      const sessionSeq = typeof event.session_seq === 'number' ? event.session_seq : undefined
      if (
        options.updateSessionCursor &&
        sessionSeq !== undefined &&
        sessionSeq <= (lastSeqBySession.get(session.id) ?? 0)
      ) continue
      if (event.seq <= (lastSeqByTurn.get(event.turn_id) ?? 0)) continue
      applyEvent(session, event, event.turn_id)
      if (stepHasArtifactDelta(event)) artifactsChanged = true
      lastSeqByTurn.set(event.turn_id, event.seq)
      if (options.updateSessionCursor && sessionSeq !== undefined) {
        lastSeqBySession.set(session.id, Math.max(lastSeqBySession.get(session.id) ?? 0, sessionSeq))
      }
    }
    return artifactsChanged
  }

  async function refreshArtifacts(session: AgentSession) {
    const summaries = await listArtifacts(session.id)
    session.artifacts = summaries.map((artifact) => artifactFromSummary(artifact, session.artifacts))
  }

  async function refreshSessionInvocations(
    session: AgentSession,
    options: { includeEvents?: boolean } = {},
  ): Promise<boolean> {
    if (session.readOnly) return false
    const includeEvents = options.includeEvents ?? session.id === activeSessionId.value
    const invocations = await listTurns(session.id)
    let artifactsChanged = false
    const knownInvocationIds = new Set(
      session.messages
        .map((message) => message.invocationId)
        .filter((id): id is string => typeof id === 'string' && !id.startsWith('session:')),
    )
    for (const invocation of invocations) {
      if (knownInvocationIds.has(invocation.id)) {
        syncInvocation(session, invocation)
      } else if (!isTurnDismissedFromQueue(invocation)) {
        appendInvocationToSession(session, invocation)
        knownInvocationIds.add(invocation.id)
      }
      if (
        invocation.status === 'queued' ||
        invocation.status === 'running' ||
        invocation.status === 'waiting_input'
      ) {
        pollInvocation(invocation.id)
      }
    }
    if (includeEvents) {
      artifactsChanged = await ingestSessionEvents(session)
    }
    return artifactsChanged && session.id === activeSessionId.value
  }

  async function upsertSessionFromDetail(detail: BackendSessionDetail): Promise<AgentSession> {
    const existing = sessions.value.find((item) => item.id === detail.id)
    const session = existing ?? sessionFromDetail(detail)
    applySessionDetail(session, detail)
    if (!existing) {
      sessions.value = [session, ...sessions.value.filter((item) => item.id !== session.id)]
    }
    return session
  }

  function applySessionDetail(session: AgentSession, detail: BackendSessionDetail) {
    session.title = detail.title
    session.agentId = detail.agent_id
    session.agentRuntime = detail.agent_runtime ?? 'native'
    session.automatedTaskId = detail.automated_task_id ?? null
    session.createdAt = detail.created_at
    session.lastActivityAt = detail.updated_at ?? detail.created_at
    session.projectId = detail.project_id ?? session.projectId
    session.projectRoot = detail.project_root ?? session.projectRoot
    session.status = statusFromSessionDetail(detail.status)
    session.elapsed = sessionDetailElapsed(detail, session.status)
    session.parentSessionId = detail.parent_session_id ?? null
    session.parentTaskId = detail.parent_task_id ?? null
    session.rootSessionId = detail.root_session_id ?? detail.id
    session.forkedFromSessionId = detail.forked_from_session_id ?? null
    session.forkedFromTurnId = detail.forked_from_turn_id ?? null
    session.forkedAt = detail.forked_at ?? null
    session.breadcrumbs = detail.breadcrumbs.map((crumb) => ({
      id: crumb.id,
      label: crumb.label,
      title: crumb.title ?? undefined,
    }))
    session.backgroundRuns = detail.background_runs.map(backgroundRunFromDetail)
    session.progressItems = detail.progress_items.map(progressItemFromDetail)
    session.browserEnvironment = browserEnvironmentFromDetail(detail.browser_environment)
    session.contextUsageBreakdown = contextUsageBreakdownFromDetail(detail)
    session.goal = goalFromBackend(detail.goal)
    session.readOnly = Boolean(detail.parent_session_id)

    if (detail.parent_session_id) {
      const rebuilt = sessionFromDetail(detail)
      session.latestInvocationId = rebuilt.latestInvocationId
      session.messages = rebuilt.messages
      session.detailEvents = rebuilt.detailEvents
      session.invocationSteps = rebuilt.invocationSteps
    }
  }

  function applySessionMeta(session: AgentSession, meta: BackendSession) {
    session.title = meta.title
    session.agentId = meta.agent_id
    session.agentRuntime = meta.agent_runtime ?? 'native'
    session.automatedTaskId = meta.automated_task_id ?? null
    session.createdAt = meta.created_at
    session.lastActivityAt = meta.updated_at ?? meta.created_at
    session.projectId = meta.project_id ?? session.projectId
    session.status = statusFromSessionDetail(meta.status)
    session.parentSessionId = meta.parent_session_id ?? null
    session.forkedFromSessionId = meta.forked_from_session_id ?? null
    session.forkedFromTurnId = meta.forked_from_turn_id ?? null
    session.forkedAt = meta.forked_at ?? null
    session.starred = meta.starred
    session.starredAt = meta.starred_at ?? null
    session.archivedAt = meta.archived_at ?? null
    session.unread = meta.unread
    session.unreadAt = meta.unread_at ?? null
  }

  function syncDetailPolling(session: AgentSession) {
    const isActiveSession = session.id === activeSessionId.value
    const shouldSettlePoll = (detailPollSettleUntil.get(session.id) ?? 0) > Date.now()
    if (
      isActiveSession &&
      ((session.readOnly && isLiveStatus(session.status)) || hasLiveBackgroundRun(session) || shouldSettlePoll)
    ) {
      pollSessionDetail(session.id)
    } else {
      detailPollSettleUntil.delete(session.id)
      stopSessionDetailPolling(session.id)
    }
  }

  async function loadArtifactContent(artifactId: string) {
    const session = activeSessionRecord.value
    const artifact = session?.artifacts.find((item) => item.id === artifactId)
    if (!session || !artifact?.filename) return
    artifact.loading = true
    artifact.error = ''
    artifact.content = ''
    try {
      const content = await readArtifact(session.id, artifact.filename)
      artifact.mimeType = artifact.mimeType ?? content.mime_type ?? null
      artifact.content = content.text ?? metadataText(content)
    } catch (exc) {
      artifact.error = errorMessageFromUnknown(exc)
      artifact.content = ''
    } finally {
      artifact.loading = false
    }
  }

  function artifactFromSummary(artifact: BackendArtifact, existingArtifacts: Artifact[]): Artifact {
    const existing = existingArtifacts.find((item) => item.id === artifact.id || item.filename === artifact.filename)
    return {
      id: artifact.id,
      filename: artifact.filename,
      title: artifact.title,
      subtitle: `${artifact.kind} · ${artifact.filetype} · v${artifact.display_version ?? 1}`,
      kind: artifact.kind,
      filetype: artifact.filetype,
      version: artifact.version,
      displayVersion: artifact.display_version,
      mimeType: artifact.mime_type ?? existing?.mimeType ?? null,
      content: existing?.content,
      loading: existing?.loading,
      error: existing?.error,
      blocks: existing?.blocks ?? [],
    }
  }

  function sessionFromMeta(meta: BackendSession, project: BackendProject): AgentSession {
    return {
      id: meta.id,
      agentId: meta.agent_id,
      agentRuntime: meta.agent_runtime ?? 'native',
      automatedTaskId: meta.automated_task_id ?? null,
      title: meta.title,
      createdAt: meta.created_at,
      lastActivityAt: meta.updated_at ?? meta.created_at,
      projectId: project.id,
      projectRoot: project.root_path,
      branch: 'main',
      status: statusFromSessionDetail(meta.status),
      starred: meta.starred,
      starredAt: meta.starred_at ?? null,
      archivedAt: meta.archived_at ?? null,
      unread: meta.unread,
      unreadAt: meta.unread_at ?? null,
      parentSessionId: meta.parent_session_id ?? null,
      forkedFromSessionId: meta.forked_from_session_id ?? null,
      forkedFromTurnId: meta.forked_from_turn_id ?? null,
      forkedAt: meta.forked_at ?? null,
      elapsed: '',
      messages: [],
      detailEvents: [],
      invocationSteps: [],
      backgroundRuns: [],
      progressItems: [],
      browserEnvironment: null,
      goal: null,
      contextUsageBreakdown: [],
      artifacts: [],
      fileChanges: [],
    }
  }

  function sessionFromInvocation(invocation: BackendTurn, project: BackendProject, agentId: string): AgentSession {
    const status = statusFromInvocation(invocation.status)
    const activeSeconds = invocationActiveSeconds(invocation)
    const elapsedText = formatWorkDuration(activeSeconds)
    const definition = agentDefinitionById(agentId)
    return {
      id: invocation.session_id,
      agentId,
      agentRuntime: definition?.runtime ?? 'native',
      latestInvocationId: invocation.id,
      latestModelConfigId: invocation.model_config_id ?? undefined,
      title: titleFromInvocation(invocation),
      createdAt: createdAtFromSessionId(invocation.session_id, invocation.created_at),
      lastActivityAt: invocation.updated_at ?? assistantCreatedAtFromInvocation(invocation),
      projectId: project.id,
      projectRoot: project.root_path,
      branch: 'main',
      status,
      starred: false,
      starredAt: null,
      archivedAt: null,
      unread: false,
      unreadAt: null,
      elapsed: elapsedText,
      messages: [
        ...(shouldShowInvocationInput(invocation) ? [{
          id: `${invocation.id}-user`,
          role: 'user',
          turnId: invocation.id,
          body: invocation.input_text,
          createdAt: invocation.created_at,
          attachments: messageAttachments(invocation),
        } as AgentMessage] : []),
        {
          id: `${invocation.id}-assistant`,
          role: 'assistant',
          turnId: invocation.id,
          createdAt: assistantCreatedAtFromInvocation(invocation),
          invocationId: invocation.id,
          triggerKind: invocation.trigger_kind,
          systemRunLabel: invocation.system_run_label ?? undefined,
          elapsed: elapsedText,
          activeSeconds,
          status,
          tokenUsage: tokenUsageFromInvocation(invocation),
          body: assistantBodyFromInvocation(invocation),
          detailEvents: [],
          timelineItems: [],
        },
      ],
      detailEvents: [],
      invocationSteps: [],
      backgroundRuns: [],
      progressItems: [],
      browserEnvironment: null,
      goal: null,
      contextUsageBreakdown: [],
      artifacts: [],
      fileChanges: [],
    }
  }

  function sessionFromDetail(detail: BackendSessionDetail): AgentSession {
    const status = statusFromSessionDetail(detail.status)
    const syntheticInvocationId = `session:${detail.id}`
    const messages: AgentMessage[] = []
    if (detail.prompt) {
      messages.push({
        id: `${syntheticInvocationId}-user`,
        role: 'user',
        body: detail.prompt,
        createdAt: sessionDetailStart(detail),
      })
    }
    messages.push({
      id: `${syntheticInvocationId}-assistant`,
      role: 'assistant',
      body: '',
      createdAt: sessionDetailEnd(detail, status) ?? sessionDetailStart(detail),
      invocationId: syntheticInvocationId,
      elapsed: sessionDetailElapsed(detail, status),
      status,
      tokenUsage: tokenUsageFromSessionDetail(detail),
      detailEvents: [],
      timelineItems: [],
    })

    const session: AgentSession = {
      id: detail.id,
      agentId: detail.agent_id,
      agentRuntime: detail.agent_runtime ?? 'native',
      automatedTaskId: detail.automated_task_id ?? null,
      latestInvocationId: syntheticInvocationId,
      title: detail.title,
      createdAt: detail.created_at,
      lastActivityAt: detail.updated_at ?? detail.created_at,
      projectId: detail.project_id ?? '',
      projectRoot: detail.project_root ?? '',
      branch: 'main',
      status,
      archivedAt: null,
      unread: false,
      unreadAt: null,
      parentSessionId: detail.parent_session_id ?? null,
      parentTaskId: detail.parent_task_id ?? null,
      rootSessionId: detail.root_session_id ?? detail.id,
      forkedFromSessionId: detail.forked_from_session_id ?? null,
      forkedFromTurnId: detail.forked_from_turn_id ?? null,
      forkedAt: detail.forked_at ?? null,
      breadcrumbs: detail.breadcrumbs.map((crumb) => ({
        id: crumb.id,
        label: crumb.label,
        title: crumb.title ?? undefined,
      })),
      backgroundRuns: detail.background_runs.map(backgroundRunFromDetail),
      progressItems: detail.progress_items.map(progressItemFromDetail),
      browserEnvironment: browserEnvironmentFromDetail(detail.browser_environment),
      goal: goalFromBackend(detail.goal),
      contextUsageBreakdown: contextUsageBreakdownFromDetail(detail),
      readOnly: Boolean(detail.parent_session_id),
      elapsed: sessionDetailElapsed(detail, status),
      messages,
      detailEvents: [],
      invocationSteps: [],
      artifacts: [],
      fileChanges: [],
    }

    for (const event of detail.steps) {
      applyEvent(session, event, syntheticInvocationId)
    }
    return session
  }

  function syncInvocation(session: AgentSession, invocation: BackendTurn) {
    const previousStatus = session.status
    const nextStatus = statusFromInvocation(invocation.status)
    session.latestModelConfigId = invocation.model_config_id ?? session.latestModelConfigId
    if (isTurnDismissedFromQueue(invocation)) {
      // The turn was cancelled while still queued — it never ran, so drop it
      // from the chat entirely instead of leaving a cancelled bubble.
      session.messages = session.messages.filter((message) => message.turnId !== invocation.id)
    }
    const assistant = assistantMessageFor(session, invocation.id)
    if (assistant) {
      assistant.activeSeconds = invocationActiveSeconds(invocation)
      assistant.elapsed = formatWorkDuration(assistant.activeSeconds)
      assistant.status = nextStatus
      assistant.triggerKind = invocation.trigger_kind
      assistant.systemRunLabel = invocation.system_run_label ?? undefined
      assistant.tokenUsage = tokenUsageFromInvocation(invocation)
      if (invocation.final_text) {
        if (!assistant.body) assistant.body = invocation.final_text
        removeDuplicateFinalProcessText(assistant, invocation.final_text)
      }
      if (invocation.status === 'failed') assistant.body ||= failedInvocationBody(invocation.error_message)
      if (invocation.status === 'cancelled' && !assistant.body.includes('Invocation terminated.')) {
        assistant.body = assistant.body.trim()
          ? `${assistant.body.trim()}\n\nInvocation terminated.`
          : 'Invocation terminated.'
      }
    }
    // Each live turn has its own poll timer, so several pollers write session
    // state concurrently. Last-writer-wins makes status flip-flop every tick
    // (e.g. waiting_input turn + queued follow-up turn), which re-toggles the
    // stop button, sidebar indicator, and streaming layout. Resolve the owning
    // turn the same way the backend does: newest running turn, else newest turn.
    const owner = sessionStatusOwner(session)
    if (!owner || owner.invocationId === invocation.id) {
      session.status = nextStatus
      session.latestInvocationId = invocation.id
      session.elapsed = formatWorkDuration(invocationActiveSeconds(invocation))
    } else {
      session.status = owner.status
      session.latestInvocationId = owner.invocationId
      if (owner.elapsed) session.elapsed = owner.elapsed
    }
    if (isLiveStatus(previousStatus) && isTerminalStatus(session.status)) {
      session.attention =
        session.id === activeSessionId.value
          ? undefined
          : session.status === 'failed'
            ? 'error'
            : session.status === 'cancelled'
              ? undefined
              : 'success'
    }
  }

  function sessionStatusOwner(
    session: AgentSession,
  ): { invocationId: string; status: AgentSession['status']; elapsed?: string } | null {
    let lastTurn: AgentMessage | undefined
    let lastRunning: AgentMessage | undefined
    for (const message of session.messages) {
      if (message.role !== 'assistant') continue
      if (!message.invocationId || message.invocationId.startsWith('session:') || !message.status) continue
      lastTurn = message
      if (message.status === 'running') lastRunning = message
    }
    const owner = lastRunning ?? lastTurn
    if (!owner?.invocationId || !owner.status) return null
    return { invocationId: owner.invocationId, status: owner.status, elapsed: owner.elapsed }
  }

  function applyEvent(session: AgentSession, event: BackendStep, invocationId: string) {
    if (event.kind === 'model_event') return

    if (event.kind === 'agent_text' || event.kind === 'agent_text_delta') {
      if (isUserAuthoredEvent(event)) return
      const assistant = assistantMessageFor(session, invocationId)
      const text = typeof event.payload.text === 'string' ? event.payload.text : ''
      if (assistant && text) {
        if (!assistant.body) assistant.createdAt = event.created_at
        const final = event.payload.final === true
        if (final) {
          assistant.body = text
          removeDuplicateFinalProcessText(assistant, text)
        } else {
          appendProcessTextItem(assistant, event, text)
        }
      }
      return
    }

    if (event.kind === 'invocation_cancelled') {
      const assistant = assistantMessageFor(session, invocationId)
      if (assistant && !assistant.body.includes('Invocation terminated.')) {
        assistant.body = assistant.body.trim()
          ? `${assistant.body.trim()}\n\nInvocation terminated.`
          : 'Invocation terminated.'
      }
    }

    if (event.kind === 'user_input_requested') {
      const assistant = assistantMessageFor(session, invocationId)
      const pending = pendingUserInputFromPayload(event.payload, invocationId)
      if (assistant && pending) assistant.pendingUserInput = pending
    }

    if (event.kind === 'user_input_submitted') {
      const assistant = assistantMessageFor(session, invocationId)
      if (assistant) assistant.pendingUserInput = undefined
    }

    const detail: InvocationDetailEvent = {
      seq: event.seq,
      kind: event.kind,
      summary: event.summary,
      payload: event.payload,
      rawEvent: event.raw_event,
      createdAt: event.created_at,
    }
    const assistant = assistantMessageFor(session, invocationId)
    if (assistant) {
      if (event.kind === 'tool_call' || event.kind === 'tool_response') {
        upsertToolTimelineItem(assistant, detail)
        if (!isDuplicateDetailEvent(assistant, detail)) {
          assistant.detailEvents = [...(assistant.detailEvents ?? []), detail]
          session.detailEvents = assistant.detailEvents
        }
      } else {
        const timelineItem = timelineItemFromDetail(detail)
        if (timelineItem.kind !== 'raw_event') {
          assistant.timelineItems = [...(assistant.timelineItems ?? []), timelineItem]
        }
        assistant.detailEvents = [...(assistant.detailEvents ?? []), detail]
        session.detailEvents = assistant.detailEvents
      }
    } else {
      session.detailEvents = [...(session.detailEvents ?? []), detail]
    }
  }

  function sessionForInvocation(invocationId: string) {
    return sessions.value.find((session) =>
      session.messages.some((message) => message.invocationId === invocationId),
    )
  }

  async function submitUserInput(payload: UserInputSubmissionPayload) {
    if (userInputSubmitting.value) return
    userInputSubmitting.value = true
    try {
      const invocation = await submitTurnUserInput(payload.turnId, {
        request_id: payload.requestId,
        answers: payload.cancelled ? undefined : payload.answers ?? [],
        cancelled: payload.cancelled === true,
      })
      const session = sessionForInvocation(payload.turnId)
      if (session) {
        const assistant = assistantMessageFor(session, payload.turnId)
        if (assistant) assistant.pendingUserInput = undefined
        syncInvocation(session, invocation)
      }
      pollInvocation(payload.turnId)
    } catch (exc) {
      notifyActionError(exc)
      await refreshInvocation(payload.turnId).catch(() => undefined)
    } finally {
      userInputSubmitting.value = false
    }
  }

  return {
    activeSession,
    activeSessionId,
    addProject,
    agentDefinitions,
    archivedProjects,
    archivedSessionCount,
    archiveSession,
    deleteSessionById,
    cancelQueuedTurn,
    draftAgentId,
    draftProjectId,
    error,
    forkActiveSession,
    editUserMessage,
    hasProjects,
    loadArtifactContent,
    loadInitial,
    loading,
    markSessionUnread,
    newSession,
    openProjectInFinder,
    projects,
    projectsLoading,
    removeProjectFromHanda,
    renameProjectDisplayName,
    renameSessionTitle,
    refreshActiveBrowserEnvironment,
    retryTurn,
    selectSession,
    sendBrowserInteraction,
    sendNewChatPrompt,
    sendError,
    sendPrompt,
    setActiveSessionGoal,
    setDraftAgent,
    sessions,
    syncSessionFromUrl,
    stopActiveInvocation,
    stopPolling,
    submitUserInput,
    clearActiveSessionGoal,
    terminateBackgroundRun,
    toggleSessionStar,
    unarchiveSession,
    userInputSubmitting,
  }

  function notifyActionError(exc: unknown) {
    options.onActionError?.(errorMessageFromUnknown(exc))
  }
}

export function stepHasArtifactDelta(event: Pick<BackendStep, 'kind' | 'payload'>): boolean {
  if (event.kind === 'artifact_delta') return true
  const projections = event.payload.projections
  if (!Array.isArray(projections)) return false
  return projections.some((projection) => {
    return isRecord(projection) && projection.kind === 'artifact_delta'
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readSessionIdFromUrl(): string {
  if (typeof window === 'undefined') return ''
  const params = new URL(window.location.href).searchParams
  return (
    params.get(SESSION_QUERY_PARAM)?.trim()
    ?? params.get(LEGACY_SESSION_QUERY_PARAM)?.trim()
    ?? ''
  )
}

function readNewChatFromUrl(): { requested: boolean; projectId: string } {
  if (typeof window === 'undefined') return { requested: false, projectId: '' }
  const url = new URL(window.location.href)
  return {
    requested: url.searchParams.get(NEW_CHAT_QUERY_PARAM) === '1',
    projectId: url.searchParams.get(PROJECT_QUERY_PARAM)?.trim() ?? '',
  }
}

function resolveNewChatProjectId(projectId: string, projects: BackendProject[]) {
  if (projectId && projects.some((project) => project.id === projectId)) return projectId
  return projects[0]?.id ?? ''
}

function initialLoadRetryDelay(attempt: number) {
  return Math.min(INITIAL_LOAD_RETRY_BASE_MS * 2 ** attempt, INITIAL_LOAD_RETRY_MAX_MS)
}

function isTransientInitialLoadError(exc: unknown) {
  if (exc instanceof ApiRequestError) {
    return exc.status === 502 || exc.status === 503 || exc.status === 504
  }
  if (exc instanceof TypeError) return true
  if (!(exc instanceof Error)) return false
  return /Failed to fetch|Load failed|NetworkError|Request failed: (502|503|504)/i.test(exc.message)
}

function initialLoadErrorMessage(exc: unknown) {
  if (isTransientInitialLoadError(exc)) return BACKEND_UNAVAILABLE_MESSAGE
  return errorMessageFromUnknown(exc)
}

function errorMessageFromUnknown(exc: unknown): string {
  if (exc instanceof Error) return exc.message
  return String(exc)
}

function writeSessionIdToUrl(sessionId: string, historyMode: 'push' | 'replace') {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  url.searchParams.delete(NEW_CHAT_QUERY_PARAM)
  url.searchParams.delete(PROJECT_QUERY_PARAM)
  url.searchParams.delete(LEGACY_SESSION_QUERY_PARAM)
  if (sessionId && sessionId !== EMPTY_SESSION_ID) {
    url.searchParams.set(SESSION_QUERY_PARAM, sessionId)
  } else {
    url.searchParams.delete(SESSION_QUERY_PARAM)
  }
  const next = `${url.pathname}${url.search}${url.hash}`
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (next === current) return
  if (historyMode === 'replace') {
    window.history.replaceState({}, '', next)
  } else {
    window.history.pushState({}, '', next)
  }
}

function writeNewChatToUrl(projectId: string, historyMode: 'push' | 'replace') {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  url.searchParams.delete(SESSION_QUERY_PARAM)
  url.searchParams.delete(LEGACY_SESSION_QUERY_PARAM)
  url.searchParams.delete(ARTIFACT_QUERY_PARAM)
  url.searchParams.set(NEW_CHAT_QUERY_PARAM, '1')
  url.searchParams.set(PROJECT_QUERY_PARAM, projectId)
  const next = `${url.pathname}${url.search}${url.hash}`
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (next === current) return
  if (historyMode === 'replace') {
    window.history.replaceState({}, '', next)
  } else {
    window.history.pushState({}, '', next)
  }
}

function messageAttachments(invocation: BackendTurn): MessageAttachment[] {
  return (invocation.attachments ?? []).map((attachment: BackendTurnAttachment) => ({
    id: attachment.id,
    turnId: invocation.id,
    filename: attachment.filename,
    mimeType: attachment.mime_type,
    kind: attachment.kind,
    byteCount: attachment.byte_count,
    url: `/api/turns/${encodeURIComponent(invocation.id)}/attachments/${encodeURIComponent(attachment.id)}`,
    isImage: attachment.kind === 'image',
  }))
}

function messageSourceTurnId(message: AgentMessage) {
  if (message.turnId) return message.turnId
  if (message.invocationId && !message.invocationId.startsWith('session:')) return message.invocationId
  if (message.id.endsWith('-user')) return message.id.slice(0, -'-user'.length)
  if (message.id.endsWith('-assistant')) return message.id.slice(0, -'-assistant'.length)
  return ''
}

function assistantMessageFor(session: AgentSession, invocationId: string) {
  const byId = session.messages.find((message) => message.id === `${invocationId}-assistant`)
  if (byId) return byId
  // Fallback for legacy sessions without per-invocation id encoding.
  return [...session.messages].reverse().find((message) => message.role === 'assistant')
}

function isUserAuthoredEvent(event: BackendStep) {
  const author = event.raw_event?.author
  return typeof author === 'string' && author.trim().toLowerCase() === 'user'
}

function appendProcessTextItem(
  assistant: AgentMessage,
  event: BackendStep,
  text: string,
) {
  const items = [...(assistant.timelineItems ?? [])]
  const last = items[items.length - 1]
  const snapshot = event.kind === 'agent_text'

  if (snapshot) {
    const shownText = processTextContent(items)
    if (shownText && (shownText.includes(text) || text === shownText)) {
      setProcessTimelineItems(assistant, items)
      return
    }
    if (shownText && text.startsWith(shownText)) {
      const suffix = text.slice(shownText.length).trimStart()
      if (suffix) items.push(processTextItem(event, suffix))
      setProcessTimelineItems(assistant, items)
      return
    }
    const existing = findMatchingProcessTextItem(items, text)
    if (existing) {
      if (text.length > (existing.text ?? '').length) existing.text = text
      setProcessTimelineItems(assistant, items)
      return
    }
  }

  if (last?.kind === 'process_text') {
    const current = last.text ?? ''
    if (snapshot) {
      if (text === current || text.includes(current)) {
        last.text = text
      } else if (!current.includes(text)) {
        items.push(processTextItem(event, text))
      }
    } else if (!current.endsWith(text)) {
      last.text = `${current}${text}`
    }
    setProcessTimelineItems(assistant, items)
    return
  }

  items.push(processTextItem(event, text))
  setProcessTimelineItems(assistant, items)
}

function setProcessTimelineItems(assistant: AgentMessage, items: InvocationTimelineItem[]) {
  assistant.timelineItems = items
  removeDuplicateFinalProcessText(assistant)
}

function processTextContent(items: InvocationTimelineItem[]) {
  return items
    .filter((item) => item.kind === 'process_text')
    .map((item) => item.text ?? '')
    .join('')
}

function findMatchingProcessTextItem(items: InvocationTimelineItem[], text: string) {
  return [...items].reverse().find((item) => {
    if (item.kind !== 'process_text') return false
    const itemText = item.text ?? ''
    if (!itemText) return false
    return text === itemText || text.includes(itemText) || itemText.includes(text)
  })
}

function processTextItem(event: BackendStep, text: string): InvocationTimelineItem {
  return {
    seq: event.seq,
    kind: 'process_text',
    summary: 'Assistant response',
    createdAt: event.created_at,
    text,
    payload: event.payload,
    rawEvent: event.raw_event,
  }
}

function isDuplicateDetailEvent(assistant: AgentMessage, event: InvocationDetailEvent) {
  if (event.kind !== 'tool_call' && event.kind !== 'tool_response') return false
  const id = typeof event.payload.id === 'string' ? event.payload.id : ''
  if (!id) return false
  return (assistant.detailEvents ?? []).some((item) => item.kind === event.kind && item.payload.id === id)
}

function upsertToolTimelineItem(assistant: AgentMessage, event: InvocationDetailEvent) {
  const items = [...(assistant.timelineItems ?? [])]
  const id = typeof event.payload.id === 'string' ? event.payload.id : ''
  const name = typeof event.payload.name === 'string' ? event.payload.name : ''
  const existingIndex = id
    ? items.findIndex((item) => item.kind === 'tool' && item.toolCallId === id)
    : -1
  const existing = existingIndex >= 0 ? items[existingIndex] : undefined

  if (event.kind === 'tool_response') {
    const next: InvocationTimelineItem = {
      ...(existing ?? toolTimelineItem(event)),
      seq: event.seq,
      createdAt: event.createdAt,
      kind: 'tool',
      status: toolResponsePayloadIndicatesFailedOutcome(event.payload) ? 'failed' : 'done',
      responseSummary: compactToolSummary(event.summary, name),
      payload: {
        ...(existing?.payload ?? {}),
        response: event.payload,
      },
      rawEvent: event.rawEvent,
    }
    if (existingIndex >= 0) items.splice(existingIndex, 1, next)
    else items.push(next)
    assistant.timelineItems = items
    return
  }

  if (existing) {
    existing.summary = compactToolSummary(existing.summary || event.summary, name)
    existing.payload = {
      ...(existing.payload ?? {}),
      call: event.payload,
    }
    assistant.timelineItems = items
    return
  }

  items.push(toolTimelineItem(event))
  assistant.timelineItems = items
}

function toolTimelineItem(event: InvocationDetailEvent): InvocationTimelineItem {
  const id = typeof event.payload.id === 'string' ? event.payload.id : ''
  const name = typeof event.payload.name === 'string' ? event.payload.name : ''
  return {
    seq: event.seq,
    kind: 'tool',
    summary: compactToolSummary(event.summary, name),
    createdAt: event.createdAt,
    status: 'running',
    toolCallId: id,
    toolName: name,
    payload: { call: event.payload },
    rawEvent: event.rawEvent,
  }
}

function compactToolSummary(summary: string, fallbackName: string) {
  return summary
    .replace(/^Called\s+/, '')
    .replace(/^Finished\s+/, '')
    .replace(/^Ran\s+/, '')
    .replace(/^Command passed:\s+/, '')
    .trim() || fallbackName || 'Tool'
}

function timelineItemFromDetail(event: InvocationDetailEvent): InvocationTimelineItem {
  return {
    seq: event.seq,
    kind: timelineKindFromEventKind(event.kind),
    summary: event.summary,
    createdAt: event.createdAt,
    payload: event.payload,
    rawEvent: event.rawEvent,
  }
}

function timelineKindFromEventKind(kind: string): InvocationTimelineItem['kind'] {
  if (kind === 'artifact_delta') return 'artifact'
  if (kind === 'error') return 'error'
  if (kind === 'invocation_cancelled') return 'cancelled'
  return 'raw_event'
}

function assistantCreatedAtFromInvocation(invocation: BackendTurn): string {
  return invocation.finished_at ?? invocation.started_at ?? invocation.created_at
}

function assistantBodyFromInvocation(invocation: BackendTurn): string {
  if (invocation.final_text?.trim()) return invocation.final_text
  if (invocation.status === 'failed') return failedInvocationBody(invocation.error_message)
  if (invocation.status === 'cancelled') return 'Invocation terminated.'
  return ''
}

// Provider errors arrive as multi-line walls (mitigation links, raw JSON).
// Lead with the line that states the actual error and keep the raw message in
// a fenced block so the markdown renderer shows it as a collapsible code block
// instead of dumping it into the conversation as prose.
function failedInvocationBody(errorMessage: string | null | undefined): string {
  const raw = (errorMessage ?? '').trim()
  if (!raw) return 'Invocation failed.'
  const longestBacktickRun = (raw.match(/`+/g) ?? []).reduce(
    (max, run) => Math.max(max, run.length),
    0,
  )
  const fence = '`'.repeat(Math.max(3, longestBacktickRun + 1))
  return `Invocation failed: ${shortErrorSummary(raw)}\n\n${fence}text\n${raw}\n${fence}`
}

function shortErrorSummary(raw: string): string {
  const lines = raw.split('\n').map((line) => line.trim()).filter(Boolean)
  let line = lines.find((item) => /\b\d{3}\b\s+[A-Z_]{4,}/.test(item)) ?? lines[0] ?? ''
  const brace = line.indexOf('{')
  if (brace > 0) line = line.slice(0, brace)
  line = line.replace(/\s+/g, ' ').trim()
  if (!line) return 'Unknown error'
  return line.length > 140 ? `${line.slice(0, 137)}...` : line
}

function shouldShowInvocationInput(invocation: BackendTurn): boolean {
  return invocation.trigger_kind !== 'task_notification'
}

function isTurnDismissedFromQueue(invocation: BackendTurn): boolean {
  // started_at is only set once a turn begins running, so cancelled + never
  // started means the user dismissed it from the queue.
  return invocation.status === 'cancelled' && !invocation.started_at
}

function statusFromInvocation(status: string): AgentSession['status'] {
  if (status === 'completed') return 'done'
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  if (status === 'queued') return 'queued'
  if (status === 'waiting_input') return 'waiting_input'
  return status === 'running' ? 'running' : 'idle'
}

function statusFromSessionDetail(status: string): AgentSession['status'] {
  if (status === 'done') return 'done'
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  if (status === 'queued') return 'queued'
  if (status === 'running') return 'running'
  if (status === 'waiting_input') return 'waiting_input'
  return 'idle'
}

function backgroundRunFromDetail(run: BackendBackgroundRun): AgentBackgroundRun {
  return {
    id: run.id,
    kind: backgroundRunKind(run.kind),
    title: run.title,
    status: backgroundRunStatus(run.status),
    childSessionId: run.child_session_id ?? undefined,
    currentStep: run.current_step ?? undefined,
    artifactCount: run.artifact_count ?? undefined,
  }
}

function progressItemFromDetail(item: BackendProgressItem): AgentProgressItem {
  return {
    id: item.id,
    title: item.title,
    status: progressStatus(item.status),
    detail: item.detail ?? undefined,
    updatedAt: item.updated_at ?? null,
    sourceTurnId: item.source_turn_id ?? null,
  }
}

function browserEnvironmentFromDetail(
  browser: BackendBrowserEnvironment | null | undefined,
): AgentBrowserEnvironment | null {
  if (!browser) return null
  return {
    success: browser.success ?? browser.status !== 'error',
    status: browser.status,
    sessionId: browser.session_id ?? null,
    url: browser.url ?? null,
    title: browser.title ?? null,
    lastAction: browser.last_action ?? null,
    lastError: browser.last_error ?? null,
    updatedAt: browser.updated_at ?? null,
    screenshotUrl: browser.screenshot_url ?? null,
    streamUrl: browser.stream_url ?? null,
    viewport: browser.viewport ?? null,
  }
}

function progressStatus(status: string): AgentProgressItem['status'] {
  if (status === 'done') return 'done'
  if (status === 'failed') return 'failed'
  if (status === 'running') return 'running'
  return 'pending'
}

function backgroundRunKind(kind: string): AgentBackgroundRun['kind'] {
  const normalized = kind.trim().toLowerCase().replace(/_/g, '-')
  if (normalized === 'sub-agent' || normalized === 'agent-run' || normalized === 'run-agent' || normalized === 'system-agent-run') {
    return 'sub-agent'
  }
  if (normalized === 'command' || normalized === 'test' || normalized === 'index' || normalized === 'sync') {
    return normalized
  }
  return 'custom'
}

function backgroundRunStatus(status: string): AgentBackgroundRun['status'] {
  if (status === 'done') return 'done'
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  if (status === 'queued') return 'queued'
  if (status === 'running') return 'running'
  if (status === 'waiting') return 'waiting'
  if (status === 'pending') return 'pending'
  return 'pending'
}

function pendingUserInputFromPayload(
  payload: Record<string, unknown>,
  turnId: string,
): PendingUserInputRequest | undefined {
  const pending = payload.pending_user_input
  if (!pending || typeof pending !== 'object') return undefined
  const record = pending as Record<string, unknown>
  const requestId = typeof record.request_id === 'string' ? record.request_id : ''
  const rawQuestions = Array.isArray(record.questions) ? record.questions : []
  const questions: UserInputQuestion[] = []
  for (const item of rawQuestions) {
    if (!item || typeof item !== 'object') continue
    const question = item as Record<string, unknown>
    const id = typeof question.id === 'string' ? question.id : ''
    const prompt = typeof question.prompt === 'string' ? question.prompt : ''
    const rawOptions = Array.isArray(question.options) ? question.options : []
    const options: UserInputOption[] = []
    for (const option of rawOptions) {
      if (!option || typeof option !== 'object') continue
      const optionRecord = option as Record<string, unknown>
      const label = typeof optionRecord.label === 'string' ? optionRecord.label : ''
      if (!label) continue
      const description =
        typeof optionRecord.description === 'string' ? optionRecord.description : undefined
      options.push(description === undefined ? { label } : { label, description })
    }
    if (!id || !prompt || options.length === 0) continue
    questions.push({
      id,
      prompt,
      options,
      multiSelect: question.multi_select === true,
      allowFreeText: question.allow_free_text !== false,
    })
  }
  if (!requestId || questions.length === 0) return undefined
  return { requestId, turnId, questions }
}

function isLiveStatus(status: AgentSession['status']) {
  return status === 'queued' || status === 'running' || status === 'waiting_input'
}

function sessionHasPendingInput(session: AgentSession) {
  if (session.status === 'waiting_input') return true
  return session.messages.some(
    (message) => message.role === 'assistant' && message.status === 'waiting_input',
  )
}

function hasLiveBackgroundRun(session?: AgentSession | null) {
  return (session?.backgroundRuns ?? []).some((run) => isLiveRunStatus(run.status))
}

function isLiveRunStatus(status: AgentBackgroundRun['status']) {
  return status === 'queued' || status === 'running' || status === 'pending' || status === 'waiting'
}

function isTerminalStatus(status: AgentSession['status']) {
  return status === 'done' || status === 'failed' || status === 'cancelled'
}

function earliestIso(a: string, b: string): string {
  const aMs = Date.parse(a)
  const bMs = Date.parse(b)
  if (Number.isNaN(aMs)) return b
  if (Number.isNaN(bMs)) return a
  return aMs <= bMs ? a : b
}

function createdAtFromSessionId(sessionId: string, fallback: string): string {
  const match = sessionId.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/)
  if (!match) return fallback
  const [, year, month, day, hour, minute, second] = match
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
  )
  return Number.isNaN(date.getTime()) ? fallback : date.toISOString()
}

function titleFromInvocation(invocation: BackendTurn): string {
  return invocation.title?.trim() || titleFromPrompt(invocation.input_text)
}

function titleFromPrompt(prompt: string): string {
  const firstLine = prompt.trim().split('\n')[0] ?? 'Chat session'
  return firstLine.length > 52 ? `${firstLine.slice(0, 49)}...` : firstLine
}

function invocationElapsedStart(invocation: BackendTurn): string {
  return invocation.started_at ?? invocation.created_at
}

function invocationElapsedEnd(invocation: BackendTurn): string | null {
  if (invocation.finished_at) return invocation.finished_at
  // A waiting_input turn is paused on the user, not working — freeze the
  // clock at the moment the turn entered the waiting state.
  if (invocation.status === 'waiting_input') return invocation.updated_at
  return null
}

function sessionDetailStart(detail: BackendSessionDetail): string {
  return detail.steps[0]?.created_at ?? detail.created_at
}

function sessionDetailEnd(detail: BackendSessionDetail, status: AgentSession['status']): string | null {
  // waiting_input pauses on the user, so its elapsed clock freezes too.
  if (!isTerminalStatus(status) && status !== 'waiting_input') return null
  return detail.steps[detail.steps.length - 1]?.created_at ?? detail.updated_at
}

function sessionDetailElapsed(detail: BackendSessionDetail, status: AgentSession['status']): string {
  return elapsed(sessionDetailStart(detail), sessionDetailEnd(detail, status))
}

function elapsed(start: string, end?: string | null): string {
  const startMs = Date.parse(start)
  const endMs = end ? Date.parse(end) : Date.now()
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return '0s'
  return formatWorkDuration(Math.max(0, (endMs - startMs) / 1000))
}

// Agent working time in seconds, excluding spans the turn was paused on the
// user. The backend computes this (started_at→end minus user-input waits);
// fall back to raw wall-clock only for older turns missing the field.
function invocationActiveSeconds(invocation: BackendTurn): number {
  const value = invocation.active_seconds
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value
  const startMs = Date.parse(invocationElapsedStart(invocation))
  const endIso = invocationElapsedEnd(invocation)
  const endMs = endIso ? Date.parse(endIso) : Date.now()
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return 0
  return Math.max(0, (endMs - startMs) / 1000)
}

function tokenUsageFromInvocation(invocation: BackendTurn): InvocationTokenUsage {
  const input = Math.max(0, Number(invocation.input_token_count) || 0)
  const output = Math.max(0, Number(invocation.output_token_count) || 0)
  const total = Math.max(0, Number(invocation.total_token_count) || 0)
  return {
    input,
    output,
    total: total || input + output,
  }
}

function tokenUsageFromSessionDetail(detail: BackendSessionDetail): InvocationTokenUsage {
  const input = Math.max(0, Number(detail.input_token_count) || 0)
  const output = Math.max(0, Number(detail.output_token_count) || 0)
  const total = Math.max(0, Number(detail.total_token_count) || 0)
  return {
    input,
    output,
    total: total || input + output,
  }
}

function contextUsageBreakdownFromDetail(detail: BackendSessionDetail): ContextUsageBreakdownItem[] {
  return (detail.context_usage_breakdown ?? []).map(contextUsageBreakdownItemFromBackend)
}

function contextUsageBreakdownItemFromBackend(item: BackendContextUsageBreakdownItem): ContextUsageBreakdownItem {
  return {
    id: item.id,
    label: item.label,
    tokenCount: Math.max(0, Number(item.token_count) || 0),
    percent: Math.max(0, Number(item.percent) || 0),
    children: (item.children ?? []).map(contextUsageBreakdownItemFromBackend),
  }
}

function metadataText(content: { mime_type?: string | null; byte_count?: number | null } | null): string {
  if (!content) return ''
  return `Binary artifact\nmime_type: ${content.mime_type ?? 'unknown'}\nbyte_count: ${
    content.byte_count ?? 0
  }`
}

function compareSessionSummary(
  a: { createdAt: string; lastActivityAt?: string | null; starred?: boolean; starredAt?: string | null },
  b: { createdAt: string; lastActivityAt?: string | null; starred?: boolean; starredAt?: string | null },
) {
  if (a.starred !== b.starred) return a.starred ? -1 : 1

  if (a.starred && b.starred) {
    const aStarredMs = a.starredAt ? Date.parse(a.starredAt) : 0
    const bStarredMs = b.starredAt ? Date.parse(b.starredAt) : 0
    if (!Number.isNaN(aStarredMs) && !Number.isNaN(bStarredMs) && aStarredMs !== bStarredMs) {
      return bStarredMs - aStarredMs
    }
  }

  const aActivityMs = Date.parse(a.lastActivityAt ?? a.createdAt)
  const bActivityMs = Date.parse(b.lastActivityAt ?? b.createdAt)
  if (Number.isNaN(aActivityMs) || Number.isNaN(bActivityMs)) return 0
  return bActivityMs - aActivityMs
}
