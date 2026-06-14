from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import reverse_spec_cli
from src.handacli import HandaCliApiError
from src.handacli import HandaCliError


@pytest.fixture(autouse=True)
def _stub_project_name(monkeypatch):
  # handacli's project-name resolution hits the web API; tests drive the phase
  # workflow offline, so stand in the directory basename as the project name.
  monkeypatch.setattr(
      reverse_spec_cli,
      "resolve_project_name_for_path",
      lambda path, **kwargs: Path(path).name,
  )


def _response(
    *,
    ok: bool = True,
    session_id: str = "session-1",
    error: dict[str, str] | None = None,
) -> SimpleNamespace:
  return SimpleNamespace(
      returncode=0 if ok else 1,
      stdout=json.dumps({"ok": ok, "session_id": session_id, "error": error}),
      stderr="",
  )


def _prompt_output_file(prompt: str) -> str:
  match = re.search(r"(?:Write|Rewrite) `generated_specs/([^`]+)`", prompt)
  assert match, prompt
  return match.group(1)


def _write_phase_output(
    project: Path,
    prompt: str,
    *,
    verify_pass: bool = True,
) -> None:
  output_file = _prompt_output_file(prompt)
  target = project / "generated_specs" / output_file
  target.parent.mkdir(parents=True, exist_ok=True)
  if target.name.startswith("verify_"):
    target.write_text(
        "Status: pass\n"
        if verify_pass
        else "Status: needs_iteration\nRequired fixes:\n- narrow weak fold\n",
        encoding="utf-8",
    )
    return
  target.write_text(
      f"# {target.stem}\n\nchildren: previous layer IDs\nevidence: fixture\n",
      encoding="utf-8",
  )


def _prompt(cmd: list[str]) -> str:
  return cmd[cmd.index("--prompt") + 1]


def test_cli_runs_phase_workflow_and_reuses_session(monkeypatch, capsys, tmp_path):
  calls = []

  def fake_run(cmd, capture_output, text, timeout, cwd=None):
    calls.append(cmd)
    _write_phase_output(tmp_path, _prompt(cmd))
    return _response()

  monkeypatch.setattr(reverse_spec_cli.subprocess, "run", fake_run)

  exit_code = reverse_spec_cli.main([str(tmp_path), "--json"])

  payload = json.loads(capsys.readouterr().out)
  assert exit_code == 0
  assert payload["ok"] is True
  assert len(calls) == len(reverse_spec_cli.build_phase_plan())
  assert "--session" not in calls[0]
  assert calls[1][calls[1].index("--session") + 1] == "session-1"
  # handacli is invoked with the resolved project name, not the path.
  assert calls[0][calls[0].index("--project") + 1] == tmp_path.name
  assert (tmp_path / "generated_specs" / "system_spec.md").is_file()
  assert (tmp_path / "generated_specs" / "_runs" / "01-evidence.prompt.md").is_file()


def test_cli_reports_project_resolution_failure(monkeypatch, capsys, tmp_path):
  def boom(path, **kwargs):
    raise HandaCliApiError(
        HandaCliError(type="ProjectNotFound", message="No Handa project named 'x'.")
    )

  monkeypatch.setattr(reverse_spec_cli, "resolve_project_name_for_path", boom)

  exit_code = reverse_spec_cli.main([str(tmp_path), "--json"])

  payload = json.loads(capsys.readouterr().out)
  assert exit_code == 1
  assert payload["error"] == {
      "type": "ProjectNotFound",
      "message": "No Handa project named 'x'.",
  }


def test_cli_repairs_current_layer_before_continuing(monkeypatch, capsys, tmp_path):
  saw_repair_l2 = False
  verify_l2_count = 0

  def fake_run(cmd, capture_output, text, timeout, cwd=None):
    nonlocal saw_repair_l2, verify_l2_count
    prompt = _prompt(cmd)
    if "Rewrite `generated_specs/l2_subsystems.md`" in prompt:
      saw_repair_l2 = True
    if "Write `generated_specs/verify_l2.md`" in prompt:
      verify_l2_count += 1
      _write_phase_output(tmp_path, prompt, verify_pass=saw_repair_l2)
    else:
      _write_phase_output(tmp_path, prompt)
    return _response()

  monkeypatch.setattr(reverse_spec_cli.subprocess, "run", fake_run)

  exit_code = reverse_spec_cli.main([str(tmp_path), "--json"])

  payload = json.loads(capsys.readouterr().out)
  phase_ids = [phase["id"] for phase in payload["phases"]]
  assert exit_code == 0
  assert saw_repair_l2 is True
  assert verify_l2_count == 2
  assert "repair-l2" in phase_ids
  assert phase_ids.index("repair-l2") < phase_ids.index("fold-l3")


