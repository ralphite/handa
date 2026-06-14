from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

from .handacli import HandaCliApiError
from .handacli import resolve_project_name_for_path
from .reverse_spec_prompts import atom_extraction_prompt
from .reverse_spec_prompts import evidence_prompt
from .reverse_spec_prompts import fold_prompt
from .reverse_spec_prompts import repair_prompt
from .reverse_spec_prompts import verify_prompt


DEFAULT_OUTPUT_DIR = "generated_specs"
DEFAULT_AGENT = "orca_adk"
DEFAULT_TIMEOUT_SEC = 3600
DEFAULT_MAX_REPAIRS = 1
MARKER_FILE = ".reverse-spec-generated"

VerificationStatus = Literal[
    "pass",
    "needs_iteration",
    "missing",
    "unknown",
    "conflict",
]


@dataclass(frozen=True)
class Layer:
  id: str
  child_file: str
  parent_file: str
  verify_file: str
  child_name: str
  parent_name: str


@dataclass(frozen=True)
class Phase:
  id: str
  title: str
  output_file: str
  prompt: str


LAYERS = [
    Layer(
        id="l2",
        child_file="spec_atoms.md",
        parent_file="l2_subsystems.md",
        verify_file="verify_l2.md",
        child_name="L1 SpecAtoms",
        parent_name="L2 subsystem responsibilities",
    ),
    Layer(
        id="l3",
        child_file="l2_subsystems.md",
        parent_file="l3_capabilities.md",
        verify_file="verify_l3.md",
        child_name="L2 subsystem claims",
        parent_name="L3 capabilities",
    ),
    Layer(
        id="l4",
        child_file="l3_capabilities.md",
        parent_file="l4_domains.md",
        verify_file="verify_l4.md",
        child_name="L3 capability claims",
        parent_name="L4 product/domain areas",
    ),
    Layer(
        id="l5",
        child_file="l4_domains.md",
        parent_file="system_spec.md",
        verify_file="verify_l5.md",
        child_name="L4 domain claims",
        parent_name="L5 as-is system identity",
    ),
]

REQUIRED_FILES = [
    "evidence.md",
    "spec_atoms.md",
    *[name for layer in LAYERS for name in (layer.parent_file, layer.verify_file)],
]

