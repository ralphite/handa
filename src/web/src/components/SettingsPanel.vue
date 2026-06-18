<script setup lang="ts">
import { Archive, Check, KeyRound, MonitorCog, Moon, Palette, Sun, X } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import SettingsArchivedChats from './SettingsArchivedChats.vue'
import { resolveThemePreset, THEME_OPTIONS } from '../themes'
import type { ThemeId, ThemeOption } from '../themes'
import type { ProjectNavItem } from '../types'
import type { Component } from 'vue'

defineOptions({
  name: 'SettingsPanel',
})

export type SettingsSection = 'theme' | 'gemini-api-key' | 'archived-chats'

const props = defineProps<{
  open: boolean
  themeId: ThemeId
  themeLoading?: boolean
  themeError?: string
  initialSection?: SettingsSection
  geminiApiKeySet?: boolean
  geminiApiKeyPreview?: string
  archivedProjects?: ProjectNavItem[]
  archivedSessionCount?: number
  archivedLoading?: boolean
  archivedError?: string
}>()

const emit = defineEmits<{
  close: []
  updateSection: [section: SettingsSection]
  updateTheme: [themeId: ThemeId]
  updateGeminiApiKey: [apiKey: string]
  unarchiveSession: [id: string]
  deleteSession: [id: string]
}>()

const geminiApiKeyInput = ref('')
const activeSection = ref<SettingsSection>(props.initialSection ?? 'theme')
const dialogEl = ref<HTMLElement | null>(null)

const sections: Array<{
  id: SettingsSection
  label: string
  icon: Component
}> = [
  { id: 'theme', label: 'Theme', icon: Palette },
  { id: 'gemini-api-key', label: 'Gemini API Key', icon: KeyRound },
  { id: 'archived-chats', label: 'Archived Chats', icon: Archive },
]

function close() {
  emit('close')
}

function saveGeminiApiKey() {
  const apiKey = geminiApiKeyInput.value.trim()
  if (!apiKey) return
  emit('updateGeminiApiKey', apiKey)
  geminiApiKeyInput.value = ''
}

function clearGeminiApiKey() {
  emit('updateGeminiApiKey', '')
  geminiApiKeyInput.value = ''
}

const activeTitle = computed(() => {
  return sections.find((section) => section.id === activeSection.value)?.label ?? 'Settings'
})

function itemClass(sectionId: SettingsSection) {
  return sectionId === activeSection.value
    ? 'bg-[var(--surface-active)] text-[color:var(--text-primary)]'
    : 'text-[color:var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)]'
}

function selectSection(section: SettingsSection) {
  activeSection.value = section
  emit('updateSection', section)
}

function themeOptionIcon(themeId: ThemeId) {
  if (themeId === 'system') return MonitorCog
  if (themeId === 'dark') return Moon
  return Sun
}

function themeSwatchStyle(option: ThemeOption) {
  if (option.id === 'system') {
    const darkPreset = resolveThemePreset('dark')
    const lightPreset = resolveThemePreset('light')
    return {
      background: `linear-gradient(135deg, ${darkPreset.variables['--panel']} 0 50%, ${lightPreset.variables['--panel']} 50% 100%)`,
      color: 'var(--accent)',
    }
  }

  const preset = resolveThemePreset(option.id)
  return {
    background: preset.variables['--panel'],
    color: preset.variables['--foreground'],
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    activeSection.value = props.initialSection ?? 'theme'
    geminiApiKeyInput.value = ''
    void nextTick(() => dialogEl.value?.focus())
  },
)

