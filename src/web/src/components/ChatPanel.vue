<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArrowDown,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  LoaderCircle,
  Pencil,
  RotateCcw,
  Split,
} from '@lucide/vue'
import { MarkdownRender, enableMermaid } from 'markstream-vue'
import 'markstream-vue/index.css'
import AgentDetails from './AgentDetails.vue'
import ArtifactViewer from './ArtifactViewer.vue'
import BrowserViewer from './BrowserViewer.vue'
import ChatHeader from './ChatHeader.vue'
import Composer from './Composer.vue'
import ImagePreviewDialog from './ImagePreviewDialog.vue'
import UserInputForm from './UserInputForm.vue'
import type { BackendAgentDefinition, BackendModelConfigOption } from '../api/types'
import { DEFAULT_AGENT_ID } from '../agentDefaults'
import { LIVE_MARKDOWN_RENDER_PROPS, STATIC_MARKDOWN_RENDER_PROPS } from '../markdownStreamProps'
import { timelineItemsWithoutDuplicateFinalText } from '../presenters/timelineDisplay'
import type { AgentBrowserEnvironment, AgentMessage, AgentSession, Artifact, BrowserInteraction, ContextUsageSummary, EditMessagePayload, MessageAttachment, PendingUserMessage, SendPromptPayload, UserInputSubmissionPayload } from '../types'

enableMermaid(() => import('mermaid'))

defineOptions({
  name: 'ChatPanel',
})

const props = defineProps<{
  session: AgentSession
  projectName: string
  loading?: boolean
  error?: string
  sendError?: string
  markdownIsDark?: boolean
  agentDefinitions?: BackendAgentDefinition[]
  agentId?: string
  modelConfigId: string
  modelConfigs: BackendModelConfigOption[]
  contextUsage?: ContextUsageSummary
  breadcrumbs?: { id: string; label: string; title?: string | null }[]
  activeArtifact?: Artifact
  activeBrowserEnvironment?: AgentBrowserEnvironment | null
  readOnly?: boolean
  hideHeader?: boolean
  draftText?: string
  draftAttachments?: MessageAttachment[]
  userInputSubmitting?: boolean
}>()

const emit = defineEmits<{
  sendPrompt: [payload: SendPromptPayload, modelConfigId: string]
  updateAgent: [agentId: string]
  updateModelConfig: [modelConfigId: string]
  updateDraftText: [draftText: string]
  updateDraftAttachments: [attachments: MessageAttachment[]]
  stopInvocation: []
  forkSession: [sourceTurnId?: string, options?: { includeSourceTurn?: boolean; prompt?: string; attachments?: MessageAttachment[] }]
  cancelQueuedTurn: [turnId: string]
  retryTurn: [turnId: string]
  editUserMessage: [payload: EditMessagePayload, modelConfigId: string]
  dictationError: [message: string]
  optimizeError: [message: string]
  selectBreadcrumb: [id: string]
  launcherError: [message: string]
  browserInteract: [payload: BrowserInteraction]
  browserUpdate: [browser: AgentBrowserEnvironment]
  submitUserInput: [payload: UserInputSubmissionPayload]
}>()

const composerRef = ref<InstanceType<typeof Composer> | null>(null)
const copiedMessageId = ref('')
const hoveredMessageId = ref('')
const expandedUserMessageIds = ref(new Set<string>())
const overflowingUserMessageIds = ref(new Set<string>())
const editingMessage = ref<{
  sourceTurnId: string
  prompt: string
  attachments: MessageAttachment[]
} | null>(null)
const previewAttachment = ref<MessageAttachment | null>(null)
const userMessageBodyRefs = new Map<string, HTMLElement>()

const scrollContainer = ref<HTMLElement | null>(null)
const scrollContent = ref<HTMLElement | null>(null)
const isAtBottom = ref(true)
const BOTTOM_THRESHOLD = 32
const USER_MESSAGE_COLLAPSED_LINE_COUNT = 10
let resizeObserver: ResizeObserver | undefined
let autoScrollFrame: number | undefined
let resetScrollFrame: number | undefined
let isResettingScroll = false
let lastScrollTop = 0

const isGenerating = computed(() => props.session.status === 'running' || props.session.status === 'queued')
const LIVE_TURN_ACTION_STATUSES = new Set(['queued', 'running', 'waiting_input'])
const RETRYABLE_TURN_ACTION_STATUSES = new Set(['failed', 'cancelled'])

// Queued turns (sent while a run is active, not started yet) are shown in the
// composer queue instead of the chat stream. Only turns that have a visible
// user message qualify, so assistant-only queued placeholders keep rendering
// in the chat.
const queuedTurnIds = computed(() => {
  if (props.readOnly) return new Set<string>()
  const queuedAssistantTurnIds = new Set<string>()
  for (const message of props.session.messages) {
    if (message.role === 'assistant' && message.status === 'queued' && message.turnId) {
      queuedAssistantTurnIds.add(message.turnId)
    }
  }
  const ids = new Set<string>()
  for (const message of props.session.messages) {
    if (message.role === 'user' && message.turnId && queuedAssistantTurnIds.has(message.turnId)) {
      ids.add(message.turnId)
    }
  }
  return ids
})

