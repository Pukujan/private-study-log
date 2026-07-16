"""Phase 4 gate 4.1: the approach **contract** — served pre-filled from the
corpus, validated, and (in 4.2) required before a write tool will run.

The contract is the accountability object of the verified write path
(`docs/BUILD-PLAN.md` Phase 4): before doing non-trivial work an agent records
what it consulted (`evidence_refs`, which MUST resolve to real corpus files --
no inventing citations), what it plans, what "done" means (`acceptance_criteria`),
and how it will show it (`verification_steps`). Proportionality is a first-class
goal: trivial work gets a 3-line auto-contract, and an `explore` task type has
relaxed gates, so the "ceremony tax" stays small (the whole design dies if it
slows the fast path it protects).

This module is data-modelling + validation + corpus-prefill only. It does NOT
yet gate any write tool -- that's gate 4.2, which changes MCP write behaviour
and gets a fresh-context review before it ships.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uuid6

from .config import resolve_workspace_override

# ONE shared task-type vocabulary, <=10 (gate 4.4 pitfall: don't let two
# vocabularies emerge -- Phase 5's library reuses this list). `explore` is the
# relaxed-gate type for genuinely exploratory work.
TASK_TYPES: tuple[str, ...] = (
    "bugfix",
    "feature",
    "refactor",
    "research",
    "docs",
    "review",
    "test",
    "chore",
    "explore",
)

# Types whose gates are relaxed (no planned approach / criteria / evidence
# required) so genuinely exploratory (`explore`) and trivial (`chore`) work
# isn't forced through full ceremony -- the proportionality escapes. A write
# review (2026-07-04) noted an agent can self-select these to skip the gate;
# that's an accepted v1 limitation, caught by the closeout-coverage-vs-git SLI,
# not the gate.
_RELAXED_TYPES = frozenset({"explore", "chore"})

CONTRACTS_DIRNAME = "contracts"


@dataclass(frozen=True)
class Contract:
    contract_id: str
    task: str
    task_type: str
    evidence_refs: list[str]  # workspace-relative corpus paths; must resolve
    planned_approach: str
    acceptance_criteria: list[str]
    verification_steps: list[str]
    model: str
    role: str
    created_at: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Contract":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        return cls(**known)  # type: ignore[arg-type]


@dataclass(frozen=True)
class MissionContract:
    """Mission-level bounded contract (Phase 5.2, 2026-07-08): acceptance criteria and
    coverage spec for the orchestrated whole, not individual workers. Stored in the
    mission task's intent JSON (no schema migration needed). See
    docs/research/MISSION-MERGE-DESIGN-2026-07-08.md Piece 2."""
    mission_id: str
    mission_task: str  # the complete ask, verbatim (feeds REVIEW scope gate)
    task_type: str
    acceptance_criteria: list[str]  # what the MERGED artifact must satisfy
    coverage_spec: dict[str, Any]  # {required_units, max_workers} for the partition gate
    reducers: dict[str, str]  # per output-key merge policy: append/union/upsert_by_id/single_writer/git_merge
    evidence_refs: list[str]
    created_at: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionContract":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        return cls(**known)  # type: ignore[arg-type]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_contract_id() -> str:
    return str(uuid6.uuid7())


def _infer_task_type(task: str) -> str:
    t = task.lower()
    for kw, tt in (
        ("fix", "bugfix"), ("bug", "bugfix"), ("regress", "bugfix"),
        ("refactor", "refactor"), ("rename", "refactor"),
        ("research", "research"), ("investigate", "research"), ("explore", "explore"),
        ("review", "review"), ("audit", "review"),
        ("test", "test"),
        ("doc", "docs"), ("readme", "docs"),
        ("add", "feature"), ("implement", "feature"), ("build", "feature"), ("ship", "feature"),
    ):
        if kw in t:
            return tt
    return "feature"


def validate_contract(contract: Contract, workspace: str | Path | None = None) -> tuple[bool, list[str]]:
    """Return (ok, errors). Every `evidence_ref` must resolve to a real file
    under the workspace (the anti-fabrication check the gate names); required
    substance fields must be non-empty except for relaxed (`explore`) types."""
    ws = resolve_workspace_override(workspace)
    errors: list[str] = []

    if contract.task_type not in TASK_TYPES:
        errors.append(f"task_type {contract.task_type!r} not in {TASK_TYPES}")
    if not contract.task.strip():
        errors.append("task is empty")

    for ref in contract.evidence_refs:
        # Reject absolute paths / escapes; the ref must resolve to a real FILE
        # inside the ws (is_file, not exists -- a directory/"."/"" is not a
        # citation, review finding L3).
        target = (ws / ref).resolve()
        if not target.is_relative_to(ws.resolve()):
            errors.append(f"evidence_ref escapes the workspace: {ref}")
        elif not target.is_file():
            errors.append(f"evidence_ref does not resolve to a real file: {ref}")

    if contract.task_type not in _RELAXED_TYPES:
        if not contract.planned_approach.strip():
            errors.append("planned_approach is empty (required for substantive tasks)")
        if not contract.acceptance_criteria:
            errors.append("acceptance_criteria is empty (required for substantive tasks)")
        if not contract.verification_steps:
            errors.append("verification_steps is empty (required for substantive tasks)")
        # Anti-fabrication is the whole point (review finding M2): a substantive
        # task must cite at least one real corpus file it consulted -- "cited
        # evidence, not trust" enforced at the contract, not just claimed.
        if not contract.evidence_refs:
            errors.append(
                "evidence_refs is empty: a substantive task must cite >=1 corpus "
                "file it consulted (use cortex_contract prefill, or task_type "
                "explore/chore if there's genuinely nothing to cite)"
            )

    return (not errors, errors)


def _relativize(path_str: str, ws: Path) -> str | None:
    try:
        return Path(path_str).resolve().relative_to(ws.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def prefill_contract(
    task: str,
    workspace: str | Path | None = None,
    model: str = "auto",
    role: str = "builder",
    max_evidence: int = 5,
) -> Contract:
    """Build a contract stub with `evidence_refs` pre-filled from a corpus search
    for the task (the agent edits, doesn't author from scratch). Prefill only
    populates what the corpus can supply objectively -- evidence + an inferred
    task_type; the agent fills the plan/criteria/steps."""
    ws = resolve_workspace_override(workspace)
    evidence: list[str] = []
    try:
        from .search import CortexSearchIndex

        index = CortexSearchIndex(ws)
        if index.needs_rebuild():
            index.rebuild()
        seen: set[str] = set()
        for r in index.search(task, limit=max_evidence * 3, tag="contract-prefill", use_vector=True):
            rel = _relativize(r.path, ws)
            if rel and rel not in seen:
                seen.add(rel)
                evidence.append(rel)
            if len(evidence) >= max_evidence:
                break
    except Exception:
        # Prefill is a convenience; a search failure yields an empty-evidence
        # stub the agent fills in, never a hard error.
        evidence = []

    return Contract(
        contract_id=str(uuid6.uuid7()),
        task=task,
        task_type=_infer_task_type(task),
        evidence_refs=evidence,
        planned_approach="",
        acceptance_criteria=[],
        verification_steps=[],
        model=model,
        role=role,
        created_at=_now(),
    )


def auto_contract(task: str, workspace: str | Path | None = None, model: str = "auto", role: str = "builder") -> Contract:
    """A minimal, immediately-valid 3-line contract for trivial work -- the
    proportionality escape so tiny tasks aren't taxed. Typed `chore`, with a
    single self-evident criterion/step, no evidence required."""
    return Contract(
        contract_id=str(uuid6.uuid7()),
        task=task,
        task_type="chore",
        evidence_refs=[],
        planned_approach=f"trivial: {task}",
        acceptance_criteria=["the described change is made"],
        verification_steps=["re-read the change; run the relevant test if one exists"],
        model=model,
        role=role,
        created_at=_now(),
    )


def contracts_dir(workspace: str | Path | None = None) -> Path:
    return resolve_workspace_override(workspace) / CONTRACTS_DIRNAME


def save_contract(contract: Contract, workspace: str | Path | None = None) -> Path:
    d = contracts_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{contract.contract_id}.json"
    path.write_text(json.dumps(contract.to_dict(), indent=2), encoding="utf-8")
    return path


def load_contract(contract_id: str, workspace: str | Path | None = None) -> Contract | None:
    path = contracts_dir(workspace) / f"{contract_id}.json"
    if not path.exists():
        return None
    return Contract.from_dict(json.loads(path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import make_stdio_encoding_safe

    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(
        description="Cortex approach contract (Phase 4.1): prefill one from the corpus"
    )
    parser.add_argument("--task", required=True, help="the task you're about to start")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--model", default="auto")
    parser.add_argument("--role", default="builder")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    con = prefill_contract(args.task, workspace=args.workspace, model=args.model, role=args.role)
    if args.json:
        print(json.dumps(con.to_dict(), indent=2))
    else:
        print(f"contract_id:   {con.contract_id}")
        print(f"task_type:     {con.task_type}  (edit if wrong)")
        print("evidence_refs (prefilled from the corpus -- prune/add):")
        for ref in con.evidence_refs:
            print(f"  - {ref}")
        print("\nFill in before your first write:")
        print("  planned_approach, acceptance_criteria[], verification_steps[]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
