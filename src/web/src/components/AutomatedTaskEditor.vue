<script setup lang="ts">
import { CalendarClock, Folder, Webhook, X } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import Composer from './Composer.vue'
import ComposerDropdown from './ComposerDropdown.vue'
import { DEFAULT_AGENT_ID } from '../agentDefaults'
import type { BackendAgentDefinition, BackendAutomatedTaskDetail, BackendModelConfigOption } from '../api/types'
import type { ProjectNavItem } from '../types'

defineOptions({ name: 'AutomatedTaskEditor' })

const props = defineProps<{
  task: BackendAutomatedTaskDetail | null
  projects: ProjectNavItem[]
  agentDefinitions: BackendAgentDefinition[]
  modelConfigs: BackendModelConfigOption[]
  defaultModelConfigId: string
  saving?: boolean
  error?: string
}>()

const emit = defineEmits<{
  save: [payload: AutomatedTaskFormPayload]
  cancel: []
  nameChange: [name: string]
}>()

export interface AutomatedTaskFormPayload {
  name?: string
  projectId: string
  agentId: string
  modelConfigId: string | null
  prompt: string
  triggers: { type: 'time' | 'event'; config: Record<string, unknown> }[]
}

interface TimeTriggerForm {
  type: 'time'
  cron: string
  timezone: string
}

interface EventTriggerForm {
  type: 'event'
  source: string
  events: string[]
  repository: string
}

type TriggerForm = TimeTriggerForm | EventTriggerForm

const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: 'Every day 06:00', cron: '0 6 * * *' },
  { label: 'Every Monday 09:00', cron: '0 9 * * 1' },
  { label: '1st of month 09:00', cron: '0 9 1 * *' },
  { label: 'Custom', cron: '' },
]

const EVENT_OPTIONS = [
  'pull_request.opened',
  'pull_request.synchronize',
  'issues.opened',
  'issues.labeled',
  'push',
  'check_suite.completed',
]

const localTimezone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
})()

const eventVarHint = '{{event.*}}'

const taskName = ref('')
const selectedProjectId = ref('')
const selectedAgentId = ref('')
const selectedModelConfigId = ref('')
const draftText = ref('')
const triggers = ref<TriggerForm[]>([])

const isEditing = computed(() => props.task !== null)

const selectedProject = computed(() => props.projects.find((p) => p.id === selectedProjectId.value) ?? null)
const projectDropdownOptions = computed(() =>
  props.projects.map((project) => ({ id: project.id, label: project.name, description: project.path })),
)

watch(
  () => props.task,
  (task) => {
    taskName.value = task?.name ?? ''
    selectedProjectId.value = task?.project_id ?? props.projects[0]?.id ?? ''
    selectedAgentId.value = task?.agent_id ?? DEFAULT_AGENT_ID
    selectedModelConfigId.value = task?.model_config_id ?? props.defaultModelConfigId
    draftText.value = task?.prompt ?? ''
    triggers.value = (task?.triggers ?? []).map(fromTriggerConfig)
  },
  { immediate: true },
)

watch(
  taskName,
  (name) => {
    emit('nameChange', name.trim())
  },
  { immediate: true },
)

function fromTriggerConfig(trigger: { type: string; config: Record<string, unknown> }): TriggerForm {
  const config = trigger.config ?? {}
  if (trigger.type === 'event') {
    const filter = (config.filter as Record<string, unknown> | undefined) ?? {}
    return {
      type: 'event',
      source: String(config.source ?? 'github'),
      events: Array.isArray(config.events) ? config.events.map(String) : [],
      repository: String(filter.repository ?? ''),
    }
  }
  return {
    type: 'time',
    cron: String(config.cron ?? '0 6 * * *'),
    timezone: String(config.timezone ?? localTimezone),
  }
}

function addSchedule() {
  triggers.value.push({ type: 'time', cron: '0 6 * * *', timezone: localTimezone })
}

function addEvent() {
  triggers.value.push({ type: 'event', source: 'github', events: ['pull_request.opened'], repository: '' })
}

function removeTrigger(index: number) {
  triggers.value.splice(index, 1)
}

