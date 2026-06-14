<script setup lang="ts">
import type { Component } from 'vue'
import { Check, Zap } from '@lucide/vue'

defineOptions({
  name: 'SlashCommandMenu',
})

export interface SlashMenuItem {
  id: string
  title: string
  /** Muted text shown after the title (e.g. the model tier). */
  subtitle?: string
  /** Right-aligned muted text, e.g. the current model on the `/model` row. */
  hint?: string
  /** Leading icon component; used by command rows. */
  icon?: Component
  /** Renders a leading checkmark — used by the model picker level. */
  selected?: boolean
  badge?: 'fast' | 'local' | null
  /** Leading provider glyph; only `gemini` is rendered today. */
  prefixKey?: string | null
}

defineProps<{
  testId: string
  items: SlashMenuItem[]
  highlightedIndex: number
}>()

const emit = defineEmits<{
  select: [index: number]
  hover: [index: number]
}>()
</script>

<template>
  <div
    class="absolute bottom-full left-0 z-30 mb-2 max-h-[320px] min-w-[280px] max-w-[calc(100vw-3rem)] overflow-y-auto rounded-xl border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] py-1 shadow-2xl shadow-[var(--shadow-color)]"
    role="menu"
    :data-testid="testId"
  >
    <button
      v-for="(item, index) in items"
      :key="item.id"
      class="slash-menu-row flex h-8 w-full items-center gap-2 px-3 text-left"
      :class="{ 'is-highlighted': index === highlightedIndex }"
      type="button"
      role="menuitem"
      :data-slash-id="item.id"
      @mousemove="emit('hover', index)"
      @click="emit('select', index)"
    >
      <span class="grid h-5 w-5 shrink-0 place-items-center">
        <Check v-if="item.selected" :size="16" stroke-width="2.3" class="text-[color:var(--accent)]" />
        <component v-else-if="item.icon" :is="item.icon" :size="15" class="text-[color:var(--text-muted)]" />
      </span>
      <svg
        v-if="item.prefixKey === 'gemini'"
        class="h-3.5 w-3.5 shrink-0 text-[color:var(--text-muted)]"
        viewBox="0 0 24 24"
        role="img"
        aria-label="Gemini"
      >
        <path
          fill="currentColor"
          d="M12 2.5c.78 5.36 4.14 8.72 9.5 9.5-5.36.78-8.72 4.14-9.5 9.5-.78-5.36-4.14-8.72-9.5-9.5 5.36-.78 8.72-4.14 9.5-9.5Z"
        />
      </svg>
      <span class="flex min-w-0 flex-1 items-baseline gap-1.5">
        <span class="truncate text-[12px] font-medium text-[color:var(--text-primary)]">{{ item.title }}</span>
        <span
          v-if="item.subtitle"
          class="truncate text-[11px] text-[color:var(--text-faint)]"
        >{{ item.subtitle }}</span>
      </span>
      <span
        v-if="item.hint"
        class="shrink-0 text-[11px] text-[color:var(--text-muted)]"
      >{{ item.hint }}</span>
      <span
        v-if="item.badge === 'fast'"
        class="inline-flex h-5 shrink-0 items-center gap-1 rounded-full border border-transparent bg-[var(--accent-soft)] px-1.5 text-[10px] font-medium text-[color:var(--accent)]"
      >
        <Zap :size="12" stroke-width="2.4" />
        Fast
      </span>
      <span
        v-else-if="item.badge === 'local'"
        class="inline-flex h-5 shrink-0 items-center rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-muted)] px-1.5 text-[10px] font-medium text-[color:var(--text-muted)]"
      >
        Local
      </span>
    </button>
  </div>
</template>

<style scoped>
.slash-menu-row {
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.slash-menu-row.is-highlighted {
  background-color: color-mix(in srgb, var(--text-primary) 7%, transparent);
}
</style>
