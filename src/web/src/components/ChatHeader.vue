<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AppWindowMac,
  ChevronDown,
  Code,
  FolderOpen,
  Globe,
  Loader2,
  PanelRight,
} from '@lucide/vue'
import { launchProjectApp, projectLauncherIconUrl } from '../api/client'
import type { BackendProjectLauncherTarget } from '../api/types'
import type { AgentBrowserEnvironment, AgentSession, Artifact } from '../types'
import { artifactIconFor } from '../artifactIcons'

defineOptions({
  name: 'ChatHeader',
})

const props = defineProps<{
  session: AgentSession
  projectName: string
  breadcrumbs?: { id: string; label: string; title?: string | null }[]
  activeArtifact?: Artifact
  activeBrowserEnvironment?: AgentBrowserEnvironment | null
  rightSidebarVisible?: boolean
}>()

const emit = defineEmits<{
  selectBreadcrumb: [id: string]
  launcherError: [message: string]
  toggleRightSidebar: []
}>()

const showSidebarToggle = computed(() => props.rightSidebarVisible !== undefined)

const projectLauncherRef = ref<HTMLElement | null>(null)
const launcherOpen = ref(false)
const launcherBusyTarget = ref<BackendProjectLauncherTarget | ''>('')
const launcherIconAvailable = ref<Record<BackendProjectLauncherTarget, boolean>>({
  finder: true,
  vscode: true,
})

const artifactLabel = computed(() => props.activeArtifact?.filename ?? props.activeArtifact?.title ?? '')
const activeArtifactIcon = computed(() => props.activeArtifact ? artifactIconFor(props.activeArtifact) : null)
const browserLabel = computed(() => props.activeBrowserEnvironment?.title || props.activeBrowserEnvironment?.url || 'Browser')

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleDocumentKeydown)
})

function isProjectBreadcrumb(id: string) {
  return id.startsWith('project:')
}

function isFinalBreadcrumb(index: number) {
  return index === (props.breadcrumbs?.length ?? 0) - 1 && !props.activeArtifact && !props.activeBrowserEnvironment
}

function isNavigableBreadcrumb(crumb: { id: string }, index: number) {
  return !isProjectBreadcrumb(crumb.id) && !isFinalBreadcrumb(index)
}

function toggleProjectLauncher() {
  launcherOpen.value = !launcherOpen.value
}

function closeProjectLauncher() {
  launcherOpen.value = false
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (!launcherOpen.value) return
  const target = event.target
  if (target instanceof Node && projectLauncherRef.value?.contains(target)) return
  closeProjectLauncher()
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeProjectLauncher()
}

async function openProjectLauncherTarget(target: BackendProjectLauncherTarget) {
  if (launcherBusyTarget.value || !props.session.projectId) return
  launcherBusyTarget.value = target
  try {
    await launchProjectApp(props.session.projectId, target)
    closeProjectLauncher()
  } catch (error) {
    emit('launcherError', readableError(error))
  } finally {
    launcherBusyTarget.value = ''
  }
}

function readableError(error: unknown) {
  if (error instanceof Error && error.message.trim()) return error.message
  return 'Unable to open Project'
}

function markLauncherIconUnavailable(target: BackendProjectLauncherTarget) {
  launcherIconAvailable.value[target] = false
}
</script>

