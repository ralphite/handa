<script setup lang="ts">
import { ChevronDown, ChevronRight, CircleAlert, FileText, Globe, TerminalSquare } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import type { InvocationDetailEvent, InvocationTimelineItem } from '../types'
import { presentToolEvent, toolEventStatus } from '../presenters/toolDisplay'

defineOptions({
  name: 'ToolEventRow',
})

const props = defineProps<{
  event: InvocationDetailEvent | InvocationTimelineItem
  initialExpanded?: boolean
}>()

const expanded = ref(props.initialExpanded ?? false)

const display = computed(() => presentToolEvent(props.event))
const isToolLike = computed(() => props.event.kind === 'tool' || props.event.kind === 'tool_call' || props.event.kind === 'tool_response')
const toolStatus = computed(() => toolEventStatus(props.event))
const toolName = computed(() => {
  if ('toolName' in props.event && props.event.toolName) return props.event.toolName
  const payload = props.event.payload ?? {}
  const name = payload.name
  const call = payload.call
  const response = payload.response
  if (typeof name === 'string') return name
  if (call && typeof call === 'object' && 'name' in call && typeof call.name === 'string') return call.name
  if (response && typeof response === 'object' && 'name' in response && typeof response.name === 'string') return response.name
  return ''
})
const commandTitle = computed(() => commandFromEvent(props.event))
const isCommandRun = computed(() => toolName.value === 'commands_run' || Boolean(commandTitle.value))

const rowIcon = computed(() => {
  if (props.event.kind === 'error') return CircleAlert
  if (props.event.kind === 'artifact_delta' || props.event.kind === 'artifact') return FileText
  if (toolName.value.startsWith('browser_')) return Globe
  if (toolName.value.startsWith('files_') || toolName.value.startsWith('artifacts_') || toolName.value.startsWith('skills_')) return FileText
  return TerminalSquare
})

const rowTone = computed(() => {
  if (props.event.kind === 'error' || toolStatus.value === 'failed') return 'text-destructive'
  return ''
})

const title = computed(() => props.event.summary)
const rowTitle = computed(() => {
  const summary = title.value.trim()
  const displayTitle = display.value.title.trim()
  const base = displayTitle || summary || 'Tool'

  if (!isToolLike.value) return summary || displayTitle
  if (isCommandRun.value) {
    const command = commandTitleBase(commandTitle.value || base)
    if (toolStatus.value === 'running') return `Running ${command}`
    return `Ran ${command}`
  }
  if (hasActionVerb(displayTitle)) return displayTitle
  if (hasActionVerb(summary)) return summary
  if (toolStatus.value === 'running') return `Running ${base}`
  if (toolStatus.value === 'failed') return `Failed ${summary || base}`
  return base
})

const meta = computed(() => {
  if (isToolLike.value) {
    if (display.value.meta) return display.value.meta
    if (isCommandRun.value && toolStatus.value === 'done') return ''
    const response = toolResponseSummary(props.event)
    if (response && response !== title.value && !title.value.includes(response)) return response
    if (toolStatus.value === 'done') return ''
    if (toolStatus.value === 'failed') return response || 'Failed'
    return ''
  }
  return ''
})

function toolResponseSummary(event: InvocationDetailEvent | InvocationTimelineItem) {
  return 'responseSummary' in event ? event.responseSummary : ''
}

watch(
  () => props.initialExpanded,
  (initialExpanded) => {
    expanded.value = initialExpanded ?? false
  },
)

function hasActionVerb(value: string) {
  return /^(Called|Captured|Checked|Clicked|Closed|Edited|Explored|Failed|Finished|Listed|Opened|Pressed|Ran|Read|Running|Saved|Scrolled|Searched|Started|Typed|Used|Waited|Wrote)\b/.test(value)
}

function commandTitleBase(value: string) {
  return value.replace(/^(Failed|Ran|Running)\s+/, '')
}

function commandFromEvent(event: InvocationDetailEvent | InvocationTimelineItem) {
  const payload = recordValue(event.payload)
  const call = recordValue(payload?.call)
  const args = recordValue(call?.args) ?? recordValue(payload?.args)
  const responseEnvelope = recordValue(payload?.response)
  const response = recordValue(responseEnvelope?.response) ?? responseEnvelope
  return stringValue(args?.command) || stringValue(response?.command) || ''
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  return value as Record<string, unknown>
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}
</script>

