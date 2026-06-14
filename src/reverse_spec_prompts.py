from __future__ import annotations


def evidence_prompt(output_dir: str) -> str:
  return f"""
Inspect the current project.

Write `{output_dir}/evidence.md`.

Build the evidence map that SpecFold will use. Do not write specs yet.
Treat the current project as the only source of truth.

Find the real implementation signals yourself:
- entry points and user/system workflows
- runtime call paths and dependency relationships
- state, storage, config, environment, generated artifacts, and external tools
- tests, docs, scripts, package metadata, and other files that prove behavior
- candidate implementation clusters, with the evidence for why each cluster exists
- gaps, conflicts, dead ends, and weak evidence

Rules:
- This works for any codebase. Do not assume frontend/backend/API/storage/test names.
- Do not group by folder unless runtime or behavioral evidence supports it.
- Prefer concrete file/function/config/test evidence over labels.
- Preserve uncertainty instead of inventing intent.
""".strip()


def atom_extraction_prompt(output_dir: str) -> str:
  return f"""
Read `{output_dir}/evidence.md`.

Write `{output_dir}/spec_atoms.md`.

Extract L1 SpecAtoms: local, evidence-backed implementation facts only.

Each atom must include:
- stable ID
- subject
- observed behavior
- trigger/input
- output/state change
- constraints
- errors/failure behavior
- evidence
- confidence
- gaps/conflicts

Rules:
- Do not write parent specs.
- Do not infer product intent.
- Keep each atom local enough that a parent claim can later cite it.
- If evidence is weak, write a gap.
- If two facts conflict, preserve both and mark the conflict.
""".strip()


FOLD_RULES = """
You are performing one SpecFold step.

SpecFold is not summarization.
It is: child claims -> operational relationship -> as-is operational responsibility -> parent claim.

Work method:
1. Normalize each child claim:
   behavior, trigger/input, output/state change, constraints, failures, evidence, gaps.
2. Find operational relationships between children.
   Strong relationships include:
   shared runtime path, caller/callee relation, same workflow stage, shared state,
   shared config/dependency, same input/output contract, same failure mode,
   or tests/docs proving they participate in the same behavior.
3. Form candidate groups only when the relationship explains how the children work together.
4. Derive the current implemented responsibility of each group.
5. Write the parent claim exactly one abstraction level above the children.
6. Check coverage: every important child is represented or explicitly left as a gap.

For each parent claim:
1. State the operational relationship.
2. Explain why these children belong together.
3. State the as-is responsibility proven by the children.
4. List child IDs.
5. Propagate evidence from children.
6. Preserve constraints, errors, gaps, conflicts, and weak confidence.

Reject weak folds:
- Folder/name/framework similarity is not enough.
- If operational relationship is unclear, write a gap or singleton responsibility.
- If a parent would add unsupported facts, narrow it.
- If the group jumps more than one abstraction level, split it.
""".strip()


VERIFY_RULES = """
You are verifying one SpecFold layer.

Be strict. Do not rewrite the spec.

For each parent claim, check:
- Grounding: child IDs exist and evidence is propagated.
- Fold validity: the parent follows from the children.
- Operational relationship: stronger than folder/name/framework similarity.
- Responsibility: supported by child behavior, not invented.
- Abstraction: exactly one layer above the children.
- Preservation: constraints, errors, gaps, conflicts, weak confidence are not lost.
- Coverage: important child claims are not omitted.

Write `Status: pass` only if there are no required fixes.
Otherwise write `Status: needs_iteration` and concrete required fixes tied to child IDs.

Output contract:
- The first non-empty line must be exactly `Status: pass` or `Status: needs_iteration`.
- Use only one Status line.
""".strip()


REPAIR_RULES = """
Repair only the current layer.

Use the verifier file as instructions.

Allowed edits:
- split weak folds
- narrow unsupported parent claims
- add missing child IDs
- propagate missing evidence
- restore constraints/errors/gaps/conflicts
- mark unclear operational intent as a gap

Do not edit child layer files.
Do not move to the next layer.
Do not add product roadmap or desired behavior.
""".strip()


def fold_prompt(output_dir: str, child_file: str, parent_file: str, child_name: str, parent_name: str) -> str:
  return f"""
Read:
- `{output_dir}/{child_file}`
- `{output_dir}/evidence.md`

Write `{output_dir}/{parent_file}`.

Fold {child_name} into {parent_name}.

{FOLD_RULES}
""".strip()


def verify_prompt(output_dir: str, child_file: str, parent_file: str, verify_file: str, parent_name: str) -> str:
  return f"""
Read:
- `{output_dir}/{child_file}`
- `{output_dir}/{parent_file}`
- `{output_dir}/evidence.md`

Write `{output_dir}/{verify_file}`.

Verify whether each {parent_name} claim is a valid SpecFold parent.

{VERIFY_RULES}
""".strip()


def repair_prompt(output_dir: str, child_file: str, parent_file: str, verify_file: str, parent_name: str) -> str:
  return f"""
Read:
- `{output_dir}/{child_file}`
- `{output_dir}/{parent_file}`
- `{output_dir}/{verify_file}`
- `{output_dir}/evidence.md`

Rewrite `{output_dir}/{parent_file}`.

Repair the {parent_name} layer until it satisfies the verifier.

{REPAIR_RULES}
""".strip()
