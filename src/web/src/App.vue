<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import AutomatedTasksPage from './components/AutomatedTasksPage.vue'
import ChatHeader from './components/ChatHeader.vue'
import ChatPanel from './components/ChatPanel.vue'
import LeftSideBar from './components/LeftSideBar.vue'
import NewChatPage from './components/NewChatPage.vue'
import SessionRightSidebar from './components/SessionRightSidebar.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import ToastStack from './components/ToastStack.vue'
import type { SidebarArtifact, SidebarBackgroundRun, SidebarProgressItem } from './components/SessionRightSidebar.vue'
import type { SettingsSection } from './components/SettingsPanel.vue'
import { getAgentContextUsage } from './api/client'
import type { BackendAgentContextUsage, BackendContextUsageBreakdownItem } from './api/types'
import { useChatSessions } from './composables/useChatSessions'
import { useThemeSettings } from './composables/useThemeSettings'
import { formatWorkDuration, parseDurationToSeconds } from './presenters/duration'
import type { AgentBrowserEnvironment, Artifact, BrowserInteraction, ContextUsageBreakdownItem, ContextUsageSummary, EditMessagePayload, MessageAttachment, SendPromptPayload } from './types'

interface ToastItem {
  id: string
  message: string
}

const toasts = ref<ToastItem[]>([])
const toastTimers = new Map<string, number>()
let toastSeq = 0
const ARTIFACT_QUERY_PARAM = 'artifact'
const DIALOG_QUERY_PARAM = 'dialog'
const SETTINGS_SECTION_QUERY_PARAM = 'settings_section'
const RESTORABLE_DIALOGS = ['settings', 'automated-tasks', 'search'] as const
type RestorableDialog = (typeof RESTORABLE_DIALOGS)[number]
const SETTINGS_SECTIONS: SettingsSection[] = ['theme', 'gemini-api-key', 'archived-chats']

const {
  activeSession,
  activeSessionId,
  addProject,
  agentDefinitions,
  archivedProjects,
  archivedSessionCount,
  archiveSession,
  deleteSessionById,
  draftAgentId,
  draftProjectId,
  error,
  editUserMessage,
  forkActiveSession,
  cancelQueuedTurn,
  hasProjects,
  loadArtifactContent,
  loadInitial,
  loading,
  markSessionUnread,
  newSession,
  openProjectInFinder,
  projects,
  projectsLoading,
  renameSessionTitle,
  removeProjectFromHanda,
  renameProjectDisplayName,
  refreshActiveBrowserEnvironment,
  retryTurn,
  selectSession,
  sendBrowserInteraction,
  sendNewChatPrompt,
  sendError,
  sendPrompt,
  setDraftAgent,
  syncSessionFromUrl,
  stopActiveInvocation,
  stopPolling,
  submitUserInput,
  terminateBackgroundRun,
  toggleSessionStar,
  unarchiveSession,
  userInputSubmitting,
} = useChatSessions({ onActionError: showErrorToast })

const {
  effectiveThemeMode,
  foldedProjectIds,
  geminiApiKeyPreview,
  geminiApiKeySet,
  loadTheme,
  loadingTheme,
  modelConfigId,
  modelConfigs,
  setFoldedProjects,
  setGeminiApiKey,
  setModelConfig,
  setTheme,
  themeError,
  themeId,
} = useThemeSettings()

const leftSideBarCollapsed = ref(false)
const leftSideBarExpandedOnNarrow = ref(false)
const activeArtifactId = ref('')

const settingsOpen = ref(false)
const automatedTasksOpen = ref(false)
const searchOpen = ref(false)
const settingsInitialSection = ref<SettingsSection>('theme')
const archivedOpen = ref(false) // Wait, we don't need archivedOpen as a main view state anymore.
const browserPreviewOpen = ref(false)
const rightSidebarOpenOnNarrow = ref(false)
const isNarrowViewport = ref(false)
const composerDrafts = ref<Record<string, string>>({})
// Server-side attachments to prefill into a session's composer, keyed the same
// as composerDrafts. Populated when forking a message that had attachments.
const composerDraftAttachments = ref<Record<string, MessageAttachment[]>>({})
let browserRefreshTimer: number | undefined

const activeArtifact = computed(() => {
  return activeSession.value.artifacts.find((artifact) => artifact.id === activeArtifactId.value)
})

const activeProjectName = computed(() => {
  return projects.value.find((project) => project.id === activeSession.value.projectId)?.name ?? ''
})

const themeIsDark = computed(() => effectiveThemeMode.value === 'dark')