const queuedPendingMessages = computed<PendingUserMessage[]>(() => {
  const queue: PendingUserMessage[] = []
  for (const message of props.session.messages) {
    if (message.role !== 'user' || !message.turnId || !queuedTurnIds.value.has(message.turnId)) continue
    queue.push({
      id: message.turnId,
      prompt: message.body,
      files: [],
      createdAt: message.createdAt,
      attachments: message.attachments,
    })
  }
  return queue
})

const visibleMessages = computed(() => {
  const queuedIds = queuedTurnIds.value
  const messages = queuedIds.size
    ? props.session.messages.filter((message) => !message.turnId || !queuedIds.has(message.turnId))
    : props.session.messages
  const editing = editingMessage.value
  if (!editing) return messages
  const index = messages.findIndex((message) => messageSourceTurnId(message) === editing.sourceTurnId)
  if (index < 0) return messages
  return messages.slice(0, index)
})

function cancelAutoScroll() {
  if (autoScrollFrame !== undefined) {
    window.cancelAnimationFrame(autoScrollFrame)
    autoScrollFrame = undefined
  }
}

function cancelResetScroll() {
  if (resetScrollFrame !== undefined) {
    window.cancelAnimationFrame(resetScrollFrame)
    resetScrollFrame = undefined
  }
}

function checkScroll() {
  const el = scrollContainer.value
  if (!el) return
  if (el.scrollHeight <= el.clientHeight) {
    isAtBottom.value = true
  } else {
    const nextIsAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
    if (nextIsAtBottom) {
      isAtBottom.value = true
    } else if (!isGenerating.value) {
      isAtBottom.value = false
    }
  }
}

function updateIsAtBottom() {
  const el = scrollContainer.value
  if (!el) return

  if (el.scrollHeight <= el.clientHeight) {
    isAtBottom.value = true
    return
  }

  const scrollTop = el.scrollTop
  // A genuine user scroll-up lowers scrollTop; streaming content growth keeps
  // scrollTop unchanged while scrollHeight grows. Only the former should stop
  // auto-follow — otherwise the async scroll event fired by scrollToBottom()
  // reads the already-grown layout, mistakes growth for a user scroll, and
  // latches isAtBottom to false permanently.
  const scrolledUp = scrollTop < lastScrollTop - 1
  lastScrollTop = scrollTop
  const nextIsAtBottom = el.scrollHeight - scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
  if (nextIsAtBottom) {
    isAtBottom.value = true
  } else if (scrolledUp) {
    cancelAutoScroll()
    isAtBottom.value = false
  }
  // Not at bottom but not scrolled up => content grew while following; keep going.
}

function scrollToBottom(behavior: ScrollBehavior = 'auto') {
  const el = scrollContainer.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior })
  lastScrollTop = el.scrollTop
  isAtBottom.value = true
}

function scrollToTop(behavior: ScrollBehavior = 'auto') {
  const el = scrollContainer.value
  if (!el) return
  el.scrollTo({ top: 0, behavior })
  lastScrollTop = el.scrollTop
  isAtBottom.value = el.scrollHeight <= el.clientHeight
}

function scheduleScrollToTop() {
  cancelAutoScroll()
  cancelResetScroll()
  isResettingScroll = true
  nextTick(() => {
    scrollToTop()
    resetScrollFrame = window.requestAnimationFrame(() => {
      scrollToTop()
      resetScrollFrame = window.requestAnimationFrame(() => {
        scrollToTop()
        resetScrollFrame = undefined
        isResettingScroll = false
      })
    })
  })
}

function scheduleAutoScroll() {
  if (props.activeArtifact || props.activeBrowserEnvironment) return
  if (!isGenerating.value) return
  if (isResettingScroll) return
  if (!isAtBottom.value) return
  cancelAutoScroll()
  autoScrollFrame = window.requestAnimationFrame(() => {
    autoScrollFrame = undefined
    if (!isGenerating.value || isResettingScroll || !isAtBottom.value) return
    scrollToBottom()
    autoScrollFrame = window.requestAnimationFrame(() => {
      autoScrollFrame = undefined
      if (!isGenerating.value || isResettingScroll || !isAtBottom.value) return
      scrollToBottom()
    })
  })
}

