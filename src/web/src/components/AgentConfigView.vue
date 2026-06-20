<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, ChevronDown, ChevronRight, FileCode2, ListOrdered, Sparkles, Workflow, Wrench } from '@lucide/vue'
import { MarkdownRender } from 'markstream-vue'
import 'markstream-vue/index.css'
import HighlightCodeBlock from './HighlightCodeBlock.vue'
import {
  formatAgentConfigJson,
  groupConfigTools,
  resolveInstructionSections,
  resolveSkills,
  resolveSubagents,
} from '../agentConfig'
import type { ParsedAgentConfig } from '../agentConfig'
import type { BackendAgentCatalog } from '../api/types'

defineOptions({
  name: 'AgentConfigView',
})

const props = defineProps<{
  config: ParsedAgentConfig
  rawContent: string
  catalog?: BackendAgentCatalog | null
  catalogLoading?: boolean
  catalogError?: string | null
  versionLabel?: string | null
  sourceLabel?: string | null
  markdownIsDark?: boolean
}>()

const viewMode = ref<'structured' | 'json'>('structured')
const expanded = ref(new Set(['custom']))

const toolGroups = computed(() => groupConfigTools(props.config.tools, props.catalog))
const sections = computed(() =>
  resolveInstructionSections(props.config.instructionSections, props.config.name, props.catalog),
)
const skills = computed(() => resolveSkills(props.config.skills, props.catalog))
const subagents = computed(() => resolveSubagents(props.config.subagents, props.catalog))
const formattedJson = computed(() => formatAgentConfigJson(props.rawContent))

const instructionCount = computed(() => {
  const base = `${props.config.instructionSections.length} section${props.config.instructionSections.length === 1 ? '' : 's'}`
  return props.config.customInstruction ? `${base} + custom · in merge order` : `${base} · in merge order`
})

const effectiveMarkdownIsDark = computed(() => {
  if (props.markdownIsDark !== undefined) return props.markdownIsDark
  if (typeof document === 'undefined') return true
  return document.documentElement.dataset.themeMode !== 'light'
})

function isExpanded(key: string) {
  return expanded.value.has(key)
}

