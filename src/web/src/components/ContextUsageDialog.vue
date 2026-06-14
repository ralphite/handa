<script setup lang="ts">
import { Info, X } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import type { ContextUsageSummary } from '../types'

defineOptions({
  name: 'ContextUsageDialog',
})

const props = defineProps<{
  open: boolean
  usage: ContextUsageSummary
}>()

const emit = defineEmits<{
  close: []
}>()

interface ContextRow {
  id: string
  label: string
  value: string
  tokenCount: number
  depth: number
  includeInChart: boolean
  emphasized: boolean
  percent?: number
  color: string
  info: string
}

type SourceId = NonNullable<ContextUsageSummary['breakdown']>[number]['id']

const SOURCE_ROW_DEFS: Array<{
  id: string
  label: string
  sourceId?: SourceId
  parentSourceId?: SourceId
  color: string
  depth?: number
  includeInChart?: boolean
  emphasized?: boolean
  info: string
}> = [
  {
    id: 'instruction',
    label: 'Instruction',
    sourceId: 'instruction',
    color: '#7c3aed',
    emphasized: true,
    info: 'Sum of the System and Project rows below. Static prompt text estimated from characters (~4 per token, CJK ~1.8); kept at its raw estimate while dynamic sources are scaled.',
  },
  {
    id: 'instruction-system',
    label: 'System',
    sourceId: 'system_instruction',
    parentSourceId: 'instruction',
    color: '#0891b2',
    depth: 1,
    includeInChart: false,
    info: "The agent's rendered system prompt (instruction sections + custom instruction), estimated at ~4 characters per token.",
  },
  {
    id: 'instruction-project',
    label: 'Project',
    sourceId: 'project_config',
    parentSourceId: 'instruction',
    color: '#16a34a',
    depth: 1,
    includeInChart: false,
    info: 'Project instruction files (AGENTS.md) rendered for the project root, estimated at ~4 characters per token.',
  },
  {
    id: 'instruction-user',
    label: 'User',
    color: '#db2777',
    depth: 1,
    includeInChart: false,
    info: 'User-level instructions. Not tracked separately for this runtime.',
  },
  {
    id: 'system-tools',
    label: 'System tools',
    sourceId: 'system_tools',
    color: '#f59e0b',
    info: 'Tool definitions (names, descriptions, parameter schemas) sent with every request, estimated at ~4 characters per token from the exported definition text.',
  },
  {
    id: 'mcp-tools',
    label: 'MCP tools',
    color: '#0d9488',
    info: 'MCP tool definitions. Not tracked separately for this runtime.',
  },
  {
    id: 'user-messages',
    label: 'User Messages',
    sourceId: 'user_messages',
    color: '#2563eb',
    info: 'All user inputs in this session, estimated at ~4 characters per token, then scaled together with the other estimated sources so all rows sum to the measured context size.',
  },
  {
    id: 'tool-call-responses',
    label: 'Tool Call Responses',
    sourceId: 'tool_call_responses',
    color: '#14b8a6',
    info: 'Tool results replayed to the model as JSON, estimated at ~3.5 characters per token (JSON tokenizes denser than prose), then scaled with the other estimated sources to fit the measured context size.',
  },
  {
    id: 'llm-responses',
    label: 'LLM Responses',
    sourceId: 'llm_responses',
    color: '#dc2626',
    emphasized: true,
    info: 'Model output replayed into later prompts — sum of Thought, Text and Tool Call Request below.',
  },
  {
    id: 'llm-response-thought',
    label: 'Thought',
    sourceId: 'llm_response_thought',
    parentSourceId: 'llm_responses',
    color: '#9333ea',
    depth: 1,
    includeInChart: false,
    info: "Exact thinking-token count from API usage metadata — not estimated and never rescaled. Thought signatures replay thinking into every later prompt, so all turns count except the final response's thoughts (not in any prompt yet).",
  },
  {
    id: 'llm-response-text',
    label: 'Text',
    sourceId: 'llm_response_text',
    parentSourceId: 'llm_responses',
    color: '#059669',
    depth: 1,
    includeInChart: false,
    info: 'Model text replies, estimated at ~4 characters per token, then scaled with the other estimated sources.',
  },
  {
    id: 'llm-response-tool-call-request',
    label: 'Tool Call Request',
    sourceId: 'llm_response_tool_call_request',
    parentSourceId: 'llm_responses',
    color: '#f97316',
    depth: 1,
    includeInChart: false,
    info: 'Tool calls emitted by the model (tool name + JSON arguments), estimated at ~3.5 characters per token, then scaled with the other estimated sources.',
  },
  {
    id: 'skills',
    label: 'Skills',
    sourceId: 'skills',
    color: '#6366f1',
    info: 'Skill instructions rendered into the prompt, estimated at ~4 characters per token; kept at the raw estimate while dynamic sources are scaled.',
  },
  {
    id: 'memory-files',
    label: 'Memory files',
    color: '#65a30d',
    info: 'Memory file contents. Not tracked separately for this runtime.',
  },
] as const