const sidebarArtifacts = computed<SidebarArtifact[]>(() => {
  return activeSession.value.artifacts.map((artifact) => ({
    id: artifact.id,
    title: artifact.title,
    kind: formatArtifactKind(artifact),
    meta: formatArtifactMeta(artifact),
  }))
})

const sidebarBackgroundRuns = computed<SidebarBackgroundRun[]>(() => activeSession.value.backgroundRuns ?? [])
const sidebarProgressItems = computed<SidebarProgressItem[]>(() => activeSession.value.progressItems ?? [])

const activeBrowserEnvironment = computed(() => activeSession.value.browserEnvironment ?? null)
const activeBrowserPanel = computed(() => browserPreviewOpen.value ? activeBrowserEnvironment.value : null)

const sidebarUsage = computed<ContextUsageSummary>(() => {
  let totalTokens = 0
  let outputTokens = 0
  let toolCalls = 0
  let agentSeconds = 0
  let contextTokens = 0
  for (const message of activeSession.value.messages) {
    if (message.role !== 'assistant') continue
    if (message.tokenUsage) {
      totalTokens += message.tokenUsage.total
      outputTokens += message.tokenUsage.output
      if (message.tokenUsage.input > 0) contextTokens = message.tokenUsage.input
    }
    toolCalls += (message.detailEvents ?? []).filter((event) => event.kind === 'tool_call').length
    agentSeconds += message.activeSeconds ?? parseDurationToSeconds(message.elapsed)
  }
  const modelConfig =
    modelConfigs.value.find((config) => config.id === activeSession.value.latestModelConfigId) ??
    modelConfigs.value.find((config) => config.id === modelConfigId.value)
  const contextLimit = modelConfig?.context_window ?? 0
  const breakdown = (activeSession.value.contextUsageBreakdown ?? []).map(formatBreakdownItem)
  return {
    contextTokens: formatTokens(contextTokens),
    contextLimit: contextLimit > 0 ? formatTokenLimit(contextLimit) : '—',
    contextPercent: contextLimit > 0 ? Math.min(100, Math.round((contextTokens / contextLimit) * 100)) : 0,
    contextTokenCount: contextTokens,
    contextLimitCount: contextLimit,
    breakdown,
    modelName: modelConfig?.label ?? '',
    totalTokens: formatTokens(totalTokens),
    totalTokenCount: totalTokens,
    outputTokens: formatTokens(outputTokens),
    outputTokenCount: outputTokens,
    toolCalls,
    agentTime: formatWorkDuration(agentSeconds),
  }
})

const activeNavigationSessionId = computed(() => {
  return activeSession.value.parentSessionId
    ? (activeSession.value.rootSessionId ?? activeSessionId.value)
    : activeSessionId.value
})

const newChatDraftText = computed(() => {
  return composerDrafts.value[newChatDraftKey(draftProjectId.value)] ?? ''
})

const activeSessionDraftText = computed(() => {
  return composerDrafts.value[sessionDraftKey(activeSession.value.id)] ?? ''
})

const activeSessionDraftAttachments = computed(() => {
  return composerDraftAttachments.value[sessionDraftKey(activeSession.value.id)] ?? []
})

async function handleNewSession(projectId: string) {
  archivedOpen.value = false
  closeRightPanel()
  await newSession(projectId)
}

function handleNewChatProjectChange(projectId: string) {
  newSession(projectId, { history: 'replace' })
}

function handleNewChatSend(payload: SendPromptPayload, projectId: string, agentId: string) {
  void sendNewChatPrompt(payload, projectId, modelConfigId.value, agentId)
}

function handleNewChatDraftTextUpdate(text: string) {
  setComposerDraft(newChatDraftKey(draftProjectId.value), text)
}

function handleActiveSessionDraftTextUpdate(text: string) {
  setComposerDraft(sessionDraftKey(activeSession.value.id), text)
}

function handleActiveSessionDraftAttachmentsUpdate(attachments: MessageAttachment[]) {
  setComposerDraftAttachments(sessionDraftKey(activeSession.value.id), attachments)
}

function handleSelectSession(id: string) {
  archivedOpen.value = false
  closeRightPanel()
  void selectSession(id)
}

function handleSelectBreadcrumb(id: string) {
  if (id.startsWith('project:')) return
  archivedOpen.value = false
  closeRightPanel()
  if (id !== activeSession.value.id) void selectSession(id)
}

function handleSelectBackgroundRun(id: string) {
  const run = sidebarBackgroundRuns.value.find((item) => item.id === id)
  if (!run?.childSessionId) return
  archivedOpen.value = false
  closeRightPanel()
  void selectSession(run.childSessionId)
}

