from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlparse


def main() -> int:
  parser = argparse.ArgumentParser(
      description="Read-only Handa session snapshot helper."
  )
  parser.add_argument(
      "session_ids",
      nargs="*",
      help="Session id or URL containing ?session_id=...",
  )
  parser.add_argument(
      "--root",
      default=os.environ.get("HANDA_STORAGE_ROOT") or "~/.handa",
      help="Handa storage root. Defaults to HANDA_STORAGE_ROOT or ~/.handa.",
  )
  parser.add_argument(
      "--max-steps",
      type=int,
      default=80,
      help="Maximum projected steps to print per session.",
  )
  parser.add_argument(
      "--raw-events",
      action="store_true",
      help="Also print compact raw runtime events.",
  )
  parser.add_argument(
      "--events-limit",
      type=int,
      default=80,
      help="Maximum raw runtime events to print per session.",
  )
  parser.add_argument(
      "--artifacts",
      action="store_true",
      help="List artifact files and metadata.",
  )
  parser.add_argument(
      "--recent-analysis",
      type=int,
      metavar="N",
      help="List recent direct session-analysis prompts instead of inspecting ids.",
  )
  args = parser.parse_args()

  root = Path(args.root).expanduser().resolve()
  print(f"STORAGE_ROOT {root}")
  print(f"handa.sqlite3 {root.joinpath('handa.sqlite3').exists()}")
  print(f"sessions {root.joinpath('sessions').exists()}")

  db = _connect_readonly(root / "handa.sqlite3")
  if args.recent_analysis is not None:
    _print_recent_analysis(db, args.recent_analysis)
    return 0

  if not args.session_ids:
    parser.error("provide at least one session id or use --recent-analysis N")

  for raw_session_id in args.session_ids:
    session_id = _extract_session_id(raw_session_id)
    _print_session(
        root=root,
        db=db,
        session_id=session_id,
        max_steps=max(0, args.max_steps),
        include_raw_events=args.raw_events,
        events_limit=max(0, args.events_limit),
        include_artifacts=args.artifacts,
    )
  return 0


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
  if not db_path.exists():
    raise SystemExit(f"missing sqlite database: {db_path}")
  db = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
  db.row_factory = sqlite3.Row
  return db


def _extract_session_id(value: str) -> str:
  parsed = urlparse(value)
  session_id = parse_qs(parsed.query).get("session_id", [None])[0]
  return session_id or value.strip()


def _print_recent_analysis(db: sqlite3.Connection, limit: int) -> None:
  print("\n== RECENT_DIRECT_SESSION_ANALYSIS ==")
  rows = db.execute(
      """
      select s.id session_id, s.title, s.agent_runtime, t.id turn_id,
             t.created_at, t.status, t.input_text, t.error_message,
             coalesce(t.final_text, '') final_text
      from web_turns t join web_sessions s on s.id = t.session_id
      where t.input_text not like 'System notification:%'
        and (
          lower(t.input_text) like '%session_id=%'
          or lower(t.input_text) like '%analyze this session%'
          or lower(t.input_text) like '%troubleshoot http://127.0.0.1:8086/?session_id=%'
          or lower(t.input_text) like '%recent sessions%'
          or lower(t.input_text) like '%chat-session-analysis%'
        )
      order by t.created_at desc, t.rowid desc
      limit ?
      """,
      (limit,),
  )
  for row in rows:
    print_json(
        {
            "session_id": row["session_id"],
            "turn_id": row["turn_id"],
            "title": row["title"],
            "runtime": row["agent_runtime"],
            "created_at": row["created_at"],
            "status": row["status"],
            "input": _squash(row["input_text"], 260),
            "final_head": _squash(row["final_text"], 220),
            "error": _squash(row["error_message"] or "", 160),
        }
    )


