<script setup lang="ts">
import { computed } from 'vue'
import { MarkdownRender } from 'markstream-vue'
import ToolEventRow from './ToolEventRow.vue'
import { LIVE_MARKDOWN_RENDER_PROPS, STATIC_MARKDOWN_RENDER_PROPS } from '../markdownStreamProps'
import type { InvocationDetailEvent, InvocationTimelineItem } from '../types'

defineOptions({
  name: 'AgentTimeline',
})

const props = defineProps<{
  timelineItems: (InvocationDetailEvent | InvocationTimelineItem)[]
  isLive: boolean
  markdownIsDark?: boolean
}>()

const markdownRenderProps = computed(() =>
  props.isLive ? LIVE_MARKDOWN_RENDER_PROPS : STATIC_MARKDOWN_RENDER_PROPS,
)

function itemText(event: InvocationDetailEvent | InvocationTimelineItem) {
  return 'text' in event ? event.text ?? '' : ''
}

function timelineItemKey(event: InvocationDetailEvent | InvocationTimelineItem, index: number) {
  if (event.kind === 'process_text') return `process_text:${index}:${event.createdAt}`
  const toolCallId = 'toolCallId' in event ? event.toolCallId ?? '' : ''
  return `${event.kind}:${event.seq}:${toolCallId}`
}
</script>

<template>
  <div
    class="timeline space-y-1.5"
    :class="isLive ? 'order-1' : 'order-3 mt-3 border-l-2 border-[color:var(--border-muted)] pl-4'"
  >
    <template v-for="(event, index) in timelineItems" :key="timelineItemKey(event, index)">
      <div
        v-if="event.kind === 'process_text'"
        class="process-block py-1 text-[13px] leading-6 text-[color:var(--text-muted)]"
      >
        <MarkdownRender
          class="markdown-body process-text"
          v-bind="markdownRenderProps"
          :content="itemText(event)"
          :final="!isLive"
          :is-dark="markdownIsDark"
        />
      </div>
      <ToolEventRow v-else :event="event" />
    </template>
  </div>
</template>

<style scoped>
.process-text :deep(p) {
  margin: 0;
}

.process-text :deep(*) {
  font-size: 13px;
  line-height: 1.6;
}

.process-text :deep(code) {
  border-radius: 5px;
  padding: 0.08rem 0.32rem;
}
</style>
