<script setup lang="ts">
import { LoaderCircle, Search, Star, X } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import type { ProjectNavItem, SessionNavSummary } from '../types'

interface ChatSearchResult {
  projectId: string
  projectName: string
  projectPath: string
  session: SessionNavSummary
}

const props = defineProps<{
  open: boolean
  projects: ProjectNavItem[]
  activeSessionId: string
  loading?: boolean
}>()

const emit = defineEmits<{
  close: []
  selectSession: [id: string]
}>()

const query = ref('')
const activeIndex = ref(0)
const searchInput = ref<HTMLInputElement | null>(null)

const allResults = computed<ChatSearchResult[]>(() =>
  props.projects.flatMap((project) =>
    project.sessions.map((session) => ({
      projectId: project.id,
      projectName: project.name,
      projectPath: project.path,
      session,
    })),
  ),
)

const normalizedQuery = computed(() => normalizeSearchText(query.value))
const queryTerms = computed(() => normalizedQuery.value.split(/\s+/).filter(Boolean))

const results = computed(() => {
  const terms = queryTerms.value
  if (terms.length === 0) return []

  return allResults.value
    .filter((item) => {
      const haystack = normalizeSearchText(`${item.session.title} ${item.projectName} ${item.projectPath}`)
      return terms.every((term) => haystack.includes(term))
    })
    .sort(compareSearchResults)
})

const statusText = computed(() => {
  if (props.loading) return 'Loading chats...'
  if (!normalizedQuery.value) return 'Type to search chats.'
  if (results.value.length === 0) return 'No matching chats.'
  return ''
})

watch(
  () => props.open,
  (open) => {
    if (!open) return
    query.value = ''
    activeIndex.value = 0
    void nextTick(() => searchInput.value?.focus())
  },
)

watch(normalizedQuery, () => {
  activeIndex.value = 0
})

watch(
  () => results.value.length,
  (length) => {
    if (length === 0) {
      activeIndex.value = 0
      return
    }
    activeIndex.value = Math.min(activeIndex.value, length - 1)
  },
)

function normalizeSearchText(value: string) {
  return value.trim().toLocaleLowerCase()
}

function compareSearchResults(a: ChatSearchResult, b: ChatSearchResult) {
  const queryText = normalizedQuery.value
  const aRank = searchRank(a, queryText)
  const bRank = searchRank(b, queryText)
  if (aRank !== bRank) return aRank - bRank

  const aTime = Date.parse(a.session.createdAt)
  const bTime = Date.parse(b.session.createdAt)
  return safeTimestamp(bTime) - safeTimestamp(aTime)
}

function searchRank(item: ChatSearchResult, queryText: string) {
  const title = normalizeSearchText(item.session.title)
  const project = normalizeSearchText(item.projectName)
  if (title.startsWith(queryText)) return 0
  if (title.includes(queryText)) return 1
  if (project.includes(queryText)) return 2
  return 3
}

function safeTimestamp(value: number) {
  return Number.isNaN(value) ? 0 : value
}

function closeSearch() {
  emit('close')
}

function selectResult(item: ChatSearchResult) {
  emit('selectSession', item.session.id)
}

function selectActiveResult() {
  const item = results.value[activeIndex.value]
  if (item) selectResult(item)
}

function moveActive(delta: number) {
  const length = results.value.length
  if (length === 0) return
  activeIndex.value = (activeIndex.value + delta + length) % length
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[70] flex items-start justify-center bg-[var(--overlay)] px-3 pt-[10vh]"
    role="dialog"
    aria-modal="true"
    aria-labelledby="chat-search-title"
    data-testid="chat-search-dialog"
    @click.self="closeSearch"
    @keydown.esc.prevent="closeSearch"
    @keydown.down.prevent="moveActive(1)"
    @keydown.up.prevent="moveActive(-1)"
    @keydown.enter.prevent="selectActiveResult"
  >
    <div
      class="flex max-h-[74vh] w-full max-w-[760px] flex-col overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] shadow-2xl"
      @click.stop
    >
      <div class="flex h-14 shrink-0 items-center gap-3 border-b border-[color:var(--border-muted)] px-4">
        <Search class="shrink-0 text-[color:var(--text-muted)]" :size="18" />
        <label id="chat-search-title" class="sr-only" for="chat-search-input">Search chats</label>
        <input
          id="chat-search-input"
          ref="searchInput"
          v-model="query"
          class="min-w-0 flex-1 bg-transparent text-[18px] text-[color:var(--text-primary)] outline-none placeholder:text-[color:var(--text-faint)]"
          type="text"
          placeholder="Search chats"
          autocomplete="off"
          data-testid="chat-search-input"
        />
        <button
          class="icon-button h-8 w-8"
          type="button"
          aria-label="Close search"
          @click="closeSearch"
        >
          <X :size="17" />
        </button>
      </div>

      <div class="min-h-[196px] overflow-y-auto p-2">
        <div
          v-if="statusText"
          class="flex h-32 items-center justify-center gap-2 text-[13px] text-[color:var(--text-muted)]"
          data-testid="chat-search-status"
        >
          <LoaderCircle v-if="loading" class="animate-spin" :size="15" />
          <span>{{ statusText }}</span>
        </div>

        <div v-else class="space-y-1" role="listbox" aria-label="Search results">
          <button
            v-for="(item, index) in results"
            :key="`${item.projectId}:${item.session.id}`"
            class="grid h-10 w-full grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-3 text-left text-[13px] transition focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
            :class="[
              index === activeIndex || item.session.id === activeSessionId
                ? 'bg-[var(--surface-active)] text-[color:var(--text-primary)]'
                : 'text-[color:var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)]',
            ]"
            type="button"
            role="option"
            :aria-selected="index === activeIndex"
            :data-testid="`chat-search-result-${index}`"
            @mouseenter="activeIndex = index"
            @click="selectResult(item)"
          >
            <span class="grid h-5 w-5 place-items-center">
              <Star
                v-if="item.session.starred"
                :size="16"
                fill="none"
                data-testid="chat-search-star"
              />
            </span>
            <span
              class="min-w-0 truncate"
              :class="item.session.unread ? 'font-semibold' : ''"
              v-tooltip="{ content: item.session.title, overflowOnly: true }"
            >
              {{ item.session.title }}
            </span>
            <span
              class="ml-3 max-w-[190px] truncate text-[12px] text-[color:var(--text-muted)]"
              v-tooltip="{ content: item.projectName, overflowOnly: true }"
            >
              {{ item.projectName }}
            </span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
