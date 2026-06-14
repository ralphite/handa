<script setup lang="ts">
import { ArrowLeft, ExternalLink, RefreshCw } from '@lucide/vue'
import type { BackendAutomatedTaskRun } from '../api/types'

defineOptions({ name: 'AutomatedTaskRuns' })

defineProps<{
  taskName: string
  runs: BackendAutomatedTaskRun[]
  loading?: boolean
}>()

const emit = defineEmits<{
  openSession: [sessionId: string]
  refresh: []
  back: []
}>()

function badgeClass(status: string): string {
  switch (status) {
    case 'completed':
      return 'border-success/30 bg-success-soft text-success'
    case 'failed':
    case 'error':
      return 'border-destructive/30 bg-destructive-soft text-destructive'
    case 'cancelled':
      return 'border-[color:var(--border-muted)] text-[color:var(--text-faint)]'
    default:
      return 'border-accent/30 bg-accent-soft text-accent'
  }
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}
</script>

<template>
  <div class="mx-auto flex w-full max-w-[640px] flex-col gap-4">
    <div class="flex items-center justify-between">
      <button type="button" class="back-btn" @click="emit('back')">
        <ArrowLeft :size="16" /> Back
      </button>
      <button type="button" class="back-btn" :disabled="loading" @click="emit('refresh')">
        <RefreshCw :size="14" :class="loading ? 'animate-spin' : ''" /> Refresh
      </button>
    </div>

    <h1 class="text-[20px] font-semibold tracking-normal text-[color:var(--text-primary)]">
      Run history — {{ taskName }}
    </h1>

    <p v-if="!runs.length && !loading" class="py-8 text-center text-[14px] text-[color:var(--text-faint)]">
      No runs yet. Use “Run now” on the task to trigger one.
    </p>

    <ul class="flex flex-col gap-2">
      <li
        v-for="run in runs"
        :key="run.id"
        class="flex items-center gap-3 rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] px-4 py-3"
      >
        <span class="shrink-0 rounded-full border px-2 py-0.5 text-[12px] font-medium capitalize" :class="badgeClass(run.status)">
          {{ run.status }}
        </span>
        <div class="min-w-0 flex-1">
          <p class="truncate text-[14px] text-[color:var(--text-primary)]">
            {{ run.trigger_kind }} · {{ formatTime(run.created_at) }}
          </p>
          <p v-if="run.error_message" class="truncate text-[12px] text-destructive">{{ run.error_message }}</p>
        </div>
        <button
          v-if="run.session_id"
          type="button"
          class="open-session-btn"
          @click="emit('openSession', run.session_id!)"
        >
          Open session <ExternalLink :size="13" />
        </button>
        <span v-else class="text-[12px] text-[color:var(--text-faint)]">No session</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 0.5rem;
  padding: 0.35rem 0.6rem;
  font-size: 13px;
  color: var(--text-secondary);
}
.back-btn:hover {
  background: var(--surface-hover);
}
.back-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.open-session-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  border-radius: 0.5rem;
  border: 1px solid var(--border-muted);
  padding: 0.35rem 0.6rem;
  font-size: 12px;
  color: var(--text-secondary);
}
.open-session-btn:hover {
  background: var(--surface-hover);
}
</style>