def _print_session(
    *,
    root: Path,
    db: sqlite3.Connection,
    session_id: str,
    max_steps: int,
    include_raw_events: bool,
    events_limit: int,
    include_artifacts: bool,
) -> None:
  print(f"\n== SESSION {session_id} ==")
  session = _fetch_one(db, "select * from web_sessions where id = ?", session_id)
  if session is None:
    print("web_sessions MISSING")
  else:
    print_json(_row_dict(session))
    project_id = session["project_id"] if "project_id" in session.keys() else None
    if project_id:
      project = _fetch_one(db, "select * from web_projects where id = ?", project_id)
      if project is not None:
        print("PROJECT")
        print_json(_row_dict(project))

  _print_turns(db, session_id)
  _print_steps(db, session_id, max_steps)
  _print_tasks(root, session_id)
  if include_artifacts:
    _print_artifacts(root, session_id)
  if include_raw_events:
    _print_raw_events(root, session_id, events_limit)


def _print_turns(db: sqlite3.Connection, session_id: str) -> None:
  print("\nTURNS")
  rows = db.execute(
      """
      select *
      from web_turns
      where session_id = ?
      order by created_at asc, rowid asc
      """,
      (session_id,),
  )
  for row in rows:
    item = _row_dict(row)
    item["input_text"] = _squash(item.get("input_text"), 300)
    item["final_text"] = _squash(item.get("final_text"), 300)
    item["error_message"] = _squash(item.get("error_message"), 240)
    print_json(item)


def _print_steps(db: sqlite3.Connection, session_id: str, max_steps: int) -> None:
  count = db.execute(
      """
      select count(*) value
      from web_steps e join web_turns t on t.id = e.turn_id
      where t.session_id = ?
      """,
      (session_id,),
  ).fetchone()["value"]
  print(f"\nSTEPS count={count} showing={min(count, max_steps)}")
  rows = db.execute(
      """
      select e.*
      from web_steps e join web_turns t on t.id = e.turn_id
      where t.session_id = ?
      order by coalesce(e.session_seq, e.seq) asc, t.created_at asc, e.seq asc
      limit ?
      """,
      (session_id, max_steps),
  )
  for row in rows:
    payload = _loads_json(row["payload_json"])
    item = {
        "turn_id": row["turn_id"],
        "seq": row["seq"],
        "session_seq": row["session_seq"],
        "kind": row["kind"],
        "summary": row["summary"],
        "created_at": row["created_at"],
    }
    item.update(_step_highlights(payload))
    print_json(item)


def _print_tasks(root: Path, session_id: str) -> None:
  print("\nTASKS")
  task_dir = root / "sessions" / session_id / "tasks"
  if not task_dir.exists():
    print("none")
    return

  task_events = task_dir / "task_events.jsonl"
  if task_events.exists():
    events = [
        _loads_json(line)
        for line in task_events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"task_events count={len(events)}")
    for event in events[-10:]:
      print_json(
          {
              "created_at": event.get("created_at"),
              "kind": event.get("kind"),
              "task_id": event.get("task_id"),
              "summary": _squash(event.get("summary"), 200),
              "payload": event.get("payload"),
          }
      )

  for path in sorted(task_dir.glob("*/task.json")):
    task = _loads_json(path.read_text(encoding="utf-8"))
    print_json(
        {
            "id": task.get("id"),
            "kind": task.get("kind"),
            "status": task.get("status"),
            "agent_runtime": task.get("agent_runtime"),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "child_session_id": task.get("child_session_id"),
            "returncode": task.get("returncode"),
            "input_text": _squash(task.get("input_text"), 220),
            "final_text": _squash(task.get("final_text"), 220),
            "error_message": _squash(task.get("error_message"), 220),
            "log_path": task.get("log_path"),
        }
    )


def _print_artifacts(root: Path, session_id: str) -> None:
  print("\nARTIFACTS")
  artifact_dir = root / "sessions" / session_id / "artifacts"
  if not artifact_dir.exists():
    print("none")
    return
  for path in sorted(artifact_dir.iterdir()):
    if path.name.startswith("."):
      continue
    item: dict[str, Any] = {
        "file": str(path),
        "bytes": path.stat().st_size,
    }
    if path.name.endswith(".metadata.json"):
      item["metadata"] = _loads_json(path.read_text(encoding="utf-8"))
    print_json(item)


