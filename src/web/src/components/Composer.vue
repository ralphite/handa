<script setup lang="ts">
import { ArrowRight, Bot, Box, FileText, Hourglass, Loader2, Mic, Paperclip, Pencil, Sparkles, Square, Target, Undo2, X } from '@lucide/vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useDictation } from '../composables/useDictation'
import { useOptimizePrompt } from '../composables/useOptimizePrompt'
import type { BackendAgentDefinition, BackendModelConfigOption } from '../api/types'
import { COMPOSER_AGENT_LABELS, COMPOSER_AGENT_ORDER, DEFAULT_AGENT_ID } from '../agentDefaults'
import type { ContextUsageSummary, EditMessagePayload, MessageAttachment, PendingUserMessage, SendPromptPayload } from '../types'
import ComposerDropdown from './ComposerDropdown.vue'
import ContextUsageDialog from './ContextUsageDialog.vue'
import ContextUsageRing from './ContextUsageRing.vue'
import SlashCommandMenu, { type SlashMenuItem } from './SlashCommandMenu.vue'
import { filterSlashCommands, slashTokenAt, type SlashCommand, type SlashCommandKind } from '../slashCommands'

defineOptions({
  name: 'Composer',
})

const props = withDefaults(defineProps<{
  disabled?: boolean
  canStop?: boolean
  sessionId?: string
  projectId?: string
  agentDefinitions?: BackendAgentDefinition[]
  agentId?: string
  modelConfigId: string
  modelConfigs: BackendModelConfigOption[]
  contextUsage?: ContextUsageSummary
  pendingMessages?: PendingUserMessage[]
  goalCommandEnabled?: boolean
  sendError?: string
  draftText?: string
  /** When set, the primary action renders as a labelled pill (e.g. "Create") instead of the → arrow. */
  submitLabel?: string
  /** Render at full container width instead of the default centered max-w-[820px]. */
  fullWidth?: boolean
  /** Focus the prompt textarea when the composer mounts. */
  autoFocus?: boolean
  editingMessage?: {
    sourceTurnId: string
    prompt: string
    attachments: MessageAttachment[]
  } | null
  /**
   * Existing server-side attachments to prefill into a fresh (non-edit) message,
   * e.g. when forking a user message that had attachments. They are cloned onto
   * the new turn on send via existingAttachmentIds.
   */
  draftAttachments?: MessageAttachment[]
}>(), {
  // Boolean props are cast to `false` when absent in Vue, so without an explicit
  // default the composer would never autofocus. Default to `true` and let callers
  // (e.g. AutomatedTaskEditor) opt out with `:auto-focus="false"`.
  autoFocus: true,
  goalCommandEnabled: true,
})

const emit = defineEmits<{
  send: [payload: SendPromptPayload]
  editSend: [payload: EditMessagePayload]
  cancelEdit: []
  updateAgent: [agentId: string]
  updateModelConfig: [modelConfigId: string]
  updateDraftText: [draftText: string]
  updateDraftAttachments: [attachments: MessageAttachment[]]
  stop: []
  removePendingMessage: [id: string]
  dictationError: [message: string]
  optimizeError: [message: string]
}>()

interface LocalAttachment {
  id: string
  file: File
  previewUrl: string | null
  isImage: boolean
}

interface ModelOptionDisplay {
  provider: 'gemini' | null
  name: string
  tier: string
  badge: 'fast' | 'local' | null
}

const MODEL_TIER_SUFFIXES = new Set(['Low', 'Medium', 'High', 'Small'])
const GEMINI_LABEL_PREFIX = /^Gemini\s+/i
const COMPOSER_TEXTAREA_MAX_HEIGHT = 240
const SLASH_COMMAND_ICONS: Record<SlashCommandKind, Component> = {
  model: Box,
  goal: Target,
  optimize: Sparkles,
}

const draft = ref(props.draftText ?? '')
const attachments = ref<LocalAttachment[]>([])
const editExistingAttachments = ref<MessageAttachment[]>([])
// Server-side attachments prefilled into a fresh (non-edit) message, e.g. when
// forking a message that had attachments. Cloned onto the new turn on send.
const prefilledAttachments = ref<MessageAttachment[]>([])
const draftGoal = ref(false)
const editSubmitting = ref(false)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const composerRootRef = ref<HTMLElement | null>(null)
const contextDialogOpen = ref(false)

// Slash-command palette state. `slashLevel` is the open level (null = closed):
// `commands` lists matching commands and is driven by the draft; `model` is the
// model picker, a keyboard-driven modal list that ignores the parked draft.
const slashLevel = ref<'commands' | 'model' | null>(null)
const slashHighlight = ref(0)
const slashCommandMatches = ref<SlashCommand[]>([])
// Source range of the active `/token` so choosing a command excises just the
// token and preserves any surrounding draft text.
const slashTokenRange = ref<{ start: number; end: number } | null>(null)
const sendAfterDictation = ref(false)
const lastSubmitted = ref<{ prompt: string; files: File[]; existingAttachments: MessageAttachment[]; goal: boolean } | null>(null)
const stashedDraftBeforeEdit = ref<string | null>(null)
let stashedAttachmentsBeforeEdit: LocalAttachment[] | null = null
let textareaResizeObserver: ResizeObserver | null = null
let observedTextareaWidth = 0

let attachmentSeq = 0

function addFiles(files: FileList | File[] | null | undefined) {
  addLocalFiles(attachments.value, files)
}