<template>
  <header class="panel-header gap-3 border-[color:var(--border-layout)] bg-background px-4">
    <div class="flex min-w-0 flex-1 items-center gap-2">
      <template v-if="breadcrumbs?.length">
        <template v-for="(crumb, index) in breadcrumbs" :key="crumb.id">
          <span
            v-if="isProjectBreadcrumb(crumb.id)"
            class="min-w-0 shrink truncate text-[13px] font-medium text-[color:var(--text-muted)]"
            v-tooltip="{ content: crumb.title ?? crumb.label, overflowOnly: true }"
          >
            {{ crumb.label }}
          </span>
          <button
            v-else-if="isNavigableBreadcrumb(crumb, index)"
            class="min-w-0 shrink truncate text-left text-[13px] font-medium text-[color:var(--text-muted)] transition hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:text-[color:var(--text-primary)]"
            type="button"
            v-tooltip="{ content: crumb.title ?? crumb.label, overflowOnly: true }"
            @click="$emit('selectBreadcrumb', crumb.id)"
          >
            {{ crumb.label }}
          </button>
          <h1
            v-else
            class="min-w-0 truncate text-[14px] font-semibold text-[color:var(--text-primary)]"
            v-tooltip="{ content: crumb.title ?? crumb.label, overflowOnly: true }"
          >
            {{ crumb.label }}
          </h1>
          <span
            v-if="!isFinalBreadcrumb(index)"
            class="shrink-0 text-[color:var(--text-faint)] opacity-50"
          >/</span>
        </template>
        <h1
          v-if="activeArtifact"
          class="inline-flex min-w-0 items-center gap-1.5 truncate text-[14px] font-semibold leading-none text-[color:var(--text-primary)]"
        >
          <component :is="activeArtifactIcon" :size="15" class="shrink-0 text-[color:var(--text-muted)]" />
          <span
            class="min-w-0 truncate"
            v-tooltip="{ content: artifactLabel, overflowOnly: true }"
          >{{ artifactLabel }}</span>
        </h1>
        <h1
          v-else-if="activeBrowserEnvironment"
          class="inline-flex min-w-0 items-center gap-1.5 truncate text-[14px] font-semibold leading-none text-[color:var(--text-primary)]"
        >
          <Globe :size="15" class="shrink-0 text-[color:var(--text-muted)]" />
          <span
            class="min-w-0 truncate"
            v-tooltip="{ content: browserLabel, overflowOnly: true }"
          >{{ browserLabel }}</span>
        </h1>
      </template>
      <template v-else-if="activeArtifact">
        <span
          v-if="projectName"
          class="shrink-0 truncate text-[14px] font-semibold text-[color:var(--text-secondary)]"
          v-tooltip="{ content: projectName, overflowOnly: true }"
        >{{ projectName }}</span>
        <span v-if="projectName" class="shrink-0 text-[color:var(--text-faint)] opacity-50">/</span>
        <button
          class="min-w-0 shrink truncate text-left text-[13px] font-medium text-[color:var(--text-muted)] transition hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:text-[color:var(--text-primary)]"
          type="button"
          v-tooltip="{ content: session.title, overflowOnly: true }"
          @click="$emit('selectBreadcrumb', session.id)"
        >
          {{ session.title }}
        </button>
        <span class="shrink-0 text-[color:var(--text-faint)] opacity-50">/</span>
        <h1
          class="inline-flex min-w-0 items-center gap-1.5 truncate text-[14px] font-semibold leading-none text-[color:var(--text-primary)]"
        >
          <component :is="activeArtifactIcon" :size="15" class="shrink-0 text-[color:var(--text-muted)]" />
          <span
            class="min-w-0 truncate"
            v-tooltip="{ content: artifactLabel, overflowOnly: true }"
          >{{ artifactLabel }}</span>
        </h1>
      </template>
      <template v-else-if="activeBrowserEnvironment">
        <span
          v-if="projectName"
          class="shrink-0 truncate text-[14px] font-semibold text-[color:var(--text-secondary)]"
          v-tooltip="{ content: projectName, overflowOnly: true }"
        >{{ projectName }}</span>
        <span v-if="projectName" class="shrink-0 text-[color:var(--text-faint)] opacity-50">/</span>
        <button
          class="min-w-0 shrink truncate text-left text-[13px] font-medium text-[color:var(--text-muted)] transition hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:text-[color:var(--text-primary)]"
          type="button"
          v-tooltip="{ content: session.title, overflowOnly: true }"
          @click="$emit('selectBreadcrumb', session.id)"
        >
          {{ session.title }}
        </button>
        <span class="shrink-0 text-[color:var(--text-faint)] opacity-50">/</span>
        <h1
          class="inline-flex min-w-0 items-center gap-1.5 truncate text-[14px] font-semibold leading-none text-[color:var(--text-primary)]"
        >
          <Globe :size="15" class="shrink-0 text-[color:var(--text-muted)]" />
          <span
            class="min-w-0 truncate"
            v-tooltip="{ content: browserLabel, overflowOnly: true }"
          >{{ browserLabel }}</span>
        </h1>
      </template>
      <template v-else-if="projectName">
        <span
          class="shrink-0 truncate text-[14px] font-semibold text-[color:var(--text-secondary)]"
          v-tooltip="{ content: projectName, overflowOnly: true }"
        >{{ projectName }}</span>
        <span class="shrink-0 text-[color:var(--text-faint)] opacity-50">/</span>
        <h1
          class="truncate text-[14px] font-semibold text-[color:var(--text-primary)]"
          v-tooltip="{ content: session.title, overflowOnly: true }"
        >{{ session.title }}</h1>
      </template>
      <h1
        v-else
        class="truncate text-[14px] font-semibold text-[color:var(--text-primary)]"
        v-tooltip="{ content: session.title, overflowOnly: true }"
      >{{ session.title }}</h1>
    </div>
    <div ref="projectLauncherRef" class="relative shrink-0">
      <button
        class="inline-flex h-8 items-center gap-1 rounded-md px-2 text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
        type="button"
        aria-haspopup="menu"
        :aria-expanded="launcherOpen"
        v-tooltip="'Open in ...'"
        @click.stop="toggleProjectLauncher"
      >
        <AppWindowMac :size="17" />
        <ChevronDown :size="15" />
      </button>
      <Transition name="project-launcher">
        <div
          v-if="launcherOpen"
          class="absolute right-0 top-[calc(100%+8px)] z-50 w-44 overflow-hidden rounded-xl border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] py-1 text-[13px] text-[color:var(--text-primary)] shadow-xl"
          role="menu"
          aria-label="Open in app"
          @click.stop
        >
          <button
            class="project-launcher-item"
            type="button"
            role="menuitem"
            :disabled="Boolean(launcherBusyTarget)"
            @click="openProjectLauncherTarget('vscode')"
          >
            <img
              v-if="launcherIconAvailable.vscode"
              class="project-launcher-app-icon"
              :src="projectLauncherIconUrl('vscode')"
              alt=""
              @error="markLauncherIconUnavailable('vscode')"
            />
            <span v-else class="project-launcher-fallback-icon">
              <Code :size="16" />
            </span>
            <span class="min-w-0 flex-1 truncate">VS Code</span>
            <Loader2
              v-if="launcherBusyTarget === 'vscode'"
              class="animate-spin text-[color:var(--text-muted)]"
              :size="15"
            />
          </button>
          <button
            class="project-launcher-item"
            type="button"
            role="menuitem"
            :disabled="Boolean(launcherBusyTarget)"
            @click="openProjectLauncherTarget('finder')"
          >
            <img
              v-if="launcherIconAvailable.finder"
              class="project-launcher-app-icon"
              :src="projectLauncherIconUrl('finder')"
              alt=""
              @error="markLauncherIconUnavailable('finder')"
            />
            <span v-else class="project-launcher-fallback-icon">
              <FolderOpen :size="16" />
            </span>
            <span class="min-w-0 flex-1 truncate">Finder</span>
            <Loader2
              v-if="launcherBusyTarget === 'finder'"
              class="animate-spin text-[color:var(--text-muted)]"
              :size="15"
            />
          </button>
        </div>
      </Transition>
    </div>
    <button
      v-if="showSidebarToggle"
      class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
      :class="rightSidebarVisible ? 'cursor-e-resize' : 'cursor-w-resize'"
      type="button"
      :aria-pressed="rightSidebarVisible"
      v-tooltip="rightSidebarVisible ? 'Hide sidebar' : 'Show sidebar'"
      @click="$emit('toggleRightSidebar')"
    >
      <PanelRight :size="17" />
    </button>
  </header>
</template>

<style scoped>
.project-launcher-item {
  display: flex;
  height: 32px;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  text-align: left;
  transition: background-color 0.14s ease, color 0.14s ease;
}

.project-launcher-item:hover,
.project-launcher-item:focus-visible {
  background: var(--surface-hover);
}

.project-launcher-item:disabled {
  cursor: wait;
  opacity: 0.72;
}

.project-launcher-app-icon {
  display: block;
  height: 18px;
  width: 18px;
  flex-shrink: 0;
  object-fit: contain;
}

.project-launcher-fallback-icon {
  display: grid;
  height: 18px;
  width: 18px;
  flex-shrink: 0;
  place-items: center;
  color: var(--text-muted);
}

.project-launcher-enter-active,
.project-launcher-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}

.project-launcher-enter-from,
.project-launcher-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}
</style>
