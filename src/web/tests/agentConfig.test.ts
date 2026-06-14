import { describe, expect, it } from 'vitest'
import {
  formatAgentConfigJson,
  groupConfigTools,
  isAgentConfigArtifact,
  parseAgentConfig,
  renderSectionTemplate,
  resolveInstructionSections,
  resolveSkills,
  resolveSubagents,
} from '../src/agentConfig'
import type { BackendAgentCatalog } from '../src/api/types'

const catalog: BackendAgentCatalog = {
  tools: [
    { name: 'files_read', namespace: 'files', definition: 'files_read(path)\nRead a file.' },
    { name: 'files_list', namespace: 'files', definition: 'files_list()\nList files.' },
    { name: 'run_agent', namespace: '', definition: 'run_agent(config_name)' },
    { name: 'tasks_list', namespace: 'tasks', definition: '' },
  ],
  instruction_sections: [
    { name: 'identity', title: 'Identity', template: '# Identity\n\nYou are {agent_name}, serving {project_name}.' },
    { name: 'tool_usage', title: 'Tool Usage', template: '# Tool Usage' },
  ],
  skills: [
    { name: 'qa', skill_name: 'qa', description: 'Exploratory QA.', source: 'system' },
    { name: 'vcs-jj', skill_name: 'jj-workflows', description: 'Use jj.', source: 'system' },
  ],
  agents: [
    { id: 'browser', runtime: 'adk', label: 'browser', description: 'Drives a headless browser.' },
    { id: 'orca_adk', runtime: 'adk', label: 'Orca ADK', description: '' },
  ],
  model_configs: [
    { id: 'gemini-3.1-pro-high', label: 'Gemini 3.1 Pro High', description: '', context_window: 1000000 },
  ],
}

describe('isAgentConfigArtifact', () => {
  it('matches the presenter projection of *.agent.json artifacts', () => {
    expect(isAgentConfigArtifact({ kind: 'agent', filetype: 'json' })).toBe(true)
    expect(isAgentConfigArtifact({ kind: 'report', filetype: 'json' })).toBe(false)
    expect(isAgentConfigArtifact({ kind: 'agent', filetype: 'md' })).toBe(false)
    expect(isAgentConfigArtifact({ kind: 'agent' })).toBe(false)
  })
})

describe('parseAgentConfig', () => {
  it('parses a full config', () => {
    const parsed = parseAgentConfig(
      JSON.stringify({
        name: 'analyst',
        description: 'Analyzes things.',
        tools: ['files_read'],
        skills: ['qa'],
        instruction_sections: ['identity'],
        custom_instruction: 'Focus on runtime modules.',
      }),
    )
    expect(parsed).toEqual({
      name: 'analyst',
      description: 'Analyzes things.',
      modelConfigId: null,
      tools: ['files_read'],
      skills: ['qa'],
      subagents: [],
      instructionSections: ['identity'],
      customInstruction: 'Focus on runtime modules.',
    })
  })

  it('parses subagents and defaults them to empty', () => {
    expect(parseAgentConfig(JSON.stringify({ name: 'a', subagents: ['self', 'browser'] }))?.subagents).toEqual([
      'self',
      'browser',
    ])
    expect(parseAgentConfig(JSON.stringify({ name: 'a' }))?.subagents).toEqual([])
    expect(parseAgentConfig(JSON.stringify({ name: 'a', subagents: 'browser' }))).toBeNull()
  })

  it('defaults missing optional fields and reads legacy model fields', () => {
    const parsed = parseAgentConfig(JSON.stringify({ name: 'orca_adk', model_config_id: 'gemini-3.1-pro-high' }))
    expect(parsed).toMatchObject({
      name: 'orca_adk',
      description: '',
      modelConfigId: 'gemini-3.1-pro-high',
      tools: [],
      skills: [],
      instructionSections: [],
      customInstruction: null,
    })
    expect(parseAgentConfig(JSON.stringify({ name: 'legacy', model: 'gemini-3.1-flash' }))?.modelConfigId).toBe(
      'gemini-3.1-flash',
    )
  })

  it('treats blank custom_instruction as absent', () => {
    expect(parseAgentConfig(JSON.stringify({ name: 'a', custom_instruction: '  ' }))?.customInstruction).toBeNull()
  })

  it('rejects malformed payloads', () => {
    expect(parseAgentConfig('not json')).toBeNull()
    expect(parseAgentConfig('"just a string"')).toBeNull()
    expect(parseAgentConfig('[]')).toBeNull()
    expect(parseAgentConfig(JSON.stringify({ description: 'missing name' }))).toBeNull()
    expect(parseAgentConfig(JSON.stringify({ name: '  ' }))).toBeNull()
    expect(parseAgentConfig(JSON.stringify({ name: 'a', tools: 'files_read' }))).toBeNull()
    expect(parseAgentConfig(JSON.stringify({ name: 'a', tools: [1] }))).toBeNull()
  })
})

