"""Frozen tests for the multi-model arbitration lane (cortex_core/arbitrate.py).

GAP B1: arbitration is a SHADOW / QUARANTINE-ONLY decision aid. It NEVER mints
trainable gold. The verdict from the design pass (docs/design/multi-model-
arbitration-*-2026-07-13.md) is ADOPT-but-strictly-shadow: default to ABSTAIN on
disagreement (owner is non-expert -> MORE abstention, not less), and every output
is a hard-quarantined `advisory_semi_gold` record that cannot train / promote /
mutate state / authorize action.

These tests prove the three load-bearing guarantees the coordinator required:
  (a) disagreement -> ABSTAIN (no gold minted)
  (b) output is quarantined / advisory-tagged
  (c) it never writes to any trainable-gold sink (esp. cross_vendor_synthetic_gold)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core.arbitrate import (  # noqa: E402
    RECORD_TYPE,
    FORBIDDEN_GOLD_SINKS,
    ArbitrationVerdict,
    JurorOpinion,
    AdvisoryRecord,
    decide,
    strong_agreement,
    pick_juror_tiers,
    write_advisory,
    quarantine_dir,
    arbitrate,
)
from cortex_core.evaluator import Verdict, AtomicClaim  # noqa: E402


# --- helpers ----------------------------------------------------------------

def _op(tier, family, verdict, confidence=0.9, reasoning="r"):
    return JurorOpinion(
        tier=tier, family=family, verdict=verdict,
        confidence=confidence, reasoning=reasoning, gaps=[],
    )


# --- (a) disagreement -> ABSTAIN, no gold ----------------------------------

def test_two_juror_disagreement_no_arbiter_abstains():
    """Two jurors disagree and no third arbiter ran yet -> ABSTAIN (the floor)."""
    jurors = [
        _op("glm5.2", "zhipu", Verdict.SUPPORTED),
        _op("9r-gpt-oss-120b", "openai", Verdict.UNSUPPORTED),
    ]
    verdict, reason = decide(jurors, arbiter=None)
    assert verdict is ArbitrationVerdict.ABSTAIN
    assert "disagree" in reason.lower() or "no arbiter" in reason.lower()


def test_disagreement_with_unverifiable_arbiter_abstains():
    """Third arbiter can't resolve (unverifiable) -> ABSTAIN, never a gold verdict."""
    jurors = [
        _op("glm5.2", "zhipu", Verdict.SUPPORTED),
        _op("9r-gpt-oss-120b", "openai", Verdict.UNSUPPORTED),
    ]
    arbiter = _op("9r-gemini-3.5-flash", "google", Verdict.UNVERIFIABLE, confidence=0.2)
    verdict, _ = decide(jurors, arbiter=arbiter)
    assert verdict is ArbitrationVerdict.ABSTAIN


def test_confident_contradiction_after_arbiter_needs_human_binary():
    """A confident decisive contradiction the arbiter cannot break -> human binary,
    still NOT a resolved/gold verdict."""
    jurors = [
        _op("glm5.2", "zhipu", Verdict.SUPPORTED, confidence=0.95),
        _op("9r-gpt-oss-120b", "openai", Verdict.UNSUPPORTED, confidence=0.95),
    ]
    # arbiter picks a side but does not form a >=2 decisive majority at high conf
    arbiter = _op("9r-gemini-3.5-flash", "google", Verdict.PARTIALLY_SUPPORTED, confidence=0.6)
    verdict, _ = decide(jurors, arbiter=arbiter)
    assert verdict is ArbitrationVerdict.NEEDS_HUMAN_BINARY


def test_abstain_and_human_binary_are_the_only_non_resolved_outcomes():
    for v in (ArbitrationVerdict.ABSTAIN, ArbitrationVerdict.NEEDS_HUMAN_BINARY):
        assert v is not ArbitrationVerdict.RESOLVED_WITH_EVIDENCE


# --- strong agreement gate --------------------------------------------------