function handleSelectParentSession() {
  const parentSessionId = activeSession.value.parentSessionId
  if (!parentSessionId) return
  archivedOpen.value = false
  closeRightPanel()
  void selectSession(parentSessionId)
}

function handleTerminateBackgroundRun(id: string) {
  void terminateBackgroundRun(activeSession.value.id, id)
}

function handleSelectBrowser() {
  if (!activeBrowserEnvironment.value?.screenshotUrl && !activeBrowserEnvironment.value?.streamUrl) return
  archivedOpen.value = false
  activeArtifactId.value = ''
  writeArtifactToUrl('')
  browserPreviewOpen.value = true
  void refreshActiveBrowserEnvironment({ quiet: true })
}

function handleBrowserInteract(payload: BrowserInteraction) {
  void sendBrowserInteraction(payload)
}

function handleBrowserUpdate(browser: AgentBrowserEnvironment) {
  activeSession.value.browserEnvironment = browser
}

function handleSendPrompt(payload: SendPromptPayload, selectedModelConfigId: string) {
  closeRightPanel()
  void sendPrompt(payload, selectedModelConfigId)
}

function handleEditUserMessage(payload: EditMessagePayload, selectedModelConfigId: string) {
  closeRightPanel()
  void editUserMessage(payload, selectedModelConfigId)
}

function newChatDraftKey(projectId: string) {
  return `new:${projectId || 'unselected'}`
}

function sessionDraftKey(sessionId: string) {
  return `session:${sessionId || 'empty'}`
}

function setComposerDraft(key: string, text: string) {
  if (text) {
    composerDrafts.value = { ...composerDrafts.value, [key]: text }
    return
  }

  if (!(key in composerDrafts.value)) return
  const nextDrafts = { ...composerDrafts.value }
  delete nextDrafts[key]
  composerDrafts.value = nextDrafts
}

function setComposerDraftAttachments(key: string, attachments: MessageAttachment[]) {
  if (attachments.length) {
    composerDraftAttachments.value = { ...composerDraftAttachments.value, [key]: attachments }
    return
  }

  if (!(key in composerDraftAttachments.value)) return
  const nextDrafts = { ...composerDraftAttachments.value }
  delete nextDrafts[key]
  composerDraftAttachments.value = nextDrafts
}

function handleForkSession(sourceTurnId?: string, options?: { includeSourceTurn?: boolean; prompt?: string; attachments?: MessageAttachment[] }) {
  closeRightPanel()
  void (async () => {
    const forkedSessionId = await forkActiveSession(sourceTurnId, {
      includeSourceTurn: options?.includeSourceTurn,
    })
    if (!forkedSessionId) return
    const draftKey = sessionDraftKey(forkedSessionId)
    if (options?.prompt) {
      setComposerDraft(draftKey, options.prompt)
    }
    if (options?.attachments?.length) {
      setComposerDraftAttachments(draftKey, options.attachments)
    }
  })()
}

const effectiveLeftSideBarCollapsed = computed(() =>
  isNarrowViewport.value ? !leftSideBarExpandedOnNarrow.value : leftSideBarCollapsed.value,
)
const showNewChatPage = computed(() => Boolean(draftProjectId.value))

// New chat has no runtime usage yet; preview the static prompt (instruction,
// tools, skills, project config) so the context ring still opens for debugging.
const newChatStaticUsage = ref<BackendAgentContextUsage | null>(null)
let newChatUsageRequestSeq = 0

watch(
  [showNewChatPage, draftProjectId, draftAgentId],
  async ([show, projectId, agentId]) => {
    if (!show || !agentId) {
      newChatStaticUsage.value = null
      return
    }
    const seq = ++newChatUsageRequestSeq
    try {
      const usage = await getAgentContextUsage(agentId, projectId || undefined)
      if (seq === newChatUsageRequestSeq) newChatStaticUsage.value = usage
    } catch {
      if (seq === newChatUsageRequestSeq) newChatStaticUsage.value = null
    }
  },
  { immediate: true },
)

