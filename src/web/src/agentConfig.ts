import type { BackendAgentCatalog } from './api/types'

export interface ParsedAgentConfig {
  name: string
  description: string
  modelConfigId: string | null
  tools: string[]
  skills: string[]
  subagents: string[]
  instructionSections: string[]
  customInstruction: string | null
}

export interface AgentConfigToolDisplay {
  name: string
  definition: string | null
  known: boolean
}

export interface AgentConfigToolGroup {
  label: string
  unregistered: boolean
  tools: AgentConfigToolDisplay[]
}

export interface AgentConfigSectionDisplay {
  name: string
  title: string | null
  body: string | null
  known: boolean
}

export interface AgentConfigSkillDisplay {
  name: string
  description: string | null
  source: string | null
  known: boolean
}

export interface AgentConfigSubagentDisplay {
  name: string
  description: string | null
  known: boolean
}

export function isAgentConfigArtifact(artifact: { kind: string; filetype?: string }): boolean {
  return artifact.kind === 'agent' && artifact.filetype === 'json'
}

export function parseAgentConfig(content: string): ParsedAgentConfig | null {
  let data: unknown
  try {
    data = JSON.parse(content)
  } catch {
    return null
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return null
  const record = data as Record<string, unknown>
  if (typeof record.name !== 'string' || !record.name.trim()) return null
  const tools = stringArray(record.tools)
  const skills = stringArray(record.skills)
  const subagents = stringArray(record.subagents)
  const instructionSections = stringArray(record.instruction_sections)
  if (!tools || !skills || !subagents || !instructionSections) return null
  const customInstruction =
    typeof record.custom_instruction === 'string' && record.custom_instruction.trim()
      ? record.custom_instruction
      : null
  return {
    name: record.name,
    description: typeof record.description === 'string' ? record.description : '',
    modelConfigId: firstNonEmptyString(record.model_config_id, record.model),
    tools,
    skills,
    subagents,
    instructionSections,
    customInstruction,
  }
}

// Without a catalog every tool lands in one unlabeled group; namespaces cannot
// be derived from names alone (`run_agent` has none, `files_list` does).
export function groupConfigTools(
  toolNames: string[],
  catalog?: BackendAgentCatalog | null,
): AgentConfigToolGroup[] {
  if (!catalog) {
    if (!toolNames.length) return []
    return [
      {
        label: '',
        unregistered: false,
        tools: toolNames.map((name) => ({ name, definition: null, known: true })),
      },
    ]
  }
  const byName = new Map(catalog.tools.map((tool) => [tool.name, tool]))
  const groups: AgentConfigToolGroup[] = []
  const groupsByLabel = new Map<string, AgentConfigToolGroup>()
  const unregistered: AgentConfigToolGroup = { label: 'unregistered', unregistered: true, tools: [] }
  for (const name of toolNames) {
    const entry = byName.get(name)
    if (!entry) {
      unregistered.tools.push({ name, definition: null, known: false })
      continue
    }
    const label = entry.namespace || 'core'
    let group = groupsByLabel.get(label)
    if (!group) {
      group = { label, unregistered: false, tools: [] }
      groupsByLabel.set(label, group)
      groups.push(group)
    }
    group.tools.push({ name, definition: entry.definition || null, known: true })
  }
  if (unregistered.tools.length) groups.push(unregistered)
  return groups
}

export function resolveInstructionSections(
  sectionNames: string[],
  agentName: string,
  catalog?: BackendAgentCatalog | null,
): AgentConfigSectionDisplay[] {
  const byName = new Map((catalog?.instruction_sections ?? []).map((section) => [section.name, section]))
  return sectionNames.map((name) => {
    const entry = byName.get(name)
    if (!entry) return { name, title: null, body: null, known: !catalog }
    return {
      name,
      title: entry.title,
      body: renderSectionTemplate(entry.template, agentName),
      known: true,
    }
  })
}

export function resolveSkills(
  skillNames: string[],
  catalog?: BackendAgentCatalog | null,
): AgentConfigSkillDisplay[] {
  const entries = catalog?.skills ?? []
  return skillNames.map((name) => {
    const entry = entries.find((skill) => skill.name === name || skill.skill_name === name)
    if (!entry) return { name, description: null, source: null, known: !catalog }
    return {
      name,
      description: entry.description || null,
      source: entry.source || null,
      known: true,
    }
  })
}

// `self` is a sentinel — the agent delegating to a fresh copy of itself — so it
// has no catalog entry; everything else resolves against the agent registry,
// and an unmatched name (e.g. a saved config not in the static catalog) shows
// as unknown only when a catalog is present to check against.
export function resolveSubagents(
  subagentNames: string[],
  catalog?: BackendAgentCatalog | null,
): AgentConfigSubagentDisplay[] {
  const entries = catalog?.agents ?? []
  return subagentNames.map((name) => {
    if (name === 'self') {
      return { name, description: 'Re-runs this same agent in an isolated child session.', known: true }
    }
    const entry = entries.find((agent) => agent.id === name)
    if (!entry) return { name, description: null, known: !catalog }
    return { name, description: entry.description || entry.label || null, known: true }
  })
}

// Mirrors the render params used by the runtime when building the agent
// (config_based.py / loaders): agent_name is the upper-cased config name and
// project_name defaults to "handa". Unknown placeholders stay verbatim.
export function renderSectionTemplate(template: string, agentName: string): string {
  return template
    .replaceAll('{agent_name}', agentName.toUpperCase())
    .replaceAll('{project_name}', 'handa')
}

export function formatAgentConfigJson(content: string): string {
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content
  }
}

function stringArray(value: unknown): string[] | null {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) return null
  if (!value.every((item): item is string => typeof item === 'string')) return null
  return value
}

function firstNonEmptyString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}