def _print_raw_events(root: Path, session_id: str, limit: int) -> None:
  print(f"\nRAW_EVENTS showing={limit}")
  runtime_dir = root / "sessions" / session_id / "runtime"
  if not runtime_dir.exists():
    print("none")
    return
  shown = 0
  for path in sorted(runtime_dir.glob("*/events.jsonl")):
    print(f"-- {path}")
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
      if shown >= limit:
        return
      if not line.strip():
        continue
      item = _loads_json(line)
      print_json(_raw_event_highlights(index, item))
      shown += 1


def _step_highlights(payload: Any) -> dict[str, Any]:
  if not isinstance(payload, dict):
    return {"payload_type": type(payload).__name__}
  result: dict[str, Any] = {}
  for key in ("id", "name", "partial_args", "will_continue", "scheduling"):
    if key in payload:
      result[key] = payload.get(key)
  if "args" in payload:
    result["args"] = _compact_value(payload.get("args"))
  if "response" in payload:
    result["response"] = _compact_value(payload.get("response"))
  if "text" in payload:
    result["text"] = _squash(payload.get("text"), 300)
  if "error_message" in payload:
    result["error_message"] = _squash(payload.get("error_message"), 240)
  if "projections" in payload:
    result["projection_count"] = len(payload.get("projections") or [])
  return result or {"payload_keys": sorted(payload.keys())}


def _raw_event_highlights(index: int, item: Any) -> dict[str, Any]:
  if not isinstance(item, dict):
    return {"line": index, "type": type(item).__name__}
  event = item.get("event") if isinstance(item.get("event"), dict) else item
  payload = event.get("payload") if isinstance(event, dict) else None
  result = {
      "line": index,
      "created_at": item.get("created_at") or event.get("created_at"),
      "kind": event.get("kind") or event.get("type") or event.get("event"),
      "summary": event.get("summary"),
      "author": event.get("author"),
  }
  if isinstance(payload, dict):
    for key in ("name", "call_id", "ok"):
      if key in payload:
        result[key] = payload.get(key)
    if "args" in payload:
      result["args"] = _compact_value(payload.get("args"))
    if "result" in payload:
      result["result"] = _compact_value(payload.get("result"))
    if "text" in payload:
      result["text"] = _squash(payload.get("text"), 300)
    if "usage_metadata" in payload:
      result["usage_metadata"] = payload.get("usage_metadata")
  else:
    result["keys"] = sorted(event.keys()) if isinstance(event, dict) else sorted(item.keys())
  return result


def _fetch_one(
    db: sqlite3.Connection,
    query: str,
    value: str,
) -> sqlite3.Row | None:
  return db.execute(query, (value,)).fetchone()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
  return {key: row[key] for key in row.keys()}


def _loads_json(value: str) -> Any:
  try:
    return json.loads(value)
  except json.JSONDecodeError:
    return {"_invalid_json": _squash(value, 300)}


def _compact_value(value: Any) -> Any:
  if isinstance(value, str):
    return _squash(value, 400)
  if isinstance(value, dict):
    result: dict[str, Any] = {}
    for key, item in value.items():
      if key in {"stdout", "stderr", "output", "error", "final_text", "content"}:
        result[key] = _squash(item, 400)
      elif key in {
          "ok",
          "success",
          "returncode",
          "duration_sec",
          "command",
          "path",
          "query",
          "truncated",
          "match_count",
      }:
        result[key] = _compact_value(item)
      elif isinstance(item, (dict, list)):
        result[key] = _squash(json.dumps(item, ensure_ascii=False), 300)
      else:
        result[key] = item
    return result
  if isinstance(value, list):
    return [_compact_value(item) for item in value[:10]]
  return value


def _squash(value: Any, limit: int) -> str:
  if value is None:
    return ""
  text = " ".join(str(value).split())
  if len(text) <= limit:
    return text
  return text[:limit] + "..."


def print_json(value: Any) -> None:
  print(json.dumps(value, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
  raise SystemExit(main())
