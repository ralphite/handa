from __future__ import annotations

import os

from src.runner import create_handa_app


def test_create_handa_app_configures_single_service_context(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  project_root = tmp_path / "project"
  project_root.mkdir()
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))

  app = create_handa_app(project_root)

  assert app.project_root == project_root.resolve()
  assert app.services.storage_root == storage_root
  assert app.services.session_service.root == str(storage_root)
  assert app.services.artifact_service.root == str(storage_root)
  assert os.environ["HANDA_PROJECT_ROOT"] == str(project_root.resolve())
  assert os.environ["HANDA_STORAGE_ROOT"] == str(storage_root)
