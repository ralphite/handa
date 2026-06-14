<script setup lang="ts">
import {
  ExternalLink,
  ImageOff,
  Maximize2,
  Minimize2,
  X,
  ZoomIn,
  ZoomOut,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { CSSProperties } from 'vue'
import type { MessageAttachment } from '../types'

type PreviewMode = 'fit' | 'actual'

const props = defineProps<{
  attachment: MessageAttachment
}>()

const emit = defineEmits<{
  close: []
}>()

const MIN_ZOOM = 0.25
const MAX_ZOOM = 4
const ZOOM_STEP = 0.25

const previewMode = ref<PreviewMode>('fit')
const zoom = ref(1)
const imageFailed = ref(false)
const naturalWidth = ref(0)

const titleId = computed(() => `image-preview-title-${props.attachment.id}`)
const zoomLabel = computed(() => (previewMode.value === 'fit' ? 'Fit' : `${Math.round(zoom.value * 100)}%`))
const imageStyle = computed<CSSProperties>(() => {
  if (previewMode.value === 'fit') {
    return {
      height: '100%',
      maxHeight: '100%',
      maxWidth: '100%',
      objectFit: 'contain',
      width: '100%',
    }
  }

  if (naturalWidth.value > 0) {
    return {
      height: 'auto',
      maxHeight: 'none',
      maxWidth: 'none',
      width: `${Math.round(naturalWidth.value * zoom.value)}px`,
    }
  }

  return {
    height: 'auto',
    maxHeight: 'none',
    maxWidth: 'none',
    width: `${Math.round(zoom.value * 100)}%`,
  }
})

watch(
  () => props.attachment.id,
  () => {
    previewMode.value = 'fit'
    zoom.value = 1
    imageFailed.value = false
    naturalWidth.value = 0
  },
  { immediate: true },
)

function closeDialog() {
  emit('close')
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeDialog()
}

function handleImageLoad(event: Event) {
  const image = event.target as HTMLImageElement
  naturalWidth.value = image.naturalWidth
  imageFailed.value = false
}

function handleImageError() {
  imageFailed.value = true
}

function setFitMode() {
  previewMode.value = 'fit'
  zoom.value = 1
}

function setActualSize() {
  previewMode.value = 'actual'
  zoom.value = 1
}

function zoomIn() {
  previewMode.value = 'actual'
  zoom.value = Math.min(MAX_ZOOM, zoom.value + ZOOM_STEP)
}

function zoomOut() {
  previewMode.value = 'actual'
  zoom.value = Math.max(MIN_ZOOM, zoom.value - ZOOM_STEP)
}

onMounted(() => {
  document.addEventListener('keydown', handleDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleDocumentKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[80] flex items-center justify-center bg-[var(--overlay)] p-3 sm:p-6"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      data-testid="image-preview-dialog"
      @click.self="closeDialog"
    >
      <div
        class="flex h-full max-h-[min(920px,calc(100vh-1.5rem))] w-full max-w-[min(1200px,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] shadow-2xl"
        @click.stop
      >
        <header class="flex h-12 shrink-0 items-center gap-3 border-b border-[color:var(--border-muted)] px-3">
          <div class="min-w-0 flex-1">
            <h2
              :id="titleId"
              class="truncate text-[13px] font-medium text-[color:var(--text-primary)]"
              v-tooltip="{ content: attachment.filename, overflowOnly: true }"
            >
              {{ attachment.filename }}
            </h2>
          </div>

          <div class="flex shrink-0 items-center gap-1">
            <button
              class="icon-button h-8 w-8"
              type="button"
              aria-label="Zoom out"
              v-tooltip="'Zoom out'"
              :disabled="imageFailed"
              @click="zoomOut"
            >
              <ZoomOut :size="16" />
            </button>
            <button
              class="icon-button h-8 w-8"
              type="button"
              aria-label="Zoom in"
              v-tooltip="'Zoom in'"
              :disabled="imageFailed"
              @click="zoomIn"
            >
              <ZoomIn :size="16" />
            </button>
            <button
              class="icon-button h-8 w-8"
              type="button"
              aria-label="Fit to window"
              v-tooltip="'Fit to window'"
              :disabled="imageFailed"
              @click="setFitMode"
            >
              <Maximize2 :size="16" />
            </button>
            <button
              class="icon-button h-8 w-8"
              type="button"
              aria-label="Actual size"
              v-tooltip="'Actual size'"
              :disabled="imageFailed"
              @click="setActualSize"
            >
              <Minimize2 :size="16" />
            </button>
            <span
              class="mx-1 w-12 text-center text-[11px] tabular-nums text-[color:var(--text-muted)]"
              data-testid="image-preview-zoom-label"
            >
              {{ zoomLabel }}
            </span>
            <a
              class="icon-button h-8 w-8"
              :href="attachment.url"
              target="_blank"
              rel="noopener"
              aria-label="Open image in new tab"
              v-tooltip="'Open image in new tab'"
            >
              <ExternalLink :size="16" />
            </a>
            <button
              class="icon-button h-8 w-8"
              type="button"
              aria-label="Close image preview"
              @click="closeDialog"
            >
              <X :size="17" />
            </button>
          </div>
        </header>

        <div class="min-h-0 flex-1 overflow-auto bg-[var(--app-bg)] p-3" data-testid="image-preview-viewport">
          <div
            v-if="imageFailed"
            class="flex h-full min-h-[260px] flex-col items-center justify-center gap-3 text-center text-[13px] text-[color:var(--text-muted)]"
            data-testid="image-preview-error"
          >
            <ImageOff :size="28" />
            <span>Image failed to load.</span>
          </div>
          <div
            v-else
            class="h-full min-h-[260px]"
            :class="previewMode === 'fit' ? 'flex items-center justify-center' : 'inline-flex min-w-full items-start justify-start'"
          >
            <img
              :src="attachment.url"
              :alt="attachment.filename"
              class="block select-none rounded-md"
              :style="imageStyle"
              data-testid="image-preview-image"
              @load="handleImageLoad"
              @error="handleImageError"
            />
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
