<script setup lang="ts">
import { Folder } from '@lucide/vue'
import { computed } from 'vue'
import Composer from './Composer.vue'
import ComposerDropdown from './ComposerDropdown.vue'
import type { BackendAgentDefinition, BackendModelConfigOption } from '../api/types'
import { DEFAULT_AGENT_ID } from '../agentDefaults'
import type { ContextUsageSummary, ProjectNavItem, SendPromptPayload } from '../types'

defineOptions({
  name: 'NewChatPage',
})

const props = defineProps<{
  projects: ProjectNavItem[]
  selectedProjectId: string
  agentDefinitions: BackendAgentDefinition[]
  selectedAgentId: string
  modelConfigId: string
  modelConfigs: BackendModelConfigOption[]
  contextUsage?: ContextUsageSummary | null
  disabled?: boolean
  error?: string
  sendError?: string
  draftText?: string
}>()

const emit = defineEmits<{
  projectChange: [projectId: string]
  agentChange: [agentId: string]
  sendPrompt: [payload: SendPromptPayload, projectId: string, agentId: string]
  updateModelConfig: [modelConfigId: string]
  updateDraftText: [draftText: string]
  dictationError: [message: string]
  optimizeError: [message: string]
}>()

const selectedProject = computed(() => {
  return props.projects.find((project) => project.id === props.selectedProjectId) ?? null
})

const projectDropdownOptions = computed(() => {
  return props.projects.map((project) => ({
    id: project.id,
    label: project.name,
    description: project.path,
  }))
})

function handleProjectSelect(projectId: string) {
  if (projectId === selectedProject.value?.id) return
  emit('projectChange', projectId)
}

function handleSend(payload: SendPromptPayload) {
  if (!selectedProject.value) return
  emit('sendPrompt', payload, selectedProject.value.id, props.selectedAgentId || DEFAULT_AGENT_ID)
}
</script>

<template>
  <main class="flex min-w-0 flex-1 bg-background text-foreground" data-testid="new-chat-page">
    <div class="mx-auto flex h-full w-full max-w-[960px] flex-col px-4 sm:px-6">
      <div class="flex min-h-0 flex-1 flex-col justify-center pb-[12vh]">
        <div class="mx-auto flex w-full max-w-[820px] flex-col gap-2">
          <div
            v-if="error"
            class="rounded-xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-[13px] text-destructive"
          >
            {{ error }}
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <ComposerDropdown
              test-id="new-chat-project-picker"
              selected-label-test-id="new-chat-selected-project-name"
              :title="selectedProject?.name ?? 'No project'"
              :options="projectDropdownOptions"
              :selected-id="selectedProject?.id ?? ''"
              :disabled="disabled || projects.length === 0"
              button-class="max-w-full sm:max-w-[260px]"
              @select="handleProjectSelect"
            >
              <template #button-prefix>
                <Folder :size="15" class="shrink-0 text-[color:var(--text-muted)]" />
              </template>
            </ComposerDropdown>
          </div>

          <div class="w-full">
            <Composer
              :disabled="disabled || !selectedProject"
              :can-stop="false"
              :send-error="sendError"
              session-id="new-chat"
              :project-id="selectedProject?.id ?? ''"
              :agent-definitions="agentDefinitions"
              :agent-id="selectedAgentId"
              :model-config-id="modelConfigId"
              :model-configs="modelConfigs"
              :context-usage="contextUsage ?? undefined"
              :draft-text="draftText"
              @send="handleSend"
              @update-agent="emit('agentChange', $event)"
              @update-model-config="emit('updateModelConfig', $event)"
              @update-draft-text="emit('updateDraftText', $event)"
              @dictation-error="emit('dictationError', $event)"
              @optimize-error="emit('optimizeError', $event)"
            />
          </div>
        </div>
      </div>
    </div>
  </main>
</template>