<template>
  <div class="tool-row" :class="{ 'tool-row--expanded': expanded, 'tool-row--failed': rowTone === 'text-destructive' }">
    <button
      class="tool-row__summary"
      type="button"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <component :is="rowIcon" :size="15" class="tool-row__icon" :class="rowTone" />
      <span class="tool-row__title">{{ rowTitle }}</span>
      <span v-if="meta" class="tool-row__meta">{{ meta }}</span>
      <ChevronDown v-if="expanded" :size="15" class="tool-row__chevron" />
      <ChevronRight v-else :size="15" class="tool-row__chevron" />
    </button>
    <div
      v-if="expanded"
      class="tool-row__details"
    >
      <template v-for="(block, index) in display.blocks" :key="index">
        <p v-if="block.type === 'text'" class="tool-row__detail-line">{{ block.content }}</p>
        <pre
          v-else-if="block.type === 'code'"
          class="tool-row__code"
        >{{ block.content }}</pre>
        <pre
          v-else-if="block.type === 'error'"
          class="tool-row__code tool-row__code--error"
        >{{ block.content }}</pre>
        <div v-else-if="block.type === 'shell'" class="tool-row__shell">
          <p class="tool-row__shell-label">Shell</p>
          <pre class="tool-row__shell-output"><span class="tool-row__shell-command">$ {{ block.command }}</span><template v-if="block.stdout">{{ '\n' }}{{ block.stdout }}</template><template v-if="block.stderr">{{ '\n' }}<span class="tool-row__shell-stderr">{{ block.stderr }}</span></template></pre>
        </div>
        <ul v-else-if="block.type === 'list'" class="tool-row__list">
          <li
            v-for="item in block.items"
            :key="item"
            class="tool-row__list-item"
            v-tooltip="{ content: item, overflowOnly: true }"
          >
            {{ item }}
          </li>
        </ul>
        <dl v-else-if="block.type === 'kv'" class="tool-row__kv">
          <template v-for="item in block.items" :key="item.label">
            <dt>{{ item.label }}</dt>
            <dd v-tooltip="{ content: item.value, overflowOnly: true }">
              <code>{{ item.value }}</code>
            </dd>
          </template>
        </dl>
      </template>
    </div>
  </div>
</template>

<style scoped>
.tool-row {
  --tool-row-subtle: var(--text-faint);
  --tool-row-detail: var(--text-primary);

  max-width: 100%;
}

@supports (color: color-mix(in srgb, white, black)) {
  .tool-row {
    --tool-row-subtle: color-mix(in srgb, var(--text-faint) 52%, var(--background));
  }
}

.tool-row__summary {
  display: flex;
  min-height: 30px;
  width: 100%;
  align-items: center;
  gap: 9px;
  border-radius: 5px;
  padding: 2px 0;
  color: var(--tool-row-subtle);
  text-align: left;
  transition: color 140ms ease;
}

.tool-row__summary:hover,
.tool-row__summary:focus-visible {
  color: var(--text-primary);
}

.tool-row--expanded .tool-row__summary {
  color: var(--text-primary);
}

.tool-row__icon {
  flex: 0 0 auto;
  color: var(--tool-row-subtle);
  transition: color 140ms ease;
}

.tool-row__summary:hover .tool-row__icon,
.tool-row__summary:focus-visible .tool-row__icon,
.tool-row--expanded .tool-row__icon {
  color: var(--text-primary);
}

.tool-row--failed .tool-row__icon {
  color: var(--destructive);
}

.tool-row__title {
  min-width: 0;
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 400;
  line-height: 1.45;
}

.tool-row__meta {
  max-width: 36%;
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  line-height: 1.45;
  color: var(--tool-row-subtle);
}

.tool-row__chevron {
  flex: 0 0 auto;
  color: var(--tool-row-subtle);
  opacity: 0;
  transition: color 140ms ease, opacity 140ms ease;
}

.tool-row__summary:hover .tool-row__chevron,
.tool-row__summary:focus-visible .tool-row__chevron,
.tool-row--expanded .tool-row__chevron {
  opacity: 1;
}

.tool-row__summary:hover .tool-row__meta,
.tool-row__summary:hover .tool-row__chevron,
.tool-row__summary:focus-visible .tool-row__meta,
.tool-row__summary:focus-visible .tool-row__chevron {
  color: var(--text-primary);
}

.tool-row--expanded .tool-row__meta,
.tool-row--expanded .tool-row__chevron {
  color: var(--text-primary);
}

.tool-row__details {
  margin-top: 1px;
  padding: 0 0 5px 24px;
  color: var(--tool-row-detail);
  font-size: 12.5px;
  line-height: 1.65;
}

.tool-row__detail-line {
  margin: 0;
  white-space: pre-wrap;
}

.tool-row__list {
  max-height: 320px;
  margin: 0;
  padding: 0;
  overflow: auto;
  list-style: none;
}

.tool-row__list-item {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-row__kv {
  display: grid;
  grid-template-columns: minmax(78px, max-content) minmax(0, 1fr);
  gap: 2px 12px;
  margin: 0;
}

.tool-row__kv dt {
  color: var(--tool-row-detail);
}

.tool-row__kv dd {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--tool-row-detail);
}

.tool-row__kv code {
  border-radius: 5px;
  background: var(--surface-muted);
  padding: 1px 5px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--tool-row-detail);
}

.tool-row__code {
  max-height: 320px;
  margin: 4px 0 0;
  overflow: auto;
  border-radius: 6px;
  background: var(--surface-muted);
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
  color: var(--tool-row-detail);
}

.tool-row__code--error {
  background: var(--destructive-soft);
  color: var(--destructive);
}

.tool-row__shell {
  max-height: 320px;
  margin: 4px 0 0;
  overflow: auto;
  border-radius: 6px;
  background: var(--surface-muted);
  padding: 8px 10px;
}

.tool-row__shell-label {
  margin: 0 0 8px;
  color: var(--tool-row-subtle);
  font-size: 12px;
  line-height: 1.35;
}

.tool-row__shell-output {
  margin: 0;
  white-space: pre;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
  color: var(--tool-row-detail);
}

.tool-row__shell-command {
  color: var(--text-primary);
}

.tool-row__shell-stderr {
  color: var(--destructive);
}

</style>