watch(
  () => props.initialSection,
  (section) => {
    if (section) activeSection.value = section
  },
)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[70] flex items-start justify-center bg-[var(--overlay)] px-3 pt-[8vh]"
    role="dialog"
    aria-modal="true"
    aria-labelledby="settings-dialog-title"
    data-testid="settings-dialog"
    @click.self="close"
  >
    <div
      ref="dialogEl"
      class="flex h-[600px] max-h-[80vh] w-full max-w-[920px] overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] text-[color:var(--text-primary)] shadow-2xl outline-none"
      tabindex="-1"
      @click.stop
      @keydown.esc.prevent="close"
    >
      <aside
        class="settings-sidebar flex shrink-0 flex-col border-r border-[color:var(--border-muted)] bg-[var(--sidebar-bg)]"
      >
        <div class="flex h-14 shrink-0 items-center px-5 text-[15px] font-semibold tracking-normal text-[color:var(--text-primary)]">
          <span id="settings-dialog-title" class="truncate">Settings</span>
        </div>

        <nav class="space-y-1 px-3 text-[14px]" aria-label="Settings sections">
          <button
            v-for="section in sections"
            :key="section.id"
            class="sidebar-action"
            :class="itemClass(section.id)"
            type="button"
            @click="selectSection(section.id)"
          >
            <component :is="section.icon" :size="16" />
            <span class="truncate">{{ section.label }}</span>
          </button>
        </nav>
      </aside>

      <main class="relative min-w-0 flex-1 overflow-y-auto px-8 pb-12 pt-7">
        <button
          class="icon-button absolute right-3 top-3 h-8 w-8"
          type="button"
          aria-label="Close settings"
          @click="close"
        >
          <X :size="17" />
        </button>

        <div class="mx-auto w-full max-w-[560px]">
          <h1 class="mb-6 text-[20px] font-semibold tracking-normal text-[color:var(--text-primary)]">{{ activeTitle }}</h1>

          <section v-if="activeSection === 'theme'" class="space-y-5">
            <p v-if="themeError" class="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-[13px] text-destructive">
              {{ themeError }}
            </p>

            <div class="overflow-hidden rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)]">
              <button
                v-for="option in THEME_OPTIONS"
                :key="option.id"
                class="flex w-full items-center gap-3 border-b border-[color:var(--border-muted)] px-4 py-3 text-left transition last:border-b-0 hover:bg-[var(--surface-hover)] disabled:cursor-not-allowed disabled:opacity-60"
                type="button"
                :disabled="themeLoading"
                @click="emit('updateTheme', option.id)"
              >
                <span
                  class="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-[color:var(--border-muted)]"
                  :style="themeSwatchStyle(option)"
                >
                  <component :is="themeOptionIcon(option.id)" :size="17" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block text-[14px] font-medium text-[color:var(--text-primary)]">{{ option.label }}</span>
                </span>
                <span
                  v-if="option.id === themeId"
                  class="grid h-5 w-5 place-items-center rounded-full bg-[var(--accent)] text-[color:var(--accent-contrast)]"
                >
                  <Check :size="13" />
                </span>
                <span v-else class="h-5 w-5 rounded-full border border-[color:var(--border-subtle)]"></span>
              </button>
            </div>
          </section>

          <section v-else-if="activeSection === 'gemini-api-key'" class="space-y-5">
            <div>
              <h2 class="text-[15px] font-medium text-[color:var(--text-primary)]">Gemini API key</h2>
              <p class="mt-1 text-[14px] leading-5 text-[color:var(--text-muted)]">
                Used to authenticate Gemini model requests. Stored locally and applied as <code>GOOGLE_API_KEY</code> when running agents.
              </p>
            </div>

            <p v-if="themeError" class="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-[13px] text-destructive">
              {{ themeError }}
            </p>

            <div class="space-y-4 rounded-lg border border-[color:var(--border-muted)] bg-[var(--surface)] p-4">
              <div
                v-if="geminiApiKeySet"
                class="flex items-center gap-2 text-[13px] text-[color:var(--text-muted)]"
              >
                <span class="grid h-5 w-5 place-items-center rounded-full bg-[var(--accent)] text-[color:var(--accent-contrast)]">
                  <Check :size="13" />
                </span>
                <span>Key set ending in <span class="font-mono text-[color:var(--text-primary)]">{{ geminiApiKeyPreview }}</span></span>
              </div>
              <p v-else class="text-[13px] text-[color:var(--text-muted)]">No key configured.</p>

              <input
                v-model="geminiApiKeyInput"
                class="api-key-input w-full rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2 text-[14px] text-[color:var(--text-primary)] outline-none focus:border-[var(--accent)]"
                type="text"
                autocomplete="off"
                autocapitalize="off"
                autocorrect="off"
                data-1p-ignore
                data-form-type="other"
                data-lpignore="true"
                :spellcheck="false"
                :placeholder="geminiApiKeySet ? 'Enter a new key to replace' : 'Paste your Gemini API key'"
                @keydown.enter.prevent="saveGeminiApiKey"
              />

              <div class="flex items-center gap-2">
                <button
                  class="rounded-lg bg-[var(--accent)] px-3 py-1.5 text-[13px] font-medium text-[color:var(--accent-contrast)] transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                  type="button"
                  :disabled="!geminiApiKeyInput.trim()"
                  @click="saveGeminiApiKey"
                >
                  Save key
                </button>
                <button
                  v-if="geminiApiKeySet"
                  class="rounded-lg border border-[color:var(--border-subtle)] px-3 py-1.5 text-[13px] font-medium text-[color:var(--text-secondary)] transition hover:bg-[var(--surface-hover)]"
                  type="button"
                  @click="clearGeminiApiKey"
                >
                  Remove key
                </button>
              </div>
            </div>
          </section>

          <section v-else-if="activeSection === 'archived-chats'">
            <SettingsArchivedChats
              :projects="archivedProjects ?? []"
              :session-count="archivedSessionCount ?? 0"
              :loading="archivedLoading"
              :error="archivedError"
              @unarchive-session="id => emit('unarchiveSession', id)"
              @delete-session="id => emit('deleteSession', id)"
            />
          </section>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.settings-sidebar {
  width: 220px;
}

.api-key-input {
  -webkit-text-security: disc;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.api-key-input::placeholder {
  -webkit-text-security: none;
  font-family: inherit;
}

@media (max-width: 720px) {
  .settings-sidebar {
    width: 180px;
  }
}
</style>
