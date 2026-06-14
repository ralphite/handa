<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowRightToLine, Check, Copy, TextWrap } from '@lucide/vue'
import hljs from 'highlight.js/lib/common'

type CodeBlockNode = {
  language?: string
  code?: string
  raw?: string
  diff?: boolean
}

const props = defineProps<{
  node: CodeBlockNode
}>()

const copied = ref(false)
const wrapEnabled = ref(true)

const isDiffBlock = computed(() => {
  const lang = normalizeLanguage(props.node.language)
  if (lang === 'diff') return true
  if (props.node.diff) return true
  return looksLikeUnifiedDiff(props.node.raw ?? props.node.code ?? '')
})
const language = computed(() => (isDiffBlock.value ? 'diff' : normalizeLanguage(props.node.language)))
const code = computed(() => {
  if (isDiffBlock.value && props.node.raw) return props.node.raw
  return props.node.code ?? ''
})
const wrapTitle = computed(() => (wrapEnabled.value ? 'Disable line wrap' : 'Enable line wrap'))

const highlightedCode = computed(() => {
  const text = code.value
  const lang = language.value

  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value
    }
    return escapeHtml(text)
  } catch {
    return escapeHtml(text)
  }
})

async function copyCode() {
  if (!code.value) return
  try {
    await navigator.clipboard.writeText(code.value)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = code.value
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
  copied.value = true
  window.setTimeout(() => {
    copied.value = false
  }, 1200)
}

function toggleLineWrap() {
  wrapEnabled.value = !wrapEnabled.value
}

function normalizeLanguage(value?: string) {
  const lang = value?.trim().toLowerCase()
  if (!lang) return ''
  if (lang === 'ts') return 'typescript'
  if (lang === 'tsx') return 'typescript'
  if (lang === 'js') return 'javascript'
  if (lang === 'jsx') return 'javascript'
  if (lang === 'py') return 'python'
  if (lang === 'sh' || lang === 'zsh') return 'bash'
  if (lang === 'yml') return 'yaml'
  if (lang === 'patch') return 'diff'
  return lang
}

function looksLikeUnifiedDiff(value: string) {
  if (!value) return false
  if (/^(diff --git|@@ )/m.test(value)) return true
  return /^--- /m.test(value) && /^\+\+\+ /m.test(value)
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}
</script>

<template>
  <figure
    class="code-block highlighted-code-block"
    :class="wrapEnabled ? 'code-block-wrap' : 'code-block-nowrap'"
  >
    <figcaption class="code-toolbar">
      <span class="truncate uppercase tracking-normal">{{ language || 'text' }}</span>
      <span class="code-toolbar-actions">
        <button
          class="code-toolbar-button"
          type="button"
          :aria-label="wrapTitle"
          :aria-pressed="wrapEnabled"
          v-tooltip="wrapTitle"
          @click="toggleLineWrap"
        >
          <TextWrap v-if="wrapEnabled" :size="15" />
          <ArrowRightToLine v-else :size="15" />
        </button>
        <button
          class="code-toolbar-button"
          type="button"
          :aria-label="copied ? 'Copied code' : 'Copy code'"
          v-tooltip="copied ? 'Copied' : 'Copy code'"
          @click="copyCode"
        >
          <Check v-if="copied" :size="15" />
          <Copy v-else :size="15" />
        </button>
      </span>
    </figcaption>
    <pre><code class="hljs" :class="language ? `language-${language}` : undefined" v-html="highlightedCode" /></pre>
  </figure>
</template>
