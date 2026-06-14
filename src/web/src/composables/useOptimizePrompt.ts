import { ref } from 'vue'
import { optimizePrompt } from '../api/client'

/**
 * Wraps the /api/optimize_prompt endpoint.
 *
 * Lifecycle: idle -> optimizing -> idle. The caller decides what to do with
 * the optimized prompt (typically replace the composer draft, keeping the
 * original around for undo).
 */
export function useOptimizePrompt(options: {
  getSessionId: () => string | undefined
  getProjectId: () => string | undefined
  onError?: (message: string) => void
}) {
  const isOptimizing = ref(false)

  async function optimize(prompt: string): Promise<string | null> {
    const trimmed = prompt.trim()
    if (!trimmed || isOptimizing.value) return null
    isOptimizing.value = true
    try {
      const { optimized } = await optimizePrompt(trimmed, {
        sessionId: options.getSessionId(),
        projectId: options.getProjectId(),
      })
      return optimized.trim() || null
    } catch (exc) {
      options.onError?.(exc instanceof Error ? exc.message : 'Prompt optimization failed')
      return null
    } finally {
      isOptimizing.value = false
    }
  }

  return {
    isOptimizing,
    optimize,
  }
}
