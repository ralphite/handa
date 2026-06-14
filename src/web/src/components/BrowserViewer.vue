<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ImageOff } from '@lucide/vue'
import type { AgentBrowserEnvironment, BrowserInteraction } from '../types'

defineOptions({
  name: 'BrowserViewer',
})

const props = defineProps<{
  browser: AgentBrowserEnvironment
}>()

const emit = defineEmits<{
  interact: [payload: BrowserInteraction]
  browserUpdate: [browser: AgentBrowserEnvironment]
}>()

const frameRef = ref<HTMLElement | null>(null)
const containerRef = ref<HTMLElement | null>(null)
const containerSize = ref({ width: 0, height: 0 })
const focused = ref(false)
let resizeObserver: ResizeObserver | null = null
const liveFrameSrc = ref('')
let wheelTimer: number | undefined
let wheelDeltaX = 0
let wheelDeltaY = 0
let streamSocket: WebSocket | null = null
let liveFrameObjectUrl = ''
let reconnectTimer: number | undefined

const screenshotSrc = computed(() => {
  if (!props.browser.screenshotUrl) return ''
  if (props.browser.screenshotUrl.startsWith('data:')) return props.browser.screenshotUrl
  const separator = props.browser.screenshotUrl.includes('?') ? '&' : '?'
  return `${props.browser.screenshotUrl}${separator}t=${encodeURIComponent(props.browser.updatedAt ?? '')}`
})

const streamUrl = computed(() => browserWebSocketUrl(props.browser.streamUrl ?? ''))
const renderedSrc = computed(() => liveFrameSrc.value || screenshotSrc.value)

// The browser surface fills the whole chat panel. We measure the available area
// and ask the live browser to resize its viewport to match, so the streamed page
// fills both width and height with no letterboxing or distortion.
const RESIZE_DEBOUNCE_MS = 200
let resizeSendTimer: number | undefined
let lastSentSize = { width: 0, height: 0 }

onMounted(() => {
  const container = containerRef.value
  if (!container) return
  resizeObserver = new ResizeObserver((entries) => {
    const rect = entries[0]?.contentRect
    if (rect) containerSize.value = { width: rect.width, height: rect.height }
  })
  resizeObserver.observe(container)
  containerSize.value = { width: container.clientWidth, height: container.clientHeight }
})

watch(containerSize, () => scheduleViewportResize(), { deep: true })

onUnmounted(() => {
  if (wheelTimer !== undefined) window.clearTimeout(wheelTimer)
  if (resizeSendTimer !== undefined) window.clearTimeout(resizeSendTimer)
  resizeObserver?.disconnect()
  resizeObserver = null
  closeStream()
})

function scheduleViewportResize() {
  if (resizeSendTimer !== undefined) window.clearTimeout(resizeSendTimer)
  resizeSendTimer = window.setTimeout(sendViewportResize, RESIZE_DEBOUNCE_MS)
}

function sendViewportResize() {
  resizeSendTimer = undefined
  const width = Math.round(containerSize.value.width)
  const height = Math.round(containerSize.value.height)
  if (width <= 0 || height <= 0) return
  if (Math.abs(width - lastSentSize.width) < 2 && Math.abs(height - lastSentSize.height) < 2) return
  lastSentSize = { width, height }
  sendBrowserInteraction({ action: 'resize', width, height })
}

watch(
  () => props.browser.streamUrl ?? '',
  () => connectStream(),
  { immediate: true },
)

function focusFrame() {
  focused.value = true
  void nextTick(() => frameRef.value?.focus({ preventScroll: true }))
}

function handlePointer(event: MouseEvent) {
  if (!renderedSrc.value) return
  const frame = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  if (!frame) return
  const rect = frame.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) return
  focusFrame()
  sendBrowserInteraction({
    action: 'click',
    x: clampUnit((event.clientX - rect.left) / rect.width),
    y: clampUnit((event.clientY - rect.top) / rect.height),
    button: mouseButton(event.button),
  })
}

