import type { InvocationDetailEvent, InvocationTimelineItem } from '../types'

export type ToolDisplayBlock =
  | { type: 'text'; content: string }
  | { type: 'code'; content: string; language?: string }
  | { type: 'shell'; command: string; stdout?: string; stderr?: string }
  | { type: 'list'; items: string[] }
  | { type: 'kv'; items: { label: string; value: string }[] }
  | { type: 'error'; content: string }

export interface ToolDisplay {
  title: string
  meta?: string
  blocks: ToolDisplayBlock[]
}

type AnyRecord = Record<string, unknown>
export type ToolEventStatus = 'running' | 'done' | 'failed'

export function presentToolEvent(
  event: InvocationDetailEvent | InvocationTimelineItem,
): ToolDisplay {
  const payload = record(event.payload) ?? {}
  const toolName = getToolName(event, payload) || ''
  const response = getToolResponse(payload)
  const args = getToolArgs(payload)

  if (event.kind === 'error') return presentErrorEvent(event, payload)

  if (response && isDirectFailure(response)) {
    return {
      title: event.summary,
      meta: 'Failed',
      blocks: failureBlocks(response),
    }
  }

  if (toolName === 'files_read') return presentReadFile(event, response, args)
  if (toolName === 'files_list') return presentFileList(event, response)
  if (toolName === 'files_search') return presentSearch(event, response)
  if (toolName === 'files_write') return presentWrite(event, response, args)
  if (toolName === 'files_replace') return presentReplace(event, response, args)
  if (toolName === 'commands_run') {
    return presentCommand(event, response, args)
  }
  if (toolName.startsWith('browser_')) return presentBrowser(event, response, args)
  if (toolName === 'artifacts_read' || toolName === 'agents_read_run_artifact' || toolName === 'skills_read') {
    return presentContentRead(event, response, args)
  }
  if (
    toolName === 'artifacts_list' ||
    toolName === 'agents_list_configs' ||
    toolName === 'agents_list_run_artifacts' ||
    toolName === 'skills_list'
  ) {
    return presentNamedList(event, response)
  }
  if (toolName === 'artifacts_save_text' || toolName === 'agents_save_config') {
    return presentSaved(event, response, args)
  }
  if (toolName === 'agents_start_run' || toolName === 'run_agent') {
    return presentAgentRun(event, response)
  }
  if (toolName === 'agents_get_run_status' || toolName === 'tasks_get_status') {
    return presentStatus(event, response)
  }
  if (toolName === 'agents_read_run_result') return presentRunResult(event, response)
  if (toolName === 'agents_read_run_log' || toolName === 'tasks_read_log') {
    return presentLog(event, response)
  }
  if (toolName === 'tasks_list') return presentTasks(event, response)
  if (toolName === 'notifications_get') return presentNotifications(event, response)
  if (toolName === 'notes_add') return presentNote(event, response)

  return presentFallback(event, response, args)
}

export function toolEventStatus(
  event: InvocationDetailEvent | InvocationTimelineItem,
): ToolEventStatus | undefined {
  const explicitStatus = 'status' in event ? event.status : undefined
  if (event.kind === 'tool' || event.kind === 'tool_response') {
    const payload = record(event.payload) ?? {}
    if (toolResponsePayloadIndicatesFailedOutcome(payload)) return 'failed'
    if (event.kind === 'tool_response') return 'done'
  }
  if (event.kind === 'tool_call') return explicitStatus ?? 'running'
  return explicitStatus
}

export function toolResponsePayloadIndicatesFailedOutcome(payload: AnyRecord): boolean {
  const response = getToolResponse(payload)
  return responseIndicatesFailedOutcome(response)
}

function presentReadFile(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  const path = stringValue(response?.path) || stringValue(args.path)
  const content = stringValue(response?.content)
  const range = lineRange(response)
  return {
    title: path ? `Read ${path}` : event.summary,
    meta: range,
    blocks: content ? [{ type: 'code', content, language: languageFromPath(path) }] : waitingBlocks(args),
  }
}