function addLocalFiles(target: LocalAttachment[], files: FileList | File[] | null | undefined) {
  if (!files) return
  for (const file of Array.from(files)) {
    const isImage = file.type.startsWith('image/')
    target.push({
      id: `att-${attachmentSeq++}`,
      file,
      previewUrl: isImage ? URL.createObjectURL(file) : null,
      isImage,
    })
  }
}

function removeAttachment(id: string) {
  removeLocalAttachment(attachments.value, id)
}

function removeExistingAttachment(id: string) {
  if (isEditing.value) {
    editExistingAttachments.value = editExistingAttachments.value.filter((item) => item.id !== id)
    return
  }
  prefilledAttachments.value = prefilledAttachments.value.filter((item) => item.id !== id)
  emit('updateDraftAttachments', prefilledAttachments.value)
}

function removeLocalAttachment(target: LocalAttachment[], id: string) {
  const index = target.findIndex((item) => item.id === id)
  if (index < 0) return
  const [removed] = target.splice(index, 1)
  if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl)
}

function clearAttachments() {
  clearLocalAttachments(attachments.value)
}

function clearLocalAttachments(target: LocalAttachment[]) {
  for (const item of target) {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
  }
  target.splice(0, target.length)
}

function openFileDialog() {
  fileInputRef.value?.click()
}

function handleFileInputChange(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(input.files)
  input.value = ''
}

function handlePaste(event: ClipboardEvent) {
  pasteFiles(event, addFiles)
}