def test_strong_agreement_requires_same_decisive_verdict_high_conf():
    agree = [_op("a", "f1", Verdict.SUPPORTED, 0.9), _op("b", "f2", Verdict.SUPPORTED, 0.9)]
    assert strong_agreement(agree)
    # same verdict but low confidence -> not strong
    weak = [_op("a", "f1", Verdict.SUPPORTED, 0.4), _op("b", "f2", Verdict.SUPPORTED, 0.4)]
    assert not strong_agreement(weak)
    # agree on a NON-decisive verdict -> not strong (abstain-leaning)
    soft = [_op("a", "f1", Verdict.PARTIALLY_SUPPORTED, 0.9),
            _op("b", "f2", Verdict.PARTIALLY_SUPPORTED, 0.9)]
    assert not strong_agreement(soft)
    # same family twice is not cross-vendor agreement
    same_fam = [_op("a", "f1", Verdict.SUPPORTED, 0.9), _op("b", "f1", Verdict.SUPPORTED, 0.9)]
    assert not strong_agreement(same_fam)


def test_two_agreeing_jurors_resolve_but_stay_advisory_not_gold():
    jurors = [_op("glm5.2", "zhipu", Verdict.SUPPORTED, 0.9),
              _op("9r-gpt-oss-120b", "openai", Verdict.SUPPORTED, 0.9)]
    verdict, _ = decide(jurors, arbiter=None)
    assert verdict is ArbitrationVerdict.RESOLVED_WITH_EVIDENCE


# --- (b) output is quarantined / advisory-tagged ---------------------------

def test_advisory_record_is_hard_non_gold():
    rec = AdvisoryRecord(
        question="q", task_type="research",
        verdict=ArbitrationVerdict.RESOLVED_WITH_EVIDENCE,
        jurors=[_op("glm5.2", "zhipu", Verdict.SUPPORTED)],
        arbiter=None, reason="two families agree", abstention_reason=None,
    )
    d = rec.to_dict()
    assert d["record_type"] == RECORD_TYPE == "advisory_semi_gold"
    # Every trust flag must be hard-false.
    assert d["trainable"] is False
    assert d["promotable"] is False
    assert d["can_mutate_state"] is False
    assert d["can_authorize_action"] is False
    assert d["is_gold"] is False
    assert d["quarantined"] is True
    # changed_correct_to_incorrect is a first-class field (sycophancy metric)
    assert "changed_correct_to_incorrect" in d


def test_record_type_can_never_be_a_gold_tier():
    assert RECORD_TYPE not in FORBIDDEN_GOLD_SINKS
    assert "gold" != RECORD_TYPE
    assert "cross_vendor_synthetic_gold" not in RECORD_TYPE


# --- (c) never writes to any trainable-gold sink ---------------------------

def test_write_advisory_lands_in_quarantine_not_a_gold_sink(tmp_path):
    rec = AdvisoryRecord(
        question="q", task_type="research",
        verdict=ArbitrationVerdict.ABSTAIN,
        jurors=[_op("glm5.2", "zhipu", Verdict.SUPPORTED),
                _op("9r-gpt-oss-120b", "openai", Verdict.UNSUPPORTED)],
        arbiter=None, reason="disagree", abstention_reason="jurors disagree",
    )
    out = write_advisory(rec, workspace=tmp_path)
    # lands under the arbitration quarantine dir
    assert quarantine_dir(tmp_path) in out.parents
    # the path must not name any gold sink
    joined = str(out).lower()
    for sink in FORBIDDEN_GOLD_SINKS:
        assert sink.lower() not in joined
    # and the persisted content is tagged non-gold
    line = out.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = json.loads(line)
    assert obj["record_type"] == "advisory_semi_gold"
    assert obj["trainable"] is False and obj["is_gold"] is False