function handleKeydown(event: KeyboardEvent) {
  if (!renderedSrc.value) return
  if (event.key === 'Dead' || event.isComposing) return
  const text = textFromKey(event)
  const key = text ? '' : pressKey(event)
  if (!text && !key) return
  event.preventDefault()
  event.stopPropagation()
  sendBrowserInteraction(text ? { action: 'type', text } : { action: 'key', key })
}

function handlePaste(event: ClipboardEvent) {
  if (!renderedSrc.value) return
  const text = event.clipboardData?.getData('text/plain') ?? ''
  if (!text) return
  event.preventDefault()
  event.stopPropagation()
  sendBrowserInteraction({ action: 'type', text })
}

function handleWheel(event: WheelEvent) {
  if (!renderedSrc.value) return
  focusFrame()
  wheelDeltaX += Math.round(event.deltaX)
  wheelDeltaY += Math.round(event.deltaY)
  if (wheelTimer !== undefined) window.clearTimeout(wheelTimer)
  wheelTimer = window.setTimeout(() => {
    const deltaX = clampInt(wheelDeltaX, -5000, 5000)
    const deltaY = clampInt(wheelDeltaY, -5000, 5000)
    wheelDeltaX = 0
    wheelDeltaY = 0
    wheelTimer = undefined
    if (deltaX !== 0 || deltaY !== 0) {
      sendBrowserInteraction({ action: 'scroll', delta_x: deltaX, delta_y: deltaY })
    }
  }, 80)
}

function connectStream() {
  closeStream()
  if (!streamUrl.value) return
  const socket = new WebSocket(streamUrl.value)
  streamSocket = socket
  socket.binaryType = 'blob'
  socket.onopen = () => {
    if (streamSocket !== socket) return
    // Re-assert the panel size: the backend restores the default viewport whenever
    // the stream disconnects, so a fresh connection must resize again.
    lastSentSize = { width: 0, height: 0 }
    sendViewportResize()
  }
  socket.onmessage = (event) => {
    if (typeof event.data === 'string') {
      handleStreamMessage(event.data)
      return
    }
    if (event.data instanceof Blob) {
      setLiveFrame(event.data)
    }
  }
  socket.onclose = () => {
    if (streamSocket !== socket) return
    streamSocket = null
    scheduleReconnect()
  }
  socket.onerror = () => {
    if (streamSocket === socket) socket.close()
  }
}

function closeStream() {
  if (reconnectTimer !== undefined) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = undefined
  }
  if (streamSocket) {
    const socket = streamSocket
    streamSocket = null
    socket.close()
  }
  clearLiveFrame()
}

function scheduleReconnect() {
  if (!streamUrl.value || reconnectTimer !== undefined) return
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined
    connectStream()
  }, 1200)
}

function handleStreamMessage(raw: string) {
  let payload: unknown
  try {
    payload = JSON.parse(raw)
  } catch {
    return
  }
  if (!payload || typeof payload !== 'object') return
  const summary = (payload as { summary?: unknown }).summary
  const browser = browserFromStreamSummary(summary)
  if (browser) emit('browserUpdate', browser)
}

function setLiveFrame(blob: Blob) {
  const nextUrl = URL.createObjectURL(blob)
  const previousUrl = liveFrameObjectUrl
  liveFrameObjectUrl = nextUrl
  liveFrameSrc.value = nextUrl
  if (previousUrl) {
    window.setTimeout(() => URL.revokeObjectURL(previousUrl), 0)
  }
}

function clearLiveFrame() {
  if (liveFrameObjectUrl) {
    URL.revokeObjectURL(liveFrameObjectUrl)
    liveFrameObjectUrl = ''
  }
  liveFrameSrc.value = ''
}

function sendBrowserInteraction(payload: BrowserInteraction) {
  if (streamSocket?.readyState === WebSocket.OPEN) {
    streamSocket.send(JSON.stringify(payload))
    return
  }
  emit('interact', payload)
}