const newChatContextUsage = computed<ContextUsageSummary | null>(() => {
  const usage = newChatStaticUsage.value
  if (!usage) return null
  const modelConfig = modelConfigs.value.find((config) => config.id === modelConfigId.value)
  const contextLimit = modelConfig?.context_window ?? 0
  if (contextLimit <= 0) return null
  const contextTokens = usage.total_token_count
  return {
    contextTokens: formatTokens(contextTokens),
    contextLimit: formatTokenLimit(contextLimit),
    contextPercent: contextLimit > 0 ? Math.min(100, Math.round((contextTokens / contextLimit) * 100)) : 0,
    contextTokenCount: contextTokens,
    contextLimitCount: contextLimit,
    breakdown: usage.breakdown.map(newChatBreakdownItem).map(formatBreakdownItem),
    modelName: modelConfig?.label ?? '',
    totalTokens: '0',
    totalTokenCount: 0,
    outputTokens: '0',
    outputTokenCount: 0,
    toolCalls: 0,
    agentTime: '0s',
  }
})

function newChatBreakdownItem(item: BackendContextUsageBreakdownItem): ContextUsageBreakdownItem {
  return {
    id: item.id,
    label: item.label,
    tokenCount: Math.max(0, Number(item.token_count) || 0),
    percent: Math.max(0, Number(item.percent) || 0),
    children: (item.children ?? []).map(newChatBreakdownItem),
  }
}
// Right sidebar starts closed and auto-opens once per session the first time that
// session has something worth showing. After the user opens/closes it manually,
// that choice wins for the session and we stop auto-opening it.
const rightSidebarHiddenBySession = reactive(new Map<string, boolean>())
const rightSidebarAutoOpened = reactive(new Set<string>())
// The right sidebar animates its width only as a direct result of a manual toggle.
// Switching sessions (and content-driven auto-open) snaps it into place instead.
const animateRightSidebar = ref(false)

// Auto-open trigger: the session has any sidebar content. The "back to parent"
// button is navigation, not content, so it deliberately does not count.
function shouldOpenRightSidebar(session: typeof activeSession.value): boolean {
  return (
    (session.progressItems?.length ?? 0) > 0 ||
    session.browserEnvironment != null ||
    (session.artifacts?.length ?? 0) > 0 ||
    (session.backgroundRuns?.length ?? 0) > 0
  )
}

const rightSidebarHidden = computed(() => {
  const manualHidden = rightSidebarHiddenBySession.get(activeSessionId.value)
  if (manualHidden !== undefined) return manualHidden
  return !rightSidebarAutoOpened.has(activeSessionId.value)
})
const showRightSidebar = computed(() =>
  !showNewChatPage.value && (isNarrowViewport.value ? rightSidebarOpenOnNarrow.value : !rightSidebarHidden.value),
)

function toggleLeftSideBar() {
  if (isNarrowViewport.value) {
    leftSideBarExpandedOnNarrow.value = !leftSideBarExpandedOnNarrow.value
    return
  }
  leftSideBarCollapsed.value = !leftSideBarCollapsed.value
}

function toggleRightSidebar() {
  animateRightSidebar.value = true
  if (isNarrowViewport.value) {
    rightSidebarOpenOnNarrow.value = !rightSidebarOpenOnNarrow.value
    return
  }
  rightSidebarHiddenBySession.set(activeSessionId.value, !rightSidebarHidden.value)
}

function openSettings() {
  settingsInitialSection.value = 'theme'
  setRestorableDialog('settings')
}

function closeSettings() {
  closeRestorableDialog('settings')
}

function handleSettingsSectionUpdate(section: SettingsSection) {
  settingsInitialSection.value = section
  if (settingsOpen.value) writeDialogToUrl('settings')
}

function openAutomatedTasks() {
  setRestorableDialog('automated-tasks')
}

function closeAutomatedTasks() {
  closeRestorableDialog('automated-tasks')
}

function openSearch() {
  setRestorableDialog('search')
}

function closeSearch() {
  closeRestorableDialog('search')
}

const DEFAULT_SIDEBAR_WIDTH = 280
const leftPanelWidth = ref(DEFAULT_SIDEBAR_WIDTH)
const rightSidebarWidth = ref(DEFAULT_SIDEBAR_WIDTH)
const isDraggingLeft = ref(false)
const isDraggingRightSidebar = ref(false)
const SIDEBAR_MIN_WIDTH = 260
const SIDEBAR_MAX_WIDTH = 460

function clampSidebarWidth(width: number) {
  return Math.max(SIDEBAR_MIN_WIDTH, Math.min(width, SIDEBAR_MAX_WIDTH))
}

function startDragLeft(e: MouseEvent) {
  e.preventDefault()
  isDraggingLeft.value = true
  
  function onMouseMove(e: MouseEvent) {
    leftPanelWidth.value = clampSidebarWidth(Math.min(e.clientX, window.innerWidth - 300))
  }
  
  function onMouseUp() {
    isDraggingLeft.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
  }
  
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  document.body.style.cursor = 'col-resize'
}

