<script setup lang="ts">
import { ArchiveRestore, Folder, Trash2 } from '@lucide/vue'
import { computed, ref } from 'vue'
import type { ProjectNavItem, SessionNavSummary } from '../types'

defineOptions({
  name: 'SettingsArchivedChats',
})

const props = defineProps<{
  projects: ProjectNavItem[]
  sessionCount: number
  loading?: boolean
  error?: string
}>()

const emit = defineEmits<{
  unarchiveSession: [id: string]
  deleteSession: [id: string]
}>()

const deleteDialogSession = ref<SessionNavSummary | null>(null)

const hasArchivedSessions = computed(() => props.sessionCount > 0)

function formatSessionAge(value: string) {
  const timestamp = Date.parse(value)
  if (Number.isNaN(timestamp)) return 'now'

  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000))
  if (seconds < 60) return 'now'

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d`

  const weeks = Math.floor(days / 7)
  if (weeks < 5) return `${weeks}w`

  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo`

  return `${Math.floor(days / 365)}y`
}

function sessionAgeSource(session: SessionNavSummary) {
  return session.lastActivityAt ?? session.createdAt
}

function statusLabel(session: SessionNavSummary) {
  if (session.status === 'done') return 'Done'
  if (session.status === 'failed') return 'Failed'
  if (session.status === 'cancelled') return 'Cancelled'
  if (session.status === 'running') return 'Running'
  if (session.status === 'queued') return 'Queued'
  return 'Idle'
}

function askDelete(session: SessionNavSummary) {
  deleteDialogSession.value = session
}

function cancelDelete() {
  deleteDialogSession.value = null
}

function confirmDelete() {
  const session = deleteDialogSession.value
  deleteDialogSession.value = null
  if (session) emit('deleteSession', session.id)
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between text-[13px] text-[color:var(--text-muted)]">
      <span>{{ sessionCount }} sessions</span>
    </div>

    <p v-if="error" class="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-[13px] text-destructive">
      {{ error }}
    </p>

    <div v-if="loading" class="text-[14px] text-[color:var(--text-muted)]">Loading...</div>

    <div v-else-if="!hasArchivedSessions" class="rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] px-4 py-5">
      <p class="text-[14px] text-[color:var(--text-muted)]">No archived sessions</p>
    </div>

    <div v-else class="space-y-8">
      <section v-for="project in projects" :key="project.id" class="space-y-3">
        <div class="flex min-w-0 items-center gap-2">
          <Folder class="shrink-0 text-[color:var(--text-muted)]" :size="16" />
          <h2
            class="min-w-0 truncate text-[15px] font-medium text-[color:var(--text-primary)]"
            v-tooltip="{ content: project.name, overflowOnly: true }"
          >{{ project.name }}</h2>
        </div>

        <div class="overflow-hidden rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)]">
          <div
            v-for="session in project.sessions"
            :key="session.id"
            class="flex min-h-12 items-center gap-3 border-b border-[color:var(--border-muted)] px-4 py-2.5 last:border-b-0"
            :data-archived-session-id="session.id"
          >
            <div class="min-w-0 flex-1">
              <p
                class="truncate text-[14px] font-medium text-[color:var(--text-primary)]"
                v-tooltip="{ content: session.title, overflowOnly: true }"
              >{{ session.title }}</p>
              <p
                class="mt-0.5 truncate text-[12px] text-[color:var(--text-muted)]"
                v-tooltip="{ content: `${statusLabel(session)} · ${formatSessionAge(sessionAgeSource(session))}`, overflowOnly: true }"
              >
                {{ statusLabel(session) }} · {{ formatSessionAge(sessionAgeSource(session)) }}
              </p>
            </div>

            <button
              class="icon-button h-8 w-8"
              type="button"
              v-tooltip="'Unarchive'"
              aria-label="Unarchive"
              data-archived-action="unarchive"
              @click="emit('unarchiveSession', session.id)"
            >
              <ArchiveRestore :size="16" />
            </button>
            <button
              class="icon-button h-8 w-8 text-destructive hover:bg-destructive-soft hover:text-destructive"
              type="button"
              aria-label="Delete"
              data-archived-action="delete"
              @click="askDelete(session)"
            >
              <Trash2 :size="16" />
            </button>
          </div>
        </div>
      </section>
    </div>

    <!-- Move modal out of relative positioning context if needed, but it's fixed anyway -->
    <div
      v-if="deleteDialogSession"
      class="fixed inset-0 z-[80] grid place-items-center bg-[var(--overlay)] px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="archive-delete-session-title"
      @click.self="cancelDelete"
    >
      <div class="w-full max-w-[380px] rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] p-4 shadow-2xl">
        <h2 id="archive-delete-session-title" class="text-[15px] font-semibold text-[color:var(--text-primary)]">Delete session?</h2>
        <p class="mt-2 text-[13px] leading-5 text-[color:var(--text-muted)]">
          This removes "{{ deleteDialogSession.title }}" from Handa. The delete is treated as permanent in the UI.
        </p>
        <div class="mt-5 flex justify-end gap-2">
          <button class="quiet-button" type="button" @click="cancelDelete">Cancel</button>
          <button
            class="inline-flex h-8 items-center justify-center rounded-lg bg-destructive px-3 text-[13px] font-medium text-destructive-foreground transition hover:opacity-90"
            type="button"
            @click="confirmDelete"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