function pasteFiles(event: ClipboardEvent, add: (files: File[]) => void) {
  const items = event.clipboardData?.items
  if (!items) return
  const pasted: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind === 'file') {
      const file = item.getAsFile()
      if (file) pasted.push(file)
    }
  }
  if (pasted.length) {
    event.preventDefault()
    add(pasted)
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function focusInput() {
  if (props.autoFocus === false) return
  nextTick(() => textareaRef.value?.focus())
}

function syncTextareaHeight() {
  const textarea = textareaRef.value
  if (!textarea) return
  textarea.style.height = 'auto'
  const nextHeight = Math.min(textarea.scrollHeight, COMPOSER_TEXTAREA_MAX_HEIGHT)
  textarea.style.height = `${nextHeight}px`
  textarea.style.overflowY = textarea.scrollHeight > COMPOSER_TEXTAREA_MAX_HEIGHT ? 'auto' : 'hidden'
}

onMounted(() => {
  if (props.autoFocus !== false) focusInput()
  syncTextareaHeight()
  if (typeof ResizeObserver !== 'undefined' && textareaRef.value) {
    textareaResizeObserver = new ResizeObserver(([entry]) => {
      const width = Math.round(entry?.contentRect.width ?? 0)
      if (width === observedTextareaWidth) return
      observedTextareaWidth = width
      syncTextareaHeight()
    })
    textareaResizeObserver.observe(textareaRef.value)
  }
})

onUnmounted(() => {
  textareaResizeObserver?.disconnect()
  clearAttachments()
  if (stashedAttachmentsBeforeEdit) clearLocalAttachments(stashedAttachmentsBeforeEdit)
})

const isEditing = computed(() => Boolean(props.editingMessage))
// In edit mode we show the edited message's existing attachments; otherwise we
// show any server-side attachments prefilled for a fresh message (e.g. forking).
const displayedExistingAttachments = computed(() =>
  isEditing.value ? editExistingAttachments.value : prefilledAttachments.value,
)
const canSend = computed(() =>
  Boolean(draft.value.trim())
  || attachments.value.length > 0
  || prefilledAttachments.value.length > 0,
)
const canSendEdit = computed(() =>
  Boolean(draft.value.trim())
  || editExistingAttachments.value.length > 0
  || attachments.value.length > 0,
)
const mainComposerDisabled = computed(() => Boolean(props.disabled))
const composerInputDisabled = computed(() => Boolean(mainComposerDisabled.value || editSubmitting.value))
const showStopAction = computed(() => {
  return props.canStop
    && !isEditing.value
    && !canSend.value
    && !dictation.isRecording.value
    && !dictation.isTranscribing.value
    && !sendAfterDictation.value
})

watch(
  () => props.disabled,
  (isDisabled) => {
    if (!isDisabled) focusInput()
  },
)

watch(
  () => props.draftText,
  (nextDraft) => {
    if (isEditing.value) return
    const normalized = nextDraft ?? ''
    if (normalized !== draft.value) draft.value = normalized
  },
)

watch(
  () => props.sessionId,
  () => {
    draftGoal.value = false
  },
)

watch(
  () => props.draftAttachments,
  (next) => {
    if (isEditing.value) return
    const nextList = next ?? []
    // Compare by id sequence so our own removal/clear emits (which round-trip
    // back through this prop) don't clobber in-progress edits.
    const sameIds = nextList.length === prefilledAttachments.value.length
      && nextList.every((item, index) => item.id === prefilledAttachments.value[index]?.id)
    if (!sameIds) prefilledAttachments.value = [...nextList]
  },
  { immediate: true },
)

watch(draft, (nextDraft) => {
  if (isEditing.value) return
  if (nextDraft !== (props.draftText ?? '')) emit('updateDraftText', nextDraft)
})

watch(draft, syncTextareaHeight, { flush: 'post' })

watch(
  () => props.sendError,
  (message) => {
    if (isEditing.value) return
    if (!message || !lastSubmitted.value) return
    draft.value = lastSubmitted.value.prompt
    draftGoal.value = lastSubmitted.value.goal
    clearAttachments()
    addFiles(lastSubmitted.value.files)
    prefilledAttachments.value = [...lastSubmitted.value.existingAttachments]
    emit('updateDraftAttachments', prefilledAttachments.value)
    lastSubmitted.value = null
    focusInput()
  },
)

watch(
  () => props.sendError,
  (message) => {
    if (!message || !editSubmitting.value) return
    editSubmitting.value = false
    focusInput()
  },
)

watch(
  () => props.editingMessage,
  (message, previous) => {
    if (!message) {
      const hadEditingState = Boolean(previous)
        || stashedDraftBeforeEdit.value !== null
        || stashedAttachmentsBeforeEdit !== null
      const submitted = editSubmitting.value
      if (hadEditingState) {
        const editLocalAttachments = attachments.value
        const restoredAttachments = submitted ? [] : (stashedAttachmentsBeforeEdit ?? [])
        if (editLocalAttachments !== restoredAttachments) clearLocalAttachments(editLocalAttachments)
        attachments.value = restoredAttachments
        draft.value = submitted ? '' : (stashedDraftBeforeEdit.value ?? '')
      }
      editExistingAttachments.value = []
      stashedDraftBeforeEdit.value = null
      stashedAttachmentsBeforeEdit = null
      editSubmitting.value = false
      focusInput()
      return
    }
    if (!previous) {
      stashedDraftBeforeEdit.value = draft.value
      stashedAttachmentsBeforeEdit = attachments.value
    } else {
      clearAttachments()
    }
    attachments.value = []
    draft.value = message.prompt
    editExistingAttachments.value = [...message.attachments]
    editSubmitting.value = false
    focusInput()
  },
  { immediate: true },
)

function appendTranscript(text: string) {
  const cleaned = text.trim()
  if (!cleaned) return
  if (draft.value && !/\s$/.test(draft.value)) {
    draft.value = `${draft.value} ${cleaned}`
  } else {
    draft.value = `${draft.value}${cleaned}`
  }
  focusInput()
}

const dictation = useDictation({
  getSessionId: () => (props.sessionId && props.sessionId !== 'empty' ? props.sessionId : undefined),
  getProjectId: () => props.projectId || undefined,
  onTranscript: appendTranscript,
  onError: (message) => emit('dictationError', message),
})

const optimizer = useOptimizePrompt({
  getSessionId: () => (props.sessionId && props.sessionId !== 'empty' ? props.sessionId : undefined),
  getProjectId: () => props.projectId || undefined,
  onError: (message) => emit('optimizeError', message),
})

// Original draft stashed when an optimized rewrite replaces it. The undo
// affordance only stays active while the draft still equals the rewrite —
// any manual edit (or send/edit-mode reset) invalidates it implicitly.
const draftBeforeOptimize = ref<string | null>(null)
const lastOptimizedText = ref('')

const canUndoOptimize = computed(() => {
  return draftBeforeOptimize.value !== null && draft.value === lastOptimizedText.value
})

// Replace the whole draft through a native, undoable edit so the browser records
// it on the textarea's own undo stack — letting cmd+z / shift+cmd+z step across
// an optimize rewrite. A plain `draft.value = ...` assignment wipes that stack,
// leaving nothing to undo. `execCommand('insertText')` fires an `input` event, so
// v-model keeps `draft` in sync; we only assign directly on the fallback path
// (no textarea mounted, or the command unsupported).
function replaceDraftUndoable(text: string) {
  const textarea = textareaRef.value
  if (textarea) {
    textarea.focus()
    textarea.setSelectionRange(0, textarea.value.length)
    if (document.execCommand('insertText', false, text)) return
  }
  draft.value = text
}

// A native undo (cmd+z) of an optimize rewrite restores the selection that was
// active when the edit was recorded — the whole textarea, since we select-all
// before replacing — leaving the reverted prompt fully highlighted. Collapse a
// full-document selection to the caret on undo so the text just comes back,
// unselected. Only the selection is touched (never the value), so redo stays
// intact.
function handleComposerInput(event: Event) {
  if ((event as InputEvent).inputType !== 'historyUndo') return
  const textarea = textareaRef.value
  if (!textarea) return
  const end = textarea.value.length
  if (end > 0 && textarea.selectionStart === 0 && textarea.selectionEnd === end) {
    textarea.setSelectionRange(end, end)
  }
}

async function handleOptimizeClick() {
  if (canUndoOptimize.value) {
    replaceDraftUndoable(draftBeforeOptimize.value ?? '')
    draftBeforeOptimize.value = null
    focusInput()
    return
  }
  const original = draft.value
  if (!original.trim()) return
  const optimized = await optimizer.optimize(original)
  if (!optimized || optimized === original) return
  // The user kept typing while we were optimizing — don't clobber their text.
  if (draft.value !== original) return
  draftBeforeOptimize.value = original
  lastOptimizedText.value = optimized
  replaceDraftUndoable(optimized)
  focusInput()
}

const optimizeDisabled = computed(() => {
  if (composerInputDisabled.value || optimizer.isOptimizing.value) return true
  if (dictation.isBusy.value) return true
  if (canUndoOptimize.value) return false
  return !draft.value.trim()
})

const optimizeTitle = computed(() => {
  if (optimizer.isOptimizing.value) return 'Optimizing...'
  if (canUndoOptimize.value) return 'Restore original prompt'
  return 'Optimize prompt'
})

const optimizeButtonClass = computed(() => {
  if (optimizer.isOptimizing.value) return 'icon-button !text-[color:var(--text-secondary)]'
  return 'icon-button'
})

const sendButtonDisabled = computed(() => {
  if (mainComposerDisabled.value) return true
  if (isEditing.value && editSubmitting.value) return true
  if (showStopAction.value) return false
  if (dictation.isTranscribing.value || sendAfterDictation.value) return true
  if (optimizer.isOptimizing.value) return true
  if (dictation.isRecording.value) return false
  return isEditing.value ? !canSendEdit.value : !canSend.value
})

const sendTitle = computed(() => {
  if (showStopAction.value) return 'Stop current invocation'
  if (sendAfterDictation.value || dictation.isTranscribing.value) return 'Transcribing...'
  if (dictation.isRecording.value) return 'Stop recording, transcribe, and send'
  if (isEditing.value) return 'Rewrite session'
  if (props.canStop) return 'Queue message'
  return props.submitLabel ?? 'Send'
})

const sendAriaLabel = computed(() => {
  if (showStopAction.value) return 'Stop invocation'
  if (dictation.isRecording.value) return 'Stop recording, transcribe, and send message'
  if (isEditing.value) return 'Rewrite session'
  if (props.canStop) return 'Queue message'
  return props.submitLabel ?? 'Send message'
})

watch(dictation.state, (state) => {
  if (!sendAfterDictation.value || state !== 'idle') return
  sendAfterDictation.value = false
  if (dictation.errorMessage.value) return
  submitCurrent()
})

const micTitle = computed(() => {
  if (dictation.isRecording.value) return 'Stop recording and transcribe'
  if (dictation.isTranscribing.value) return 'Transcribing...'
  return 'Voice input'
})

const micButtonClass = computed(() => {
  if (dictation.isRecording.value) return 'icon-button !text-destructive animate-pulse'
  if (dictation.isTranscribing.value) return 'icon-button !text-[color:var(--text-secondary)]'
  return 'icon-button'
})

const selectedModelConfig = computed(() => {
  return props.modelConfigs.find((config) => config.id === props.modelConfigId)
    ?? props.modelConfigs[0]
    ?? null
})

const selectedModelLabel = computed(() => {
  return selectedModelConfig.value?.label ?? 'Model'
})

const contextUsageDisplay = computed(() => {
  const usage = props.contextUsage
  // Show whenever the model's context window is known. Don't gate on percent:
  // with a 1M-token window, low usage rounds to 0% but the indicator should
  // still appear (Codex-style always-visible).
  if (!usage || !usage.contextLimit || usage.contextLimit === '—') return null
  return usage
})

const showGoalChip = computed(() => (
  props.goalCommandEnabled && draftGoal.value && !isEditing.value
))

function openContextDialog() {
  if (!contextUsageDisplay.value) return
  contextDialogOpen.value = true
}

const modelDropdownOptions = computed(() => {
  return props.modelConfigs.map((config) => {
    const display = modelOptionDisplay(config)
    return {
      id: config.id,
      label: display.name,
      secondaryText: display.tier,
      description: config.description,
      badge: display.badge,
      prefixKey: display.provider,
    }
  })
})

const agentOptionItems = computed(() => {
  const definitions = props.agentDefinitions ?? []
  const currentAgentId = props.agentId ?? DEFAULT_AGENT_ID
  const definitionsById = new Map(definitions.map((agent) => [agent.id, agent]))
  const mainAgents = COMPOSER_AGENT_ORDER.flatMap((agentId) => {
    const agent = definitionsById.get(agentId)
    return agent ? [agent] : []
  })
  const selected = definitions.find((agent) => agent.id === currentAgentId)
  if (selected && !mainAgents.some((agent) => agent.id === selected.id)) {
    return [selected, ...mainAgents]
  }
  return mainAgents
})

const selectedAgent = computed(() => {
  const currentAgentId = props.agentId ?? DEFAULT_AGENT_ID
  return agentOptionItems.value.find((agent) => agent.id === currentAgentId)
    ?? agentOptionItems.value[0]
    ?? null
})

const agentDropdownOptions = computed(() => {
  return agentOptionItems.value.map((agent) => ({
    id: agent.id,
    label: COMPOSER_AGENT_LABELS[agent.id] ?? agent.label,
  }))
})

const agentSelectorDisabled = computed(() => {
  const lockedToSession = Boolean(props.sessionId && props.sessionId !== 'new-chat' && props.sessionId !== 'empty')
  return Boolean(props.disabled || lockedToSession || agentOptionItems.value.length === 0)
})

function modelOptionDisplay(config: BackendModelConfigOption): ModelOptionDisplay {
  const provider = GEMINI_LABEL_PREFIX.test(config.label) ? 'gemini' : null
  const label = config.label.replace(GEMINI_LABEL_PREFIX, '').trim() || config.label
  const words = label.split(/\s+/).filter(Boolean)
  const suffix = words.at(-1) ?? ''
  const hasTierSuffix = MODEL_TIER_SUFFIXES.has(suffix)
  const name = hasTierSuffix ? words.slice(0, -1).join(' ') : label
  const lowerId = config.id.toLowerCase()
  const lowerLabel = config.label.toLowerCase()

  return {
    provider,
    name: name || label,
    tier: hasTierSuffix ? suffix : inferredModelTier(config),
    badge: lowerId.includes('flash') || lowerLabel.includes('flash')
      ? 'fast'
      : lowerId.includes('local') || lowerLabel.includes('local')
        ? 'local'
        : null,
  }
}

function inferredModelTier(config: BackendModelConfigOption): string {
  const lowerId = config.id.toLowerCase()
  const lowerDescription = config.description.toLowerCase()
  if (lowerId.includes('high') || lowerDescription.includes('high thinking')) return 'High'
  if (lowerId.includes('medium') || lowerId.includes('flash')) return 'Medium'
  if (lowerId.includes('low')) return 'Low'
  return ''
}

function submit() {
  if (mainComposerDisabled.value || isEditing.value) return
  const prompt = draft.value.trim()
  const files = attachments.value.map((item) => item.file)
  const existingAttachments = [...prefilledAttachments.value]
  const existingAttachmentIds = existingAttachments.map((item) => item.id)
  if (!prompt && files.length === 0 && existingAttachmentIds.length === 0) return
  const goal = draftGoal.value
  lastSubmitted.value = { prompt, files, existingAttachments, goal }
  emit('send', { prompt, files, existingAttachmentIds, goal })
  draft.value = ''
  draftGoal.value = false
  clearAttachments()
  prefilledAttachments.value = []
  emit('updateDraftAttachments', [])
  focusInput()
}

function submitEdit() {
  const editing = props.editingMessage
  if (!editing || props.disabled || editSubmitting.value) return
  const prompt = draft.value.trim()
  const files = attachments.value.map((item) => item.file)
  const existingAttachmentIds = editExistingAttachments.value.map((attachment) => attachment.id)
  if (!prompt && files.length === 0 && existingAttachmentIds.length === 0) return
  editSubmitting.value = true
  emit('editSend', {
    sourceTurnId: editing.sourceTurnId,
    prompt,
    files,
    existingAttachmentIds,
  })
}

function submitCurrent() {
  if (isEditing.value) submitEdit()
  else submit()
}

function handlePrimaryAction() {
  if (showStopAction.value) {
    emit('stop')
    return
  }
  if (dictation.isRecording.value) {
    sendAfterDictation.value = true
    dictation.stop()
    return
  }
  submitCurrent()
}

function pendingAttachmentNames(message: PendingUserMessage): string[] {
  return [
    ...message.files.map((file) => file.name),
    ...(message.attachments ?? []).map((attachment) => attachment.filename),
  ]
}

function pendingPromptLabel(message: PendingUserMessage): string {
  return message.prompt || (pendingAttachmentNames(message).length ? 'Attachment-only message' : '')
}

function editPendingMessage(message: PendingUserMessage) {
  if (isEditing.value) return
  const existingDraft = draft.value.trim()
  draft.value = existingDraft ? `${message.prompt}\n${existingDraft}` : message.prompt
  addFiles(message.files)
  emit('removePendingMessage', message.id)
  focusInput()
}

function selectModelConfig(modelConfigId: string) {
  if (modelConfigId === props.modelConfigId) return
  emit('updateModelConfig', modelConfigId)
}

function selectAgent(agentId: string) {
  if (agentId === (props.agentId ?? DEFAULT_AGENT_ID)) return
  emit('updateAgent', agentId)
}

const slashMenuOpen = computed(() => slashLevel.value !== null)

const slashMenuItems = computed<SlashMenuItem[]>(() => {
  if (slashLevel.value === 'commands') {
    return slashCommandMatches.value.map((command) => ({
      id: command.id,
      title: command.title,
      icon: SLASH_COMMAND_ICONS[command.kind],
      subtitle: command.kind === 'model' ? selectedModelLabel.value : command.description,
    }))
  }
  if (slashLevel.value === 'model') {
    return modelDropdownOptions.value.map((option) => ({
      id: option.id,
      title: option.label,
      subtitle: option.secondaryText,
      selected: option.id === props.modelConfigId,
      badge: option.badge,
      prefixKey: option.prefixKey,
    }))
  }
  return []
})

function closeSlashMenu() {
  slashLevel.value = null
  slashHighlight.value = 0
  slashTokenRange.value = null
}

// Recompute the command palette from the slash token at the caret. No-op while
// the model picker is open — that level is keyboard-driven, not draft-derived.
function refreshSlashMenu() {
  if (slashLevel.value === 'model') return
  if (isEditing.value || composerInputDisabled.value) {
    closeSlashMenu()
    return
  }
  const caret = textareaRef.value?.selectionStart ?? draft.value.length
  const token = slashTokenAt(draft.value, caret)
  if (!token) {
    closeSlashMenu()
    return
  }
  // Commands flagged `requiresLeadingText` (e.g. optimize) only make sense when
  // there is real prompt text before the slash to act on.
  const hasLeadingText = draft.value.slice(0, token.start).trim().length > 0
  const matches = filterSlashCommands(token.query).filter((command) => {
    if (command.kind === 'goal' && !props.goalCommandEnabled) return false
    if (command.requiresLeadingText && !hasLeadingText) return false
    return true
  })
  if (matches.length === 0) {
    closeSlashMenu()
    return
  }
  slashTokenRange.value = { start: token.start, end: token.end }
  slashCommandMatches.value = matches
  slashLevel.value = 'commands'
  if (slashHighlight.value >= matches.length) slashHighlight.value = 0
}

// Replace the active `/token` while keeping any text around it.
function replaceActiveSlashToken(replacement = '') {
  const range = slashTokenRange.value
  if (!range) return
  const text = draft.value
  draft.value = text.slice(0, range.start) + replacement + text.slice(range.end)
  nextTick(() => {
    const caret = range.start + replacement.length
    textareaRef.value?.setSelectionRange(caret, caret)
  })
  slashTokenRange.value = null
}

function runSlashCommand(command: SlashCommand) {
  if (command.kind === 'model') {
    // Consume the `/model` token (preserving surrounding text) and hand off to
    // the model picker level.
    replaceActiveSlashToken()
    slashLevel.value = 'model'
    const currentIndex = modelDropdownOptions.value.findIndex((option) => option.id === props.modelConfigId)
    slashHighlight.value = currentIndex >= 0 ? currentIndex : 0
  } else if (command.kind === 'goal') {
    replaceActiveSlashToken()
    draftGoal.value = true
    closeSlashMenu()
    focusInput()
  } else if (command.kind === 'optimize') {
    // Drop the `/optimize` token, then run the exact same path as the optimize
    // button so the rewrite acts on the surrounding prompt text alone.
    replaceActiveSlashToken()
    closeSlashMenu()
    void handleOptimizeClick()
  }
}

function chooseSlashModel(index: number) {
  const option = modelDropdownOptions.value[index]
  closeSlashMenu()
  if (option) selectModelConfig(option.id)
  focusInput()
}

function selectSlashHighlighted() {
  if (slashLevel.value === 'commands') {
    const command = slashCommandMatches.value[slashHighlight.value]
    if (command) runSlashCommand(command)
  } else if (slashLevel.value === 'model') {
    chooseSlashModel(slashHighlight.value)
  }
}

function onSlashMenuSelect(index: number) {
  slashHighlight.value = index
  selectSlashHighlighted()
}

function moveSlashHighlight(delta: number) {
  const count = slashMenuItems.value.length
  if (count === 0) return
  slashHighlight.value = (slashHighlight.value + delta + count) % count
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (slashLevel.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      moveSlashHighlight(1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      moveSlashHighlight(-1)
      return
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault()
      selectSlashHighlighted()
      return
    }
    if (event.key === 'Tab' && !event.shiftKey) {
      event.preventDefault()
      selectSlashHighlighted()
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeSlashMenu()
      return
    }
    // The model picker is modal: swallow typing/backspace so it can't leak into
    // the parked textarea. The command level lets keys through so the draft
    // keeps filtering the palette.
    if (slashLevel.value === 'model') {
      if (event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault()
        closeSlashMenu()
        return
      }
      if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault()
      }
    }
    return
  }
  // Menu closed: preserve the original Enter-to-send / Shift+Enter-newline.
  if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    submitCurrent()
  }
}

