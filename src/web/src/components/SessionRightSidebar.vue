<script setup lang="ts">
import { computed } from 'vue'
import {
  ArrowLeft,
  Bot,
  Check,
  Circle,
  CircleAlert,
  CircleDot,
  FlaskConical,
  Globe,
  ImageOff,
  LoaderCircle,
  RefreshCw,
  SearchCode,
  Square,
  TerminalSquare,
} from '@lucide/vue'
import { artifactIconFor } from '../artifactIcons'

defineOptions({
  name: 'SessionRightSidebar',
})

type Status = 'queued' | 'running' | 'waiting' | 'pending' | 'done' | 'failed' | 'cancelled'
type ProgressStatus = 'pending' | 'running' | 'done' | 'failed'
type BackgroundRunKind = 'sub-agent' | 'command' | 'test' | 'index' | 'sync' | 'custom'

export interface SidebarBackgroundRun {
  id: string
  kind: BackgroundRunKind
  title: string
  status: Status
  meta?: string
  childSessionId?: string
  currentStep?: string
}

export interface SidebarArtifact {
  id: string
  title: string
  kind: string
  meta: string
  note?: string
}

export interface SidebarProgressItem {
  id: string
  title: string
  status: ProgressStatus
  detail?: string
}

export interface SidebarBrowserEnvironment {
  success: boolean
  status: string
  url?: string | null
  title?: string | null
  lastAction?: string | null
  lastError?: string | null
  updatedAt?: string | null
  screenshotUrl?: string | null
  viewport?: { width?: number; height?: number } | null
}

const props = defineProps<{
  backgroundRuns: SidebarBackgroundRun[]
  progressItems?: SidebarProgressItem[]
  browserEnvironment?: SidebarBrowserEnvironment | null
  artifacts: SidebarArtifact[]
  parentSessionId?: string | null
  selectedArtifactId?: string
  width?: number
}>()

defineEmits<{
  selectParentSession: []
  selectArtifact: [id: string]
  selectBrowser: []
  selectBackgroundRun: [id: string]
  terminateBackgroundRun: [id: string]
}>()

const runningCount = computed(() => props.backgroundRuns.filter((run) => run.status === 'running').length)
const progressItems = computed(() => props.progressItems ?? [])
const doneProgressCount = computed(() => progressItems.value.filter((item) => item.status === 'done').length)
const browserEnvironment = computed(() => props.browserEnvironment ?? null)
const browserScreenshotSrc = computed(() => {
  const browser = browserEnvironment.value
  if (!browser?.screenshotUrl) return ''
  if (browser.screenshotUrl.startsWith('data:')) return browser.screenshotUrl
  const separator = browser.screenshotUrl.includes('?') ? '&' : '?'
  return `${browser.screenshotUrl}${separator}t=${encodeURIComponent(browser.updatedAt ?? '')}`
})

function statusLabel(status: Status) {
  const labels: Record<Status, string> = {
    queued: 'Queued',
    running: 'Running',
    waiting: 'Waiting',
    pending: 'Pending',
    done: 'Done',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }
  return labels[status]
}

function statusToneClass(status: Status) {
  if (status === 'done') return 'text-success'
  if (status === 'failed' || status === 'cancelled') return 'text-destructive'
  if (status === 'running' || status === 'queued') return 'text-info'
  if (status === 'waiting') return 'text-warning'
  return 'text-[color:var(--text-muted)]'
}

function progressStatusLabel(status: ProgressStatus) {
  const labels: Record<ProgressStatus, string> = {
    pending: 'Pending',
    running: 'Running',
    done: 'Done',
    failed: 'Failed',
  }
  return labels[status]
}

function progressToneClass(status: ProgressStatus) {
  if (status === 'done') return 'text-[color:var(--text-muted)]'
  if (status === 'failed') return 'text-destructive'
  if (status === 'running') return 'text-info'
  return 'text-[color:var(--text-muted)]'
}

function browserStatusToneClass(status: string, success: boolean) {
  const normalized = status.trim().toLowerCase()
  if (!success || normalized === 'error') return 'text-destructive'
  if (normalized === 'running') return 'text-info'
  if (normalized === 'open') return 'text-success'
  if (normalized === 'closed') return 'text-[color:var(--text-muted)]'
  return 'text-[color:var(--text-faint)]'
}

