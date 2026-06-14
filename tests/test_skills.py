from __future__ import annotations

import textwrap

from src.agents.skill_prompt import render_skill_instructions
from src.tools import skills


def _write_skill(root, name, content):
  skill_dir = root / name
  skill_dir.mkdir()
  skill_path = skill_dir / "SKILL.md"
  skill_path.write_text(
      textwrap.dedent(content).strip() + "\n",
      encoding="utf-8",
  )
  return skill_path


def test_list_skills_from_system_and_user_skill_dirs(tmp_path, monkeypatch):
  system_root = tmp_path / "system"
  user_root = tmp_path / "user"
  system_root.mkdir()
  user_root.mkdir()
  system_skill_path = _write_skill(
      system_root,
      "vcs-jj",
      """
      ---
      name: vcs-jj
      description: "Use jj for local version control."
      ---

      # jj (Jujutsu)
      """,
  )
  user_skill_path = _write_skill(
      user_root,
      "testing",
      """
      ---
      name: testing
      description: "Run verification."
      ---

      # Testing
      """,
  )
  monkeypatch.setattr(skills, "SYSTEM_SKILLS_DIR", system_root)
  monkeypatch.setattr(skills, "SKILLS_DIR", user_root)

  assert skills.list() == {
      "skills": [
          {
              "name": "vcs-jj",
              "skill_name": "vcs-jj",
              "title": "vcs-jj",
              "description": "Use jj for local version control.",
              "source": "system",
              "path": str(system_skill_path.resolve()),
          },
          {
              "name": "testing",
              "skill_name": "testing",
              "title": "testing",
              "description": "Run verification.",
              "source": "user",
              "path": str(user_skill_path.resolve()),
          }
      ]
  }


def test_describe_and_read_skill_by_directory_name(tmp_path, monkeypatch):
  user_root = tmp_path / "user"
  user_root.mkdir()
  skill_path = _write_skill(
      user_root,
      "vcs-jj",
      "---\nname: vcs-jj\n---\n\n# jj\n",
  )
  monkeypatch.setattr(skills, "SYSTEM_SKILLS_DIR", tmp_path / "system")
  monkeypatch.setattr(skills, "SKILLS_DIR", user_root)

  metadata = skills.describe("vcs-jj")
  full_content = skills.read("vcs-jj")

  assert metadata == {
      "success": True,
      "name": "vcs-jj",
      "skill_name": "vcs-jj",
      "title": "vcs-jj",
      "description": "",
      "source": "user",
      "path": str(skill_path.resolve()),
  }
  assert full_content["success"] is True
  assert full_content["name"] == "vcs-jj"
  assert full_content["source"] == "user"
  assert full_content["path"] == str(skill_path.resolve())
  assert full_content["content"].endswith("# jj\n")


def test_read_unknown_or_unsafe_skill_fails(tmp_path, monkeypatch):
  monkeypatch.setattr(skills, "SYSTEM_SKILLS_DIR", tmp_path / "system")
  monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)

  assert skills.read("missing")["success"] is False
  assert skills.read("../missing")["success"] is False


def test_system_skill_wins_name_collision(tmp_path, monkeypatch):
  system_root = tmp_path / "system"
  user_root = tmp_path / "user"
  system_root.mkdir()
  user_root.mkdir()
  system_skill_path = _write_skill(
      system_root,
      "vcs-jj",
      "---\nname: vcs-jj\n---\n\n# system\n",
  )
  _write_skill(
      user_root,
      "vcs-jj",
      "---\nname: vcs-jj\n---\n\n# user\n",
  )
  monkeypatch.setattr(skills, "SYSTEM_SKILLS_DIR", system_root)
  monkeypatch.setattr(skills, "SKILLS_DIR", user_root)

  result = skills.read("vcs-jj")

  assert result["source"] == "system"
  assert result["path"] == str(system_skill_path.resolve())
  assert result["content"].endswith("# system\n")


def test_public_repo_does_not_bundle_markdown_system_skills():
  listed = skills.list()["skills"]

  system_names = {
      item["name"]
      for item in listed
      if item.get("source") == "system"
  }
  assert system_names == set()


def test_render_skill_instructions_for_agent_config(tmp_path, monkeypatch):
  user_root = tmp_path / "user"
  user_root.mkdir()
  skill_path = _write_skill(
      user_root,
      "testing",
      """
      ---
      name: testing
      description: Run verification when code changes.
      ---

      # Testing

      Run the smallest meaningful verification.
      """,
  )
  monkeypatch.setattr(skills, "SYSTEM_SKILLS_DIR", tmp_path / "system")
  monkeypatch.setattr(skills, "SKILLS_DIR", user_root)

  rendered = render_skill_instructions(["testing"])

  assert rendered.startswith("<skills>")
  assert "You have the following skills." in rendered
  assert "<name>testing</name>" in rendered
  assert "<description>Run verification when code changes.</description>" in rendered
  assert "<source>user</source>" in rendered
  assert f"<path>{skill_path.resolve()}</path>" in rendered
  assert "<skill_usage>" in rendered
  assert "Run the smallest meaningful verification." not in rendered
