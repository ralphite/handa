import { onScopeDispose, ref } from 'vue'
import { getSettings, updateSettings } from '../api/client'
import { applyMarkdownTheme } from '../markdownThemes'
import {
  applyThemePreset,
  DEFAULT_THEME_ID,
  resolveThemeMode,
  SYSTEM_THEME_MEDIA_QUERY,
} from '../themes'
import type { BackendModelConfigOption } from '../api/types'
import type { ThemeId, ThemeMode } from '../themes'

const SETTINGS_LOAD_RETRY_DELAYS_MS = [100, 200, 500, 1000, 2000, 3000] as const

export function useThemeSettings() {
  const themeId = ref<ThemeId>(DEFAULT_THEME_ID)
  const effectiveThemeMode = ref<ThemeMode>(resolveThemeMode(DEFAULT_THEME_ID))
  const modelConfigId = ref('gemini-3.1-pro-high')
  const modelConfigs = ref<BackendModelConfigOption[]>([])
  const streamingModeEnabled = ref(true)
  const foldedProjectIds = ref<string[]>([])
  const geminiApiKeySet = ref(false)
  const geminiApiKeyPreview = ref('')
  const loadingTheme = ref(false)
  const themeError = ref('')
  const systemThemeMedia =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(SYSTEM_THEME_MEDIA_QUERY)
      : null

  if (systemThemeMedia) {
    systemThemeMedia.addEventListener('change', handleSystemThemeChange)
    onScopeDispose(() => {
      systemThemeMedia.removeEventListener('change', handleSystemThemeChange)
    })
  }

  async function loadTheme() {
    loadingTheme.value = true
    themeError.value = ''
    try {
      const settings = await getSettingsWithStartupRetry()
      applySettings(settings)
    } catch (exc) {
      themeError.value = exc instanceof Error ? exc.message : String(exc)
      applyThemePreference(DEFAULT_THEME_ID)
    } finally {
      loadingTheme.value = false
    }
  }

  async function getSettingsWithStartupRetry() {
    let lastError: unknown
    for (const retryDelayMs of SETTINGS_LOAD_RETRY_DELAYS_MS) {
      try {
        return await getSettings()
      } catch (exc) {
        lastError = exc
      }
      await wait(retryDelayMs)
    }
    try {
      return await getSettings()
    } catch (exc) {
      throw lastError ?? exc
    }
  }

  async function setTheme(nextThemeId: ThemeId) {
    const previousThemeId = themeId.value
    applyThemePreference(nextThemeId)
    themeError.value = ''
    try {
      const settings = await updateSettings({ theme_id: nextThemeId })
      applySettings(settings)
    } catch (exc) {
      applyThemePreference(previousThemeId)
      themeError.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function setModelConfig(nextModelConfigId: string) {
    const previousModelConfigId = modelConfigId.value
    modelConfigId.value = nextModelConfigId
    themeError.value = ''
    try {
      const settings = await updateSettings({ model_config_id: nextModelConfigId })
      applySettings(settings)
    } catch (exc) {
      modelConfigId.value = previousModelConfigId
      themeError.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function setStreamingModeEnabled(enabled: boolean) {
    const previous = streamingModeEnabled.value
    streamingModeEnabled.value = enabled
    themeError.value = ''
    try {
      const settings = await updateSettings({ streaming_mode_enabled: enabled })
      applySettings(settings)
    } catch (exc) {
      streamingModeEnabled.value = previous
      themeError.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function setFoldedProjects(projectIds: string[]) {
    const previous = foldedProjectIds.value
    foldedProjectIds.value = projectIds
    themeError.value = ''
    try {
      const settings = await updateSettings({ folded_project_ids: projectIds })
      applySettings(settings)
    } catch (exc) {
      foldedProjectIds.value = previous
      themeError.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function setGeminiApiKey(apiKey: string) {
    themeError.value = ''
    try {
      const settings = await updateSettings({ gemini_api_key: apiKey })
      applySettings(settings)
    } catch (exc) {
      themeError.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  function applySettings(settings: Awaited<ReturnType<typeof getSettings>>) {
    applyThemePreference(settings.theme_id)
    modelConfigId.value = settings.model_config_id
    modelConfigs.value = settings.model_configs
    streamingModeEnabled.value = settings.streaming_mode_enabled
    foldedProjectIds.value = settings.folded_project_ids
    geminiApiKeySet.value = settings.gemini_api_key_set
    geminiApiKeyPreview.value = settings.gemini_api_key_preview
  }

  function applyThemePreference(nextThemeId: string | null | undefined) {
    themeId.value = applyThemePreset(nextThemeId)
    effectiveThemeMode.value = resolveThemeMode(themeId.value)
    applyMarkdownTheme(effectiveThemeMode.value === 'dark')
  }

  function handleSystemThemeChange() {
    if (themeId.value !== 'system') return
    applyThemePreference('system')
  }

  function wait(ms: number) {
    return new Promise((resolve) => window.setTimeout(resolve, ms))
  }

  return {
    foldedProjectIds,
    geminiApiKeyPreview,
    geminiApiKeySet,
    effectiveThemeMode,
    loadTheme,
    loadingTheme,
    modelConfigId,
    modelConfigs,
    setFoldedProjects,
    setGeminiApiKey,
    setModelConfig,
    setStreamingModeEnabled,
    setTheme,
    streamingModeEnabled,
    themeError,
    themeId,
  }
}