function browserStatusLabel(status: string) {
  if (!status) return 'Idle'
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function browserHost(url?: string | null) {
  if (!url) return ''
  try {
    return new URL(url).host || url
  } catch {
    return url
  }
}

const backgroundRunIcons = {
  'sub-agent': Bot,
  command: TerminalSquare,
  test: FlaskConical,
  index: SearchCode,
  sync: RefreshCw,
  custom: CircleDot,
} satisfies Record<BackgroundRunKind, typeof Bot>

function backgroundRunIcon(kind: BackgroundRunKind) {
  return backgroundRunIcons[kind] ?? CircleDot
}

</script>

<template>
  <aside
    class="flex h-full min-h-0 shrink-0 flex-col border-l border-[color:var(--border-layout)] bg-[var(--right-panel-bg)] text-[color:var(--text-primary)]"
    :style="{ width: width ? `${width}px` : '328px' }"
  >
    <div class="min-h-0 flex-1 overflow-y-auto px-3 py-3">
      <div v-if="parentSessionId" class="mb-3 px-1">
        <button
          class="inline-flex h-9 w-full items-center justify-start gap-2 rounded-lg bg-[var(--text-primary)] px-3 text-[13px] font-semibold text-[color:var(--app-bg)] shadow-sm transition hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--text-primary)]"
          type="button"
          aria-label="Back to parent"
          @click.stop="$emit('selectParentSession')"
        >
          <ArrowLeft :size="15" class="shrink-0" />
          <span class="truncate">Back to parent</span>
        </button>
      </div>

      <section v-if="progressItems.length" class="mb-5">
        <div class="mb-2 flex items-center justify-between gap-2 px-1">
          <h2 class="text-[11px] font-medium uppercase tracking-wide text-[color:var(--text-faint)]">Progress</h2>
          <span class="text-[12px] text-[color:var(--text-faint)]">{{ doneProgressCount }}/{{ progressItems.length }}</span>
        </div>
        <div class="space-y-1">
          <div
            v-for="item in progressItems"
            :key="item.id"
            class="flex min-w-0 items-start gap-2 rounded-lg px-2 py-0.5"
          >
            <span
              class="grid h-5 w-5 shrink-0 place-items-center"
              :class="progressToneClass(item.status)"
              v-tooltip="progressStatusLabel(item.status)"
            >
              <span v-if="item.status === 'done'" class="grid h-3.5 w-3.5 place-items-center rounded-full bg-[color:var(--text-muted)] text-white">
                <Check :size="10" :stroke-width="3" />
              </span>
              <span v-else-if="item.status === 'running'" class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent"></span>
              <CircleAlert v-else-if="item.status === 'failed'" :size="14" />
              <Circle v-else :size="14" />
            </span>
            <span class="min-w-0 flex-1">
              <span class="block break-words text-[13px] leading-5 text-[color:var(--text-primary)]">{{ item.title }}</span>
              <span v-if="item.detail" class="mt-0.5 block break-words text-[12px] leading-4 text-[color:var(--text-faint)]">{{ item.detail }}</span>
            </span>
          </div>
        </div>
      </section>

      <section v-if="browserEnvironment" class="mb-5">
        <div class="mb-2 flex items-center justify-between gap-2 px-1">
          <h2 class="text-[11px] font-medium uppercase tracking-wide text-[color:var(--text-faint)]">Browser</h2>
          <span
            class="inline-flex items-center gap-1.5 text-[12px] text-[color:var(--text-faint)]"
            :class="browserStatusToneClass(browserEnvironment.status, browserEnvironment.success)"
          >
            <span class="h-1.5 w-1.5 rounded-full" style="background: currentColor"></span>
            {{ browserStatusLabel(browserEnvironment.status) }}
          </span>
        </div>
        <div class="rounded-lg border border-[color:var(--border-muted)] bg-[var(--panel-bg)] p-2">
          <div class="mb-2 flex min-w-0 items-center gap-2 px-1">
            <span class="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-[var(--surface-muted)] text-[color:var(--text-muted)]">
              <Globe :size="14" />
            </span>
            <span class="min-w-0 flex-1">
              <span
                class="block truncate text-[13px] text-[color:var(--text-primary)]"
                v-tooltip="{ content: browserEnvironment.title || browserEnvironment.url || 'Browser', overflowOnly: true }"
              >{{ browserEnvironment.title || browserHost(browserEnvironment.url) || 'Browser' }}</span>
              <span
                v-if="browserEnvironment.url"
                class="mt-0.5 block truncate text-[12px] text-[color:var(--text-faint)]"
                v-tooltip="{ content: browserEnvironment.url, overflowOnly: true }"
              >{{ browserEnvironment.url }}</span>
            </span>
          </div>

          <button
            v-if="browserScreenshotSrc"
            class="group relative block aspect-video w-full overflow-hidden rounded-md border border-[color:var(--border-muted)] bg-[var(--surface-muted)] text-left outline-none transition hover:border-[color:var(--accent)] focus-visible:border-[color:var(--accent)] focus-visible:ring-2 focus-visible:ring-[color:var(--accent-soft)]"
            type="button"
            aria-label="Open browser"
            @click.stop="$emit('selectBrowser')"
          >
            <img
              class="pointer-events-none h-full w-full object-contain"
              :src="browserScreenshotSrc"
              alt=""
              loading="lazy"
            />
            <span class="absolute bottom-2 right-2 rounded-md bg-[var(--surface)] px-2 py-1 text-[11px] font-medium text-[color:var(--text-muted)] opacity-0 shadow-sm transition group-hover:opacity-100 group-focus:opacity-100">
              Open
            </span>
          </button>
          <div
            v-else
            class="grid aspect-video w-full place-items-center rounded-md border border-dashed border-[color:var(--border-muted)] bg-[var(--surface-muted)] text-[color:var(--text-faint)]"
          >
            <ImageOff :size="18" />
          </div>

          <p
            v-if="browserEnvironment.lastError || browserEnvironment.lastAction"
            class="mt-2 truncate px-1 text-[12px]"
            :class="browserEnvironment.lastError ? 'text-destructive' : 'text-[color:var(--text-faint)]'"
            v-tooltip="{ content: browserEnvironment.lastError || browserEnvironment.lastAction || '', overflowOnly: true }"
          >
            {{ browserEnvironment.lastError || browserEnvironment.lastAction }}
          </p>
        </div>
      </section>

      <section v-if="artifacts.length" class="mb-5">
        <div class="mb-2 flex items-center justify-between gap-2 px-1">
          <h2 class="text-[11px] font-medium uppercase tracking-wide text-[color:var(--text-faint)]">Artifacts</h2>
          <span class="text-[12px] text-[color:var(--text-faint)]">{{ artifacts.length }}</span>
        </div>
        <div class="space-y-1">
          <button
            v-for="artifact in artifacts"
            :key="artifact.id"
            class="flex w-full items-center gap-2 rounded-lg px-2 py-0.5 text-left transition"
            :class="artifact.id === selectedArtifactId ? 'bg-[var(--surface-active)] hover:bg-[var(--surface-active)]' : 'hover:bg-[var(--surface-hover)]'"
            type="button"
            @click.stop="$emit('selectArtifact', artifact.id)"
          >
            <span class="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[var(--surface-muted)] text-[color:var(--text-muted)]">
              <component :is="artifactIconFor(artifact)" :size="13" />
            </span>
            <span
              class="min-w-0 flex-1 truncate text-[13px] text-[color:var(--text-primary)]"
              v-tooltip="{ content: artifact.title, overflowOnly: true }"
            >{{ artifact.title }}</span>
            <span v-if="artifact.meta" class="shrink-0 text-[12px] text-[color:var(--text-faint)]">{{ artifact.meta }}</span>
          </button>
        </div>
      </section>

      <section v-if="backgroundRuns.length">
        <div class="mb-2 flex items-center justify-between gap-2 px-1">
          <h2 class="text-[11px] font-medium uppercase tracking-wide text-[color:var(--text-faint)]">Background Tasks</h2>
          <span v-if="runningCount" class="text-[12px] text-[color:var(--text-faint)]">{{ runningCount }} running</span>
        </div>
        <div class="space-y-1">
          <div
            v-for="run in backgroundRuns"
            :key="run.id"
            class="group flex items-start rounded-lg text-left transition hover:bg-[var(--surface-hover)] focus-within:bg-[var(--surface-hover)]"
          >
            <button
              class="flex min-w-0 flex-1 items-center gap-2.5 px-2 py-0.5 text-left focus:outline-none"
              type="button"
              @click.stop="$emit('selectBackgroundRun', run.id)"
            >
              <span class="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[var(--surface-muted)] text-[color:var(--text-muted)]">
                <component :is="backgroundRunIcon(run.kind)" :size="13" />
              </span>
              <span class="min-w-0 flex-1">
                <span
                  class="block truncate text-[13px] text-[color:var(--text-primary)]"
                  v-tooltip="{ content: run.title, overflowOnly: true }"
                >{{ run.title }}</span>
                <span
                  v-if="run.currentStep"
                  class="mt-0.5 block truncate text-[12px] text-[color:var(--text-faint)]"
                  v-tooltip="{ content: run.currentStep, overflowOnly: true }"
                >{{ run.currentStep }}</span>
              </span>
            </button>
            <div class="flex shrink-0 items-center gap-1.5 py-0.5 pr-2">
              <button
                v-if="run.status === 'running'"
                class="grid h-5 w-5 shrink-0 place-items-center rounded text-[color:var(--text-muted)] opacity-0 transition hover:text-destructive group-hover:opacity-100 focus:opacity-100 focus:outline-none focus-visible:opacity-100"
                type="button"
                v-tooltip="'Terminate task'"
                aria-label="Terminate task"
                @click.stop="$emit('terminateBackgroundRun', run.id)"
              >
                <Square :size="12" fill="currentColor" />
              </button>
              <span
                class="grid h-3.5 w-3.5 shrink-0 place-items-center"
                :class="statusToneClass(run.status)"
                v-tooltip="statusLabel(run.status)"
              >
                <LoaderCircle v-if="run.status === 'running'" :size="13" class="animate-spin" />
                <span v-else class="h-2 w-2 rounded-full" style="background: currentColor"></span>
              </span>
            </div>
          </div>
        </div>
      </section>
    </div>
  </aside>
</template>
