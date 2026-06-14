import type { AgentSession, InvocationDetailEvent, InvocationTimelineItem, ProjectNavItem } from '../types'

const completedDetailEvents: InvocationDetailEvent[] = [
  {
    seq: 1,
    kind: 'tool_call',
    summary: 'Read src/agents/handa_adk/ralph/agent.py',
    payload: {
      id: 'call-read-ralph-agent',
      name: 'files_read',
      args: {
        path: 'src/agents/handa_adk/ralph/agent.py',
      },
    },
    rawEvent: { type: 'tool_call' },
    createdAt: '2026-05-17T09:00:00Z',
  },
  {
    seq: 2,
    kind: 'tool_response',
    summary: 'Finished files_read',
    payload: {
      id: 'call-read-ralph-agent',
      name: 'files_read',
      response: {
        ok: true,
        path: 'src/agents/handa_adk/ralph/agent.py',
        start_line: 1,
        end_line: 120,
      },
    },
    rawEvent: { type: 'tool_response' },
    createdAt: '2026-05-17T09:00:12Z',
  },
  {
    seq: 3,
    kind: 'tool_call',
    summary: 'Ran pytest tests/test_ralph_loop.py -q',
    payload: {
      id: 'call-test-ralph-loop',
      name: 'commands_run',
      args: {
        command: 'pytest tests/test_ralph_loop.py -q',
      },
    },
    rawEvent: { type: 'tool_call' },
    createdAt: '2026-05-17T09:01:00Z',
  },
  {
    seq: 4,
    kind: 'tool_response',
    summary: 'Command passed: pytest tests/test_ralph_loop.py -q',
    payload: {
      id: 'call-test-ralph-loop',
      name: 'commands_run',
      response: {
        command: 'pytest tests/test_ralph_loop.py -q',
        returncode: 0,
        stdout: '71 passed, 1 skipped',
      },
    },
    rawEvent: { type: 'tool_response' },
    createdAt: '2026-05-17T09:01:18Z',
  },
]

const completedTimelineItems: InvocationTimelineItem[] = [
  {
    seq: 2,
    kind: 'tool',
    summary: 'Read src/agents/handa_adk/ralph/agent.py',
    createdAt: '2026-05-17T09:00:12Z',
    status: 'done',
    toolCallId: 'call-read-ralph-agent',
    toolName: 'files_read',
    responseSummary: 'Finished files_read',
    payload: {
      call: {
        id: 'call-read-ralph-agent',
        name: 'files_read',
        args: {
          path: 'src/agents/handa_adk/ralph/agent.py',
        },
      },
      response: {
        id: 'call-read-ralph-agent',
        name: 'files_read',
        response: {
          ok: true,
          path: 'src/agents/handa_adk/ralph/agent.py',
          start_line: 1,
          end_line: 120,
        },
      },
    },
    rawEvent: { type: 'tool_response' },
  },
  {
    seq: 4,
    kind: 'tool',
    summary: 'pytest tests/test_ralph_loop.py -q',
    createdAt: '2026-05-17T09:01:18Z',
    status: 'done',
    toolCallId: 'call-test-ralph-loop',
    toolName: 'commands_run',
    responseSummary: 'Command passed',
    payload: {
      call: {
        id: 'call-test-ralph-loop',
        name: 'commands_run',
        args: {
          command: 'pytest tests/test_ralph_loop.py -q',
        },
      },
      response: {
        id: 'call-test-ralph-loop',
        name: 'commands_run',
        response: {
          command: 'pytest tests/test_ralph_loop.py -q',
          returncode: 0,
          stdout: '71 passed, 1 skipped',
        },
      },
    },
    rawEvent: { type: 'tool_response' },
  },
]