function startDragRightSidebar(e: MouseEvent) {
  e.preventDefault()
  e.stopPropagation()
  isDraggingRightSidebar.value = true
  const startX = e.clientX
  const startWidth = rightSidebarWidth.value

  function onMouseMove(e: MouseEvent) {
    rightSidebarWidth.value = clampSidebarWidth(startWidth + startX - e.clientX)
  }

  function onMouseUp() {
    isDraggingRightSidebar.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  document.body.style.cursor = 'col-resize'
}

let mediaQuery: MediaQueryList | undefined
let browserRefreshPollGeneration = 0

function syncViewportMode() {
  isNarrowViewport.value = mediaQuery?.matches ?? false
}

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 860px)')
  syncViewportMode()
  mediaQuery.addEventListener('change', syncViewportMode)
  window.addEventListener('popstate', syncSessionFromUrl)
  window.addEventListener('popstate', syncArtifactFromUrl)
  window.addEventListener('popstate', syncDialogFromUrl)
  syncDialogFromUrl()
  void loadTheme()
  void loadInitial().then(() => {
    syncArtifactFromUrl()
    syncDialogFromUrl()
  })
})

onUnmounted(() => {
  mediaQuery?.removeEventListener('change', syncViewportMode)
  window.removeEventListener('popstate', syncSessionFromUrl)
  window.removeEventListener('popstate', syncArtifactFromUrl)
  window.removeEventListener('popstate', syncDialogFromUrl)
  stopPolling()
  stopBrowserRefreshPolling()
  for (const timer of toastTimers.values()) {
    window.clearTimeout(timer)
  }
  toastTimers.clear()
})

watch(
  () => [
    activeSession.value.id,
    activeBrowserEnvironment.value?.status ?? '',
    activeBrowserEnvironment.value?.screenshotUrl ?? '',
    activeBrowserEnvironment.value?.streamUrl ?? '',
    browserPreviewOpen.value ? 'open' : 'closed',
  ],
  () => syncBrowserRefreshPolling(),
  { immediate: true },
)

watch(
  () => activeSession.value.artifacts,
  () => {
    if (!activeSession.value.artifacts.some((artifact) => artifact.id === activeArtifactId.value)) {
      activeArtifactId.value = ''
    }
    syncArtifactFromUrl()
  },
  { deep: true },
)

watch(
  () => activeSessionId.value,
  () => {
    activeArtifactId.value = ''
    rightSidebarOpenOnNarrow.value = false
    syncArtifactFromUrl()
    // Switching sessions snaps the right sidebar into place; only a manual toggle
    // (handled in toggleRightSidebar) animates the width.
    animateRightSidebar.value = false
  },
)

watch(
  () => isNarrowViewport.value,
  (isNarrow) => {
    if (!isNarrow) return
    leftSideBarExpandedOnNarrow.value = false
    rightSidebarOpenOnNarrow.value = false
  },
)

// First time a session has sidebar content, auto-open the right sidebar once.
// Skipped once the user has taken manual control of that session's sidebar.
watch(
  () => [activeSessionId.value, shouldOpenRightSidebar(activeSession.value)] as const,
  ([sessionId, shouldOpen]) => {
    if (!shouldOpen) return
    if (rightSidebarHiddenBySession.has(sessionId)) return
    rightSidebarAutoOpened.add(sessionId)
  },
  { immediate: true },
)

function selectArtifact(id: string) {
  archivedOpen.value = false
  browserPreviewOpen.value = false
  if (activeArtifactId.value === id) {
    activeArtifactId.value = ''
    writeArtifactToUrl('')
    return
  }
  activeArtifactId.value = id
  writeArtifactToUrl(id)
  void loadArtifactContent(id)
}

function closeRightPanel() {
  activeArtifactId.value = ''
  browserPreviewOpen.value = false
  writeArtifactToUrl('')
}

function syncBrowserRefreshPolling() {
  stopBrowserRefreshPolling()
  const browser = activeBrowserEnvironment.value
  const status = browser?.status?.trim().toLowerCase()
  if (browserPreviewOpen.value && browser?.streamUrl) return
  if (!browser?.screenshotUrl || (status !== 'open' && status !== 'running')) return
  const generation = ++browserRefreshPollGeneration
  const interval = browserPreviewOpen.value ? 1000 : 2000
  const tick = async () => {
    browserRefreshTimer = undefined
    if (generation !== browserRefreshPollGeneration) return
    const current = activeBrowserEnvironment.value
    const currentStatus = current?.status?.trim().toLowerCase()
    if (!current?.screenshotUrl || (currentStatus !== 'open' && currentStatus !== 'running')) return
    await refreshActiveBrowserEnvironment({ quiet: true })
    if (generation !== browserRefreshPollGeneration) return
    browserRefreshTimer = window.setTimeout(tick, interval)
  }
  browserRefreshTimer = window.setTimeout(tick, interval)
}

