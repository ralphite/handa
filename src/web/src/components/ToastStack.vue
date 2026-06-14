<script setup lang="ts">
import { CircleAlert, X } from '@lucide/vue'

defineOptions({
  name: 'ToastStack',
})

interface ToastItem {
  id: string
  message: string
}

defineProps<{
  toasts: ToastItem[]
}>()

const emit = defineEmits<{
  dismiss: [id: string]
}>()
</script>

<template>
  <div
    v-if="toasts.length"
    class="pointer-events-none fixed right-4 top-4 z-[90] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2"
    aria-live="polite"
  >
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="pointer-events-auto flex items-start gap-2 rounded-lg border border-destructive/25 bg-[var(--surface)] px-3 py-2.5 text-[13px] text-[color:var(--text-primary)] shadow-2xl shadow-[var(--shadow-color)]"
      role="alert"
    >
      <CircleAlert :size="16" class="mt-0.5 shrink-0 text-destructive" />
      <p class="min-w-0 flex-1 break-words leading-5">{{ toast.message }}</p>
      <button
        class="grid h-6 w-6 shrink-0 place-items-center rounded-md text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)]"
        type="button"
        aria-label="Dismiss notification"
        @click="emit('dismiss', toast.id)"
      >
        <X :size="14" />
      </button>
    </div>
  </div>
</template>