describe('groupConfigTools', () => {
  it('groups by catalog namespace in first-appearance order', () => {
    const groups = groupConfigTools(['run_agent', 'files_read', 'tasks_list', 'files_list'], catalog)
    expect(groups.map((group) => group.label)).toEqual(['core', 'files', 'tasks'])
    expect(groups[1].tools.map((tool) => tool.name)).toEqual(['files_read', 'files_list'])
    expect(groups[1].tools[0].definition).toContain('Read a file.')
    expect(groups[2].tools[0].definition).toBeNull()
  })

  it('collects unregistered tools into a trailing danger group', () => {
    const groups = groupConfigTools(['files_read', 'browser_use'], catalog)
    const last = groups[groups.length - 1]
    expect(last.unregistered).toBe(true)
    expect(last.tools).toEqual([{ name: 'browser_use', definition: null, known: false }])
  })

  it('returns one unlabeled group without a catalog', () => {
    const groups = groupConfigTools(['files_read', 'browser_use'], null)
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('')
    expect(groups[0].tools.every((tool) => tool.known)).toBe(true)
    expect(groupConfigTools([], null)).toEqual([])
  })
})

describe('resolveInstructionSections', () => {
  it('resolves titles and renders template params', () => {
    const sections = resolveInstructionSections(['identity', 'tool_usage'], 'analyst', catalog)
    expect(sections[0].title).toBe('Identity')
    expect(sections[0].body).toContain('You are ANALYST, serving handa.')
    expect(sections[1].known).toBe(true)
  })

  it('flags sections missing from the catalog, but not when the catalog is absent', () => {
    expect(resolveInstructionSections(['nope'], 'a', catalog)[0]).toMatchObject({ known: false, body: null })
    expect(resolveInstructionSections(['nope'], 'a', null)[0]).toMatchObject({ known: true, body: null })
  })
})

describe('resolveSkills', () => {
  it('matches by directory name or frontmatter name', () => {
    const resolved = resolveSkills(['qa', 'jj-workflows'], catalog)
    expect(resolved[0]).toMatchObject({ known: true, description: 'Exploratory QA.' })
    expect(resolved[1]).toMatchObject({ known: true, name: 'jj-workflows' })
  })

  it('flags unknown skills only when the catalog is present', () => {
    expect(resolveSkills(['nope'], catalog)[0].known).toBe(false)
    expect(resolveSkills(['nope'], null)[0].known).toBe(true)
  })
})

describe('resolveSubagents', () => {
  it('resolves the self sentinel and predefined agents by id', () => {
    const resolved = resolveSubagents(['self', 'browser'], catalog)
    expect(resolved[0]).toMatchObject({ name: 'self', known: true })
    expect(resolved[0].description).toContain('child session')
    expect(resolved[1]).toMatchObject({ name: 'browser', known: true, description: 'Drives a headless browser.' })
  })

  it('flags unknown agents only when the catalog is present', () => {
    expect(resolveSubagents(['my_saved_config'], catalog)[0].known).toBe(false)
    expect(resolveSubagents(['my_saved_config'], null)[0].known).toBe(true)
  })
})

describe('renderSectionTemplate', () => {
  it('substitutes known params and leaves unknown placeholders alone', () => {
    expect(renderSectionTemplate('{agent_name} on {project_name} keeps {other}', 'analyst')).toBe(
      'ANALYST on handa keeps {other}',
    )
  })
})

describe('formatAgentConfigJson', () => {
  it('pretty-prints valid json and passes through invalid content', () => {
    expect(formatAgentConfigJson('{"name":"a"}')).toBe('{\n  "name": "a"\n}')
    expect(formatAgentConfigJson('not json')).toBe('not json')
  })
})
