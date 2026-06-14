<script setup lang="ts">
import { History, Loader2, MoreVertical, Pencil, Play, Power, PowerOff, Trash2 } from '@lucide/vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { BackendAutomatedTask, BackendAutomatedTaskTrigger } from '../api/types'

defineOptions({ name: 'AutomatedTaskListItem' })

const props = withDefaults(
  defineProps<{
    task: BackendAutomatedTask
    projectName: string
    busy?: boolean
  }>(),
  {
    busy: false,
  },
)

const emit = defineEmits<{
  'toggle-enabled': [value: boolean]
  'run-now': []
  'open-runs': []
  edit: []
  delete: []
}>()

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const SOON_HINT =
  'GitHub event triggers don’t fire yet — they start in a later release. Schedules and “Run now” work today.'

const menuButton = ref<HTMLButtonElement | null>(null)
const menuOpen = ref(false)
const menuPosition = ref({ top: 0, left: 0 })

const triggerText = computed(() => triggerSummary(props.task.triggers))
const pendingTrigger = computed(() => hasPendingTrigger(props.task))

function toggleMenu() {
  if (props.busy) return
  if (menuOpen.value) {
    menuOpen.value = false
    return
  }
  const rect = menuButton.value?.getBoundingClientRect()
  if (!rect) return
  const width = 144
  const viewportPadding = 12
  menuPosition.value = {
    top: rect.bottom + 6,
    left: Math.min(Math.max(viewportPadding, rect.right - width), window.innerWidth - width - viewportPadding),
  }
  menuOpen.value = true
}

function deleteFromMenu() {
  menuOpen.value = false
  emit('delete')
}

function toggleEnabledFromMenu() {
  menuOpen.value = false
  emit('toggle-enabled', !props.task.enabled)
}

function closeMenu() {
  menuOpen.value = false
}

function handleDocumentMouseDown(event: MouseEvent) {
  if (!menuOpen.value) return
  const target = event.target
  if (!(target instanceof Node)) return
  if (menuButton.value?.contains(target)) return
  menuOpen.value = false
}

function hasPendingTrigger(task: BackendAutomatedTask): boolean {
  return task.triggers.some((trigger) => trigger.type === 'event')
}