const contentSignal = computed(() => {
  if (props.activeArtifact) {
    return [
      'artifact',
      props.activeArtifact.id,
      props.activeArtifact.loading ? 'loading' : 'done',
      props.activeArtifact.error ?? '',
      props.activeArtifact.content?.length ?? 0,
      props.activeArtifact.blocks?.length ?? 0,
    ].join(':')
  }

  if (props.activeBrowserEnvironment) {
    return [
      'browser',
      props.activeBrowserEnvironment.status ?? '',
      props.activeBrowserEnvironment.url ?? '',
      props.activeBrowserEnvironment.title ?? '',
      props.activeBrowserEnvironment.screenshotUrl ?? '',
      props.activeBrowserEnvironment.streamUrl ?? '',
      props.activeBrowserEnvironment.updatedAt ?? '',
      props.activeBrowserEnvironment.lastAction ?? '',
      props.activeBrowserEnvironment.lastError ?? '',
    ].join(':')
  }

  const messages = props.session.messages
  const last = messages[messages.length - 1]
  const lastTimelineTextLength = last?.timelineItems?.reduce((total, item) => {
    return total + (item.text?.length ?? 0) + (item.summary?.length ?? 0) + (item.responseSummary?.length ?? 0)
  }, 0) ?? 0
  return `${messages.length}:${last?.body.length ?? 0}:${last?.detailEvents?.length ?? 0}:${last?.timelineItems?.length ?? 0}:${lastTimelineTextLength}`
})

watch(contentSignal, () => {
  if (props.activeArtifact || props.activeBrowserEnvironment) {
    scheduleScrollToTop()
    return
  }
  nextTick(scheduleAutoScroll)
})

watch(
  () => props.activeArtifact?.id ?? '',
  (id) => {
    if (id) {
      scheduleScrollToTop()
      return
    }
    scheduleScrollToTop()
  },
)

watch(
  () => Boolean(props.activeBrowserEnvironment),
  () => {
    scheduleScrollToTop()
  },
)

watch(
  () => props.session.id,
  () => {
    cancelEditingMessage()
    expandedUserMessageIds.value = new Set()
    scheduleScrollToTop()
    // Switching to a session leaves ChatPanel (and the Composer) mounted, so the
    // Composer's own onMounted autofocus doesn't re-fire. Focus it here so the
    // textarea is ready for typing the moment a session opens.
    focusInput()
  },
)

watch(
  () => props.session.messages.map((message) => message.id).join('|'),
  () => {
    const editing = editingMessage.value
    if (!editing) return
    const stillExists = props.session.messages.some((message) => messageSourceTurnId(message) === editing.sourceTurnId)
    if (!stillExists) cancelEditingMessage()
  },
)

const userMessageContentSignal = computed(() =>
  visibleMessages.value
    .filter((message) => message.role === 'user')
    .map((message) => `${message.id}:${message.body.length}`)
    .join('|'),
)

watch(userMessageContentSignal, () => {
  pruneUserMessageCollapseState()
  nextTick(measureUserMessageOverflow)
}, { immediate: true })

onMounted(() => {
  nextTick(() => {
    scheduleScrollToTop()
    measureUserMessageOverflow()
    if (scrollContainer.value && typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => {
        checkScroll()
        measureUserMessageOverflow()
        scheduleAutoScroll()
      })
      resizeObserver.observe(scrollContainer.value)
      if (scrollContent.value) {
        resizeObserver.observe(scrollContent.value)
      }
    }
  })
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  cancelAutoScroll()
  cancelResetScroll()
})
const detailMessageIds = computed(() => {
  const ids = new Set<string>()
  let latestAssistantId = ''
  for (const message of props.session.messages) {
    if (message.role !== 'assistant') continue
    latestAssistantId = message.id
    if (message.invocationId || message.detailEvents?.length) ids.add(message.id)
  }
  if (!ids.size && latestAssistantId && props.session.status !== 'idle') {
    ids.add(latestAssistantId)
  }
  return ids
})

function focusInput() {
  composerRef.value?.focus()
}

defineExpose({
  focusInput
})

const streamingMessageId = computed(() => {
  if (props.session.status !== 'running' && props.session.status !== 'queued') return ''
  let latestAssistantId = ''
  for (const message of props.session.messages) {
    if (message.role === 'assistant') latestAssistantId = message.id
  }
  return latestAssistantId
})

const finalAssistantActionMessageIds = computed(() => {
  const ids = new Set<string>()
  let latestTurnAssistantId = ''

  for (const message of props.session.messages) {
    if (message.role === 'user') {
      if (latestTurnAssistantId) ids.add(latestTurnAssistantId)
      latestTurnAssistantId = ''
      continue
    }

    if (message.role === 'assistant' && isMessageFinal(message)) {
      latestTurnAssistantId = message.id
    }
  }

  if (latestTurnAssistantId) ids.add(latestTurnAssistantId)
  return ids
})

function isMessageFinal(message: AgentMessage) {
  return message.id !== streamingMessageId.value
}

