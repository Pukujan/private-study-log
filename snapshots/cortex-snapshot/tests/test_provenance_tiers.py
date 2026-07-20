"""Tests for the never-wait provenance-tier trust model.

Proves the three owner-required properties:
  (a) EVERY minted/used record carries a provenance tier.
  (b) a `non_human_verified` record is USABLE (never blocked pending a human).
  (c) a recorded exit-code closeout makes `self_learning` mint a real
      positive / anti_pattern (deterministically, no judge).
Plus the no-masquerade backstop: a claimed tier can't exceed its evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from cortex_core import provenance_tiers as pt
from cortex_core import promotion
from cortex_core import self_learning
from cortex_core.audit import test_evidence as make_test_evidence  # aliased: pytest else collects it
from cortex_core.audit import write_closeout


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


# --------------------------------------------------------------------------- (a) every record carries a tier
def test_every_closeout_carries_a_provenance_tier(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    path = write_closeout(ws, task="do a thing", result="did it", status="completed")
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert pt.FIELD in data
    assert pt.is_valid_tier(data[pt.FIELD])
    # An honest self-reported closeout is non_human_verified by default — usable now.
    assert data[pt.FIELD] == pt.NON_HUMAN_VERIFIED


def test_every_promotion_decision_carries_a_provenance_tier() -> None:
    ev = {"checker_decided": True, "objective_verdict": "pass", "label_authority": "checker"}
    d = promotion.classify("item-1", ev).asdict()
    assert d["provenance_tier"] == pt.HARD_GOLD
    # a quarantined decision still carries a (valid) tier
    q = promotion.classify("item-2", {}).asdict()
    assert pt.is_valid_tier(q["provenance_tier"])
    assert q["provenance_tier"] == pt.QUARANTINE


def test_stamp_always_sets_a_valid_tier() -> None:
    rec: dict = {}
    pt.stamp(rec)
    assert rec[pt.FIELD] == pt.NON_HUMAN_VERIFIED  # no evidence -> honest default


# --------------------------------------------------------------------------- (b) non_human_verified is USABLE
def test_non_human_verified_record_is_usable_not_blocked() -> None:
    rec = {pt.FIELD: pt.NON_HUMAN_VERIFIED}
    assert pt.is_usable(rec) is True          # usable NOW
    assert pt.is_trainable(rec) is False      # but not trainable
    # and it is NOT filtered out of a usable set (the anti-block property)
    records = [
        {pt.FIELD: pt.NON_HUMAN_VERIFIED, "id": "unreviewed"},
        {pt.FIELD: pt.QUARANTINE, "id": "unsafe"},
        {pt.FIELD: pt.HARD_GOLD, "id": "gold"},
    ]
    keep = {r["id"] for r in pt.usable_records(records)}
    assert keep == {"unreviewed", "gold"}     # only quarantine is dropped


def test_only_quarantine_is_unusable() -> None:
    for tier in pt.TIER_ORDER:
        assert pt.is_usable(tier) == (tier != pt.QUARANTINE)


def test_human_verify_is_an_upgrade_not_a_gate() -> None:
    rec = {pt.FIELD: pt.NON_HUMAN_VERIFIED, "id": "x"}
    assert pt.is_usable(rec)                    # already usable before any human touches it
    pt.upgrade_to_human_verified(rec, reviewer="pujan")
    assert rec[pt.FIELD] == pt.HUMAN_VERIFIED
    assert rec["provenance_prior_tier"] == pt.NON_HUMAN_VERIFIED
    assert pt.is_trainable(rec)                 # the upgrade raised confidence


# --------------------------------------------------------------------------- no-masquerade backstop
def test_claimed_gold_without_evidence_is_downgraded() -> None:
    rec: dict = {}
    pt.stamp(rec, evidence=None, claimed_tier=pt.HARD_GOLD)
    assert rec[pt.FIELD] == pt.NON_HUMAN_VERIFIED       # cannot mint gold with no evidence
    assert rec["provenance_downgraded"]["claimed"] == pt.HARD_GOLD
    assert rec["provenance_downgraded"]["granted"] == pt.NON_HUMAN_VERIFIED


def test_claimed_gold_with_checker_evidence_is_honored() -> None:
    rec: dict = {}
    ev = {"checker_decided": True, "objective_verdict": "pass"}
    pt.stamp(rec, evidence=ev, claimed_tier=pt.HARD_GOLD)
    assert rec[pt.FIELD] == pt.HARD_GOLD
    assert "provenance_downgraded" not in rec


def test_consensus_masquerade_is_downgraded_without_three_families() -> None:
    rec: dict = {}
    ev = {"agreeing_families": ["anthropic", "openai"],  # only 2 families
          "prometheus_present": True}
    pt.stamp(rec, evidence=ev, claimed_tier=pt.SYNTHETIC_CONSENSUS)
    assert rec[pt.FIELD] == pt.NON_HUMAN_VERIFIED
    # a genuine 3-family blinded consensus IS honored
    rec2: dict = {}
    ev2 = {"agreeing_families": ["anthropic", "openai", "zhipu"], "prometheus_present": True}
    pt.stamp(rec2, evidence=ev2, claimed_tier=pt.SYNTHETIC_CONSENSUS)
    assert rec2[pt.FIELD] == pt.SYNTHETIC_CONSENSUS


# --------------------------------------------------------------------------- sol@xhigh hardening
def test_quarantine_vetoes_before_gold() -> None:
    """sol@xhigh #5: contradictory evidence (a checker 'pass' AND an undecidable flag)
    must resolve to quarantine, never gold — the unsafe signal vetoes first."""
    ev = {"checker_decided": True, "objective_verdict": "pass", "undecidable": True}
    assert pt.derive_tier(ev) == pt.QUARANTINE


def test_human_verified_is_not_derivable_from_a_boolean() -> None:
    """sol@xhigh #4: a caller-supplied human_verified:true must NOT self-certify the tier."""
    assert pt.derive_tier({"human_verified": True}) != pt.HUMAN_VERIFIED