const CONTEXT_TOTAL_INFO =
  'Prompt token count of the latest LLM request, as reported by the model API. The final response’s own output joins the context on the next request.'

const dialogEl = ref<HTMLElement | null>(null)

const clampedPercent = computed(() => Math.min(100, Math.max(0, Math.round(props.usage.contextPercent))))
const windowTitle = computed(() =>
  `${props.usage.contextTokens} / ${props.usage.contextLimit} (${clampedPercent.value}%)`,
)

const sourceRows = computed<ContextRow[]>(() =>
  SOURCE_ROW_DEFS.map((definition) => {
    const source = findSource(definition.sourceId, definition.parentSourceId)
    const tokenCount = source?.tokenCount ?? 0
    const percent = source?.percent
    const hasSource = source !== undefined
    return {
      id: definition.id,
      label: definition.label,
      value: hasSource ? (source?.tokenText ?? formatTokenLimit(tokenCount)) : '-',
      tokenCount,
      depth: definition.depth ?? 0,
      includeInChart: definition.includeInChart ?? true,
      emphasized: definition.emphasized ?? false,
      percent: hasSource ? percent : undefined,
      color: definition.color,
      info: definition.info,
    }
  }),
)

const chartSegments = computed(() =>
  sourceRows.value.filter((row) => row.includeInChart && row.tokenCount > 0 && row.percent !== undefined && row.color),
)

const sessionCostRows = computed(() => [
  {
    id: 'tokens-used',
    label: 'Tokens Used',
    value: props.usage.totalTokens ?? '0',
    info: 'Sum of totalTokenCount over every LLM request in this session (prompt + output + thinking each time, cached prompt tokens included) — the cumulative billing-style count, not the live context size.',
  },
  {
    id: 'tool-calls',
    label: 'Tool Calls',
    value: String(props.usage.toolCalls ?? 0),
    info: 'Number of tool calls the agent made across this session.',
  },
  {
    id: 'agent-time',
    label: 'Agent Time',
    value: props.usage.agentTime ?? '0s',
    info: 'Total time the agent spent actively working across all turns.',
  },
])

watch(
  () => props.open,
  (open) => {
    if (!open) return
    void nextTick(() => dialogEl.value?.focus())
  },
)

function close() {
  emit('close')
}

function formatTokenLimit(count: number) {
  if (count >= 1_000_000) return `${Math.round(count / 1_000_000)}M`
  if (count >= 1_000) return `${Math.round(count / 1_000)}K`
  return String(count)
}

function formatPercent(value?: number) {
  const percent = Math.max(0, Number(value) || 0)
  return `${percent >= 10 ? Math.round(percent) : Math.round(percent * 10) / 10}%`
}