function presentFileList(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks({}) }
  const listing = stringValue(response?.listing)
  const files = stringArray(response?.files)
  const path = stringValue(response?.path)
  const total = numberValue(response?.file_count) ?? files.length
  const shown = numberValue(response?.shown_count) ?? files.length
  const truncated = booleanValue(response?.truncated)
  const meta = truncated ? `${shown}/${total} files` : `${total} files`
  return {
    title: path ? `Files in ${path}` : event.summary,
    meta,
    blocks: listing
      ? [{ type: 'code', content: listing, language: 'text' }]
      : files.length
        ? [{ type: 'list', items: files }]
        : [{ type: 'text', content: 'No files.' }],
  }
}

function presentSearch(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks({}) }
  const matches = stringArray(response?.matches)
  const query = stringValue(response?.query)
  const count = numberValue(response?.match_count)
  return {
    title: query ? `Search "${query}"` : event.summary,
    meta: typeof count === 'number' ? `${count} matches` : undefined,
    blocks: matches.length ? [{ type: 'code', content: matches.join('\n'), language: 'text' }] : [{ type: 'text', content: 'No matches.' }],
  }
}

function presentWrite(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks(args) }
  const path = stringValue(response?.path) || stringValue(args.path)
  return {
    title: path ? `Wrote ${path}` : event.summary,
    blocks: [{ type: 'kv', items: [{ label: 'Status', value: 'Saved' }] }],
  }
}

function presentReplace(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks(args) }
  const path = stringValue(response?.path) || stringValue(args.path)
  const replacements = numberValue(response?.replacements)
  return {
    title: path ? `Edited ${path}` : event.summary,
    blocks: [
      {
        type: 'kv',
        items: [
          { label: 'Status', value: 'Updated' },
          ...(typeof replacements === 'number' ? [{ label: 'Replacements', value: String(replacements) }] : []),
        ],
      },
    ],
  }
}

function presentCommand(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  const command = stringValue(response?.command) || stringValue(args.command) || event.summary
  const returncode = numberValue(response?.returncode)
  const stdout = stringValue(response?.stdout)
  const stderr = stringValue(response?.stderr)
  return {
    title: command,
    meta: typeof returncode === 'number' && returncode !== 0 ? `exit ${returncode}` : undefined,
    blocks: [
      {
        type: 'shell',
        command,
        stdout: stdout ? stdout.trimEnd() : undefined,
        stderr: stderr ? stderr.trimEnd() : undefined,
      },
    ],
  }
}

function presentBrowser(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks(args) }
  // Rows repeat for every browser step, so lead with what the step did
  // (last_action) instead of the page title, which rarely changes.
  const title =
    stringValue(response?.last_action) ||
    stringValue(response?.title) ||
    stringValue(args.url) ||
    event.summary
  const elements = arrayValue(response?.elements)
    .map((item) => {
      const element = record(item)
      return [
        stringValue(element?.id),
        stringValue(element?.tag),
        stringValue(element?.text),
      ].filter(Boolean).join(' · ')
    })
    .filter(Boolean)
  const blocks: ToolDisplayBlock[] = [
    ...kvFrom(response, ['status', 'url', 'title', 'last_action', 'screenshot_url']),
  ]
  const text = stringValue(response?.text)
  if (text) blocks.push({ type: 'text', content: text })
  if (elements.length) blocks.push({ type: 'list', items: elements })
  return {
    title,
    meta: stringValue(response?.status),
    blocks: blocks.length ? blocks : fallbackBlocks(response, args),
  }
}

function presentContentRead(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  const filename = stringValue(response?.filename) || stringValue(response?.path) || stringValue(args.filename) || stringValue(args.name)
  const content = stringValue(response?.content)
  return {
    title: filename ? `Read ${filename}` : event.summary,
    blocks: content ? [{ type: 'code', content, language: languageFromPath(filename) }] : fallbackBlocks(response, args),
  }
}

function presentNamedList(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks({}) }
  const values = [
    ...stringArray(response?.artifacts),
    ...stringArray(response?.configs),
    ...stringArray(response?.skills).map((item) => item),
  ]
  const count = numberValue(response?.count) ?? values.length
  return {
    title: event.summary,
    meta: `${count} items`,
    blocks: values.length ? [{ type: 'list', items: values }] : [{ type: 'text', content: 'No items.' }],
  }
}

function presentSaved(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks(args) }
  const filename = stringValue(response?.filename) || stringValue(args.filename) || stringValue(args.name)
  const version = numberValue(response?.display_version) ?? numberValue(response?.version)
  return {
    title: filename ? `Saved ${filename}` : event.summary,
    meta: typeof version === 'number' ? `v${version}` : undefined,
    blocks: [
      {
        type: 'kv',
        items: [
          ...(filename ? [{ label: 'File', value: filename }] : []),
          ...(typeof version === 'number' ? [{ label: 'Version', value: `v${version}` }] : []),
        ],
      },
    ],
  }
}