function describeCron(cron: string): string {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return cron ? `Schedule ${cron}` : 'Schedule'
  const [min = '', hour = '', dom = '', mon = '', dow = ''] = parts
  const hh = Number(hour)
  const mm = Number(min)
  const time = Number.isNaN(hh) || Number.isNaN(mm)
    ? null
    : `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
  if (time && dom === '*' && mon === '*' && dow === '*') return `Every day at ${time}`
  if (time && dom === '*' && mon === '*' && /^[0-6]$/.test(dow)) return `Every ${WEEKDAYS[Number(dow)]} at ${time}`
  if (time && /^\d+$/.test(dom) && mon === '*' && dow === '*') return `Monthly on day ${Number(dom)} at ${time}`
  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    return `Hourly at :${String(Number(min)).padStart(2, '0')}`
  }
  return `Schedule ${cron}`
}

function triggerSummary(triggers: BackendAutomatedTaskTrigger[]): string {
  if (!triggers.length) return 'Manual only'
  return triggers
    .map((trigger) => {
      if (trigger.type === 'time') return describeCron(String(trigger.config.cron ?? ''))
      const events = Array.isArray(trigger.config.events) ? (trigger.config.events as string[]) : []
      return `${String(trigger.config.source ?? 'event')}: ${events.join(', ') || 'any'}`
    })
    .join(' · ')
}

watch(
  () => props.busy,
  (busy) => {
    if (busy) menuOpen.value = false
  },
)

onMounted(() => {
  document.addEventListener('mousedown', handleDocumentMouseDown)
  window.addEventListener('resize', closeMenu)
  window.addEventListener('scroll', closeMenu, true)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleDocumentMouseDown)
  window.removeEventListener('resize', closeMenu)
  window.removeEventListener('scroll', closeMenu, true)
})
</script>

<template>
  <li
    class="task-row grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] px-4 py-3 transition hover:border-[color:var(--border-subtle)]"
    :class="{ 'task-row-menu-open': menuOpen }"
  >
    <div class="min-w-0">
      <p
        class="-mx-1 truncate px-1 text-[14px] font-medium text-[color:var(--text-primary)]"
      >{{ projectName }} / {{ task.name }}</p>
    </div>

    <p class="task-row-meta col-start-2 row-start-1 flex min-w-0 items-center justify-end gap-1.5 text-[12px] text-[color:var(--text-faint)]">
      <span class="truncate">{{ triggerText }}</span>
      <span v-if="pendingTrigger" class="soon-chip shrink-0" v-tooltip="SOON_HINT">Soon</span>
      <span v-if="!task.enabled" class="disabled-chip shrink-0">Disabled</span>
    </p>

    <div class="task-row-actions col-start-2 row-start-1 flex shrink-0 items-center justify-end gap-0.5">
      <button type="button" class="row-action row-action-run" :disabled="busy" aria-label="Run now" v-tooltip="'Run now'" @click="emit('run-now')">
        <Loader2 v-if="busy" :size="15" class="animate-spin" />
        <Play v-else :size="15" />
      </button>
      <button type="button" class="row-action" aria-label="Run history" v-tooltip="'Run history'" @click="emit('open-runs')">
        <History :size="15" />
      </button>
      <button type="button" class="row-action" aria-label="Edit" v-tooltip="'Edit'" @click="emit('edit')">
        <Pencil :size="15" />
      </button>
      <span class="mx-1 h-5 w-px bg-[var(--border-muted)]"></span>
      <button
        ref="menuButton"
        type="button"
        class="row-action"
        :disabled="busy"
        aria-label="Task menu"
        aria-haspopup="menu"
        :aria-expanded="menuOpen"
        v-tooltip="{ content: 'Task menu', disabled: menuOpen }"
        @click="toggleMenu"
        @keydown.esc="menuOpen = false"
      >
        <MoreVertical :size="15" />
      </button>
    </div>

    <div
      v-if="menuOpen"
      class="fixed z-[75] w-36 rounded-md border border-[color:var(--border-subtle)] bg-[var(--surface)] py-1 text-[13px] text-[color:var(--text-primary)] shadow-2xl"
      :style="{ top: `${menuPosition.top}px`, left: `${menuPosition.left}px` }"
      role="menu"
      @click.stop
      @mousedown.stop
    >
      <button
        class="menu-item"
        type="button"
        role="menuitem"
        @click="toggleEnabledFromMenu"
      >
        <PowerOff v-if="task.enabled" :size="15" />
        <Power v-else :size="15" />
        <span>{{ task.enabled ? 'Disable' : 'Enable' }}</span>
      </button>
      <div class="my-1 border-t border-[color:var(--border-muted)]"></div>
      <button
        class="menu-item menu-item-danger"
        type="button"
        role="menuitem"
        @click="deleteFromMenu"
      >
        <Trash2 :size="15" />
        <span>Delete</span>
      </button>
    </div>
  </li>
</template>

<style scoped>
.task-row-meta {
  max-width: min(24rem, 42vw);
  opacity: 1;
  transform: translateX(0);
  transition: none;
}

.task-row-actions {
  border-radius: 0.5rem;
  background: var(--surface);
  opacity: 0;
  pointer-events: none;
  transform: translateX(0.25rem);
  transition: none;
}

.task-row:hover .task-row-meta,
.task-row:focus-within .task-row-meta,
.task-row-menu-open .task-row-meta {
  opacity: 0;
  pointer-events: none;
  transform: translateX(0.25rem);
}

.task-row:hover .task-row-actions,
.task-row:focus-within .task-row-actions,
.task-row-menu-open .task-row-actions {
  opacity: 1;
  pointer-events: auto;
  transform: translateX(0);
}

.row-action {
  display: grid;
  place-items: center;
  height: 2rem;
  width: 2rem;
  border-radius: 0.5rem;
  color: var(--text-muted);
  transition: background 0.15s, color 0.15s;
}

.row-action:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
}

.row-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.row-action-run {
  color: var(--text-secondary);
}

.row-action-run:not(:disabled):hover {
  background: var(--accent);
  color: var(--accent-contrast);
}

.menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.65rem;
  text-align: left;
  color: var(--text-primary);
  transition: background 0.15s, color 0.15s;
}

.menu-item:hover {
  background: var(--surface-hover);
}

.menu-item-danger {
  color: var(--destructive);
}

.menu-item-danger:hover {
  background: var(--destructive-soft);
  color: var(--destructive);
}

.soon-chip {
  display: inline-flex;
  align-items: center;
  border-radius: 9999px;
  border: 1px solid var(--border-muted);
  padding: 0 0.4rem;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--text-faint);
}

.disabled-chip {
  color: var(--text-muted);
  font-weight: 500;
}

@media (hover: none) {
  .task-row-meta {
    opacity: 0;
    pointer-events: none;
    transform: translateX(0.25rem);
  }

  .task-row-actions {
    opacity: 1;
    pointer-events: auto;
    transform: translateX(0);
  }
}
</style>
