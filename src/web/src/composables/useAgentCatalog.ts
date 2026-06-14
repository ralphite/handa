import { ref } from 'vue'
import { getAgentCatalog } from '../api/client'
import type { BackendAgentCatalog } from '../api/types'

// The catalog is static reference data (tool definitions, section templates,
// skill metadata). It loads on demand the first time a consumer needs it and
// lives in component state only — no shared cache layer, per the demand
// loaded data constraints.
export function useAgentCatalog() {
  const catalog = ref<BackendAgentCatalog | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function ensureLoaded() {
    if (catalog.value || loading.value) return
    loading.value = true
    error.value = null
    try {
      catalog.value = await getAgentCatalog()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load agent catalog'
    } finally {
      loading.value = false
    }
  }

  return { catalog, loading, error, ensureLoaded }
}
