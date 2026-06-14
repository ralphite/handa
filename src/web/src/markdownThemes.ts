import markdownDarkCss from 'github-markdown-css/github-markdown-dark.css?raw'
import markdownLightCss from 'github-markdown-css/github-markdown-light.css?raw'

const MARKDOWN_THEME_STYLE_ID = 'handa-markdown-theme-css'
const APP_MARKDOWN_CSS = `
.markdown-body,
.markdown-body.markstream-vue,
.markdown-body .markstream-vue {
  background: transparent;
  font-family: var(--font-sans);
  font-size: var(--markdown-font-size);
  line-height: var(--markdown-line-height);
  --markdown-font-size: var(--app-font-size, 14px);
  --markdown-line-height: calc(var(--markdown-font-size) * 1.714285714);
  --ms-text-body: var(--markdown-font-size);
  --ms-text-h1: calc(var(--markdown-font-size) * 2.25);
  --ms-text-h2: calc(var(--markdown-font-size) * 1.5);
  --ms-text-h3: calc(var(--markdown-font-size) * 1.25);
  --ms-text-h4: var(--markdown-font-size);
  --ms-text-h5: var(--markdown-font-size);
  --ms-text-h6: var(--markdown-font-size);
  --ms-text-label: calc(var(--markdown-font-size) * 0.75);
  --ms-leading-body: var(--markdown-line-height);
  --ms-flow-paragraph-y: 0.35rem;
}

.process-block .markdown-body,
.process-block .markdown-body.markstream-vue,
.process-block .markdown-body .markstream-vue {
  --markdown-font-size: 13px;
  --ms-flow-paragraph-y: 0.2rem;
}

.markdown-body p {
  margin-top: 0.35rem;
  margin-bottom: 0.35rem;
}

.process-block .markdown-body p {
  margin-top: 0.2rem;
  margin-bottom: 0.2rem;
}

.markdown-body hr,
.markdown-body.markstream-vue hr,
.markdown-body .markstream-vue hr {
  height: 1px;
  padding: 0;
  margin: 1.25rem 0;
  border: 0;
  background-color: var(--border-muted);
  opacity: 0.55;
}

.markdown-body :not(pre) > code,
.markdown-body .inline-code.inline-code {
  font-size: 0.92em;
  line-height: 1.45;
  padding: 0.12em 0.42em;
}

.markdown-body .inline-code.inline-code {
  -webkit-box-decoration-break: clone;
  box-decoration-break: clone;
}

.markdown-body .highlighted-code-block {
  margin: 12px 0 1.5rem;
  border-color: var(--code-border);
  background-color: var(--code-bg);
}

.markdown-body .highlighted-code-block .code-toolbar {
  display: flex;
  height: 2.25rem;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0 0.75rem;
  border-bottom: 1px solid var(--code-border);
}

.markdown-body .highlighted-code-block .code-toolbar button {
  display: grid;
  width: 1.75rem;
  height: 1.75rem;
  flex: none;
  place-items: center;
  padding: 0;
  border: 0;
  margin: 0;
  appearance: none;
  background: transparent;
  color: inherit;
}

.markdown-body .highlighted-code-block .code-toolbar button:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
}

.markdown-body .highlighted-code-block pre {
  margin: 0;
  border: 0;
  border-radius: 0;
  background-color: var(--code-bg);
  color: var(--code-fg);
  font-size: 13px;
  line-height: 1.5rem;
}

.markdown-body .highlighted-code-block.code-block-wrap pre,
.markdown-body .highlighted-code-block.code-block-wrap code {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.markdown-body .highlighted-code-block.code-block-nowrap pre,
.markdown-body .highlighted-code-block.code-block-nowrap code {
  white-space: pre;
  overflow-wrap: normal;
}

/* Fix TableNode column misalignment caused by github-markdown-css overriding table display to block */
.markdown-body table.table-node {
  display: table !important;
  width: 100% !important;
  max-width: 100% !important;
  overflow: hidden !important;
  border-collapse: separate !important;
  border-spacing: 0 !important;
  margin: var(--ms-flow-table-y) 0 !important;
}

/* Reset github-markdown-css table styles that conflict with markstream-vue TableNode */
.markdown-body .table-node tr {
  background-color: transparent !important;
  border-top: none !important;
}

.markdown-body .table-node th,
.markdown-body .table-node td {
  padding: var(--ms-flow-table-cell) !important;
  border: none !important; /* clear github-markdown-css full border */
}

/* Re-apply markstream-vue TableNode borders with higher specificity */
.markdown-body .table-node th,
.markdown-body .table-node td {
  border-bottom: 1px solid var(--table-border) !important;
  border-right: 1px solid var(--table-border) !important;
}

.markdown-body .table-node th:last-child,
.markdown-body .table-node td:last-child {
  border-right: none !important;
}

.markdown-body .table-node tbody tr:last-child td {
  border-bottom: none !important;
}

.markdown-body .table-node tbody tr:nth-child(2n) {
  background-color: hsl(var(--ms-muted) / .35) !important;
}

.markdown-body .table-node tbody tr:hover {
  background-color: var(--code-action-hover-bg) !important;
}
`

export function markdownThemeCss(isDark: boolean) {
  return `${isDark ? markdownDarkCss : markdownLightCss}\n${APP_MARKDOWN_CSS}`
}

export function applyMarkdownTheme(isDark: boolean) {
  if (typeof document === 'undefined') return

  let styleEl = document.getElementById(MARKDOWN_THEME_STYLE_ID) as HTMLStyleElement | null
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.id = MARKDOWN_THEME_STYLE_ID
    document.head.append(styleEl)
  }
  styleEl.dataset.markdownTheme = 'default'
  styleEl.dataset.markdownMode = isDark ? 'dark' : 'light'
  styleEl.textContent = markdownThemeCss(isDark)
}