STATUS_LINE_RE = re.compile(
    r"^\s*status\s*:\s*(pass|needs_iteration)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class PhaseResult(BaseModel):
  id: str
  title: str
  output_file: str
  ok: bool
  returncode: int
  session_id: str | None = None
  error: dict[str, Any] | None = None


class ReverseSpecResult(BaseModel):
  ok: bool
  output_dir: str
  session_id: str | None = None
  phases: list[PhaseResult] = Field(default_factory=list)
  missing_files: list[str] = Field(default_factory=list)
  verification_errors: list[str] = Field(default_factory=list)
  error: dict[str, Any] | None = None


def build_phase_plan(output_dir: str = DEFAULT_OUTPUT_DIR) -> list[Phase]:
  phases = [
      Phase(
          id="evidence",
          title="Build evidence map",
          output_file="evidence.md",
          prompt=evidence_prompt(output_dir),
      ),
      Phase(
          id="atoms",
          title="Extract L1 SpecAtoms",
          output_file="spec_atoms.md",
          prompt=atom_extraction_prompt(output_dir),
      ),
  ]
  for layer in LAYERS:
    phases.append(build_fold_phase(layer, output_dir))
    phases.append(build_verify_phase(layer, output_dir))
  return phases


def build_fold_phase(layer: Layer, output_dir: str = DEFAULT_OUTPUT_DIR) -> Phase:
  return Phase(
      id=f"fold-{layer.id}",
      title=f"Fold {layer.child_name} to {layer.parent_name}",
      output_file=layer.parent_file,
      prompt=fold_prompt(
          output_dir,
          layer.child_file,
          layer.parent_file,
          layer.child_name,
          layer.parent_name,
      ),
  )


def build_verify_phase(
    layer: Layer,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    *,
    repair_count: int | None = None,
) -> Phase:
  suffix = "" if repair_count is None else f"-after-repair-{repair_count}"
  return Phase(
      id=f"verify-{layer.id}{suffix}",
      title=f"Verify {layer.parent_name}",
      output_file=layer.verify_file,
      prompt=verify_prompt(
          output_dir,
          layer.child_file,
          layer.parent_file,
          layer.verify_file,
          layer.parent_name,
      ),
  )


def build_repair_phase(layer: Layer, output_dir: str = DEFAULT_OUTPUT_DIR) -> Phase:
  return Phase(
      id=f"repair-{layer.id}",
      title=f"Repair {layer.parent_name}",
      output_file=layer.parent_file,
      prompt=repair_prompt(
          output_dir,
          layer.child_file,
          layer.parent_file,
          layer.verify_file,
          layer.parent_name,
      ),
  )


def run_reverse_spec_cli(
    *,
    project: Path,
    output_dir_name: str = DEFAULT_OUTPUT_DIR,
    agent_id: str = DEFAULT_AGENT,
    session_id: str | None = None,
    handacli_command: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    clean: bool = False,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
) -> ReverseSpecResult:
  project = project.expanduser().resolve()
  if not project.is_dir():
    return ReverseSpecResult(
        ok=False,
        output_dir=str(project / output_dir_name),
        session_id=session_id,
        error={
            "type": "InvalidProject",
            "message": f"project must be a directory: {project}",
        },
    )
  if timeout_sec <= 0:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(project / output_dir_name),
        session_id=session_id,
        error={"type": "InvalidTimeout", "message": "timeout must be positive"},
    )
  if max_repairs < 0:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(project / output_dir_name),
        session_id=session_id,
        error={
            "type": "InvalidMaxRepairs",
            "message": "max repairs must be zero or greater",
        },
    )

  try:
    output_dir = _resolve_output_dir(project, output_dir_name)
  except ValueError as exc:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(project / output_dir_name),
        session_id=session_id,
        error={"type": "InvalidOutputDir", "message": str(exc)},
    )

  try:
    handacli_base = _handacli_base(handacli_command)
  except ValueError as exc:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(output_dir),
        session_id=session_id,
        error={"type": "InvalidHandacliCommand", "message": str(exc)},
    )

  output_error = _prepare_output_dir(output_dir, clean=clean)
  if output_error is not None:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(output_dir),
        session_id=session_id,
        error=output_error,
    )

  # handacli takes a project name, not a path; register-or-find this directory in
  # Handa so its sessions are visible in the web, then pass the canonical name.
  try:
    project_name = resolve_project_name_for_path(project)
  except HandaCliApiError as exc:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(output_dir),
        session_id=session_id,
        error={"type": exc.error.type, "message": exc.error.message},
    )
  except Exception as exc:  # noqa: BLE001 - surface a structured JSON error.
    return ReverseSpecResult(
        ok=False,
        output_dir=str(output_dir),
        session_id=session_id,
        error={"type": "ProjectResolutionFailed", "message": str(exc)},
    )

  phases: list[PhaseResult] = []
  active_session = session_id
  phase_index = 0

  def run_phase(phase: Phase) -> ReverseSpecResult | None:
    nonlocal active_session, phase_index
    phase_index += 1
    result = _run_phase(
        phase,
        index=phase_index,
        project_name=project_name,
        output_dir=output_dir,
        session_id=active_session,
        agent_id=agent_id,
        handacli_base=handacli_base,
        handacli_cwd=_handacli_cwd(handacli_command),
        timeout_sec=timeout_sec,
    )
    phases.append(result)
    if result.session_id:
      active_session = result.session_id
    if result.ok:
      return None
    return ReverseSpecResult(
        ok=False,
        output_dir=str(output_dir),
        session_id=active_session,
        phases=phases,
        error=result.error
        or {"type": "PhaseFailed", "message": f"phase failed: {phase.id}"},
    )

  for phase in build_phase_plan(output_dir_name)[:2]:
    failed = run_phase(phase)
    if failed is not None:
      return failed

  for layer in LAYERS:
    failed = run_phase(build_fold_phase(layer, output_dir_name))
    if failed is not None:
      return failed

    failed = run_phase(build_verify_phase(layer, output_dir_name))
    if failed is not None:
      return failed

    status = _verification_status(output_dir / layer.verify_file)
    if status not in {"pass", "needs_iteration"}:
      return _verification_failure(
          output_dir=output_dir,
          session_id=active_session,
          phases=phases,
          verify_file=layer.verify_file,
          status=status,
      )

    repair_count = 0
    while status == "needs_iteration" and repair_count < max_repairs:
      repair_count += 1
      failed = run_phase(build_repair_phase(layer, output_dir_name))
      if failed is not None:
        return failed

      failed = run_phase(
          build_verify_phase(
              layer,
              output_dir_name,
              repair_count=repair_count,
          )
      )
      if failed is not None:
        return failed

      status = _verification_status(output_dir / layer.verify_file)
      if status not in {"pass", "needs_iteration"}:
        return _verification_failure(
            output_dir=output_dir,
            session_id=active_session,
            phases=phases,
            verify_file=layer.verify_file,
            status=status,
        )

    if status != "pass":
      return _verification_failure(
          output_dir=output_dir,
          session_id=active_session,
          phases=phases,
          verify_file=layer.verify_file,
          status=status,
      )

  missing_files = [
      name for name in REQUIRED_FILES if not (output_dir / name).is_file()
  ]
  verification_errors = _verification_errors(output_dir)
  if missing_files or verification_errors:
    return ReverseSpecResult(
        ok=False,
        output_dir=str(output_dir),
        session_id=active_session,
        phases=phases,
        missing_files=missing_files,
        verification_errors=verification_errors,
        error={
            "type": "VerificationFailed",
            "message": "generated specs did not pass final checks",
        },
    )

  return ReverseSpecResult(
      ok=True,
      output_dir=str(output_dir),
      session_id=active_session,
      phases=phases,
  )


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="handa-reverse-spec")
  parser.add_argument("project", nargs="?")
  parser.add_argument("--project", dest="project_option")
  parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
  parser.add_argument("--agent", default=DEFAULT_AGENT)
  parser.add_argument("--session")
  parser.add_argument("--handacli-command")
  parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
  parser.add_argument("--max-repairs", type=int, default=DEFAULT_MAX_REPAIRS)
  parser.add_argument("--clean", action="store_true")
  parser.add_argument("--json", action="store_true", dest="json_output")
  args = parser.parse_args(argv)

  raw_project = args.project_option or args.project
  if not raw_project:
    parser.error("project path is required")
  project = Path(raw_project).expanduser().resolve()
  if not project.is_dir():
    parser.error(f"project must be a directory: {project}")
  if args.timeout <= 0:
    parser.error("--timeout must be positive")
  if args.max_repairs < 0:
    parser.error("--max-repairs must be zero or greater")

  result = run_reverse_spec_cli(
      project=project,
      output_dir_name=args.output_dir,
      agent_id=args.agent,
      session_id=args.session.strip() if args.session and args.session.strip() else None,
      handacli_command=args.handacli_command,
      timeout_sec=args.timeout,
      clean=args.clean,
      max_repairs=args.max_repairs,
  )
  if args.json_output:
    print(result.model_dump_json(indent=2))
  elif result.ok:
    print(f"Reverse specs generated: {result.output_dir}")
  else:
    print(f"Reverse spec generation failed: {result.error}", file=sys.stderr)
  return 0 if result.ok else 1