function presentAgentRun(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  return {
    title: event.summary,
    meta: stringValue(response?.status),
    blocks: kvFrom(response, ['task_id', 'status', 'agent_id', 'config_name', 'child_session_id']),
  }
}

function presentStatus(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  const task = record(response?.task)
  return {
    title: event.summary,
    meta: stringValue(task?.status) || stringValue(response?.status),
    blocks: task ? kvFrom(task, ['id', 'kind', 'status', 'summary', 'child_session_id']) : fallbackBlocks(response, {}),
  }
}

function presentRunResult(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  const blocks: ToolDisplayBlock[] = []
  const result = record(response?.result)
  const task = record(response?.task)
  const summary = stringValue(result?.summary) || stringValue(response?.summary)
  if (summary) blocks.push({ type: 'text', content: summary })
  if (task) blocks.push(...kvFrom(task, ['id', 'status', 'summary']))
  const report = stringValue(response?.report) || stringValue(result?.report)
  if (report) blocks.push({ type: 'code', content: report, language: 'markdown' })
  return {
    title: event.summary,
    blocks: blocks.length ? blocks : fallbackBlocks(response, {}),
  }
}

function presentLog(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  const content = stringValue(response?.content) || stringValue(response?.log) || stringArray(response?.lines).join('\n')
  return {
    title: event.summary,
    blocks: content ? [{ type: 'code', content, language: 'text' }] : fallbackBlocks(response, {}),
  }
}

function presentTasks(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks({}) }
  const tasks = arrayValue(response?.tasks)
  const items = tasks.map((task) => {
    const taskRecord = record(task)
    return [
      stringValue(taskRecord?.id),
      stringValue(taskRecord?.status),
      stringValue(taskRecord?.summary),
    ].filter(Boolean).join(' · ')
  }).filter(Boolean)
  return {
    title: event.summary,
    meta: `${items.length} tasks`,
    blocks: items.length ? [{ type: 'list', items }] : [{ type: 'text', content: 'No tasks.' }],
  }
}

function presentNotifications(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  if (!response) return { title: event.summary, blocks: waitingBlocks({}) }
  const events = arrayValue(response?.events)
  const items = events.map((item) => {
    const notification = record(item)
    return [stringValue(notification?.kind), stringValue(notification?.summary)].filter(Boolean).join(' · ')
  }).filter(Boolean)
  return {
    title: event.summary,
    meta: `${items.length} events`,
    blocks: items.length ? [{ type: 'list', items }] : [{ type: 'text', content: 'No new events.' }],
  }
}

function presentNote(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
): ToolDisplay {
  const note = record(response?.note)
  const summary = stringValue(note?.summary)
  return {
    title: event.summary,
    blocks: summary ? [{ type: 'text', content: summary }] : fallbackBlocks(response, {}),
  }
}

function presentFallback(
  event: InvocationDetailEvent | InvocationTimelineItem,
  response: AnyRecord | undefined,
  args: AnyRecord,
): ToolDisplay {
  return {
    title: event.summary,
    blocks: fallbackBlocks(response, args),
  }
}

// The row title carries a one-line summary; expanding the row must still give
// access to the full provider error (multi-line message, raw JSON and all).
function presentErrorEvent(
  event: InvocationDetailEvent | InvocationTimelineItem,
  payload: AnyRecord,
): ToolDisplay {
  const message = stringValue(payload.error_message) || stringValue(payload.message)
  const code = stringValue(payload.error_code) || stringValue(payload.code)
  const blocks: ToolDisplayBlock[] = []
  if (code) blocks.push({ type: 'kv', items: [{ label: 'Code', value: code }] })
  if (message) blocks.push({ type: 'error', content: message })
  return {
    title: event.summary,
    blocks: blocks.length ? blocks : [{ type: 'text', content: event.summary }],
  }
}

function getToolName(event: InvocationDetailEvent | InvocationTimelineItem, payload: AnyRecord) {
  return (
    ('toolName' in event ? event.toolName : '') ||
    stringValue(record(payload.call)?.name) ||
    stringValue(record(payload.response)?.name) ||
    stringValue(payload.name)
  )
}