export const sessions: AgentSession[] = [
  {
    id: 'session-raf-agent',
    latestInvocationId: 'run-raf-agent',
    title: 'Fix RAF Agent static output',
    createdAt: '2026-05-10T09:00:00Z',
    projectId: 'handa',
    projectRoot: '/Users/yadong/dev2/handa',
    branch: 'codex/ralph-loop-runner',
    status: 'done',
    elapsed: '8m 18s',
    messages: [
      {
        id: 'm1',
        role: 'user',
        createdAt: '2026-05-10T09:00:00Z',
        body:
          'User input, draft plan already exists in current session. Please fix Ralph loop: plan must come from planner, and builder/verifier should only start after user confirmation.',
      },
      {
        id: 'm2',
        role: 'assistant',
        createdAt: '2026-05-10T09:08:18Z',
        invocationId: 'run-raf-agent',
        elapsed: '8m 18s',
        status: 'done',
        detailEvents: completedDetailEvents,
        timelineItems: completedTimelineItems,
        body:
          'Core flow fixed: Ralph entry no longer hardcodes plan, added LLM planner, and passes confirmed plan to loop runner.\n\nKey changes:\n\n- Added `ralph_planner` config.\n- Passed user confirmed plan to loop runner.\n- Verifications:\n  - planner output no longer uses static template.\n  - builder/verifier only start after confirmation.',
      },
    ],
    detailEvents: completedDetailEvents,
    invocationSteps: [
      {
        id: 'step-1',
        title: 'Planning',
        status: 'done',
        detail: 'Read existing design docs and ralph agent implementation to locate the source of the static plan.',
      },
      {
        id: 'step-2',
        title: 'Implementation',
        status: 'done',
        detail: 'Added planner config, parent session saves draft / final plan.',
      },
      {
        id: 'step-3',
        title: 'Verification',
        status: 'done',
        detail: 'Completed runner JSON parsing test, and verified pytest passes.',
      },
    ],
    artifacts: [
      {
        id: 'artifact-design',
        title: 'DESIGN.md',
        subtitle: 'Document · MD',
        kind: 'markdown',
        blocks: [
          {
            type: 'heading',
            text: 'Ralph Loop Design',
          },
          {
            type: 'heading',
            text: 'Purpose',
          },
          {
            type: 'paragraph',
            text:
              'ralph loop is the minimal implementation of workflow / graph workflow support in Handa. It verifies one thing: can an existing ConfigBasedAgent be composed and executed cyclically as a workflow node, leaving a reproducible process via session / artifact.',
          },
          {
            type: 'heading',
            text: 'Core Idea',
          },
          {
            type: 'paragraph',
            text:
              'The key of Ralph loop is not letting an agent continue in the same long conversation indefinitely, but executing each round with a new agent session, persisting necessary state via artifact / result.',
          },
          {
            type: 'code',
            language: 'text',
            text:
              'user input\n  -> RalphAgent planning phase\n  -> user confirmation\n  -> builder node\n  -> verifier node\n  -> done: stop\n  -> not done: feedback/resources -> next builder round',
          },
          {
            type: 'heading',
            text: 'Execution Model',
          },
          {
            type: 'list',
            items: [
              'The entry point is a parent custom ADK agent session, which only handles the workflow state machine.',
              'The real builder / verifier runs in a new child session.',
              'loop-level result / report must be saved in the parent session.',
              'Can deterministically stop when reaching max rounds.',
            ],
          },
        ],
      },
      {
        id: 'artifact-report',
        title: 'README.md',
        subtitle: 'Document · MD',
        kind: 'report',
        blocks: [
          {
            type: 'heading',
            text: 'Run Report',
          },
          {
            type: 'paragraph',
            text:
              'This run completed the loop from planner to loop runner. Users can see the task status, final plan, test results, and related artifacts in the parent session.',
          },
          {
            type: 'code',
            language: 'bash',
            text: '.venv/bin/python -m pytest -q\n# 71 passed, 1 skipped',
          },
          {
            type: 'list',
            items: [
              'Verified plan comes from planner output, not static template.',
              'Verified plan can be modified before confirmation.',
              'Verified loop is executed using the final plan after confirmation.',
            ],
          },
        ],
      },
      {
        id: 'artifact-changelog',
        title: 'CHANGELOG.md',
        subtitle: 'Document · MD',
        kind: 'markdown',
        blocks: [
          {
            type: 'heading',
            text: 'Changelog',
          },
          {
            type: 'list',
            items: [
              'Added ralph_planner.agent.json.',
              'Updated RalphAgent workflow state machine.',
              'Added Ralph loop related tests.',
            ],
          },
        ],
      },
    ],
    fileChanges: [
      {
        path: 'src/agents/handa_adk/ralph/agent.py',
        additions: 243,
        deletions: 29,
      },
      {
        path: 'src/agents/handa_adk/ralph/ralph_planner.agent.json',
        additions: 9,
        deletions: 0,
      },
      {
        path: 'tests/test_ralph_loop.py',
        additions: 220,
        deletions: 4,
      },
    ],
  },
  {
    id: 'session-verifier',
    latestInvocationId: 'run-verifier',
    title: 'Update verifier acceptance rules',
    createdAt: '2026-05-17T09:03:00Z',
    projectId: 'handa',
    projectRoot: '/Users/yadong/dev2/handa',
    branch: 'codex/verifier-acceptance',
    status: 'running',
    elapsed: '3m 42s',
    messages: [
      {
        id: 'm3',
        role: 'user',
        createdAt: '2026-05-17T09:03:00Z',
        body: 'Change verifier acceptance standard to be based on real artifact and test output, rather than just reading assistant summary.',
      },
      {
        id: 'm4',
        role: 'assistant',
        createdAt: '2026-05-17T09:03:20Z',
        invocationId: 'run-verifier',
        elapsed: '3m 42s',
        status: 'running',
        detailEvents: [
          {
            seq: 1,
            kind: 'tool_call',
            summary: 'Ran rg verifier',
            payload: { name: 'commands_run' },
            rawEvent: {},
            createdAt: '2026-05-17T09:03:00Z',
          },
        ],
        body: 'Splitting verifier input: plan, changed files, task output, artifact manifest will be passed as separate fields.',
      },
    ],
    detailEvents: [
      {
        seq: 1,
        kind: 'tool_call',
        summary: 'Ran rg verifier',
        payload: { name: 'commands_run' },
        rawEvent: {},
        createdAt: '2026-05-17T09:03:00Z',
      },
    ],
    invocationSteps: [
      {
        id: 'step-4',
        title: 'Map inputs',
        status: 'done',
        detail: 'Confirmed verifier needs to read plan, report, test output.',
      },
      {
        id: 'step-5',
        title: 'Patch verifier prompt',
        status: 'running',
        detail: 'Converging prompt output JSON schema.',
      },
      {
        id: 'step-6',
        title: 'Browser QA',
        status: 'pending',
        detail: 'Waiting for implementation to trigger a real run from Handa Web.',
      },
    ],
    artifacts: [
      {
        id: 'artifact-verifier-plan',
        title: 'verification.plan.md',
        subtitle: 'Artifact · Plan',
        kind: 'plan',
        blocks: [
          {
            type: 'heading',
            text: 'Verifier Contract',
          },
          {
            type: 'paragraph',
            text:
              'Verifier only judges observable facts: file changes, command output, artifact content, and user-confirmed plan.',
          },
          {
            type: 'list',
            items: [
              "Prohibited to pass just based on builder's natural language summary.",
              'Must provide a blocking issue or explicit pass.',
              'When failing, output actionable feedback for the next builder round.',
            ],
          },
        ],
      },
    ],
    fileChanges: [
      {
        path: 'src/agents/handa_adk/verifier/agent.py',
        additions: 74,
        deletions: 18,
      },
      {
        path: 'tests/test_verifier_contract.py',
        additions: 96,
        deletions: 0,
      },
    ],
  },
  {
    id: 'session-sandbox',
    title: 'Research project execution boundary',
    createdAt: '2026-05-10T11:00:00Z',
    projectId: 'handa',
    projectRoot: '/Users/yadong/dev2/handa',
    branch: 'codex/project-boundary',
    status: 'idle',
    elapsed: '1m 04s',
    messages: [
      {
        id: 'm5',
        role: 'user',
        createdAt: '2026-05-10T11:00:00Z',
        body: "Outline Handa's project boundary for executing development tasks.",
      },
      {
        id: 'm6',
        role: 'assistant',
        createdAt: '2026-05-10T11:01:04Z',
        body: 'The preliminary solution is to make project selection explicit, and session storage only records references and artifact manifest.',
      },
    ],
    invocationSteps: [
      {
        id: 'step-7',
        title: 'Research',
        status: 'done',
        detail: 'Organized boundaries of project / storage / child session.',
      },
    ],
    artifacts: [
      {
        id: 'artifact-sandbox',
        title: 'RESEARCH.md',
        subtitle: 'Document · MD',
        kind: 'markdown',
        blocks: [
          {
            type: 'heading',
            text: 'Project Execution Boundary',
          },
          {
            type: 'paragraph',
            text:
              "In the Web product, a project must be explicitly selected. The system shouldn't fallback to the Handa source directory by default, to avoid accidentally modifying the product code or polluting session reproduction.",
          },
        ],
      },
    ],
    fileChanges: [],
  },
]

export const projects: ProjectNavItem[] = [
  {
    id: 'handa',
    name: 'handa',
    path: '/Users/yadong/dev2/handa',
    sessions: sessions.map((session) => ({
      id: session.id,
      title: session.title,
      createdAt: session.createdAt,
      status: session.status,
      attention: session.id === 'session-sandbox' ? 'error' : undefined,
    })),
  },
  {
    id: 'claude-code',
    name: 'claude-code',
    path: '/Users/yadong/dev2/claude-code',
    sessions: [],
  },
  {
    id: 'vibedoc',
    name: 'vibedoc',
    path: '/Users/yadong/dev2/vibedoc',
    sessions: [],
  },
]