def _resolve_output_dir(project: Path, output_dir_name: str) -> Path:
  relative = Path(output_dir_name)
  parts = relative.parts
  if relative.is_absolute() or not parts or ".." in parts:
    raise ValueError("output dir must be generated_specs or a child of it")
  if parts[0] != DEFAULT_OUTPUT_DIR:
    raise ValueError("output dir must be generated_specs or a child of it")
  resolved = (project / relative).resolve(strict=False)
  project_resolved = project.resolve()
  if resolved != project_resolved and project_resolved not in resolved.parents:
    raise ValueError("output dir must stay inside project")
  return resolved


def _prepare_output_dir(
    output_dir: Path,
    *,
    clean: bool,
) -> dict[str, Any] | None:
  if output_dir.exists() and not output_dir.is_dir():
    return {
        "type": "UnsafeOutputDir",
        "message": "refusing to write into a non-directory output path",
    }
  if clean and output_dir.exists():
    if not _is_generated_output_dir(output_dir):
      return {
          "type": "UnsafeCleanOutputDir",
          "message": "refusing to clean an unknown output dir",
      }
    shutil.rmtree(output_dir)
  if output_dir.exists() and not _is_generated_output_dir(output_dir):
    return {
        "type": "UnsafeOutputDir",
        "message": "refusing to write into an unknown output dir",
    }

  output_dir.mkdir(parents=True, exist_ok=True)
  (output_dir / MARKER_FILE).write_text(
      "generated by handa-reverse-spec\n",
      encoding="utf-8",
  )
  return None


def _is_generated_output_dir(path: Path) -> bool:
  if not path.exists():
    return True
  if not path.is_dir():
    return False
  if not any(path.iterdir()):
    return True
  return (path / MARKER_FILE).is_file()


def _handacli_base(command: str | None) -> list[str]:
  if command is None:
    return [sys.executable, "-m", "src.handacli"]
  parts = shlex.split(command)
  if not parts:
    raise ValueError("handacli command must not be empty")
  return parts


def _handacli_cwd(command: str | None) -> Path | None:
  if command is not None:
    return None
  return Path(__file__).resolve().parents[1]


