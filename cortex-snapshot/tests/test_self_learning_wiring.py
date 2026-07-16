"""GAP G1 (auto-wiring): a closeout — or a deterministic gate failure — auto-mints
a QUARANTINED pattern CANDIDATE without hand-running any CLI.

TDD (RED-first): these pin the flywheel wiring that G1 asks for. Before this, the
oracle miner (`self_learning.mine`) and the repeat-class detector
(`patterns.promote_candidates`) only ran from a CLI, `gate_failures.jsonl` was never
written, and closeouts fed nothing. These tests require:

  1. writing closeouts through the normal write path (`audit.write_closeout`) alone
     produces a pattern candidate (no CLI, no manual mine call) — the proof metric.
  2. the deterministic regression oracle gates it: an UNVERIFIABLE closeout mints
     nothing (never guessed), a failed→passed pair mints a `positive`.
  3. a gate failure is recorded to `gate_failures.jsonl` AND mints an anti_pattern
     candidate.
  4. minting is idempotent per task_key (upsert, not duplicate-append) and the
     candidates are quarantined (never auto-promoted to an active pattern).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_core import self_learning as sl
from cortex_core import patterns as p
from cortex_core.audit import write_closeout


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    return ws


# --------------------------------------------------------------- the proof metric: no CLI
def test_closeout_write_path_auto_mints_pattern_candidate(tmp_path):
    ws = _make_ws(tmp_path)
    # A failed attempt, then a passing attempt at the SAME task — written through the
    # normal closeout write path, NOT via any CLI or manual mine() call.
    write_closeout(ws, "Fix the flaky parser", "first cut broke",
                   status="failed", tests="1 failed")
    write_closeout(ws, "Fix the flaky parser", "corrected the tokenizer",
                   status="completed", tests="9 passed")

    cands = p.load_pattern_candidates(ws)
    mine = [c for c in cands if c["task_key"] == sl.task_key("Fix the flaky parser")]
    assert len(mine) == 1, "the closeout write path alone must mint exactly one candidate"
    assert mine[0]["label"] == sl.POSITIVE
    # quarantined — never an auto-promoted active pattern.
    assert mine[0]["promoted"] is False
    assert mine[0]["promotion_status"] == "quarantined"
    # and it did NOT leak into the indexed active-pattern corpus.
    assert p.load_patterns(ws) == []


def test_unverifiable_closeout_mints_nothing(tmp_path):
    ws = _make_ws(tmp_path)
    # No deterministic outcome anywhere -> UNVERIFIABLE -> never guessed into a candidate.
    write_closeout(ws, "Investigate the flakiness", "poked around",
                   status="in-progress", tests="")
    write_closeout(ws, "Investigate the flakiness", "still poking",
                   status="unknown", tests="")
    assert p.load_pattern_candidates(ws) == []


def test_never_fixed_failure_mints_anti_pattern(tmp_path):
    ws = _make_ws(tmp_path)
    write_closeout(ws, "Wire the broken gate", "attempt 1", status="failed", tests="2 failed")
    write_closeout(ws, "Wire the broken gate", "attempt 2", status="failed", tests="3 failed")
    cands = [c for c in p.load_pattern_candidates(ws)
             if c["task_key"] == sl.task_key("Wire the broken gate")]
    assert len(cands) == 1
    assert cands[0]["label"] == sl.ANTI_PATTERN


def test_mint_is_idempotent_per_task_key(tmp_path):
    ws = _make_ws(tmp_path)
    write_closeout(ws, "Fix the parser", "broke", status="failed", tests="1 failed")
    # first mint: anti_pattern (only a failure so far)
    first = [c for c in p.load_pattern_candidates(ws)
             if c["task_key"] == sl.task_key("Fix the parser")]
    assert first and first[0]["label"] == sl.ANTI_PATTERN
    # a later passing attempt upgrades the SAME candidate to positive — not a 2nd row.
    write_closeout(ws, "Fix the parser", "fixed", status="completed", tests="7 passed")
    after = [c for c in p.load_pattern_candidates(ws)
             if c["task_key"] == sl.task_key("Fix the parser")]
    assert len(after) == 1, "same task_key must upsert, not duplicate-append"
    assert after[0]["label"] == sl.POSITIVE


# --------------------------------------------------------------- gate-failure path
def test_record_gate_failure_populates_ledger_and_mints_candidate(tmp_path):
    ws = _make_ws(tmp_path)
    out = sl.record_gate_failure(ws, gate="contract_gate", tool="cortex_write_log",
                                 detail="no approved contract for this session")
    ledger = ws / "audit" / "self-learning" / "gate_failures.jsonl"
    assert ledger.is_file(), "gate_failures.jsonl must be populated (was previously never written)"
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["gate"] == "contract_gate"
    # a gate failure is a deterministic regression signal -> mints an anti_pattern candidate.
    assert out["candidate"] is not None
    assert out["candidate"]["label"] == sl.ANTI_PATTERN
    cands = p.load_pattern_candidates(ws)
    assert any(c["reason"].startswith("deterministic gate failure") for c in cands)


def test_on_closeout_is_deterministic_and_judge_free():
    # the wiring hook must not import any model/network/promotion module.
    import ast
    tree = ast.parse(Path(sl.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    banned = ("judge", "codex_judge", "openai", "anthropic", "requests", "httpx",
              "urllib.request", "urllib", "socket", "http.client", "promotion")
    hits = [m for m in imported for b in banned if b in m]
    assert not hits, f"self_learning wiring must stay judge/network free: {hits}"
