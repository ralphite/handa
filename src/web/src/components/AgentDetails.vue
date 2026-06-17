<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, LoaderCircle } from '@lucide/vue'
import AgentTimeline from './AgentTimeline.vue'
import type { AgentSessionStatus, InvocationDetailEvent, InvocationTimelineItem, InvocationTokenUsage } from '../types'

defineOptions({
  name: 'AgentDetails',
})

const props = defineProps<{
  elapsed: string
  status: AgentSessionStatus
  tokenUsage?: InvocationTokenUsage
  events?: InvocationDetailEvent[]
  timelineItems?: InvocationTimelineItem[]
  markdownIsDark?: boolean
  runDividerLabel?: string
  // When false, the live "Working for…" footer is omitted so the host can
  // render it below the message body/form instead. Done-state header always shows.
  showLiveSummary?: boolean
}>()

const showSummary = computed(() => !isLive.value || props.showLiveSummary !== false)
const runDividerLabel = computed(() => (props.runDividerLabel ?? '').trim())
const hasRunDividerLabel = computed(() => Boolean(runDividerLabel.value))
// Only labeled dividers (e.g. background/task-notification runs) get a divider.
// User-message-triggered turns have no label, so they render no divider line.
const showRunDivider = computed(() => !isLive.value && showSummary.value && hasRunDividerLabel.value)

const summaryPrefix = computed(() => {
  if (props.status === 'cancelled') {
    return `Terminated after ${props.elapsed}`
  }
  const label = props.status === 'queued'
    ? 'Queued'
    : props.status === 'running'
      ? 'Working'
      : 'Worked'
  return `${label} for ${props.elapsed}`
})

const timelineItems = computed<(InvocationDetailEvent | InvocationTimelineItem)[]>(() => props.timelineItems ?? props.events ?? [])

const isLive = computed(() => isLiveStatus(props.status))
const hasTimelineItems = computed(() => timelineItems.value.length > 0)
const isExpandable = computed(() => !isLive.value && hasTimelineItems.value)

const detailsOpen = ref(false)

function isLiveStatus(status: AgentSessionStatus) {
  return status === 'running' || status === 'queued'
}

function toggleOpen() {
  if (!isExpandable.value) return
  detailsOpen.value = !detailsOpen.value
}

watch(
  () => props.status,
  (status) => {
    detailsOpen.value = isLiveStatus(status)
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex flex-col">
    <div
      v-if="showRunDivider"
      class="run-summary-divider run-summary-divider--labeled"
      data-testid="run-summary-divider"
    >
      <span class="run-summary-divider__label">{{ runDividerLabel }}</span>
    </div>
    <component
      v-if="showSummary"
      :is="isExpandable ? 'button' : 'div'"
      :type="isExpandable ? 'button' : undefined"
      class="agent-summary-row select-none text-[13px] font-medium text-[color:var(--text-muted)] outline-none"
      :class="[
        isLive
          ? ['order-2 inline-flex items-center gap-1.5', hasTimelineItems ? 'mt-3' : '']
          : [
              'order-1 flex w-full items-center gap-[9px] text-left',
              'pb-0.5',
              isExpandable
                ? 'cursor-pointer focus-visible:text-[color:var(--text-secondary)]'
                : '',
            ],
      ]"
      @click="toggleOpen"
    >
      <LoaderCircle v-if="isLive" aria-hidden="true" :size="14" class="animate-spin shrink-0" />
      <span class="elapsed-summary" :class="{ 'elapsed-summary--stable': isLive }">{{ summaryPrefix }}</span>
      <ChevronDown
        v-if="isExpandable && detailsOpen"
        aria-hidden="true"
        :size="15"
        class="elapsed-summary__chevron"
      />
      <ChevronRight
        v-else-if="isExpandable"
        aria-hidden="true"
        :size="15"
        class="elapsed-summary__chevron"
      />
    </component>
    <AgentTimeline
      v-if="hasTimelineItems && (isLive || detailsOpen)"
      :timeline-items="timelineItems"
      :is-live="isLive"
      :markdown-is-dark="markdownIsDark"
    />
  </div>
</template>

<style scoped>
.elapsed-summary {
  display: inline-block;
  font-variant-numeric: tabular-nums;
}

.agent-summary-row {
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
}

.elapsed-summary--stable {
  min-width: 13ch;
}

.elapsed-summary__chevron {
  flex: 0 0 auto;
}

.run-summary-divider {
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  margin-bottom: 6px;
}

.run-summary-divider--labeled {
  align-items: center;
  display: flex;
  gap: 8px;
  margin-bottom: -2px;
}

.run-summary-divider--labeled::before,
.run-summary-divider--labeled::after {
  border-top: 1px solid var(--border-muted);
  content: "";
  flex: 1 1 0;
  min-width: 24px;
}

.run-summary-divider__label {
  min-width: 0;
  max-width: 70%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