def _run_phase(
    phase: Phase,
    *,
    index: int,
    project_name: str,
    output_dir: Path,
    session_id: str | None,
    agent_id: str,
    handacli_base: list[str],
    handacli_cwd: Path | None,
    timeout_sec: int,
) -> PhaseResult:
  run_dir = output_dir / "_runs"
  _write(run_dir / f"{index:02d}-{phase.id}.prompt.md", phase.prompt)

  cmd = [
      *handacli_base,
      "--project",
      project_name,
      "--prompt",
      phase.prompt,
      "--agent",
      agent_id,
      "--json",
  ]
  if session_id:
    cmd.extend(["--session", session_id])

  try:
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        cwd=handacli_cwd,
    )
  except subprocess.TimeoutExpired as exc:
    _write(run_dir / f"{index:02d}-{phase.id}.stdout.txt", _as_text(exc.stdout))
    _write(run_dir / f"{index:02d}-{phase.id}.stderr.txt", _as_text(exc.stderr))
    return PhaseResult(
        id=phase.id,
        title=phase.title,
        output_file=phase.output_file,
        ok=False,
        returncode=124,
        session_id=session_id,
        error={
            "type": "TimeoutExpired",
            "message": f"phase {phase.id} timed out after {exc.timeout}s",
        },
    )
  except OSError as exc:
    return PhaseResult(
        id=phase.id,
        title=phase.title,
        output_file=phase.output_file,
        ok=False,
        returncode=127,
        session_id=session_id,
        error={"type": type(exc).__name__, "message": str(exc)},
    )

  _write(run_dir / f"{index:02d}-{phase.id}.stderr.txt", completed.stderr)
  payload = _parse_handacli_json(
      completed.stdout,
      stdout_path=run_dir / f"{index:02d}-{phase.id}.stdout.txt",
  )
  if not isinstance(payload, dict):
    return PhaseResult(
        id=phase.id,
        title=phase.title,
        output_file=phase.output_file,
        ok=False,
        returncode=completed.returncode,
        session_id=session_id,
        error={
            "type": "InvalidHandacliJson",
            "message": "handacli stdout was not a JSON object",
        },
    )

  _write(
      run_dir / f"{index:02d}-{phase.id}.json",
      json.dumps(payload, indent=2),
  )
  payload_session = payload.get("session_id")
  result_session = payload_session if isinstance(payload_session, str) else session_id
  payload_error = payload.get("error") if isinstance(payload.get("error"), dict) else None
  phase_ok = completed.returncode == 0 and payload.get("ok") is True
  if phase_ok and not (output_dir / phase.output_file).is_file():
    return PhaseResult(
        id=phase.id,
        title=phase.title,
        output_file=phase.output_file,
        ok=False,
        returncode=completed.returncode,
        session_id=result_session,
        error={
            "type": "PhaseOutputMissing",
            "message": f"phase {phase.id} did not create {phase.output_file}",
        },
    )

  return PhaseResult(
      id=phase.id,
      title=phase.title,
      output_file=phase.output_file,
      ok=phase_ok,
      returncode=completed.returncode,
      session_id=result_session,
      error=payload_error,
  )


def _parse_handacli_json(stdout: str, *, stdout_path: Path) -> Any:
  try:
    return json.loads(stdout)
  except json.JSONDecodeError as exc:
    _write(stdout_path, stdout)
    return {
        "ok": False,
        "error": {"type": "InvalidHandacliJson", "message": str(exc)},
    }


def _verification_status(path: Path) -> VerificationStatus:
  if not path.is_file():
    return "missing"
  statuses = [match.group(1).lower() for match in STATUS_LINE_RE.finditer(path.read_text(encoding="utf-8"))]
  if not statuses:
    return "unknown"
  if len(set(statuses)) > 1:
    return "conflict"
  return statuses[0]  # type: ignore[return-value]


def _verification_failure(
    *,
    output_dir: Path,
    session_id: str | None,
    phases: list[PhaseResult],
    verify_file: str,
    status: VerificationStatus,
) -> ReverseSpecResult:
  message = _verification_error_message(verify_file, status)
  return ReverseSpecResult(
      ok=False,
      output_dir=str(output_dir),
      session_id=session_id,
      phases=phases,
      verification_errors=[message],
      error={
          "type": "VerificationFailed"
          if status == "needs_iteration"
          else "InvalidVerificationStatus",
          "message": message,
      },
  )


def _verification_errors(output_dir: Path) -> list[str]:
  errors = []
  for layer in LAYERS:
    status = _verification_status(output_dir / layer.verify_file)
    if status != "pass":
      errors.append(_verification_error_message(layer.verify_file, status))
  return errors


def _verification_error_message(
    verify_file: str,
    status: VerificationStatus,
) -> str:
  if status == "missing":
    return f"{verify_file} is missing"
  if status == "unknown":
    return f"{verify_file} has no valid Status line"
  if status == "conflict":
    return f"{verify_file} has conflicting Status lines"
  return f"{verify_file} status is {status}"


def _write(path: Path, text: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")


def _as_text(value: str | bytes | None) -> str:
  if value is None:
    return ""
  if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace")
  return value


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