function browserWebSocketUrl(value: string) {
  if (!value) return ''
  const url = new URL(value, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function browserFromStreamSummary(summary: unknown): AgentBrowserEnvironment | null {
  if (!summary || typeof summary !== 'object') return null
  const value = summary as Record<string, unknown>
  return {
    success: typeof value.success === 'boolean' ? value.success : value.status !== 'error',
    status: typeof value.status === 'string' ? value.status : 'idle',
    sessionId: typeof value.session_id === 'string' ? value.session_id : null,
    url: typeof value.url === 'string' ? value.url : null,
    title: typeof value.title === 'string' ? value.title : null,
    lastAction: typeof value.last_action === 'string' ? value.last_action : null,
    lastError: typeof value.last_error === 'string' ? value.last_error : null,
    updatedAt: typeof value.updated_at === 'string' ? value.updated_at : null,
    screenshotUrl: typeof value.screenshot_url === 'string' ? value.screenshot_url : null,
    streamUrl: typeof value.stream_url === 'string' ? value.stream_url : null,
    viewport: isViewport(value.viewport) ? value.viewport : null,
  }
}

function isViewport(value: unknown): value is { width?: number; height?: number } {
  return Boolean(value && typeof value === 'object')
}

function textFromKey(event: KeyboardEvent) {
  if (event.metaKey || event.ctrlKey || event.altKey) return ''
  if (event.key.length === 1) return event.key
  return ''
}

function pressKey(event: KeyboardEvent) {
  const keyMap: Record<string, string> = {
    ' ': 'Space',
    Enter: 'Enter',
    Backspace: 'Backspace',
    Delete: 'Delete',
    Tab: 'Tab',
    Escape: 'Escape',
    ArrowUp: 'ArrowUp',
    ArrowDown: 'ArrowDown',
    ArrowLeft: 'ArrowLeft',
    ArrowRight: 'ArrowRight',
    Home: 'Home',
    End: 'End',
    PageUp: 'PageUp',
    PageDown: 'PageDown',
  }
  const base = keyMap[event.key] ?? (event.key.length === 1 ? event.key.toUpperCase() : '')
  if (!base) return ''
  const modifiers = []
  if (event.metaKey) modifiers.push('Meta')
  if (event.ctrlKey) modifiers.push('Control')
  if (event.altKey) modifiers.push('Alt')
  if (event.shiftKey && event.key.length !== 1) modifiers.push('Shift')
  return [...modifiers, base].join('+')
}

function mouseButton(button: number): 'left' | 'right' | 'middle' {
  if (button === 1) return 'middle'
  if (button === 2) return 'right'
  return 'left'
}

function clampUnit(value: number) {
  return Math.max(0, Math.min(1, value))
}

function clampInt(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}
</script>

<template>
  <article class="flex h-full min-w-0 flex-col bg-background">
    <div ref="containerRef" class="flex min-h-0 flex-1 items-center justify-center bg-background p-3">
      <div
        v-if="renderedSrc"
        ref="frameRef"
        class="relative h-full w-full overflow-hidden rounded-md border bg-white outline-none"
        :class="focused ? 'border-[color:var(--accent)] ring-2 ring-[color:var(--accent-soft)]' : 'border-[color:var(--border-muted)]'"
        role="application"
        tabindex="0"
        aria-label="Browser surface"
        @blur="focused = false"
        @contextmenu.prevent
        @keydown="handleKeydown"
        @mousedown.prevent.stop="handlePointer"
        @paste="handlePaste"
        @wheel.prevent.stop="handleWheel"
      >
        <img
          class="pointer-events-none h-full w-full object-fill"
          :src="renderedSrc"
          alt=""
          draggable="false"
        />
      </div>
      <div
        v-else
        class="flex aspect-video w-full max-w-5xl flex-col items-center justify-center gap-2 rounded-md border border-dashed border-[color:var(--border-muted)] bg-[var(--surface-muted)] px-4 text-center text-[color:var(--text-faint)]"
      >
        <ImageOff :size="24" />
        <span v-if="browser.lastError" class="max-w-full truncate text-[12px] text-destructive">{{ browser.lastError }}</span>
      </div>
    </div>
  </article>
</template>