function markdownRenderPropsFor(message: AgentMessage) {
  return isMessageFinal(message) ? STATIC_MARKDOWN_RENDER_PROPS : LIVE_MARKDOWN_RENDER_PROPS
}

function shouldShowAgentDetails(message: AgentMessage) {
  return message.role === 'assistant' && detailMessageIds.value.has(message.id)
}

function detailEventsFor(message: AgentMessage) {
  return message.detailEvents ?? (message.id === `${props.session.latestInvocationId}-assistant` ? props.session.detailEvents : [])
}

function timelineItemsFor(message: AgentMessage) {
  return timelineItemsWithoutDuplicateFinalText(message.timelineItems, message.body)
}

function detailElapsedFor(message: AgentMessage) {
  return message.elapsed ?? props.session.elapsed
}

function detailStatusFor(message: AgentMessage) {
  return message.status ?? props.session.status
}

function detailTokenUsageFor(message: AgentMessage) {
  return message.tokenUsage
}

function runDividerLabelFor(message: AgentMessage) {
  if (message.triggerKind !== 'task_notification') return undefined
  const label = (message.systemRunLabel ?? '').trim()
  if (label) return label
  if (message.status === 'failed') return 'Background run failed'
  if (message.status === 'cancelled') return 'Background run cancelled'
  return 'Background run completed'
}

function isTaskNotificationMessage(message: AgentMessage) {
  return message.role === 'assistant' && message.triggerKind === 'task_notification'
}

function messageArticleClass(message: AgentMessage) {
  return [
    message.role === 'user' ? 'justify-end' : 'justify-start w-full',
    isTaskNotificationMessage(message) ? '-mt-3' : '',
  ]
}

function isMessageWorking(message: AgentMessage) {
  if (!shouldShowAgentDetails(message)) return false
  const status = detailStatusFor(message)
  return status === 'running' || status === 'queued'
}

function workingSummaryFor(message: AgentMessage) {
  const label = detailStatusFor(message) === 'queued' ? 'Queued' : 'Working'
  return `${label} for ${detailElapsedFor(message)}`
}

const canStopInvocation = computed(() => {
  if (props.readOnly) return false
  return props.session.status === 'running' || props.session.status === 'queued'
})

const effectiveMarkdownIsDark = computed(() => {
  if (props.markdownIsDark !== undefined) return props.markdownIsDark
  if (typeof document === 'undefined') return true
  return document.documentElement.dataset.themeMode !== 'light'
})