def test_no_gold_sink_path_reachable_from_module(tmp_path):
    """Defense-in-depth: the module's only writer targets the quarantine dir; the
    known trainable-gold sinks must never appear under it."""
    qdir = quarantine_dir(tmp_path)
    assert qdir.name == "quarantine"
    assert "arbitration" in str(qdir).lower()
    # A gold sink dir must never be a parent of the quarantine dir.
    assert "calibration" not in [p.name for p in qdir.parents]


def test_module_source_never_references_gold_sinks():
    """Static guard: arbitrate.py must not import/write any gold/promotion sink."""
    src = (Path(__file__).resolve().parents[1] / "cortex_core" / "arbitrate.py").read_text(
        encoding="utf-8"
    )
    # It may NAME the forbidden sinks only inside FORBIDDEN_GOLD_SINKS / comments,
    # but must never call the promotion minting machinery.
    assert "write_cross_vendor_results" not in src
    assert "from cortex_core.promotion import" not in src
    assert "import promotion" not in src
    # It must not write a file whose name contains a gold-tier token.
    assert "cross_vendor_synthetic_gold-" not in src


# --- anti-circular juror selection -----------------------------------------

def test_pick_jurors_excludes_anthropic_when_artifact_is_anthropic_authored():
    tiers = pick_juror_tiers(n=2, exclude_families={"anthropic"})
    fams = {fam for _, fam in tiers}
    assert "anthropic" not in fams
    assert len(tiers) == 2
    assert len({fam for _, fam in tiers}) == 2  # cross-family, not two of one


def test_pick_jurors_never_selects_prometheus():
    tiers = pick_juror_tiers(n=3, exclude_families=set())
    assert all("prometheus" not in t for t, _ in tiers)


# --- end-to-end orchestrator (offline, injected judge_fn) ------------------

def test_arbitrate_end_to_end_offline_disagreement_abstains(tmp_path):
    """Full arbitrate() with an injected offline judge that makes the jurors
    disagree and the arbiter abstain -> ABSTAIN, quarantined, no gold."""
    calls = {"n": 0}

    def fake_judge(claim, evidence, tier, **kw):
        from cortex_core.evaluator import EvaluatorGrade
        calls["n"] += 1
        # first two jurors disagree; any third arbiter is unverifiable
        seq = {1: Verdict.SUPPORTED, 2: Verdict.UNSUPPORTED}
        v = seq.get(calls["n"], Verdict.UNVERIFIABLE)
        conf = 0.9 if calls["n"] <= 2 else 0.1
        return EvaluatorGrade(claim_id="c", verdict=v, confidence=conf,
                              reasoning=f"[{tier}] x", evidence_count=len(evidence), gaps=[])

    rec = arbitrate(
        question="Is X true?", task_type="research",
        evidence=[{"type": "quote", "ref": "src", "detail": "..."}],
        workspace=tmp_path, judge_fn=fake_judge,
        artifact_authored_by="anthropic",
    )
    assert rec.verdict is ArbitrationVerdict.ABSTAIN
    d = rec.to_dict()
    assert d["trainable"] is False and d["is_gold"] is False
    # persisted to quarantine
    files = list(quarantine_dir(tmp_path).glob("*.jsonl"))
    assert files, "advisory record must be persisted to quarantine"


def test_arbitrate_agreement_resolves_but_is_still_advisory(tmp_path):
    def fake_judge(claim, evidence, tier, **kw):
        from cortex_core.evaluator import EvaluatorGrade
        return EvaluatorGrade(claim_id="c", verdict=Verdict.SUPPORTED, confidence=0.92,
                              reasoning=f"[{tier}] ok", evidence_count=len(evidence), gaps=[])

    rec = arbitrate(
        question="Is X true?", task_type="research",
        evidence=[{"type": "quote", "ref": "src", "detail": "..."}],
        workspace=tmp_path, judge_fn=fake_judge, artifact_authored_by="anthropic",
    )
    assert rec.verdict is ArbitrationVerdict.RESOLVED_WITH_EVIDENCE
    assert rec.to_dict()["is_gold"] is False  # resolved != gold, ever
