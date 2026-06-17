---
name: qa
description: Systematically plan, delegate, and report exploratory QA for a web application. Use when asked to "dogfood", "QA", "exploratory test", "find issues", "bug hunt", "test this app/site/platform", or review the quality of a web application. Browser work is delegated to Handa's dedicated browser agent.
allowed-tools: run_agent, agents_read_run_result, agents_list_run_artifacts, agents_read_run_artifact, artifacts_save_text
---

# QA

Systematically explore a web application, find issues, and produce a concise report with reproducible evidence.

## Setup

Only the **Target URL** is required. Everything else has sensible defaults; use them unless the user explicitly provides an override.

| Parameter | Default | Example override |
|-----------|---------|-----------------|
| **Target URL** | _(required)_ | `vercel.com`, `http://localhost:3000` |
| **Scope** | Full app | `Focus on the billing page` |
| **Authentication** | None | `Sign in to user@example.com` |
| **Output** | QA report artifact | `Save as checkout-qa.md` |

If the user says something like "dogfood vercel.com", start immediately with defaults. Do not ask clarifying questions unless authentication is mentioned but credentials are missing.

## Browser Delegation

All browser automation must go through Handa's dedicated `browser` sub-agent:

- Use `run_agent` with `agent_id="browser"` for every browser task.
- Do not use `agent-browser`, `npx agent-browser`, `browser-use`, Playwright, Chrome DevTools, or direct `browser_*` tools from the parent QA agent.
- Keep each browser-agent prompt focused: include the target URL, scope, exact workflow to test, evidence to collect, and artifact names to save.
- After starting `run_agent`, if the next QA step depends on its result, stop the current turn and wait for the system task notification. Do not poll task status or logs just to wait.
- Use `agents_read_run_result`, `agents_list_run_artifacts`, and `agents_read_run_artifact` only after the browser-agent task completes.

The browser agent owns the live Browser Environment in its child session. Ask it to use `browser_screenshot` for visible evidence and `artifacts_save_text` for structured findings, reproduction steps, inspected states, and any limitations.

## Workflow

```
1. Initialize     Decide target, scope, report artifact name, and issue counter
2. Calibrate      Read the issue taxonomy
3. Orient         Delegate initial navigation and app map to browser agent
4. Explore        Delegate focused user workflows to browser agent
5. Document       Convert child-agent evidence into report issues immediately
6. Wrap up        Verify counts, save final report artifact, summarize findings
```

### 1. Initialize

Choose a report artifact name such as `qa-report.md`. Copy the report shape from [templates/qa-report-template.md](templates/qa-report-template.md) into your working notes and fill in:

- Target URL
- Scope
- Browser child session ids as they become available
- Issue counter starting at `ISSUE-001`

### 2. Calibrate

Read [references/issue-taxonomy.md](references/issue-taxonomy.md) before exploration. Use it to classify severity and category.

### 3. Orient

Delegate the initial orientation to the browser agent:

```text
Run the browser agent on {TARGET_URL}.
Goal: orient for exploratory QA of {SCOPE}.
Actions:
- Open the target URL.
- Identify top-level navigation, primary workflows, forms, destructive actions, and visible state.
- Capture a browser screenshot for the initial view.
- Save an artifact named qa-orientation.md with current URL/title, page map, notable states, and suggested workflows to test.
Return the artifact name, child session id, and any immediate issues.
```

When the task completes, read the result and orientation artifact. Use it to plan focused exploration passes.

### 4. Explore

Delegate one focused workflow or area per browser-agent task. Good prompts include:

- The exact workflow or page area to test.
- Inputs to try, including empty/invalid/boundary values.
- Expected evidence artifacts, such as `qa-checkout-flow.md` or `qa-settings-forms.md`.
- Instructions to stop and save a separate `qa-issue-NNN.md` artifact immediately when a reproducible issue is found.

Example:

```text
Run exploratory QA for the Settings workflow on {TARGET_URL}.
Use the existing browser session if available.
Test navigation, form editing, validation, save/cancel behavior, empty inputs, and visible error states.
For each reproducible issue:
- Capture a browser screenshot at the broken state.
- Save an artifact named qa-issue-00N.md with severity, category, URL, expected/actual behavior, and exact repro steps.
Also save qa-settings-pass.md summarizing what was tested and remaining risk.
```

### 5. Document Issues

Steps 4 and 5 happen together. When the browser agent reports an issue, document it in the QA report before starting the next exploration pass.

Every issue must include:

- Issue id and short title
- Severity and category
- URL or route
- Browser child session id
- Evidence artifact name(s)
- Screenshot/browser preview reference when available
- Expected vs actual behavior
- Numbered repro steps
- Remaining uncertainty, if any

If the browser agent cannot preserve a separate screenshot for each step, use its saved issue artifact and final browser preview as evidence, and state that limitation in the issue.

### 6. Wrap Up

Aim for 5-10 well-documented issues unless the user asked for a lighter pass. Depth of evidence matters more than count.

Before finalizing:

1. Confirm every `ISSUE-` block appears in the severity totals.
2. Save the final report with `artifacts_save_text`.
3. Summarize total issues, severity breakdown, most critical findings, tested areas, and remaining risk.

## Guidance

- **Delegate browser work.** Parent QA agent plans, reads child results, and writes the final report; browser agent operates the app.
- **Repro is everything.** Do not report an issue unless the browser agent reproduced it or the broken state is visible on load.
- **Document immediately.** Append issues to the report as they are found so interruptions do not lose findings.
- **Test like a user.** Prioritize realistic end-to-end workflows over synthetic edge cases.
- **Stay scoped.** Spend more time on core user flows and less on peripheral pages.
- **Do not read the target app's source code.** QA findings must come from browser-observed behavior, not implementation inspection.
- **Be honest about tool limits.** If console logs, network traces, videos, downloads, uploads, or multi-step screenshots are unavailable through the browser agent, say so in the report rather than inventing evidence.

## References

| Reference | When to Read |
|-----------|--------------|
| [references/issue-taxonomy.md](references/issue-taxonomy.md) | Start of session; calibrate what to look for, severity levels, and exploration checklist |

## Templates

| Template | Purpose |
|----------|---------|
| [templates/qa-report-template.md](templates/qa-report-template.md) | Use as the final report structure |