function formatMessageDateTime(message: AgentMessage) {
  const date = new Date(message.createdAt)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const sameYear = date.getFullYear() === now.getFullYear()
  const sameDay =
    sameYear &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()

  return new Intl.DateTimeFormat(undefined, {
    month: sameDay ? undefined : 'short',
    day: sameDay ? undefined : 'numeric',
    year: sameYear ? undefined : 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function clearHoveredMessage(messageId: string) {
  if (hoveredMessageId.value === messageId) hoveredMessageId.value = ''
}

function showMessageActions(message: AgentMessage) {
  if (shouldShowMessageActions(message)) hoveredMessageId.value = message.id
}

function shouldShowMessageActions(message: AgentMessage) {
  return message.role === 'user' || finalAssistantActionMessageIds.value.has(message.id)
}

function setUserMessageBodyRef(messageId: string, el: unknown) {
  if (el instanceof HTMLElement) {
    userMessageBodyRefs.set(messageId, el)
    nextTick(measureUserMessageOverflow)
    return
  }
  userMessageBodyRefs.delete(messageId)
}

function shouldShowUserMessageToggle(message: AgentMessage) {
  return message.role === 'user' && overflowingUserMessageIds.value.has(message.id)
}

function isUserMessageExpanded(message: AgentMessage) {
  return expandedUserMessageIds.value.has(message.id)
}

function toggleUserMessageExpanded(messageId: string) {
  const next = new Set(expandedUserMessageIds.value)
  if (next.has(messageId)) {
    next.delete(messageId)
  } else {
    next.add(messageId)
  }
  expandedUserMessageIds.value = next
  nextTick(() => {
    measureUserMessageOverflow()
    checkScroll()
  })
}

function measureUserMessageOverflow() {
  if (typeof window === 'undefined') return

  const next = new Set<string>()
  for (const [messageId, el] of userMessageBodyRefs) {
    const lineHeight = Number.parseFloat(window.getComputedStyle(el).lineHeight)
    const collapsedHeight = (Number.isFinite(lineHeight) ? lineHeight : 24) * USER_MESSAGE_COLLAPSED_LINE_COUNT
    if (el.scrollHeight > collapsedHeight + 1) next.add(messageId)
  }

  if (!setsEqual(overflowingUserMessageIds.value, next)) {
    overflowingUserMessageIds.value = next
  }

  pruneUserMessageCollapseState()
}

function pruneUserMessageCollapseState() {
  const visibleUserIds = new Set(
    visibleMessages.value
      .filter((message) => message.role === 'user')
      .map((message) => message.id),
  )
  const nextExpanded = new Set([...expandedUserMessageIds.value].filter((id) => visibleUserIds.has(id)))
  if (!setsEqual(expandedUserMessageIds.value, nextExpanded)) {
    expandedUserMessageIds.value = nextExpanded
  }
}

function setsEqual(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false
  for (const item of left) {
    if (!right.has(item)) return false
  }
  return true
}

function messageSourceTurnId(message: AgentMessage) {
  if (message.turnId) return message.turnId
  if (message.invocationId && !message.invocationId.startsWith('session:')) return message.invocationId
  if (message.id.endsWith('-user')) return message.id.slice(0, -'-user'.length)
  if (message.id.endsWith('-assistant')) return message.id.slice(0, -'-assistant'.length)
  return ''
}

function canEditMessage(message: AgentMessage) {
  const sourceTurnId = messageSourceTurnId(message)
  return Boolean(
    message.role === 'user'
      && sourceTurnId
      && !sourceTurnId.startsWith('session:')
      && !props.readOnly
      && !isGenerating.value
      && props.session.id !== 'empty',
  )
}

function canForkUserMessage(message: AgentMessage) {
  const sourceTurnId = messageSourceTurnId(message)
  return Boolean(
    message.role === 'user'
      && sourceTurnId
      && !sourceTurnId.startsWith('session:')
      && !props.readOnly
      && !isLiveSourceTurn(sourceTurnId)
      && props.session.id !== 'empty',
  )
}

function isLiveSourceTurn(sourceTurnId: string) {
  return props.session.messages.some((message) =>
    messageSourceTurnId(message) === sourceTurnId
      && Boolean(message.status && LIVE_TURN_ACTION_STATUSES.has(message.status)),
  )
}

function canRetryMessage(message: AgentMessage) {
  const sourceTurnId = messageSourceTurnId(message)
  return Boolean(
    message.role === 'assistant'
      && message.status
      && RETRYABLE_TURN_ACTION_STATUSES.has(message.status)
      && sourceTurnId
      && !sourceTurnId.startsWith('session:')
      && !props.readOnly
      && !isGenerating.value
      && props.session.id !== 'empty',
  )
}

function retryMessage(message: AgentMessage) {
  const sourceTurnId = messageSourceTurnId(message)
  if (!canRetryMessage(message) || !sourceTurnId) return
  hoveredMessageId.value = ''
  emit('retryTurn', sourceTurnId)
}

function startEditingMessage(message: AgentMessage) {
  if (!canEditMessage(message)) return
  editingMessage.value = {
    sourceTurnId: messageSourceTurnId(message),
    prompt: message.body,
    attachments: [...(message.attachments ?? [])],
  }
  hoveredMessageId.value = ''
  nextTick(() => composerRef.value?.focus())
}

function cancelEditingMessage() {
  editingMessage.value = null
}

function submitEditedMessage(payload: EditMessagePayload) {
  emit('editUserMessage', payload, props.modelConfigId)
}

function canPreviewAttachment(message: AgentMessage, attachment: MessageAttachment) {
  return message.role === 'user' && attachment.isImage
}

function openImagePreview(attachment: MessageAttachment) {
  previewAttachment.value = attachment
}

function closeImagePreview() {
  previewAttachment.value = null
}

async function copyMessage(message: AgentMessage) {
  const text = message.body.trim()
  if (!text) return

  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }

  copiedMessageId.value = message.id
  window.setTimeout(() => {
    if (copiedMessageId.value === message.id) copiedMessageId.value = ''
  }, 1200)
}

function forkMessage(message: AgentMessage) {
  const sourceTurnId = messageSourceTurnId(message)
  const turnId = sourceTurnId.startsWith('session:') ? undefined : sourceTurnId || undefined
  if (message.role === 'user' && turnId) {
    // Forking a user message drops it (and everything below) from the new
    // session and restores its text and attachments in the composer for
    // re-editing.
    emit('forkSession', turnId, {
      includeSourceTurn: false,
      prompt: message.body,
      attachments: message.attachments ?? [],
    })
    return
  }
  emit('forkSession', turnId)
}

function pendingUserInputFor(message: AgentMessage) {
  if (props.readOnly) return undefined
  if (message.role !== 'assistant' || !message.pendingUserInput) return undefined
  if (message.status !== 'waiting_input') return undefined
  return message.pendingUserInput
}

function submitUserInputAnswers(payload: { requestId: string; turnId: string; answers: { id: string; selected: string[]; free_text?: string }[] }) {
  emit('submitUserInput', {
    turnId: payload.turnId,
    requestId: payload.requestId,
    answers: payload.answers,
  })
}

function cancelUserInput(payload: { requestId: string; turnId: string }) {
  emit('submitUserInput', {
    turnId: payload.turnId,
    requestId: payload.requestId,
    cancelled: true,
  })
}
</script>

<template>
  <main class="flex min-w-0 flex-1 flex-col bg-background text-foreground">
    <ChatHeader
      v-if="!hideHeader"
      :session="session"
      :project-name="projectName"
      :breadcrumbs="breadcrumbs"
      :active-artifact="activeArtifact"
      :active-browser-environment="activeBrowserEnvironment"
      @select-breadcrumb="emit('selectBreadcrumb', $event)"
      @launcher-error="emit('launcherError', $event)"
    />

    <div class="chat-scroll-frame relative min-h-0 flex-1">
      <div
        ref="scrollContainer"
        class="h-full overflow-y-auto"
        @scroll.passive="updateIsAtBottom"
      >
      <div
        ref="scrollContent"
        :class="activeBrowserEnvironment
          ? 'h-full w-full'
          : activeArtifact
            ? 'mx-auto w-full max-w-[820px] px-6 py-6'
            : 'mx-auto flex w-full max-w-[820px] flex-col gap-7 px-6 py-6'"
      >
        <BrowserViewer
          v-if="activeBrowserEnvironment"
          :browser="activeBrowserEnvironment"
          @interact="emit('browserInteract', $event)"
          @browser-update="emit('browserUpdate', $event)"
        />

        <ArtifactViewer
          v-else-if="activeArtifact"
          :artifact="activeArtifact"
          :markdown-is-dark="effectiveMarkdownIsDark"
        />

        <template v-else>
          <div v-if="error" class="rounded-xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-[13px] text-destructive">
            {{ error }}
          </div>

          <article
            v-for="message in visibleMessages"
            :key="message.id"
            class="flex"
            :class="messageArticleClass(message)"
            @mouseenter="showMessageActions(message)"
            @mouseleave="clearHoveredMessage(message.id)"
            @click="showMessageActions(message)"
          >
            <div :class="message.role === 'user' ? 'max-w-[78%]' : 'w-full'">
              <div
                class="text-[14px] leading-6"
                :class="
                  message.role === 'user'
                    ? 'rounded-xl border border-[color:var(--user-message-border)] px-4 py-3 bg-[var(--user-message-bg)] text-[color:var(--user-message-fg)]'
                    : 'w-full text-[color:var(--text-secondary)]'
                "
              >
                <p
                  v-if="message.meta && !shouldShowAgentDetails(message)"
                  class="mb-2 border-b border-[color:var(--border-muted)] pb-2 text-[12px] text-[color:var(--text-muted)]"
                >
                  {{ message.meta }}
                </p>
                <div
                  v-if="message.attachments?.length"
                  class="mb-2 flex flex-wrap gap-2"
                >
                  <template
                    v-for="attachment in message.attachments"
                    :key="attachment.id"
                  >
                    <button
                      v-if="canPreviewAttachment(message, attachment)"
                      class="message-attachment-card flex max-w-[220px] items-center gap-2 rounded-lg border p-1.5 text-left no-underline transition focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                      :class="message.role === 'user' ? 'message-attachment-card-user' : 'message-attachment-card-default'"
                      type="button"
                      :aria-label="`Preview image ${attachment.filename}`"
                      :data-testid="`message-image-attachment-${attachment.id}`"
                      @click.stop="openImagePreview(attachment)"
                    >
                      <img
                        :src="attachment.url"
                        :alt="attachment.filename"
                        class="h-10 w-10 shrink-0 rounded-md object-cover"
                      />
                      <span class="min-w-0">
                        <span
                          class="message-attachment-title block truncate text-[12px] font-medium"
                          v-tooltip="{ content: attachment.filename, overflowOnly: true }"
                        >{{ attachment.filename }}</span>
                        <span class="message-attachment-kind block text-[11px] uppercase">{{ attachment.kind }}</span>
                      </span>
                    </button>
                    <a
                      v-else
                      :href="attachment.url"
                      target="_blank"
                      rel="noopener"
                      class="message-attachment-card flex max-w-[220px] items-center gap-2 rounded-lg border p-1.5 no-underline transition focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                      :class="message.role === 'user' ? 'message-attachment-card-user' : 'message-attachment-card-default'"
                    >
                      <img
                        v-if="attachment.isImage"
                        :src="attachment.url"
                        :alt="attachment.filename"
                        class="h-10 w-10 shrink-0 rounded-md object-cover"
                      />
                      <span
                        v-else
                        class="message-attachment-icon flex h-10 w-10 shrink-0 items-center justify-center rounded-md"
                      >
                        <FileText :size="16" />
                      </span>
                      <span class="min-w-0">
                        <span
                          class="message-attachment-title block truncate text-[12px] font-medium"
                          v-tooltip="{ content: attachment.filename, overflowOnly: true }"
                        >{{ attachment.filename }}</span>
                        <span class="message-attachment-kind block text-[11px] uppercase">{{ attachment.kind }}</span>
                      </span>
                    </a>
                  </template>
                </div>
                <template v-if="message.role === 'user'">
                  <p
                    :ref="(el) => setUserMessageBodyRef(message.id, el)"
                    class="user-message-body whitespace-pre-wrap"
                    :class="{
                      'is-collapsed': shouldShowUserMessageToggle(message) && !isUserMessageExpanded(message),
                    }"
                    :data-testid="`user-message-body-${message.id}`"
                  >{{ message.body || (message.attachments?.length ? '' : '...') }}</p>
                  <button
                    v-if="shouldShowUserMessageToggle(message)"
                    class="mt-2 inline-flex h-6 appearance-none items-center gap-1 rounded-md border-0 bg-transparent p-0 text-[13px] font-medium text-[color:var(--text-muted)] transition hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                    type="button"
                    :aria-expanded="isUserMessageExpanded(message)"
                    :aria-label="isUserMessageExpanded(message) ? 'Show less' : 'Show more'"
                    data-testid="user-message-toggle"
                    @click.stop="toggleUserMessageExpanded(message.id)"
                  >
                    <span>{{ isUserMessageExpanded(message) ? 'Show less' : 'Show more' }}</span>
                    <ChevronUp v-if="isUserMessageExpanded(message)" :size="16" />
                    <ChevronDown v-else :size="16" />
                  </button>
                </template>
                <template v-else>
                  <AgentDetails
                    v-if="shouldShowAgentDetails(message)"
                    :elapsed="detailElapsedFor(message)"
                    :status="detailStatusFor(message)"
                    :token-usage="detailTokenUsageFor(message)"
                    :events="detailEventsFor(message)"
                    :timeline-items="timelineItemsFor(message)"
                    :markdown-is-dark="effectiveMarkdownIsDark"
                    :run-divider-label="runDividerLabelFor(message)"
                    :show-live-summary="false"
                  />
                  <MarkdownRender
                    v-if="message.body || !shouldShowAgentDetails(message)"
                    class="markdown-body"
                    v-bind="markdownRenderPropsFor(message)"
                    :content="message.body || '...'"
                    :final="isMessageFinal(message)"
                    :html-policy="isMessageFinal(message) ? 'trusted' : 'safe'"
                    :is-dark="effectiveMarkdownIsDark"
                  />
                  <UserInputForm
                    v-if="pendingUserInputFor(message)"
                    :request="pendingUserInputFor(message)!"
                    :submitting="userInputSubmitting"
                    @submit="submitUserInputAnswers"
                    @cancel="cancelUserInput"
                  />
                  <div
                    v-if="isMessageWorking(message)"
                    class="mb-2 mt-3 inline-flex items-center gap-1.5 text-[13px] font-medium text-[color:var(--text-muted)]"
                  >
                    <LoaderCircle aria-hidden="true" :size="14" class="animate-spin shrink-0" />
                    <span class="inline-block min-w-[13ch] [font-variant-numeric:tabular-nums]">{{ workingSummaryFor(message) }}</span>
                  </div>
                  <slot
                    v-if="message.role === 'assistant'"
                    name="assistant-after"
                    :message="message"
                  />
                </template>
              </div>
              <div
                v-if="shouldShowMessageActions(message)"
                class="mt-2 flex h-6 items-center gap-2 text-[12px] text-[color:var(--text-muted)] transition-opacity"
                :class="[
                  message.role === 'user' ? 'ml-auto justify-end pr-1' : 'justify-start',
                  hoveredMessageId === message.id || copiedMessageId === message.id ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0',
                ]"
              >
                <span v-if="message.role === 'user'" class="mr-3">{{ formatMessageDateTime(message) }}</span>
                <button
                  v-if="canForkUserMessage(message)"
                  class="grid h-6 w-6 place-items-center rounded-md transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                  type="button"
                  aria-label="Fork session from this message"
                  data-testid="message-fork"
                  v-tooltip="'Fork session'"
                  @click.stop.prevent="forkMessage(message)"
                >
                  <Split :size="15" />
                </button>
                <button
                  v-if="canEditMessage(message)"
                  class="grid h-6 w-6 place-items-center rounded-md transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                  type="button"
                  aria-label="Edit message"
                  data-testid="message-edit"
                  @click.stop.prevent="startEditingMessage(message)"
                >
                  <Pencil :size="15" />
                </button>
                <template v-if="message.role === 'assistant'">
                  <button
                    v-if="canRetryMessage(message)"
                    class="grid h-6 w-6 place-items-center rounded-md transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                    type="button"
                    aria-label="Retry this turn"
                    data-testid="message-retry"
                    v-tooltip="'Retry'"
                    @click.stop.prevent="retryMessage(message)"
                  >
                    <RotateCcw :size="15" />
                  </button>
                  <button
                    class="grid h-6 w-6 place-items-center rounded-md transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                    type="button"
                    aria-label="Fork session from this message"
                    v-tooltip="'Fork session'"
                    @click.stop.prevent="forkMessage(message)"
                  >
                    <Split :size="15" />
                  </button>
                </template>
                <button
                  class="grid h-6 w-6 place-items-center rounded-md transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                  type="button"
                  :aria-label="copiedMessageId === message.id ? 'Copied message' : 'Copy message'"
                  v-tooltip="copiedMessageId === message.id ? 'Copied' : 'Copy message'"
                  @click.stop="copyMessage(message)"
                >
                  <Check v-if="copiedMessageId === message.id" :size="15" />
                  <Copy v-else :size="15" />
                </button>
                <span v-if="message.role === 'assistant'" class="ml-3">{{ formatMessageDateTime(message) }}</span>
              </div>
            </div>
          </article>
        </template>

      </div>
      </div>

      <Transition name="scroll-arrow">
        <button
          v-if="!activeArtifact && !activeBrowserEnvironment && !isAtBottom"
          class="absolute bottom-2 left-1/2 z-20 grid h-9 w-9 -translate-x-1/2 place-items-center rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-muted)] text-[color:var(--text-secondary)] shadow-md transition hover:bg-[linear-gradient(var(--surface-hover),var(--surface-hover))] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
          type="button"
          aria-label="Scroll to bottom"
          @click="scrollToBottom()"
        >
          <ArrowDown :size="18" />
        </button>
      </Transition>
    </div>

    <footer v-if="!readOnly && !activeBrowserEnvironment" class="chat-composer-footer bg-background px-6 pt-0 pb-3">
      <Composer
        ref="composerRef"
        :disabled="loading"
        :can-stop="canStopInvocation"
        :send-error="sendError"
        :session-id="session.id"
        :project-id="session.projectId"
        :agent-definitions="agentDefinitions ?? []"
        :agent-id="session.agentId ?? agentId ?? DEFAULT_AGENT_ID"
        :model-config-id="modelConfigId"
        :model-configs="modelConfigs"
        :context-usage="contextUsage"
        :draft-text="draftText"
        :draft-attachments="draftAttachments"
        :editing-message="editingMessage"
        :pending-messages="queuedPendingMessages"
        @remove-pending-message="emit('cancelQueuedTurn', $event)"
        @send="emit('sendPrompt', $event, modelConfigId)"
        @edit-send="submitEditedMessage"
        @cancel-edit="cancelEditingMessage"
        @update-agent="emit('updateAgent', $event)"
        @update-model-config="emit('updateModelConfig', $event)"
        @update-draft-text="emit('updateDraftText', $event)"
        @update-draft-attachments="emit('updateDraftAttachments', $event)"
        @stop="emit('stopInvocation')"
        @dictation-error="emit('dictationError', $event)"
        @optimize-error="emit('optimizeError', $event)"
      />
    </footer>

    <ImagePreviewDialog
      v-if="previewAttachment"
      :attachment="previewAttachment"
      @close="closeImagePreview"
    />
  </main>