def test_upgrade_rejects_placeholder_reviewer() -> None:
    import pytest
    for bad in ("", "human", "agent", "auto"):
        with pytest.raises(ValueError):
            pt.upgrade_to_human_verified({pt.FIELD: pt.NON_HUMAN_VERIFIED}, reviewer=bad)


def test_is_authoritative_blocks_unreviewed_data_from_defining_truth() -> None:
    """sol@xhigh #1: non_human_verified/advisory are usable as context but may not define
    tests/rubrics/promotion evidence; only the gold-grade tiers are authoritative."""
    assert pt.is_authoritative(pt.HARD_GOLD)
    assert pt.is_authoritative(pt.SYNTHETIC_CONSENSUS)
    assert pt.is_authoritative(pt.HUMAN_VERIFIED)
    assert not pt.is_authoritative(pt.NON_HUMAN_VERIFIED)
    assert not pt.is_authoritative(pt.ADVISORY)


def test_verify_stamp_catches_a_masquerade() -> None:
    """sol@xhigh #3: ingestion/training-export must re-derive, not trust a stored label."""
    ok, _ = pt.verify_stamp({pt.FIELD: pt.HARD_GOLD}, evidence=None)   # no checker evidence
    assert ok is False
    ok2, _ = pt.verify_stamp(
        {pt.FIELD: pt.HARD_GOLD},
        evidence={"checker_decided": True, "objective_verdict": "pass"},
    )
    assert ok2 is True
    # human_verified is honored only with a real reviewer receipt
    assert pt.verify_stamp({pt.FIELD: pt.HUMAN_VERIFIED, "human_verified_by": "pujan"})[0] is True
    assert pt.verify_stamp({pt.FIELD: pt.HUMAN_VERIFIED})[0] is False


# --------------------------------------------------------------------------- (c) exit-code closeout -> self_learning mints
def _closeout(ws: Path, task: str, exit_code: int, ref: str, ts: str) -> None:
    """Write a closeout carrying a machine test-exit-code evidence item."""
    path = write_closeout(
        ws, task=task, result="attempt", status="completed",
        evidence=[make_test_evidence(exit_code, ref)],
    )
    # pin an explicit timestamp so the miner's chronological ordering is deterministic
    jp = path.with_suffix(".json")
    data = json.loads(jp.read_text(encoding="utf-8"))
    data["timestamp"] = ts
    jp.write_text(json.dumps(data), encoding="utf-8")


def test_exit_code_closeouts_mint_a_positive(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    task = "fix the flaky parser"
    _closeout(ws, task, exit_code=1, ref="pytest tests/test_parser.py", ts="2026-07-14T01:00:00Z")
    _closeout(ws, task, exit_code=0, ref="pytest tests/test_parser.py", ts="2026-07-14T02:00:00Z")

    records = self_learning.load_closeouts(ws / "audit")
    candidates = self_learning.mine(records)
    mine_for = {c["task_key"]: c for c in candidates}
    key = self_learning.task_key(task)
    assert key in mine_for
    assert mine_for[key]["label"] == self_learning.POSITIVE
    # the deciding signal was the structured exit code, not prose
    assert "test_evidence_exit" in mine_for[key]["outcome_signals"]
    # nothing is auto-promoted — it lands quarantined for later (human or checker) promotion
    assert mine_for[key]["promoted"] is False


def test_exit_code_closeout_mints_an_anti_pattern(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    task = "make the impossible endpoint work"
    _closeout(ws, task, exit_code=1, ref="pytest tests/test_endpoint.py", ts="2026-07-14T03:00:00Z")
    _closeout(ws, task, exit_code=2, ref="pytest tests/test_endpoint.py", ts="2026-07-14T04:00:00Z")

    candidates = self_learning.mine(self_learning.load_closeouts(ws / "audit"))
    c = {x["task_key"]: x for x in candidates}[self_learning.task_key(task)]
    assert c["label"] == self_learning.ANTI_PATTERN


def test_test_evidence_shape_is_read_by_self_learning() -> None:
    """The writer's evidence item must be exactly what the miner's strongest signal reads."""
    ev = make_test_evidence(0, "pytest -q")
    assert ev["type"] == "test" and ev["exit_code"] == 0 and ev["passed"] is True
    outcome, signal = self_learning.test_outcome({"task": "t", "evidence": [ev]})
    assert outcome is True and signal == "test_evidence_exit"
    outcome_f, _ = self_learning.test_outcome({"task": "t", "evidence": [make_test_evidence(1, "x")]})
    assert outcome_f is False
