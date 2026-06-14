<script setup lang="ts">
import { computed } from 'vue'

defineOptions({
  name: 'ContextUsageRing',
})

const props = defineProps<{
  percent: number
  usedTokens: string
  limitTokens: string
}>()

defineEmits<{
  open: []
}>()

const RADIUS = 7
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

const clampedPercent = computed(() => Math.min(100, Math.max(0, Math.round(props.percent))))
const dashOffset = computed(() => CIRCUMFERENCE * (1 - clampedPercent.value / 100))

const ringColor = computed(() => {
  if (clampedPercent.value >= 90) return 'var(--destructive)'
  if (clampedPercent.value >= 75) return 'var(--warning)'
  return 'var(--accent)'
})

const tooltip = computed(
  () => `Context: ${props.usedTokens} / ${props.limitTokens} · ${clampedPercent.value}% full`,
)

const ariaLabel = computed(
  () => `Context window ${clampedPercent.value}% full, ${props.usedTokens} of ${props.limitTokens} tokens used`,
)
</script>

<template>
  <button
    class="grid h-8 w-8 shrink-0 place-items-center rounded-md text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--accent)]"
    type="button"
    :aria-label="ariaLabel"
    data-testid="context-usage-ring"
    v-tooltip="tooltip"
    @click="$emit('open')"
  >
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" :r="RADIUS" stroke="var(--border-subtle)" :stroke-width="2.5" />
      <circle
        cx="10"
        cy="10"
        :r="RADIUS"
        :stroke="ringColor"
        :stroke-width="2.5"
        stroke-linecap="round"
        :stroke-dasharray="CIRCUMFERENCE"
        :stroke-dashoffset="dashOffset"
        transform="rotate(-90 10 10)"
        class="transition-[stroke-dashoffset] duration-300 ease-out"
      />
    </svg>
  </button>
</template>