</template>

<style scoped>
.chat-composer-footer {
  border-color: transparent;
}

.message-attachment-card-default {
  background: var(--panel-bg);
  border-color: var(--border-subtle);
}

.message-attachment-card-default:hover {
  background: var(--surface-hover);
}

.message-attachment-card-default .message-attachment-title {
  color: var(--text-primary);
}

.message-attachment-card-default .message-attachment-kind {
  color: var(--text-muted);
}

.message-attachment-card-default .message-attachment-icon {
  background: var(--surface-hover);
  color: var(--text-secondary);
}

.message-attachment-card-user {
  background: color-mix(in srgb, var(--user-message-bg) 92%, var(--user-message-fg) 8%);
  border-color: color-mix(in srgb, var(--user-message-fg) 18%, transparent);
}

.message-attachment-card-user:hover {
  background: color-mix(in srgb, var(--user-message-bg) 88%, var(--user-message-fg) 12%);
}

.message-attachment-card-user .message-attachment-title {
  color: var(--user-message-fg);
}

.message-attachment-card-user .message-attachment-kind {
  color: color-mix(in srgb, var(--user-message-fg) 64%, transparent);
}

.message-attachment-card-user .message-attachment-icon {
  background: color-mix(in srgb, var(--user-message-bg) 84%, var(--user-message-fg) 16%);
  color: color-mix(in srgb, var(--user-message-fg) 72%, transparent);
}

.user-message-body.is-collapsed {
  max-height: calc(1.5rem * 10);
  overflow: hidden;
}

.scroll-arrow-enter-active,
.scroll-arrow-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.scroll-arrow-enter-from,
.scroll-arrow-leave-to {
  opacity: 0;
  transform: translateY(0.5rem);
}
</style>