function toggleExpanded(key: string) {
  const next = new Set(expanded.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expanded.value = next
}

function tooltipForTool(definition: string | null, known: boolean) {
  if (!known) return 'Not in the current tool registry'
  if (!definition) return null
  return definition.length > 600 ? `${definition.slice(0, 600)}…` : definition
}
</script>

<template>
  <section class="agent-config-view min-w-0">
    <header class="flex items-start gap-3">
      <div class="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--surface-hover)] text-[color:var(--text-secondary)]">
        <Bot :size="18" />
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <h1 class="min-w-0 truncate font-mono text-[16px] font-medium leading-6 text-foreground" :title="config.name">{{ config.name }}</h1>
          <span
            v-if="versionLabel"
            class="rounded-full bg-[var(--surface-hover)] px-2 py-0.5 text-[12px] text-[color:var(--text-muted)]"
          >{{ versionLabel }}</span>
          <span class="rounded-full bg-[var(--surface-hover)] px-2 py-0.5 text-[12px] text-[color:var(--text-muted)]">agent config</span>
        </div>
        <p v-if="config.description" class="mt-1.5 text-[14px] leading-6 text-[color:var(--text-secondary)]">{{ config.description }}</p>
        <div v-if="sourceLabel" class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[color:var(--text-faint)]">
          <span class="inline-flex items-center gap-1.5"><FileCode2 :size="13" />{{ sourceLabel }}</span>
        </div>
      </div>
      <div class="flex shrink-0 gap-1">
        <button
          type="button"
          class="rounded-md border px-2.5 py-1 text-[12px] transition-colors"
          :class="viewMode === 'structured'
            ? 'border-[color:var(--border-muted)] bg-[var(--surface-active)] text-foreground'
            : 'border-transparent text-[color:var(--text-muted)] hover:bg-[var(--surface-hover)]'"
          @click="viewMode = 'structured'"
        >Structured</button>
        <button
          type="button"
          class="rounded-md border px-2.5 py-1 text-[12px] transition-colors"
          :class="viewMode === 'json'
            ? 'border-[color:var(--border-muted)] bg-[var(--surface-active)] text-foreground'
            : 'border-transparent text-[color:var(--text-muted)] hover:bg-[var(--surface-hover)]'"
          @click="viewMode = 'json'"
        >JSON</button>
      </div>
    </header>

    <div class="my-4 border-t border-[color:var(--border-muted)]"></div>

    <p v-if="catalogLoading" class="mb-4 text-[12px] text-[color:var(--text-faint)]">Loading tool and instruction definitions…</p>
    <p v-else-if="catalogError" class="mb-4 text-[12px] text-[color:var(--text-faint)]">Definitions unavailable — showing config keys only.</p>

    <template v-if="viewMode === 'structured'">
      <div class="flex items-center gap-2 text-[13px] font-medium text-[color:var(--text-secondary)]">
        <Wrench :size="15" />
        <span>Tools</span>
        <span class="text-[12px] font-normal text-[color:var(--text-faint)]">{{ config.tools.length }}</span>
      </div>
      <p v-if="!config.tools.length" class="mt-2 text-[13px] text-[color:var(--text-faint)]">No tools.</p>
      <div v-for="group in toolGroups" :key="group.label" class="mt-2.5">
        <div
          v-if="group.label"
          class="mb-1.5 text-[12px]"
          :class="group.unregistered ? 'text-destructive' : 'text-[color:var(--text-faint)]'"
        >{{ group.label }} · {{ group.tools.length }}</div>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="tool in group.tools"
            :key="tool.name"
            v-tooltip="tooltipForTool(tool.definition, tool.known)"
            class="rounded-full border px-2.5 py-1 font-mono text-[12px]"
            :class="tool.known
              ? 'border-[color:var(--border-muted)] text-[color:var(--text-secondary)]'
              : 'border-destructive/40 bg-destructive-soft text-destructive'"
          >{{ tool.name }}</span>
        </div>
      </div>

      <div class="mt-6 flex items-center gap-2 text-[13px] font-medium text-[color:var(--text-secondary)]">
        <Sparkles :size="15" />
        <span>Skills</span>
        <span class="text-[12px] font-normal text-[color:var(--text-faint)]">{{ config.skills.length }}</span>
      </div>
      <p v-if="!config.skills.length" class="mt-2 text-[13px] text-[color:var(--text-faint)]">No skills — no skill instructions are injected.</p>
      <ul v-else class="mt-1">
        <li
          v-for="skill in skills"
          :key="skill.name"
          class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-t border-[color:var(--border-muted)] py-2 first:border-t-0"
        >
          <span class="font-mono text-[13px]" :class="skill.known ? 'text-foreground' : 'text-destructive'">{{ skill.name }}</span>
          <span v-if="!skill.known" class="text-[12px] text-destructive">unknown skill</span>
          <span v-else-if="skill.description" class="min-w-0 flex-1 text-[13px] text-[color:var(--text-secondary)]">{{ skill.description }}</span>
        </li>
      </ul>

      <div class="mt-6 flex items-center gap-2 text-[13px] font-medium text-[color:var(--text-secondary)]">
        <Workflow :size="15" />
        <span>Subagents</span>
        <span class="text-[12px] font-normal text-[color:var(--text-faint)]">{{ config.subagents.length }}</span>
      </div>
      <p v-if="!config.subagents.length" class="mt-2 text-[13px] text-[color:var(--text-faint)]">No subagents — no delegation targets are advertised.</p>
      <ul v-else class="mt-1">
        <li
          v-for="subagent in subagents"
          :key="subagent.name"
          class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-t border-[color:var(--border-muted)] py-2 first:border-t-0"
        >
          <span class="font-mono text-[13px]" :class="subagent.known ? 'text-foreground' : 'text-destructive'">{{ subagent.name }}</span>
          <span v-if="!subagent.known" class="text-[12px] text-destructive">unknown agent</span>
          <span v-else-if="subagent.description" class="min-w-0 flex-1 text-[13px] text-[color:var(--text-secondary)]">{{ subagent.description }}</span>
        </li>
      </ul>

      <div class="mt-6 flex items-center gap-2 text-[13px] font-medium text-[color:var(--text-secondary)]">
        <ListOrdered :size="15" />
        <span>Instruction</span>
        <span class="text-[12px] font-normal text-[color:var(--text-faint)]">{{ instructionCount }}</span>
      </div>
      <p v-if="!config.instructionSections.length && !config.customInstruction" class="mt-2 text-[13px] text-[color:var(--text-faint)]">No instruction configured.</p>
      <div v-else class="mt-1">
        <template v-for="(section, index) in sections" :key="section.name">
          <button
            type="button"
            class="flex w-full items-center gap-2.5 border-t border-[color:var(--border-muted)] px-1 py-2 text-left first:border-t-0"
            :class="section.body ? 'cursor-pointer hover:bg-[var(--surface-hover)]' : 'cursor-default'"
            @click="section.body && toggleExpanded(section.name)"
          >
            <span class="w-4 shrink-0 font-mono text-[12px] text-[color:var(--text-faint)]">{{ index + 1 }}</span>
            <span class="font-mono text-[13px]" :class="section.known ? 'text-foreground' : 'text-destructive'">{{ section.name }}</span>
            <span v-if="section.title" class="text-[13px] text-[color:var(--text-secondary)]">{{ section.title }}</span>
            <span v-if="!section.known" class="text-[12px] text-destructive">unknown section</span>
            <component
              :is="isExpanded(section.name) ? ChevronDown : ChevronRight"
              v-if="section.body"
              :size="14"
              class="ml-auto shrink-0 text-[color:var(--text-faint)]"
            />
          </button>
          <div
            v-if="section.body && isExpanded(section.name)"
            class="mb-2 ml-[26px] whitespace-pre-line rounded-lg bg-[var(--surface-hover)] px-3.5 py-2.5 text-[13px] leading-6 text-[color:var(--text-secondary)]"
          >{{ section.body }}</div>
        </template>
        <template v-if="config.customInstruction">
          <button
            type="button"
            class="flex w-full cursor-pointer items-center gap-2.5 border-t border-[color:var(--border-muted)] px-1 py-2 text-left hover:bg-[var(--surface-hover)]"
            :class="!sections.length && 'first:border-t-0'"
            @click="toggleExpanded('custom')"
          >
            <span class="w-4 shrink-0 font-mono text-[12px] text-[color:var(--text-faint)]">+</span>
            <span class="font-mono text-[13px] text-foreground">custom_instruction</span>
            <span class="rounded-full bg-[var(--surface-hover)] px-2 py-0.5 text-[12px] text-[color:var(--text-muted)]">appended last</span>
            <component
              :is="isExpanded('custom') ? ChevronDown : ChevronRight"
              :size="14"
              class="ml-auto shrink-0 text-[color:var(--text-faint)]"
            />
          </button>
          <div
            v-if="isExpanded('custom')"
            class="mb-2 ml-[26px] rounded-lg bg-[var(--surface-hover)] px-3.5 py-2.5"
          >
            <MarkdownRender
              class="markdown-body text-[13px]"
              :content="config.customInstruction"
              :is-dark="effectiveMarkdownIsDark"
              :final="true"
            />
          </div>
        </template>
      </div>
    </template>

    <HighlightCodeBlock v-else :node="{ language: 'json', code: formattedJson }" />
  </section>
</template>