function stopBrowserRefreshPolling() {
  browserRefreshPollGeneration += 1
  if (browserRefreshTimer === undefined) return
  window.clearTimeout(browserRefreshTimer)
  browserRefreshTimer = undefined
}

function handleDictationError(message: string) {
  showErrorToast(message)
}

function showErrorToast(message: string) {
  const trimmed = message.trim()
  if (!trimmed) return
  const id = `toast-${++toastSeq}`
  const nextToasts = [...toasts.value, { id, message: trimmed }].slice(-4)
  const visibleIds = new Set(nextToasts.map((toast) => toast.id))
  for (const [toastId, timer] of toastTimers) {
    if (visibleIds.has(toastId)) continue
    window.clearTimeout(timer)
    toastTimers.delete(toastId)
  }
  toasts.value = nextToasts
  toastTimers.set(id, window.setTimeout(() => dismissToast(id), 5000))
}

function dismissToast(id: string) {
  const timer = toastTimers.get(id)
  if (timer) window.clearTimeout(timer)
  toastTimers.delete(id)
  toasts.value = toasts.value.filter((toast) => toast.id !== id)
}

function formatArtifactKind(artifact: Artifact) {
  return artifact.kind ? artifact.kind.charAt(0).toUpperCase() + artifact.kind.slice(1) : 'Artifact'
}

function formatArtifactMeta(artifact: Artifact) {
  if (artifact.displayVersion) return `v${artifact.displayVersion}`
  if (artifact.version) return `v${artifact.version}`
  return artifact.filetype ?? artifact.subtitle
}

