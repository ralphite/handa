<script setup lang="ts">
import { Check, Clock, Plus, X } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import AutomatedTaskEditor from './AutomatedTaskEditor.vue'
import type { AutomatedTaskFormPayload } from './AutomatedTaskEditor.vue'
import AutomatedTaskListItem from './AutomatedTaskListItem.vue'
import AutomatedTaskRuns from './AutomatedTaskRuns.vue'
import {
  ApiRequestError,
  createAutomatedTask,
  deleteAutomatedTask,
  getAutomatedTask,
  listAutomatedTaskRuns,
  listAutomatedTasks,
  runAutomatedTaskNow,
  setAutomatedTaskEnabled,
  updateAutomatedTask,
} from '../api/client'
import type {
  BackendAgentDefinition,
  BackendAutomatedTask,
  BackendAutomatedTaskDetail,
  BackendAutomatedTaskRun,
  BackendModelConfigOption,
} from '../api/types'
import type { ProjectNavItem } from '../types'

defineOptions({ name: 'AutomatedTasksPage' })

const props = defineProps<{
  open: boolean
  projects: ProjectNavItem[]
  agentDefinitions: BackendAgentDefinition[]
  modelConfigs: BackendModelConfigOption[]
  defaultModelConfigId: string
}>()

const emit = defineEmits<{
  close: []
  openSession: [sessionId: string]
  error: [message: string]
}>()

type View = 'list' | 'editor' | 'runs'

const view = ref<View>('list')
const mainEl = ref<HTMLElement | null>(null)
const tasks = ref<BackendAutomatedTask[]>([])
const listLoading = ref(false)
const loadError = ref('')
const busyTaskId = ref('')

const editingTask = ref<BackendAutomatedTaskDetail | null>(null)
const saving = ref(false)
const saveError = ref('')

const runsTask = ref<{ id: string; name: string } | null>(null)
const runs = ref<BackendAutomatedTaskRun[]>([])
const runsLoading = ref(false)

const pendingDelete = ref<BackendAutomatedTask | null>(null)

const noticeText = ref('')
let noticeTimer: number | undefined

const projectName = (id: string) => props.projects.find((project) => project.id === id)?.name ?? 'Unknown project'

const taskRows = computed(() =>
  tasks.value.map((task) => ({
    task,
    projectName: projectName(task.project_id),
  })),
)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    // Always start at the list and reload, so a reopened panel is never stale.
    if (view.value === 'list') void loadTasks()
    else view.value = 'list'
  },
)
// Returning to the list (Cancel / Back) reloads, so edits/deletes show up.
watch(view, (v) => {
  void nextTick(() => {
    if (mainEl.value) mainEl.value.scrollTop = 0
  })
  if (v === 'list' && props.open) void loadTasks()
})

function messageFor(err: unknown): string {
  if (err instanceof ApiRequestError) return err.message
  if (err instanceof Error) return err.message
  return 'Something went wrong'
}

function reportError(err: unknown) {
  emit('error', messageFor(err))
}

async function loadTasks() {
  listLoading.value = true
  loadError.value = ''
  try {
    tasks.value = await listAutomatedTasks()
  } catch (err) {
    loadError.value = messageFor(err)
  } finally {
    listLoading.value = false
  }
}

function startCreate() {
  editingTask.value = null
  saveError.value = ''
  view.value = 'editor'
}

async function startEdit(task: BackendAutomatedTask) {
  saveError.value = ''
  try {
    editingTask.value = await getAutomatedTask(task.id)
    view.value = 'editor'
  } catch (err) {
    reportError(err)
  }
}

