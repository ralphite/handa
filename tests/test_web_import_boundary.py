from __future__ import annotations

import ast
from pathlib import Path


WEB_API_ROOT = Path(__file__).resolve().parents[1] / "src" / "api"

# The Web layer may import the contract package and itself — nothing else
# from src. src/contract is the Python client of the web↔runtime contract
# (storage formats, run-record/task control, trace appenders, the user-input
# contract, the browser daemon client, read-only product metadata).
ALLOWED_SRC_PREFIXES = (
    "src.contract",
    "src.api",
)

# Runtime/LLM packages the Web process must not load. google.genai is allowed
# only in the product features that call Gemini directly.
FORBIDDEN_EXTERNAL_PREFIXES = (
    "google.adk",
    "google.genai",
    "langgraph",
    "playwright",
)
GENAI_ALLOWED_FILES = {
    WEB_API_ROOT / "title_generation.py",
    WEB_API_ROOT / "routes" / "dictate.py",
    WEB_API_ROOT / "routes" / "optimize_prompt.py",
}


def _module_package(path: Path) -> list[str]:
  repo_root = WEB_API_ROOT.parents[1]
  relative = path.relative_to(repo_root)
  parts = list(relative.with_suffix("").parts)
  if parts[-1] == "__init__":
    parts = parts[:-1]
  return parts[:-1]


def _resolve_relative(package: list[str], level: int, module: str | None) -> str:
  base = package[: len(package) - (level - 1)] if level > 1 else package
  resolved = list(base)
  if module:
    resolved.extend(module.split("."))
  return ".".join(resolved)


def _imports_outside_type_checking(tree: ast.AST):
  """Yield (node, is_type_checking) for every import statement."""

  def walk(node: ast.AST, in_type_checking: bool):
    for child in ast.iter_child_nodes(node):
      child_flag = in_type_checking
      if isinstance(child, ast.If):
        test = child.test
        is_tc = (
            isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        ) or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_tc:
          for body_child in child.body:
            yield from walk(body_child, True)
            if isinstance(body_child, (ast.Import, ast.ImportFrom)):
              yield body_child, True
          for else_child in child.orelse:
            yield from walk(else_child, in_type_checking)
            if isinstance(else_child, (ast.Import, ast.ImportFrom)):
              yield else_child, in_type_checking
          continue
      if isinstance(child, (ast.Import, ast.ImportFrom)):
        yield child, child_flag
      yield from walk(child, child_flag)

  yield from walk(tree, False)


def _collect_violations(path: Path) -> list[str]:
  package = _module_package(path)
  tree = ast.parse(path.read_text(encoding="utf-8"))
  violations: list[str] = []
  for node, in_type_checking in _imports_outside_type_checking(tree):
    if in_type_checking:
      continue
    targets: list[str] = []
    if isinstance(node, ast.Import):
      targets = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
      if node.level:
        resolved = _resolve_relative(package, node.level, node.module)
        targets = [resolved] if node.module else [
            f"{resolved}.{alias.name}" for alias in node.names
        ]
      else:
        targets = [node.module or ""]
    for target in targets:
      if not target:
        continue
      if target == "src" or target.startswith("src."):
        if not target.startswith(ALLOWED_SRC_PREFIXES):
          violations.append(f"{path.relative_to(WEB_API_ROOT)}: {target}")
        continue
      for prefix in FORBIDDEN_EXTERNAL_PREFIXES:
        if target == prefix or target.startswith(prefix + "."):
          if prefix == "google.genai" and path in GENAI_ALLOWED_FILES:
            continue
          violations.append(f"{path.relative_to(WEB_API_ROOT)}: {target}")
  return violations


def test_web_api_only_imports_the_runtime_contract():
  violations: list[str] = []
  for path in sorted(WEB_API_ROOT.rglob("*.py")):
    if "fe" in path.parts or "__pycache__" in path.parts:
      continue
    violations.extend(_collect_violations(path))
  assert violations == [], "\n".join(violations)