function handleSlashOutsideMouseDown(event: MouseEvent) {
  if (slashLevel.value === null) return
  const target = event.target
  if (target instanceof Node && composerRootRef.value?.contains(target)) return
  closeSlashMenu()
}

watch(draft, refreshSlashMenu)
watch([() => props.disabled, isEditing], () => {
  if (props.disabled || isEditing.value) closeSlashMenu()
})

onMounted(() => {
  document.addEventListener('mousedown', handleSlashOutsideMouseDown)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleSlashOutsideMouseDown)
})

defineExpose({
  focus: focusInput,
})
</script>

<template>
  <div ref="composerRootRef" class="flex flex-col gap-3" :class="fullWidth ? 'w-full' : 'mx-auto max-w-[820px]'">
    <div
      v-if="sendError"
      class="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-[13px] text-destructive"
      data-testid="composer-send-error"
    >
      {{ sendError }}
    </div>

    <div
      class="relative w-full rounded-xl border border-[color:var(--border-subtle)] bg-[var(--surface)] px-3 pb-3 pt-3 shadow-sm shadow-[var(--shadow-color)]"
      data-testid="composer"
      :data-mode="isEditing ? 'edit' : 'send'"
    >
    <SlashCommandMenu
      v-if="slashMenuOpen"
      test-id="composer-slash-menu"
      :items="slashMenuItems"
      :highlighted-index="slashHighlight"
      @select="onSlashMenuSelect"
      @hover="slashHighlight = $event"
    />
    <div
      v-if="pendingMessages?.length"
      class="-mx-3 -mt-3 mb-2 flex flex-col border-b border-[color:var(--border-muted)] px-3 py-1.5"
      data-testid="composer-pending-messages"
    >
      <div
        v-for="message in pendingMessages"
        :key="message.id"
        class="flex min-w-0 items-center gap-2 rounded-md px-1 py-1"
      >
        <Hourglass :size="13" class="shrink-0 text-[color:var(--text-muted)]" />
        <p
          class="min-w-0 flex-1 truncate text-[13px] text-[color:var(--text-primary)]"
          v-tooltip="{ content: pendingPromptLabel(message), overflowOnly: true }"
        >
          {{ pendingPromptLabel(message) }}
        </p>
        <span
          v-if="pendingAttachmentNames(message).length"
          class="inline-flex shrink-0 items-center gap-1 rounded-md border border-[color:var(--border-subtle)] bg-[var(--surface-muted)] px-1.5 py-0.5 text-[11px] text-[color:var(--text-muted)]"
          v-tooltip="pendingAttachmentNames(message).join('\n')"
        >
          <FileText :size="11" class="shrink-0" />
          {{ pendingAttachmentNames(message).length }}
        </span>
        <button
          class="icon-button h-6 w-6 shrink-0"
          type="button"
          v-tooltip="'Edit pending message'"
          aria-label="Edit pending message"
          :disabled="isEditing"
          data-testid="composer-pending-edit"
          @click="editPendingMessage(message)"
        >
          <Pencil :size="13" />
        </button>
        <button
          class="icon-button h-6 w-6 shrink-0"
          type="button"
          v-tooltip="'Cancel pending message'"
          aria-label="Cancel pending message"
          data-testid="composer-pending-remove"
          @click="emit('removePendingMessage', message.id)"
        >
          <X :size="13" />
        </button>
      </div>
    </div>
    <button
      v-if="showGoalChip"
      class="group mb-2 inline-flex h-6 items-center gap-1.5 rounded-md px-1.5 text-[12px] leading-4 text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-secondary)] focus:outline-none focus-visible:bg-[var(--surface-hover)] focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
      type="button"
      aria-label="Remove goal label"
      data-testid="composer-goal"
      @click="draftGoal = false"
    >
      <Target :size="14" class="shrink-0" />
      <span>Goal</span>
      <X
        :size="12"
        class="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
        aria-hidden="true"
      />
    </button>
    <div
      v-if="editingMessage"
      class="mb-2 flex min-h-7 flex-wrap items-center gap-x-3 gap-y-1"
      data-testid="composer-edit-controls"
    >
      <span
        class="text-[12px] text-destructive"
        data-testid="edit-rewrite-warning"
      >
        Later messages will be removed.
      </span>
      <button
        class="icon-button ml-auto h-7 w-7"
        type="button"
        v-tooltip="'Cancel edit'"
        aria-label="Cancel edit"
        data-testid="edit-cancel-icon"
        :disabled="editSubmitting"
        @click="emit('cancelEdit')"
      >
        <X :size="14" />
      </button>
    </div>
    <div
      v-if="displayedExistingAttachments.length || attachments.length"
      class="mb-2 flex flex-wrap gap-2"
      data-testid="composer-attachments"
    >
      <div
        v-for="attachment in displayedExistingAttachments"
        :key="`existing-${attachment.id}`"
        class="group relative flex max-w-[220px] items-center gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] py-1.5 pl-1.5 pr-7"
      >
        <img
          v-if="attachment.isImage"
          :src="attachment.url"
          :alt="attachment.filename"
          class="h-9 w-9 shrink-0 rounded-md object-cover"
        />
        <span
          v-else
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-hover)] text-[color:var(--text-secondary)]"
        >
          <FileText :size="16" />
        </span>
        <span class="min-w-0">
          <span
            class="block truncate text-[12px] font-medium text-[color:var(--text-primary)]"
            v-tooltip="{ content: attachment.filename, overflowOnly: true }"
          >{{ attachment.filename }}</span>
          <span class="block text-[11px] text-[color:var(--text-muted)]">{{ formatBytes(attachment.byteCount) }}</span>
        </span>
        <button
          class="absolute right-1 top-1 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--surface)] text-[color:var(--text-muted)] opacity-0 transition hover:text-[color:var(--text-primary)] group-hover:opacity-100"
          type="button"
          :aria-label="`Remove ${attachment.filename}`"
          :disabled="editSubmitting"
          @click="removeExistingAttachment(attachment.id)"
        >
          <X :size="12" />
        </button>
      </div>
      <div
        v-for="attachment in attachments"
        :key="attachment.id"
        class="group relative flex items-center gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] py-1.5 pl-1.5 pr-7 max-w-[220px]"
      >
        <img
          v-if="attachment.isImage && attachment.previewUrl"
          :src="attachment.previewUrl"
          :alt="attachment.file.name"
          class="h-9 w-9 shrink-0 rounded-md object-cover"
        />
        <span
          v-else
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-hover)] text-[color:var(--text-secondary)]"
        >
          <FileText :size="16" />
        </span>
        <span class="min-w-0">
          <span
            class="block truncate text-[12px] font-medium text-[color:var(--text-primary)]"
            v-tooltip="{ content: attachment.file.name, overflowOnly: true }"
          >{{ attachment.file.name }}</span>
          <span class="block text-[11px] text-[color:var(--text-muted)]">{{ formatBytes(attachment.file.size) }}</span>
        </span>
        <button
          class="absolute right-1 top-1 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--surface)] text-[color:var(--text-muted)] opacity-0 transition hover:text-[color:var(--text-primary)] group-hover:opacity-100"
          type="button"
          :aria-label="`Remove ${attachment.file.name}`"
          :disabled="editSubmitting"
          @click="removeAttachment(attachment.id)"
        >
          <X :size="12" />
        </button>
      </div>
    </div>
    <input
      ref="fileInputRef"
      type="file"
      multiple
      class="hidden"
      data-testid="composer-file-input"
      @change="handleFileInputChange"
    />
    <textarea
      ref="textareaRef"
      v-model="draft"
      class="min-h-16 max-h-[240px] w-full resize-none overflow-hidden bg-transparent px-1 text-[14px] leading-6 text-[color:var(--text-primary)] outline-none placeholder:text-[color:var(--text-faint)]"
      :placeholder="isEditing ? 'Edit message...' : pendingMessages?.length ? 'Describe another task to add to the queue...' : 'Describe a task or ask a question...'"
      :disabled="composerInputDisabled"
      data-testid="composer-input"
      @keydown="handleComposerKeydown"
      @input="handleComposerInput"
      @paste="handlePaste"
    ></textarea>
    <div class="flex flex-wrap items-center gap-2">
      <button
        class="icon-button"
        type="button"
        v-tooltip="'Attach files'"
        data-testid="composer-attach"
        :disabled="composerInputDisabled"
        @click="openFileDialog"
      >
        <Paperclip :size="16" />
      </button>
      <button
        class="not-implemented-button inline-flex h-8 shrink-0 items-center justify-center whitespace-nowrap rounded-md px-1.5 text-[12px] font-medium text-warning transition hover:bg-[var(--surface-hover)]"
        type="button"
        v-tooltip="'Not implemented'"
      >
        YOLO Mode
      </button>
      <div class="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2 text-[12px] text-[color:var(--text-muted)] max-sm:ml-0 max-sm:w-full">
        <ContextUsageRing
          v-if="contextUsageDisplay"
          :percent="contextUsageDisplay.contextPercent"
          :used-tokens="contextUsageDisplay.contextTokens"
          :limit-tokens="contextUsageDisplay.contextLimit"
          @open="openContextDialog"
        />
        <ComposerDropdown
          class="composer-model-dropdown"
          test-id="composer-model-picker"
          :title="selectedModelLabel"
          tooltip="Select model"
          :options="modelDropdownOptions"
          :selected-id="modelConfigId"
          @select="selectModelConfig"
        >
          <template #button-prefix="{ option }">
            <svg
              v-if="option?.prefixKey === 'gemini'"
              class="h-3.5 w-3.5 shrink-0 text-[color:var(--text-muted)]"
              viewBox="0 0 24 24"
              role="img"
              aria-label="Gemini"
            >
              <path
                fill="currentColor"
                d="M12 2.5c.78 5.36 4.14 8.72 9.5 9.5-5.36.78-8.72 4.14-9.5 9.5-.78-5.36-4.14-8.72-9.5-9.5 5.36-.78 8.72-4.14 9.5-9.5Z"
              />
            </svg>
          </template>
          <template #option-prefix="{ option }">
            <svg
              v-if="option.prefixKey === 'gemini'"
              class="h-3.5 w-3.5 shrink-0 text-[color:var(--text-muted)]"
              viewBox="0 0 24 24"
              role="img"
              aria-label="Gemini"
            >
              <path
                fill="currentColor"
                d="M12 2.5c.78 5.36 4.14 8.72 9.5 9.5-5.36.78-8.72 4.14-9.5 9.5-.78-5.36-4.14-8.72-9.5-9.5 5.36-.78 8.72-4.14 9.5-9.5Z"
              />
            </svg>
          </template>
        </ComposerDropdown>
        <ComposerDropdown
          v-if="!agentSelectorDisabled"
          test-id="composer-agent-picker"
          :title="selectedAgent?.label ?? 'Agent'"
          tooltip="Select agent"
          :options="agentDropdownOptions"
          :selected-id="selectedAgent?.id ?? ''"
          @select="selectAgent"
        >
          <template #button-prefix>
            <Bot :size="14" class="shrink-0 text-[color:var(--text-muted)]" />
          </template>
        </ComposerDropdown>
        <button
          :class="optimizeButtonClass"
          type="button"
          v-tooltip="optimizeTitle"
          :disabled="optimizeDisabled"
          data-testid="composer-optimize"
          :aria-label="optimizeTitle"
          @click="handleOptimizeClick"
        >
          <Loader2 v-if="optimizer.isOptimizing.value" :size="16" class="animate-spin" />
          <Undo2 v-else-if="canUndoOptimize" :size="16" />
          <Sparkles v-else :size="16" />
        </button>
        <button
          :class="micButtonClass"
          type="button"
          v-tooltip="micTitle"
          :disabled="composerInputDisabled || dictation.isTranscribing.value || optimizer.isOptimizing.value"
          data-testid="composer-dictate"
          :aria-pressed="dictation.isRecording.value"
          @click="dictation.toggle"
        >
          <Loader2 v-if="dictation.isTranscribing.value" :size="16" class="animate-spin" />
          <Square v-else-if="dictation.isRecording.value" :size="14" fill="currentColor" />
          <Mic v-else :size="16" />
        </button>
        <button
          class="send-button"
          :class="{ 'send-button-stop': showStopAction, 'send-button-labeled': submitLabel && !showStopAction }"
          type="button"
          :disabled="sendButtonDisabled"
          v-tooltip="sendTitle"
          data-testid="composer-send"
          :aria-label="sendAriaLabel"
          @click="handlePrimaryAction"
        >
          <Square v-if="showStopAction" :size="13" fill="currentColor" />
          <span v-else-if="submitLabel" class="whitespace-nowrap">{{ submitLabel }}</span>
          <ArrowRight v-else :size="18" stroke-width="2" />
        </button>
      </div>
    </div>
    </div>
    <ContextUsageDialog
      v-if="contextUsageDisplay"
      :open="contextDialogOpen"
      :usage="contextUsageDisplay"
      @close="contextDialogOpen = false"
    />
  </div>
</template>

<style scoped>
.composer-model-dropdown :deep(button),
.composer-model-dropdown :deep(span) {
  color: var(--text-muted);
}
</style>