async function handleSave(payload: AutomatedTaskFormPayload) {
  saving.value = true
  saveError.value = ''
  const wasCreate = !editingTask.value
  try {
    if (editingTask.value) {
      await updateAutomatedTask(editingTask.value.id, {
        name: payload.name,
        prompt: payload.prompt,
        agent_id: payload.agentId,
        model_config_id: payload.modelConfigId,
        triggers: payload.triggers,
      })
    } else {
      await createAutomatedTask({
        ...(payload.name ? { name: payload.name } : {}),
        project_id: payload.projectId,
        prompt: payload.prompt,
        agent_id: payload.agentId,
        model_config_id: payload.modelConfigId,
        triggers: payload.triggers,
      })
    }
    view.value = 'list'
    await loadTasks()
    if (wasCreate) {
      // The LLM-generated title (like a session name) lands shortly after
      // create; refetch once so the list shows it instead of the raw fallback.
      window.setTimeout(() => { void loadTasks() }, 1800)
    }
  } catch (err) {
    saveError.value = messageFor(err)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(task: BackendAutomatedTask) {
  busyTaskId.value = task.id
  try {
    const updated = await setAutomatedTaskEnabled(task.id, !task.enabled)
    tasks.value = tasks.value.map((item) => (item.id === task.id ? { ...item, enabled: updated.enabled } : item))
  } catch (err) {
    reportError(err)
  } finally {
    busyTaskId.value = ''
  }
}

async function runNow(task: BackendAutomatedTask) {
  busyTaskId.value = task.id
  try {
    const run = await runAutomatedTaskNow(task.id)
    if (run.status === 'error') {
      emit('error', run.error_message || 'Run failed to start')
    } else {
      showNotice('Run started — check Run history for progress.')
    }
    await loadTasks()
  } catch (err) {
    reportError(err)
  } finally {
    busyTaskId.value = ''
  }
}

async function openRuns(task: BackendAutomatedTask) {
  runsTask.value = { id: task.id, name: task.name }
  runs.value = []
  view.value = 'runs'
  await refreshRuns()
}

async function refreshRuns() {
  if (!runsTask.value) return
  runsLoading.value = true
  try {
    runs.value = await listAutomatedTaskRuns(runsTask.value.id)
  } catch (err) {
    reportError(err)
  } finally {
    runsLoading.value = false
  }
}

function askDelete(task: BackendAutomatedTask) {
  pendingDelete.value = task
}

function cancelDelete() {
  pendingDelete.value = null
}

async function confirmDelete() {
  const task = pendingDelete.value
  pendingDelete.value = null
  if (!task) return
  busyTaskId.value = task.id
  try {
    await deleteAutomatedTask(task.id)
    await loadTasks()
  } catch (err) {
    reportError(err)
  } finally {
    busyTaskId.value = ''
  }
}

function handleOpenSession(sessionId: string) {
  emit('openSession', sessionId)
  emit('close')
}

function handleBackdrop() {
  // Don't discard a half-filled editor when the user clicks just outside it.
  if (view.value === 'editor') return
  emit('close')
}

function showNotice(text: string) {
  noticeText.value = text
  if (noticeTimer) window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => { noticeText.value = '' }, 4000)
}

</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[70] flex items-start justify-center bg-[var(--overlay)] px-3 pt-[8vh]"
    role="dialog"
    aria-modal="true"
    aria-labelledby="automated-tasks-title"
    @click.self="handleBackdrop"
  >
    <div
      class="relative flex max-h-[86vh] w-full max-w-[920px] flex-col overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] text-[color:var(--text-primary)] shadow-2xl outline-none"
      @click.stop
    >
      <header class="flex h-14 shrink-0 items-center gap-2 border-b border-[color:var(--border-muted)] px-5">
        <span id="automated-tasks-title" class="text-[15px] font-semibold">Automated tasks</span>
        <button type="button" class="icon-button ml-auto h-8 w-8" aria-label="Close" @click="emit('close')">
          <X :size="16" />
        </button>
      </header>

      <!-- Transient success notice — floats so it never pushes the list down. -->
      <transition name="notice">
        <div
          v-if="noticeText && view === 'list'"
          class="pointer-events-none absolute left-1/2 top-[4.25rem] z-10 flex -translate-x-1/2 items-start gap-2 rounded-lg border border-success/30 bg-success-soft px-3 py-2 text-[13px] text-success shadow-2xl shadow-[var(--shadow-color)]"
        >
          <Check :size="16" class="mt-px shrink-0" />
          <span class="leading-5">{{ noticeText }}</span>
        </div>
      </transition>

      <main ref="mainEl" class="min-w-0 flex-1 overflow-y-auto px-6 py-6">
        <!-- LIST -->
        <template v-if="view === 'list'">
          <div class="mx-auto w-full max-w-[920px]">
            <p
              v-if="loadError"
              class="mb-4 rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-[13px] text-destructive"
            >
              {{ loadError }}
            </p>

            <p v-if="listLoading" class="py-10 text-center text-[14px] text-[color:var(--text-faint)]">Loading…</p>

            <div
              v-else-if="!tasks.length"
              class="flex flex-col items-center py-16 text-center"
            >
              <span class="mb-4 grid h-12 w-12 place-items-center rounded-full bg-[var(--surface-muted)] text-[color:var(--text-faint)]">
                <Clock :size="22" />
              </span>
              <p class="text-[14px] font-medium text-[color:var(--text-secondary)]">No automated tasks yet</p>
              <p class="mt-1 max-w-[360px] text-[13px] text-[color:var(--text-faint)]">
                Create one to run an agent on a schedule, on an event, or on demand.
              </p>
              <button type="button" class="primary-btn mt-5" :disabled="!projects.length" @click="startCreate">
                <Plus :size="15" /> New task
              </button>
            </div>

            <template v-else>
              <div class="mb-3 flex justify-end">
                <button type="button" class="primary-btn" :disabled="!projects.length" @click="startCreate">
                  <Plus :size="15" /> New task
                </button>
              </div>
              <ul class="flex flex-col gap-2">
                <AutomatedTaskListItem
                  v-for="row in taskRows"
                  :key="row.task.id"
                  :task="row.task"
                  :project-name="row.projectName"
                  :busy="busyTaskId === row.task.id"
                  @toggle-enabled="toggleEnabled(row.task)"
                  @run-now="runNow(row.task)"
                  @open-runs="openRuns(row.task)"
                  @edit="startEdit(row.task)"
                  @delete="askDelete(row.task)"
                />
              </ul>
            </template>
          </div>
        </template>

        <!-- EDITOR -->
        <AutomatedTaskEditor
          v-else-if="view === 'editor'"
          :task="editingTask"
          :projects="projects"
          :agent-definitions="agentDefinitions"
          :model-configs="modelConfigs"
          :default-model-config-id="defaultModelConfigId"
          :saving="saving"
          :error="saveError"
          @save="handleSave"
          @cancel="view = 'list'"
        />

        <!-- RUNS -->
        <AutomatedTaskRuns
          v-else-if="view === 'runs' && runsTask"
          :task-name="runsTask.name"
          :runs="runs"
          :loading="runsLoading"
          @open-session="handleOpenSession"
          @refresh="refreshRuns"
          @back="view = 'list'"
        />
      </main>
    </div>

    <!-- Delete confirmation — matches the app's styled confirm dialog. -->
    <div
      v-if="pendingDelete"
      class="fixed inset-0 z-[80] grid place-items-center bg-[var(--overlay)] px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-task-title"
      @click.self="cancelDelete"
    >
      <div class="w-full max-w-[380px] rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] p-4 shadow-2xl">
        <h2 id="delete-task-title" class="text-[15px] font-semibold text-[color:var(--text-primary)]">Delete automated task?</h2>
        <p class="mt-2 text-[13px] leading-5 text-[color:var(--text-muted)]">
          This permanently removes “{{ pendingDelete.name }}”, including its triggers and run history.
        </p>
        <div class="mt-5 flex justify-end gap-2">
          <button class="quiet-button" type="button" @click="cancelDelete">Cancel</button>
          <button
            class="inline-flex h-8 items-center justify-center rounded-lg bg-destructive px-3 text-[13px] font-medium text-destructive-foreground transition hover:opacity-90"
            type="button"
            @click="confirmDelete"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 0.5rem;
  background: var(--accent);
  color: var(--accent-contrast);
  padding: 0.4rem 0.8rem;
  font-size: 13px;
  font-weight: 500;
  transition: opacity 0.15s;
}
.primary-btn:hover {
  opacity: 0.9;
}
.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.notice-enter-active,
.notice-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.notice-enter-from,
.notice-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