def test_cli_stops_when_layer_still_fails_after_repairs(
    monkeypatch,
    capsys,
    tmp_path,
):
  def fake_run(cmd, capture_output, text, timeout, cwd=None):
    prompt = _prompt(cmd)
    if "Write `generated_specs/verify_l2.md`" in prompt:
      _write_phase_output(tmp_path, prompt, verify_pass=False)
    else:
      _write_phase_output(tmp_path, prompt)
    return _response()

  monkeypatch.setattr(reverse_spec_cli.subprocess, "run", fake_run)

  exit_code = reverse_spec_cli.main(
      [str(tmp_path), "--json", "--max-repairs", "1"]
  )

  payload = json.loads(capsys.readouterr().out)
  phase_ids = [phase["id"] for phase in payload["phases"]]
  assert exit_code == 1
  assert payload["error"]["type"] == "VerificationFailed"
  assert payload["verification_errors"] == ["verify_l2.md status is needs_iteration"]
  assert "verify-l2-after-repair-1" in phase_ids
  assert "fold-l3" not in phase_ids


def test_verification_status_requires_exact_status_line(tmp_path):
  verify_file = tmp_path / "verify.md"

  verify_file.write_text(
      "Status: needs_iteration\nDo not write `Status: pass` until fixed.\n",
      encoding="utf-8",
  )
  assert reverse_spec_cli._verification_status(verify_file) == "needs_iteration"

  verify_file.write_text("The final Status: pass should not count.\n", encoding="utf-8")
  assert reverse_spec_cli._verification_status(verify_file) == "unknown"

  verify_file.write_text(
      "Status: pass\nStatus: needs_iteration\n",
      encoding="utf-8",
  )
  assert reverse_spec_cli._verification_status(verify_file) == "conflict"


def test_cli_rejects_unsafe_output_dirs(tmp_path):
  invalid_parent = reverse_spec_cli.run_reverse_spec_cli(
      project=tmp_path,
      output_dir_name="../outside",
  )
  invalid_name = reverse_spec_cli.run_reverse_spec_cli(
      project=tmp_path,
      output_dir_name="src",
  )

  output_dir = tmp_path / "generated_specs"
  output_dir.mkdir()
  (output_dir / "user-owned.md").write_text("do not overwrite\n", encoding="utf-8")
  unsafe = reverse_spec_cli.run_reverse_spec_cli(project=tmp_path)

  assert invalid_parent.error["type"] == "InvalidOutputDir"
  assert invalid_name.error["type"] == "InvalidOutputDir"
  assert unsafe.error["type"] == "UnsafeOutputDir"


def test_cli_reports_handacli_failure(monkeypatch, capsys, tmp_path):
  def fake_run(cmd, capture_output, text, timeout, cwd=None):
    return _response(
        ok=False,
        error={"type": "RuntimeError", "message": "boom"},
    )

  monkeypatch.setattr(reverse_spec_cli.subprocess, "run", fake_run)

  exit_code = reverse_spec_cli.main([str(tmp_path), "--json"])

  payload = json.loads(capsys.readouterr().out)
  assert exit_code == 1
  assert payload["error"] == {"type": "RuntimeError", "message": "boom"}


def test_cli_fails_when_phase_does_not_create_output(monkeypatch, capsys, tmp_path):
  def fake_run(cmd, capture_output, text, timeout, cwd=None):
    return _response()

  monkeypatch.setattr(reverse_spec_cli.subprocess, "run", fake_run)

  exit_code = reverse_spec_cli.main([str(tmp_path), "--json"])

  payload = json.loads(capsys.readouterr().out)
  assert exit_code == 1
  assert payload["error"]["type"] == "PhaseOutputMissing"