function getToolArgs(payload: AnyRecord): AnyRecord {
  return record(record(payload.call)?.args) ?? record(payload.args) ?? {}
}

function getToolResponse(payload: AnyRecord): AnyRecord | undefined {
  const responseEnvelope = record(payload.response)
  if (responseEnvelope && 'response' in responseEnvelope) return record(responseEnvelope.response) ?? {}
  if (responseEnvelope) return responseEnvelope
  return undefined
}

function isDirectFailure(response: AnyRecord) {
  return response.ok === false || response.success === false || response.found === false || Boolean(response.error)
}

function responseIndicatesFailedOutcome(response: AnyRecord | undefined): boolean {
  if (!response) return false
  if (isDirectFailure(response)) return true
  if (recordStatusIndicatesFailure(response)) return true

  const task = record(response.task)
  if (task && recordStatusIndicatesFailure(task)) return true

  const result = record(response.result)
  return Boolean(result && (isDirectFailure(result) || recordStatusIndicatesFailure(result)))
}

function recordStatusIndicatesFailure(value: AnyRecord) {
  const status = (stringValue(value.status) ?? '').toLowerCase()
  if (status === 'failed' || status === 'cancelled' || status === 'error') return true
  const returncode = numberValue(value.returncode)
  return typeof returncode === 'number' && returncode !== 0
}

function failureBlocks(response: AnyRecord): ToolDisplayBlock[] {
  const error = response.error
  if (typeof error === 'string') return [{ type: 'error', content: error }]
  const recordError = record(error)
  if (recordError) {
    const message = stringValue(recordError.message) || JSON.stringify(recordError, null, 2)
    return [{ type: 'error', content: message }]
  }
  if (response.found === false) return [{ type: 'error', content: 'Requested item was not found.' }]
  return [{ type: 'error', content: 'The tool call failed.' }]
}

function waitingBlocks(args: AnyRecord): ToolDisplayBlock[] {
  const items = Object.entries(args)
    .filter(([, value]) => value !== undefined && value !== null && typeof value !== 'object')
    .map(([label, value]) => ({ label, value: String(value) }))
  if (!items.length) return [{ type: 'text', content: 'Waiting for result.' }]
  return [{ type: 'kv', items }]
}

function fallbackBlocks(response: AnyRecord | undefined, args: AnyRecord): ToolDisplayBlock[] {
  if (response) return [{ type: 'code', content: JSON.stringify(response, null, 2), language: 'json' }]
  return waitingBlocks(args)
}

function kvFrom(source: AnyRecord | undefined, keys: string[]): ToolDisplayBlock[] {
  if (!source) return []
  const items = keys
    .map((key) => ({ label: labelize(key), value: stringValue(source[key]) }))
    .filter((item): item is { label: string; value: string } => Boolean(item.value))
  return items.length ? [{ type: 'kv', items }] : []
}

function lineRange(response: AnyRecord | undefined) {
  const start = numberValue(response?.start_line)
  const end = numberValue(response?.end_line)
  if (typeof start !== 'number' || typeof end !== 'number') return undefined
  const range = start === end ? `line ${start}` : `lines ${start}-${end}`
  const total = numberValue(response?.total_lines)
  return typeof total === 'number' ? `${range} of ${total}` : range
}

function languageFromPath(path?: string) {
  const extension = path?.split('.').pop()?.toLowerCase()
  if (!extension) return 'text'
  const map: Record<string, string> = {
    js: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    py: 'python',
    md: 'markdown',
    json: 'json',
    vue: 'vue',
    sh: 'bash',
    zsh: 'bash',
    css: 'css',
    html: 'html',
    yaml: 'yaml',
    yml: 'yaml',
  }
  return map[extension] ?? extension
}

function labelize(value: string) {
  return value.replaceAll('_', ' ').replace(/^\w/, (match) => match.toUpperCase())
}

function record(value: unknown): AnyRecord | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  return value as AnyRecord
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (typeof item === 'string') return item
      const itemRecord = record(item)
      return stringValue(itemRecord?.name) || stringValue(itemRecord?.title) || stringValue(itemRecord?.filename)
    })
    .filter((item): item is string => Boolean(item))
}

function stringValue(value: unknown): string | undefined {
  if (typeof value === 'string' && value) return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function booleanValue(value: unknown): boolean {
  return value === true
}
