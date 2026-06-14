<script setup lang="ts">
import { computed, watch } from 'vue'
import { MarkdownRender, enableMermaid } from 'markstream-vue'
import 'markstream-vue/index.css'
import AgentConfigView from './AgentConfigView.vue'
import { isAgentConfigArtifact, parseAgentConfig } from '../agentConfig'
import { useAgentCatalog } from '../composables/useAgentCatalog'
import type { Artifact } from '../types'

enableMermaid(() => import('mermaid'))

defineOptions({
  name: 'ArtifactViewer',
})

const props = defineProps<{
  artifact: Artifact
  markdownIsDark?: boolean
}>()

const isMarkdownArtifact = computed(() => {
  const artifact = props.artifact
  const hasMdName = artifact.title.toLowerCase().endsWith('.md') || (artifact.filename?.toLowerCase().endsWith('.md') ?? false)
  return hasMdName || artifact.kind === 'markdown' || artifact.filetype === 'markdown'
})

// Unparseable agent configs fall through to the plain text branch.
const agentConfig = computed(() => {
  const artifact = props.artifact
  if (!artifact.content || !isAgentConfigArtifact(artifact)) return null
  return parseAgentConfig(artifact.content)
})

const agentVersionLabel = computed(() => {
  const artifact = props.artifact
  if (artifact.displayVersion != null) return `v${artifact.displayVersion}`
  if (artifact.version != null) return `v${artifact.version}`
  return null
})

const {
  catalog: agentCatalog,
  loading: agentCatalogLoading,
  error: agentCatalogError,
  ensureLoaded: ensureAgentCatalogLoaded,
} = useAgentCatalog()

watch(
  agentConfig,
  (value) => {
    if (value) void ensureAgentCatalogLoaded()
  },
  { immediate: true },
)

const effectiveMarkdownIsDark = computed(() => {
  if (props.markdownIsDark !== undefined) return props.markdownIsDark
  if (typeof document === 'undefined') return true
  return document.documentElement.dataset.themeMode !== 'light'
})
</script>

<template>
  <article class="artifact-viewer min-w-0">
    <p v-if="artifact.loading" class="text-[13px] text-[color:var(--text-muted)]">Loading artifact...</p>
    <p v-else-if="artifact.error" class="text-[13px] text-destructive">{{ artifact.error }}</p>

    <AgentConfigView
      v-else-if="agentConfig && artifact.content"
      :config="agentConfig"
      :raw-content="artifact.content"
      :catalog="agentCatalog"
      :catalog-loading="agentCatalogLoading"
      :catalog-error="agentCatalogError"
      :version-label="agentVersionLabel"
      :source-label="artifact.filename ?? artifact.title"
      :markdown-is-dark="effectiveMarkdownIsDark"
    />

    <MarkdownRender
      v-else-if="artifact.content && isMarkdownArtifact"
      class="markdown-body"
      :content="artifact.content"
      :is-dark="effectiveMarkdownIsDark"
      :final="true"
    />

    <pre
      v-else-if="artifact.content"
      class="code-block px-4 py-4 font-mono text-[13px] leading-6 text-[color:var(--text-secondary)]"
    >{{ artifact.content }}</pre>

    <div v-else class="markdown-body">
      <template v-for="(block, index) in artifact.blocks ?? []" :key="index">
        <h1 v-if="block.type === 'heading'">{{ block.text }}</h1>
        <p v-else-if="block.type === 'paragraph'">{{ block.text }}</p>
        <ul v-else-if="block.type === 'list'">
          <li v-for="item in block.items" :key="item">{{ item }}</li>
        </ul>
        <pre v-else class="code-block px-4 py-4 font-mono text-[13px] leading-6 text-[color:var(--text-secondary)]">{{ block.text }}</pre>
      </template>
    </div>
  </article>
</template>