function findSource(sourceId?: SourceId, parentSourceId?: SourceId) {
  if (!sourceId) return undefined
  if (!parentSourceId) {
    return props.usage.breakdown?.find((row) => row.id === sourceId)
  }
  return props.usage.breakdown
    ?.find((row) => row.id === parentSourceId)
    ?.children?.find((row) => row.id === sourceId)
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[70] flex items-start justify-center bg-[var(--overlay)] px-3 pt-[8vh]"
    role="dialog"
    aria-modal="true"
    aria-labelledby="context-usage-dialog-title"
    data-testid="context-usage-dialog"
    @click.self="close"
  >
    <div
      ref="dialogEl"
      class="relative max-h-[80vh] w-full max-w-[760px] overflow-y-auto rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] px-8 pb-8 pt-7 text-[color:var(--text-primary)] shadow-2xl outline-none"
      tabindex="-1"
      @click.stop
      @keydown.esc.prevent="close"
    >
      <button
        class="icon-button absolute right-3 top-3 h-8 w-8"
        type="button"
        aria-label="Close context window"
        data-testid="context-usage-close"
        @click="close"
      >
        <X :size="17" />
      </button>

      <div class="mx-auto w-full max-w-[620px]">
        <div class="mb-6">
          <h1 id="context-usage-dialog-title" class="text-[20px] font-semibold tracking-normal text-[color:var(--text-primary)]">
            Context Window
          </h1>
          <p class="mt-1 flex items-center gap-1.5 text-[14px] text-[color:var(--text-muted)]">
            <span>
              {{ windowTitle }}
              <span v-if="usage.modelName"> · {{ usage.modelName }}</span>
            </span>
            <span
              v-tooltip="{ content: CONTEXT_TOTAL_INFO, delay: 150 }"
              class="inline-flex shrink-0 cursor-help text-[color:var(--text-faint)] hover:text-[color:var(--text-secondary)]"
              tabindex="0"
              role="img"
              aria-label="How the context size is measured"
              data-testid="context-usage-total-info"
            >
              <Info :size="13" />
            </span>
          </p>
        </div>

        <section v-if="sourceRows.length" class="mt-5">
          <h2 class="mb-2 text-[12px] font-medium uppercase tracking-wide text-[color:var(--text-faint)]">Source breakdown</h2>
          <div class="overflow-hidden rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)]">
            <div class="border-b border-[color:var(--border-muted)] px-4 py-3">
              <div class="flex h-2 overflow-hidden rounded-full bg-[var(--surface-muted)]">
                <div
                  v-for="segment in chartSegments"
                  :key="segment.id"
                  class="h-full"
                  :title="`${segment.label}: ${formatPercent(segment.percent)}`"
                  :style="{ width: `${Math.max(0, segment.percent ?? 0)}%`, backgroundColor: segment.color }"
                ></div>
              </div>
            </div>
            <div
              v-for="row in sourceRows"
              :key="row.id"
              class="flex items-center justify-between gap-3 border-b border-[color:var(--border-muted)] px-4 py-2.5 last:border-b-0"
              :style="{ paddingLeft: row.depth ? '2rem' : '1rem' }"
            >
              <span class="flex min-w-0 items-center gap-2">
                <span
                  class="shrink-0 rounded-full bg-[var(--surface-muted)]"
                  :style="{
                    width: row.depth ? '0.5rem' : '0.625rem',
                    height: row.depth ? '0.5rem' : '0.625rem',
                    backgroundColor: row.color,
                  }"
                ></span>
                <span
                  class="min-w-0 break-words text-[13px] leading-5"
                  :class="row.emphasized ? 'font-medium text-[color:var(--text-primary)]' : 'text-[color:var(--text-secondary)]'"
                >
                  {{ row.label }}
                </span>
                <span
                  v-tooltip="{ content: row.info, delay: 150 }"
                  class="inline-flex shrink-0 cursor-help text-[color:var(--text-faint)] hover:text-[color:var(--text-secondary)]"
                  tabindex="0"
                  role="img"
                  :aria-label="`How ${row.label} is calculated`"
                  :data-testid="`context-usage-info-${row.id}`"
                >
                  <Info :size="12" />
                </span>
              </span>
              <span class="flex shrink-0 items-baseline gap-3 text-right">
                <span class="w-16 text-[13px] font-semibold text-[color:var(--text-primary)]">{{ row.value }}</span>
                <span v-if="row.percent !== undefined" class="w-12 text-[12px] text-[color:var(--text-muted)]">{{ formatPercent(row.percent) }}</span>
              </span>
            </div>
          </div>
        </section>

        <section class="mt-6">
          <h2 class="mb-2 text-[12px] font-medium uppercase tracking-wide text-[color:var(--text-faint)]">Session Cost</h2>
          <div class="grid grid-cols-3 gap-4 rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] px-4 py-4 max-sm:grid-cols-1">
            <div v-for="item in sessionCostRows" :key="item.id" class="min-w-0">
              <p class="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide text-[color:var(--text-muted)]">
                <span class="truncate">{{ item.label }}</span>
                <span
                  v-tooltip="{ content: item.info, delay: 150 }"
                  class="inline-flex shrink-0 cursor-help text-[color:var(--text-faint)] hover:text-[color:var(--text-secondary)]"
                  tabindex="0"
                  role="img"
                  :aria-label="`How ${item.label} is calculated`"
                  :data-testid="`context-usage-info-${item.id}`"
                >
                  <Info :size="12" />
                </span>
              </p>
              <p
                class="mt-1 truncate text-[22px] font-semibold leading-7 text-[color:var(--text-primary)]"
                v-tooltip="{ content: item.value, overflowOnly: true }"
              >
                {{ item.value }}
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
