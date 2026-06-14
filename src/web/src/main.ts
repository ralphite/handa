import { createApp } from 'vue'
import App from './App.vue'
import { configureMarkdownRenderer } from './markdownRenderer'
import { applyMarkdownTheme } from './markdownThemes'
import { DEFAULT_THEME_ID, applyThemePreset, isDarkTheme } from './themes'
import { tooltipDirective } from './directives/tooltip'
import './style.css'

configureMarkdownRenderer()
const initialThemeId = applyThemePreset(DEFAULT_THEME_ID)
applyMarkdownTheme(isDarkTheme(initialThemeId))

createApp(App)
  .directive('tooltip', tooltipDirective)
  .mount('#app')
