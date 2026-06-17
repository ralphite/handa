---
name: chat-session-analysis
description: "Read, reconstruct, and analyze Handa chat session data from local storage. Use when asked to inspect a Handa chat/session URL or id, debug a conversation, explain what happened in a run, compare sessions, trace parent/child agent runs, review stored turns, steps, runtime events, tasks, artifacts, attachments, recent session usage, or diagnose why a Handa session failed or produced a result."
---

# Chat Session Analysis

Use this skill to inspect Handa session data from local storage and reconstruct what happened in a conversation or agent run.

## Principles

- Work read-only by default. Do not delete, archive, rename, mutate SQLite rows, edit `session.json`, rewrite events, or remove artifacts unless the user explicitly asks.
- Identify the Handa storage root first. Use the user-provided root, Web API `--handa-dir`, or `HANDA_STORAGE_ROOT`; otherwise default to `~/.handa`. Do not fall back to the current project or Handa source checkout as storage.
- Extract `session_id` from URLs such as `http://127.0.0.1:8086/?session_id=...` before querying storage.
- Preserve evidence in the answer: cite session id, turn id, step sequence, task id, artifact filename, and relevant timestamps.
- If multiple matching sessions exist, show likely candidates and ask the user to choose before deep analysis.
- Stop once the session-level root cause is supported. Do not keep chasing product implementation details unless the user asked for implementation diagnosis.

## Fast Path

Prefer the bundled read-only snapshot helper before writing custom SQL. Resolve the script relative to this `SKILL.md` path returned by `skills_read`.

Inspect one or more sessions:

```bash
python "<skill_dir>/scripts/session_snapshot.py" \
  "http://127.0.0.1:8086/?session_id=20260612-130500-zeb3f5" \
  --raw-events --artifacts --max-steps 120
```

List recent direct session-analysis prompts:

```bash
python "<skill_dir>/scripts/session_snapshot.py" --recent-analysis 20
```

Use custom SQL only when the helper output is insufficient, the question needs a special aggregation, or the script is unavailable.

## Storage Map

Expected root layout:

- `<storage_root>/handa.sqlite3`: Web UI index and projected timeline.
- `<storage_root>/sessions/<session_id>/session.json`: native session metadata and state.
- `<storage_root>/sessions/<session_id>/runtime/<runtime>/events.jsonl`: raw runtime events, usually `native`.
- `<storage_root>/sessions/<session_id>/artifacts/`: saved artifacts plus `*.metadata.json`.
- `<storage_root>/sessions/<session_id>/attachments/`: uploaded files for turns.
- `<storage_root>/sessions/<session_id>/tasks/<task_id>/task.json`: background task and agent-run state.
- `<storage_root>/sessions/<session_id>/tasks/<task_id>/stdout.log`: task worker log.
- `<storage_root>/sessions/<session_id>/tasks/task_events.jsonl`: task lifecycle events.

Important SQLite tables:

- `web_sessions`: session metadata, project, agent, runtime, parent ids, fork/archive/delete/unread/star state, `created_at`. It does not currently have `updated_at`.
- `web_turns`: user inputs, trigger kind, status, model config, lifecycle timestamps, final text, errors, and token fields `input_token_count`, `output_token_count`, `total_token_count`.
- `web_steps`: projected timeline events ordered by `session_seq`; current payload shapes are usually:
  - `agent_text`: `{text, partial, final}`
  - `tool_call`: `{id, name, args, partial_args, will_continue}`
  - `tool_response`: `{id, name, response, scheduling, will_continue}`
  - `error`: `{error_type, error_message, ...}`
- `web_turn_attachments`: attachment metadata and storage path.
- `web_projects`: project id to root path mapping.

## Source Selection

Use sources in this order, but fall through when data is missing:

1. `handa.sqlite3` for session, turn, project, and projected step indexes.
2. `tasks/*.json` and `tasks/task_events.jsonl` for web-turn lifecycle, background tasks, child sessions, and stale/restarted invocations.
3. Raw runtime events in `runtime/*/events.jsonl` when projected `web_steps` are missing, empty, truncated, or the session is still running.
4. Artifacts only when the question depends on final reports, plans, saved verification, or the final answer cites them.
5. `stdout.log` only when `task.json` and runtime events do not explain worker failure.

Recent usage showed common traps:

- Running sessions can have `web_steps` count `0` while `runtime/native/events.jsonl` contains live tool calls. Treat raw events as authoritative fallback.
- A session's `tasks/` directory can contain older invocation records that are not present in the current `web_turns` rows. Report this as lifecycle evidence, not as a contradiction.
- Project file tools may be restricted to the project root. To read `~/.handa` session files or artifacts, use `commands_run` with Python/read-only shell commands, not `files_read`.
- Do not assume older column names such as `token_input`, `input_tokens`, `output_tokens`, or `updated_at` on `web_sessions`. Use exact current names or `pragma table_info(...)`.
- Do not assume `payload_json` contains `tool_calls` / `tool_responses` arrays. Current projections are usually flat objects with `name`, `args`, or `response`.
- If a command output is truncated, rerun a narrower query instead of dumping all steps or full artifacts.

## Workflow

1. Resolve storage root and verify `handa.sqlite3` / `sessions/` exist.
2. Locate the target session.
   - If a session URL or id is given, inspect it directly.
   - If only a title, project, or time is given, query recent matching `web_sessions` and `web_turns`.
3. Run the snapshot helper for the target id(s). Include `--raw-events` for running, failed, or empty-step sessions; include `--artifacts` when comparing outputs or reports.
4. Read the session summary: metadata, project root, runtime, status, parent ids, turns, token usage, errors.
5. Reconstruct the visible timeline from `web_steps`; correlate each step to `turn_id`.
6. Follow tasks and child runs through `task.json` `child_session_id`, `web_sessions.parent_session_id`, and `parent_task_id`.
7. Inspect artifacts only when they affect the answer.
8. Produce a compact report with conclusion, evidence, timeline, root cause, and uncertainty.

## Custom SQL Guardrails

When writing custom scripts, first introspect schema if you need columns not listed above:

```bash
python - <<'PY'
from pathlib import Path
import os, sqlite3
root = Path(os.environ.get("HANDA_STORAGE_ROOT") or "~/.handa").expanduser().resolve()
db = sqlite3.connect((root / "handa.sqlite3").as_uri() + "?mode=ro", uri=True)
for table in ("web_sessions", "web_turns", "web_steps", "web_projects"):
  print("==", table)
  for row in db.execute(f"pragma table_info({table})"):
    print(row[1])
PY
```

Extract tool names from current `web_steps` payloads:

```bash
SESSION_ID="replace-me" python - <<'PY'
from pathlib import Path
import json, os, sqlite3
sid = os.environ["SESSION_ID"]
root = Path(os.environ.get("HANDA_STORAGE_ROOT") or "~/.handa").expanduser().resolve()
db = sqlite3.connect((root / "handa.sqlite3").as_uri() + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
for row in db.execute("""
  select e.seq, e.session_seq, e.kind, e.summary, e.payload_json
  from web_steps e join web_turns t on t.id = e.turn_id
  where t.session_id = ?
  order by coalesce(e.session_seq, e.seq), e.seq
""", (sid,)):
  payload = json.loads(row["payload_json"])
  print(row["session_seq"], row["kind"], payload.get("name"), row["summary"])
PY
```

Read storage artifacts outside the project root:

```bash
SESSION_ID="replace-me" python - <<'PY'
from pathlib import Path
import os
sid = os.environ["SESSION_ID"]
root = Path(os.environ.get("HANDA_STORAGE_ROOT") or "~/.handa").expanduser().resolve()
for path in sorted((root / "sessions" / sid / "artifacts").glob("*")):
  if path.is_file() and not path.name.startswith("."):
    print(path, path.stat().st_size)
    print(path.read_text(encoding="utf-8")[:2000])
PY
```

## Analysis Checklist

- Session identity: id, title, project root, agent id, runtime, created time, status.
- Parent/child chain: `parent_session_id`, `parent_task_id`, task `child_session_id`, session state parent fields.
- User-visible conversation: each `web_turns.input_text`, `trigger_kind`, `status`, `final_text`, error fields.
- Execution timeline: `web_steps.session_seq`, kind, summary, payload highlights.
- Tool behavior: tool call names/args, command outputs, failed tools, repeated or redundant tool use.
- Artifacts: saved filenames, versions, metadata, and whether final claims are supported by artifact content.
- Attachments: filename, MIME type, byte count, and which turn used them.
- Tasks: kind, status, prompt/context, child session, result, stdout tail, lifecycle events.
- Failures: first error, subsequent retries, cancellation, stale worker cancellation, missing project/session, model/tool errors.
- Data gaps: missing `handa.sqlite3`, missing raw runtime events, deleted session, child session without Web metadata, truncated command output.

## Report Shape

Keep the final report concise:

1. **Conclusion**: what happened and whether the session/run achieved the user goal.
2. **Evidence**: session id, turn ids, key step sequences, task ids, artifacts, timestamps.
3. **Timeline**: only meaningful events, not every raw event.
4. **Issues**: root cause or likely failure point, with evidence.
5. **Remaining Risk**: missing data or uncertainty.