function toggleEvent(trigger: EventTriggerForm, event: string) {
  const idx = trigger.events.indexOf(event)
  if (idx === -1) trigger.events.push(event)
  else trigger.events.splice(idx, 1)
}

function presetFor(cron: string): string {
  return CRON_PRESETS.find((preset) => preset.cron === cron && preset.cron)?.cron ?? ''
}

function applyPreset(trigger: TimeTriggerForm, cron: string) {
  if (cron) trigger.cron = cron
}

function toTriggerConfig(trigger: TriggerForm): { type: 'time' | 'event'; config: Record<string, unknown> } {
  if (trigger.type === 'event') {
    return {
      type: 'event',
      config: {
        source: trigger.source,
        events: trigger.events,
        ...(trigger.repository.trim() ? { filter: { repository: trigger.repository.trim() } } : {}),
      },
    }
  }
  return { type: 'time', config: { cron: trigger.cron.trim(), timezone: trigger.timezone.trim() || 'UTC' } }
}

function handleSubmit(payload: { prompt: string; files: File[] }) {
  if (!selectedProjectId.value || !payload.prompt.trim()) return
  const name = taskName.value.trim()
  if (isEditing.value && !name) return
  emit('save', {
    ...(name ? { name } : {}),
    projectId: selectedProjectId.value,
    agentId: selectedAgentId.value || props.agentDefinitions[0]?.id || DEFAULT_AGENT_ID,
    modelConfigId: selectedModelConfigId.value || null,
    prompt: payload.prompt.trim(),
    triggers: triggers.value.map(toTriggerConfig),
  })
}

const inputClass =
  'w-full rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] px-3 py-2 text-[14px] text-[color:var(--text-primary)] outline-none transition focus:border-[color:var(--accent)]'
const labelClass = 'mb-1.5 block text-[13px] font-medium text-[color:var(--text-secondary)]'
</script>

