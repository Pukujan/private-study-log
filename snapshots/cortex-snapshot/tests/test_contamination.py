"""Frozen tests for benchmark contamination controls (gap J6).

Guarantee under test: a row whose INPUT hash matches a known-public benchmark sha is REFUSED
for trainable gold (dedup vs an independent public-sha manifest); a novel input passes. No
model, no network, no judge anywhere in the path.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.contamination import (  # noqa: E402
    Manifest,
    MutatedVariantProvenance,
    contamination_check,
    input_hash,
    not_public_contaminated_gate,
    record_mutated_variant,
    time_holdout_partition,
)
from evals.contamination.seed import PUBLIC_SAMPLES, build  # noqa: E402


def _manifest_with(public_input, benchmark="public_bench"):
    m = Manifest()
    m.add(public_input, benchmark=benchmark, note="seeded public")
    return m


# --- input hashing is canonical --------------------------------------------------------------
def test_input_hash_canonical_across_container_shape():
    # dict key order must not change the hash (canonicalization)
    assert input_hash({"a": 1, "b": 2}) == input_hash({"b": 2, "a": 1})
    # a precomputed 64-hex sha passes through unchanged
    h = input_hash("some prompt")
    assert input_hash(h) == h
    assert input_hash("x") != input_hash("y")


# --- the core RED->GREEN contract ------------------------------------------------------------
def test_known_public_hash_is_flagged():
    pub = "What is 2+2? Explain your reasoning step by step."
    m = _manifest_with(pub, benchmark="gsm8k")
    res = contamination_check(pub, m)
    assert res.risk_flag is True
    assert res.benchmark == "gsm8k"
    assert res.sha256 == input_hash(pub)


def test_novel_input_passes():
    m = _manifest_with("a known public benchmark question")
    res = contamination_check("a brand new private task never published anywhere", m)
    assert res.risk_flag is False


def test_known_public_hash_is_refused_trainable_gold():
    pub = "Public benchmark prompt X that leaked into the wild."
    m = _manifest_with(pub, benchmark="leaky_bench")
    gate = not_public_contaminated_gate({"input": pub}, manifest=m)
    assert gate.passed is False
    assert "leaky_bench" in gate.detail


def test_novel_input_passes_the_gate():
    m = _manifest_with("public thing")
    gate = not_public_contaminated_gate({"input": "a genuinely novel private input"}, manifest=m)
    assert gate.passed is True


def test_gate_accepts_precomputed_input_hash():
    pub = "another public prompt"
    m = _manifest_with(pub)
    gate = not_public_contaminated_gate({"input_hash": input_hash(pub)}, manifest=m)
    assert gate.passed is False


def test_gate_passes_when_no_input_present():
    # nothing to check -> gate can only ever REFUSE a positive match, never fabricate one.
    m = _manifest_with("public")
    assert not_public_contaminated_gate({"label_authority": "checker"}, manifest=m).passed is True


# --- committed seed manifest is real & loadable ---------------------------------------------
def test_seed_manifest_flags_a_seeded_public_sample():
    m = build()
    benchmark, sample, _ = PUBLIC_SAMPLES[0]
    res = contamination_check(sample, m)
    assert res.risk_flag is True and res.benchmark == benchmark
    assert len(m) >= len(PUBLIC_SAMPLES)


# --- manifest round-trips on disk ------------------------------------------------------------
def test_manifest_save_load_roundtrip(tmp_path):
    m = Manifest()
    m.add("pub one", benchmark="b1")
    m.add(sha=input_hash("pub two"), benchmark="b2")
    p = m.save(tmp_path / "man.jsonl")
    m2 = Manifest.load(p)
    assert len(m2) == 2
    assert contamination_check("pub one", m2).risk_flag is True


# --- time-based holdout ----------------------------------------------------------------------
def test_time_holdout_partition_splits_on_cutoff():
    recs = [
        {"id": "old", "created_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "created_at": "2026-12-01T00:00:00Z"},
        {"id": "nodate"},
    ]
    parts = time_holdout_partition(recs, cutoff="2026-07-01T00:00:00Z")
    assert [r["id"] for r in parts["train"]] == ["old"]
    assert [r["id"] for r in parts["holdout"]] == ["new"]
    assert [r["id"] for r in parts["undated"]] == ["nodate"]


def test_time_holdout_accepts_datetime_cutoff():
    recs = [{"id": "a", "created_at": "2025-01-01"}]
    parts = time_holdout_partition(recs, cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert [r["id"] for r in parts["train"]] == ["a"]


# --- mutated-variant provenance --------------------------------------------------------------
def test_mutated_variant_must_be_distinct_from_public_origin():
    pub = "public benchmark input"
    variant = "public benchmark input (mutated: numbers changed, entities swapped)"
    prov = record_mutated_variant(pub, variant, mutation="renumber+swap", source_benchmark="gsm8k")
    assert isinstance(prov, MutatedVariantProvenance)
    assert prov.is_distinct is True
    assert prov.public_sha == input_hash(pub)
    assert prov.variant_sha == input_hash(variant)


def test_byte_identical_variant_is_not_distinct():
    pub = "unchanged"
    prov = record_mutated_variant(pub, pub, mutation="none")
    assert prov.is_distinct is False


# --- anti-circular wiring into the trainable-gold path (promotion) ---------------------------
def test_promotion_hard_gold_refuses_a_public_contaminated_row():
    from cortex_core import promotion
    pub = "leaked public benchmark question"
    m = Manifest()
    m.add(pub, benchmark="public_leak")
    # Point the promotion gate at our test manifest via manifest_path.
    p = m.save(ROOT / "evals" / "contamination" / "_test_tmp_manifest.jsonl")
    try:
        ev = {"label_authority": "bfcl_ast_checker", "objective_verdict": "pass",
              "checker_decided": True, "input": pub, "contamination_manifest_path": str(p)}
        d = promotion.decide("row1", ev, "hard_gold")
        assert d.state == promotion.State.QUARANTINED
        assert any("not_public_contaminated" in r for r in d.reasons)
        # a novel input with the same checker evidence still promotes
        ev2 = {**ev, "input": "a genuinely novel private input never public"}
        d2 = promotion.decide("row2", ev2, "hard_gold")
        assert d2.state == promotion.State.PROMOTED and d2.tier == "hard_gold"
    finally:
        p.unlink(missing_ok=True)