function formatTokens(count: number) {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(2)}M`
  if (count >= 1_000) return `${Math.round(count / 1_000)}K`
  return String(count)
}

function formatBreakdownItem(item: ContextUsageBreakdownItem): ContextUsageBreakdownItem {
  return {
    ...item,
    tokenText: formatTokens(item.tokenCount),
    percent: item.percent,
    children: item.children?.map(formatBreakdownItem),
  }
}

function formatTokenLimit(count: number) {
  if (count >= 1_000_000) return `${Math.round(count / 1_000_000)}M`
  if (count >= 1_000) return `${Math.round(count / 1_000)}K`
  return String(count)
}

function readArtifactIdFromUrl() {
  if (typeof window === 'undefined') return ''
  return new URL(window.location.href).searchParams.get(ARTIFACT_QUERY_PARAM) ?? ''
}

function syncArtifactFromUrl() {
  const artifactId = readArtifactIdFromUrl()
  if (!artifactId) {
    activeArtifactId.value = ''
    return
  }
  const artifact = activeSession.value.artifacts.find((item) => item.id === artifactId)
  if (!artifact) return
  activeArtifactId.value = artifactId
  if (!artifact.content && !artifact.loading) void loadArtifactContent(artifactId)
}

function writeArtifactToUrl(artifactId: string) {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  if (artifactId) url.searchParams.set(ARTIFACT_QUERY_PARAM, artifactId)
  else url.searchParams.delete(ARTIFACT_QUERY_PARAM)
  const next = `${url.pathname}${url.search}${url.hash}`
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (next !== current) window.history.pushState({}, '', next)
}

function readDialogFromUrl(): RestorableDialog | '' {
  if (typeof window === 'undefined') return ''
  const dialog = new URL(window.location.href).searchParams.get(DIALOG_QUERY_PARAM)
  return isRestorableDialog(dialog) ? dialog : ''
}

function isRestorableDialog(value: string | null): value is RestorableDialog {
  return Boolean(value && (RESTORABLE_DIALOGS as readonly string[]).includes(value))
}

function syncDialogFromUrl() {
  const dialog = readDialogFromUrl()
  if (dialog === 'settings') settingsInitialSection.value = readSettingsSectionFromUrl()
  applyRestorableDialog(dialog)
}

function setRestorableDialog(dialog: RestorableDialog | '') {
  applyRestorableDialog(dialog)
  writeDialogToUrl(dialog)
}

function closeRestorableDialog(dialog: RestorableDialog) {
  if (!isDialogOpen(dialog)) return
  setRestorableDialog('')
}

function applyRestorableDialog(dialog: RestorableDialog | '') {
  settingsOpen.value = dialog === 'settings'
  automatedTasksOpen.value = dialog === 'automated-tasks'
  searchOpen.value = dialog === 'search'
}

function readSettingsSectionFromUrl(): SettingsSection {
  if (typeof window === 'undefined') return 'theme'
  const section = new URL(window.location.href).searchParams.get(SETTINGS_SECTION_QUERY_PARAM)
  return isSettingsSection(section) ? section : 'theme'
}

function isSettingsSection(value: string | null): value is SettingsSection {
  return Boolean(value && (SETTINGS_SECTIONS as readonly string[]).includes(value))
}

function isDialogOpen(dialog: RestorableDialog) {
  if (dialog === 'settings') return settingsOpen.value
  if (dialog === 'automated-tasks') return automatedTasksOpen.value
  return searchOpen.value
}

function writeDialogToUrl(dialog: RestorableDialog | '') {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  if (dialog) {
    url.searchParams.set(DIALOG_QUERY_PARAM, dialog)
    if (dialog === 'settings') url.searchParams.set(SETTINGS_SECTION_QUERY_PARAM, settingsInitialSection.value)
    else url.searchParams.delete(SETTINGS_SECTION_QUERY_PARAM)
  } else {
    url.searchParams.delete(DIALOG_QUERY_PARAM)
    url.searchParams.delete(SETTINGS_SECTION_QUERY_PARAM)
  }
  const next = `${url.pathname}${url.search}${url.hash}`
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (next !== current) window.history.pushState({}, '', next)
}
</script>

<template>
  <div
    class="relative flex h-screen overflow-hidden bg-background"
    :class="{ 'select-none': isDraggingLeft || isDraggingRightSidebar }"
  >
    <LeftSideBar
      :projects="projects"
      :archived-session-count="archivedSessionCount"
      :active-session-id="activeNavigationSessionId"
      :collapsed="effectiveLeftSideBarCollapsed"
      :has-projects="hasProjects"
      :projects-loading="projectsLoading"
      :projects-error="error"
      :width="leftPanelWidth"
      :is-dragging="isDraggingLeft"
      :automated-tasks-active="automatedTasksOpen"
      :search-open="searchOpen"
      :folded-project-ids="foldedProjectIds"
      @toggle="toggleLeftSideBar"
      @add-project="addProject"
      @rename-project="renameProjectDisplayName"
      @remove-project="removeProjectFromHanda"
      @open-project-in-finder="openProjectInFinder"
      @new-session="handleNewSession"
      @open-settings="openSettings"
      @open-automated-tasks="openAutomatedTasks"
      @open-search="openSearch"
      @close-search="closeSearch"
      @update-folded-projects="setFoldedProjects"
      @select-session="handleSelectSession"
      @toggle-session-star="toggleSessionStar"
      @rename-session="renameSessionTitle"
      @mark-session-unread="markSessionUnread"
      @archive-session="archiveSession"
      @delete-session="deleteSessionById"
    />

    <div
      v-if="!effectiveLeftSideBarCollapsed"
      class="relative z-10 -ml-[2px] -mr-[2px] w-[4px] cursor-col-resize hover:bg-surface-active active:bg-surface-active"
      @mousedown="startDragLeft"
    ></div>

    <NewChatPage
      v-if="showNewChatPage"
      :projects="projects"
      :selected-project-id="draftProjectId"
      :agent-definitions="agentDefinitions"
      :selected-agent-id="draftAgentId"
      :model-config-id="modelConfigId"
      :model-configs="modelConfigs"
      :context-usage="newChatContextUsage"
      :disabled="loading"
      :error="error"
      :send-error="sendError"
      :draft-text="newChatDraftText"
      @project-change="handleNewChatProjectChange"
      @agent-change="setDraftAgent"
      @send-prompt="handleNewChatSend"
      @update-model-config="setModelConfig"
      @update-draft-text="handleNewChatDraftTextUpdate"
      @dictation-error="handleDictationError"
      @optimize-error="showErrorToast"
    />

    <div v-else class="relative flex min-w-0 flex-1 flex-col bg-background text-foreground">
      <ChatHeader
        :session="activeSession"
        :project-name="activeProjectName"
        :breadcrumbs="activeSession.breadcrumbs"
        :active-artifact="activeArtifact"
        :active-browser-environment="activeBrowserPanel"
        :right-sidebar-visible="showRightSidebar"
        @select-breadcrumb="handleSelectBreadcrumb"
        @launcher-error="showErrorToast"
        @toggle-right-sidebar="toggleRightSidebar"
      />

      <div class="relative flex min-h-0 flex-1">
        <ChatPanel
          hide-header
          :session="activeSession"
          :project-name="activeProjectName"
          :loading="loading"
          :error="error"
          :can-retry-load="Boolean(error) && !loading && !hasProjects"
          :send-error="sendError"
          :markdown-is-dark="themeIsDark"
          :agent-definitions="agentDefinitions"
          :agent-id="activeSession.agentId ?? draftAgentId"
          :model-config-id="modelConfigId"
          :model-configs="modelConfigs"
          :context-usage="sidebarUsage"
          :breadcrumbs="activeSession.breadcrumbs"
          :active-artifact="activeArtifact"
          :active-browser-environment="activeBrowserPanel"
          :read-only="activeSession.readOnly"
          :draft-text="activeSessionDraftText"
          :draft-attachments="activeSessionDraftAttachments"
          :user-input-submitting="userInputSubmitting"
          @send-prompt="handleSendPrompt"
          @fork-session="handleForkSession"
          @edit-user-message="handleEditUserMessage"
          @update-agent="setDraftAgent"
          @update-model-config="setModelConfig"
          @update-draft-text="handleActiveSessionDraftTextUpdate"
          @update-draft-attachments="handleActiveSessionDraftAttachmentsUpdate"
          @stop-invocation="stopActiveInvocation"
          @cancel-queued-turn="cancelQueuedTurn"
          @retry-turn="retryTurn"
          @dictation-error="handleDictationError"
          @optimize-error="showErrorToast"
          @select-breadcrumb="handleSelectBreadcrumb"
          @launcher-error="showErrorToast"
          @browser-interact="handleBrowserInteract"
          @browser-update="handleBrowserUpdate"
          @submit-user-input="submitUserInput"
          @retry-load="loadInitial"
        />

        <div
          class="relative z-30 flex h-full shrink-0 flex-col overflow-hidden border-l border-[color:var(--border-layout)]"
          :class="[
            isDraggingRightSidebar || !animateRightSidebar ? '' : 'transition-[width] duration-300 ease-in-out',
            showRightSidebar ? 'border-l' : 'border-none',
          ]"
          :style="{ width: showRightSidebar ? `${rightSidebarWidth}px` : '0px' }"
        >
          <div
            v-if="showRightSidebar"
            class="absolute bottom-0 left-0 top-0 z-40 w-[5px] -translate-x-1/2 cursor-col-resize hover:bg-[var(--surface-active)]"
            v-tooltip="'Resize right sidebar'"
            @mousedown="startDragRightSidebar"
          ></div>
          <div class="min-w-[260px] h-full flex flex-col">
            <SessionRightSidebar
              :background-runs="sidebarBackgroundRuns"
              :progress-items="sidebarProgressItems"
              :browser-environment="activeBrowserEnvironment"
              :artifacts="sidebarArtifacts"
              :parent-session-id="activeSession.parentSessionId"
              :selected-artifact-id="activeArtifactId"
              :width="rightSidebarWidth"
              @select-parent-session="handleSelectParentSession"
              @select-browser="handleSelectBrowser"
              @select-artifact="selectArtifact"
              @select-background-run="handleSelectBackgroundRun"
              @terminate-background-run="handleTerminateBackgroundRun"
            />
          </div>
        </div>
      </div>
    </div>
    <SettingsPanel
      :open="settingsOpen"
      :initial-section="settingsInitialSection"
      :theme-id="themeId"
      :theme-loading="loadingTheme"
      :theme-error="themeError"
      :gemini-api-key-set="geminiApiKeySet"
      :gemini-api-key-preview="geminiApiKeyPreview"
      :archived-projects="archivedProjects"
      :archived-session-count="archivedSessionCount"
      :archived-loading="loading"
      :archived-error="error"
      @close="closeSettings"
      @update-section="handleSettingsSectionUpdate"
      @update-theme="setTheme"
      @update-gemini-api-key="setGeminiApiKey"
      @unarchive-session="unarchiveSession"
      @delete-session="deleteSessionById"
    />

    <AutomatedTasksPage
      :open="automatedTasksOpen"
      :projects="projects"
      :agent-definitions="agentDefinitions"
      :model-configs="modelConfigs"
      :default-model-config-id="modelConfigId"
      @close="closeAutomatedTasks"
      @open-session="handleSelectSession"
      @error="showErrorToast"
    />

    <ToastStack :toasts="toasts" @dismiss="dismissToast" />
  </div>
</template>