<template>
  <div class="mx-auto flex w-full max-w-[820px] flex-col gap-5">
    <div class="flex items-center justify-between">
      <h1 class="text-[20px] font-semibold tracking-normal text-[color:var(--text-primary)]">
        {{ isEditing ? 'Edit automated task' : 'New automated task' }}
      </h1>
      <button type="button" class="cancel-btn" @click="emit('cancel')">Cancel</button>
    </div>

    <p
      v-if="error"
      class="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-[13px] text-destructive"
    >
      {{ error }}
    </p>

    <div class="rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] p-4">
      <label :class="labelClass" for="automated-task-name">Task name</label>
      <input
        id="automated-task-name"
        v-model="taskName"
        :class="inputClass"
        type="text"
        :placeholder="isEditing ? 'Task name' : 'Auto-generate from prompt'"
      />
    </div>

    <!-- Triggers (above the composer) -->
    <div class="rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] p-4">
      <div class="mb-3 flex items-center justify-between">
        <div>
          <p class="text-[14px] font-medium text-[color:var(--text-primary)]">Triggers</p>
          <p class="text-[12px] text-[color:var(--text-faint)]">
            Schedules run automatically once the task is enabled. GitHub event triggers start firing in a later release; “Run now” works today.
          </p>
        </div>
        <div class="flex gap-2">
          <button type="button" class="trigger-add-btn" @click="addSchedule">
            <CalendarClock :size="14" /> Schedule
          </button>
          <button type="button" class="trigger-add-btn" @click="addEvent">
            <Webhook :size="14" /> Event
          </button>
        </div>
      </div>

      <p v-if="!triggers.length" class="py-1 text-[13px] text-[color:var(--text-faint)]">
        No triggers yet — the task can still be run manually with “Run now”.
      </p>

      <div
        v-for="(trigger, index) in triggers"
        :key="index"
        class="mb-2 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-hover)] p-3"
      >
        <div class="mb-2 flex items-center justify-between">
          <span class="flex items-center gap-1.5 text-[13px] font-medium text-[color:var(--text-secondary)]">
            <CalendarClock v-if="trigger.type === 'time'" :size="14" />
            <Webhook v-else :size="14" />
            {{ trigger.type === 'time' ? 'Schedule' : 'Event' }}
          </span>
          <button type="button" class="icon-button h-7 w-7" aria-label="Remove trigger" @click="removeTrigger(index)">
            <X :size="14" />
          </button>
        </div>

        <template v-if="trigger.type === 'time'">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label :class="labelClass">Preset</label>
              <select
                :class="inputClass"
                :value="presetFor(trigger.cron)"
                @change="applyPreset(trigger, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="preset in CRON_PRESETS" :key="preset.label" :value="preset.cron">{{ preset.label }}</option>
              </select>
            </div>
            <div>
              <label :class="labelClass">Cron</label>
              <input v-model="trigger.cron" :class="inputClass" type="text" placeholder="0 6 * * *" />
            </div>
          </div>
          <div class="mt-3">
            <label :class="labelClass">Timezone</label>
            <input v-model="trigger.timezone" :class="inputClass" type="text" placeholder="UTC" />
          </div>
        </template>

        <template v-else>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label :class="labelClass">Source</label>
              <select v-model="trigger.source" :class="inputClass">
                <option value="github">GitHub</option>
              </select>
            </div>
            <div>
              <label :class="labelClass">Repository filter <span class="text-[color:var(--text-faint)]">(optional)</span></label>
              <input v-model="trigger.repository" :class="inputClass" type="text" placeholder="owner/repo" />
            </div>
          </div>
          <div class="mt-3">
            <label :class="labelClass">Events</label>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="event in EVENT_OPTIONS"
                :key="event"
                type="button"
                class="rounded-full border px-2.5 py-1 text-[12px] transition"
                :class="trigger.events.includes(event)
                  ? 'border-[color:var(--accent)] bg-[var(--accent)] text-[color:var(--accent-contrast)]'
                  : 'border-[color:var(--border-muted)] text-[color:var(--text-secondary)] hover:bg-[var(--surface)]'"
                @click="toggleEvent(trigger, event)"
              >
                {{ event }}
              </button>
            </div>
          </div>
          <p class="mt-3 text-[12px] text-[color:var(--text-faint)]">
            Reference payload fields in your prompt with <code>{{ eventVarHint }}</code> template variables.
          </p>
        </template>
      </div>
    </div>

    <!-- Project + Composer (mirrors the New Chat experience) -->
    <div class="flex flex-col gap-2">
      <div class="flex flex-wrap items-center gap-2">
        <ComposerDropdown
          test-id="automated-task-project-picker"
          :title="selectedProject?.name ?? 'No project'"
          :options="projectDropdownOptions"
          :selected-id="selectedProjectId"
          :disabled="projects.length === 0"
          button-class="max-w-full sm:max-w-[260px]"
          @select="selectedProjectId = $event"
        >
          <template #button-prefix>
            <Folder :size="15" class="shrink-0 text-[color:var(--text-muted)]" />
          </template>
        </ComposerDropdown>
      </div>

      <Composer
        :disabled="saving || !selectedProject"
        :can-stop="false"
        session-id="new-chat"
        :project-id="selectedProjectId"
        :agent-definitions="agentDefinitions"
        :agent-id="selectedAgentId"
        :model-config-id="selectedModelConfigId"
        :model-configs="modelConfigs"
        :draft-text="draftText"
        :submit-label="isEditing ? 'Save' : 'Create'"
        full-width
        :auto-focus="false"
        @send="handleSubmit"
        @update-agent="selectedAgentId = $event"
        @update-model-config="selectedModelConfigId = $event"
      />

      <p class="px-1 text-[12px] text-[color:var(--text-faint)]">
        An automated run is unattended — avoid the <code>request_user_input</code> tool.
      </p>
    </div>
  </div>
</template>

<style scoped>
.trigger-add-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 0.5rem;
  border: 1px solid var(--border-muted);
  padding: 0.35rem 0.6rem;
  font-size: 12px;
  color: var(--text-secondary);
  transition: background 0.15s;
}
.trigger-add-btn:hover {
  background: var(--surface-hover);
}
.cancel-btn {
  border-radius: 0.5rem;
  padding: 0.4rem 0.8rem;
  font-size: 13px;
  color: var(--text-secondary);
}
.cancel-btn:hover {
  background: var(--surface-hover);
}
</style>
