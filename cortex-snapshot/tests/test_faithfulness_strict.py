"""Tests for the hardened "strict" faithfulness backend (per-citation statuses).

The frozen lexical-backend tests live in test_faithfulness.py and must keep passing
unchanged; everything here exercises only the opt-in strict backend."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core.faithfulness import (  # noqa: E402
    STRICT_STATUSES,
    decompose_claims_cited,
    faithfulness,
    strict_status,
)

SOURCES = {
    "S1": "The rebuild lock uses an atomic O_CREAT flag. Recall@5 was 0.82.",
    "S2": "At 40,000 events per second the ingest overhead measured 30%. "
          "Cold-start latency measured 210ms in testing. "
          "The total cost came to $1,200 for the month. "
          "Adoption reached 48% in the survey.",
}


# ---------------------------------------------------------------- statuses

def test_uncited():
    assert strict_status("Latency was 210ms.", [], SOURCES) == "UNCITED"


def test_unresolved_citation():
    assert strict_status("Latency was 210ms [S9].", ["S9"], SOURCES) == "UNRESOLVED_CITATION"


def test_quote_supported_tolerates_case_and_punctuation():
    s = strict_status('The design used an "atomic O_CREAT flag" [S1].', ["S1"], SOURCES)
    assert s == "QUOTE_SUPPORTED"


def test_quote_supported_ellipsis_segments():
    src = {"S1": "the rebuild lock uses an atomic flag under contention"}
    s = strict_status('It says "the rebuild lock ... atomic flag" [S1].', ["S1"], src)
    assert s == "QUOTE_SUPPORTED"


def test_quote_unsupported():
    s = strict_status('It relied on a "Redis-backed queue" [S1].', ["S1"], SOURCES)
    assert s == "QUOTE_UNSUPPORTED"


def test_number_supported_thousands_separator():
    src = {"S1": "The pipeline sustained 40000 events per second."}
    s = strict_status("Throughput reached 40,000 events per second [S1].", ["S1"], src)
    assert s == "NUMBER_SUPPORTED"


def test_number_supported_unit_attached():
    s = strict_status("Cold-start latency was 210ms [S2].", ["S2"], SOURCES)
    assert s == "NUMBER_SUPPORTED"


def test_number_supported_dollar_prefix():
    s = strict_status("The run cost $1,200 in total [S2].", ["S2"], SOURCES)
    assert s == "NUMBER_SUPPORTED"


def test_number_supported_spelled_out_percent():
    s = strict_status("Adoption hit 48 percent [S2].", ["S2"], SOURCES)
    assert s == "NUMBER_SUPPORTED"


def test_contradicted_same_metric_different_value():
    s = strict_status("Ingest overhead was 12% at 40,000 events per second [S2].",
                      ["S2"], SOURCES)
    assert s == "CONTRADICTED"


def test_contradicted_metric_after_number():
    src = {"S1": "Cold-start latency measured 340ms under sustained load."}
    s = strict_status("Cold-start latency was 210ms [S1].", ["S1"], src)
    assert s == "CONTRADICTED"


def test_number_unsupported_when_no_same_metric():
    src = {"S1": "The corpus holds 151 candidate sources."}
    s = strict_status("Latency was 210ms [S1].", ["S1"], src)
    assert s == "NUMBER_UNSUPPORTED"


def test_unverifiable_abstains_without_anchor():
    s = strict_status("The design is modular and easy to extend [S1].", ["S1"], SOURCES)
    assert s == "UNVERIFIABLE"


def test_all_statuses_are_declared():
    assert "CONTRADICTED" in STRICT_STATUSES and "UNVERIFIABLE" in STRICT_STATUSES


# ---------------------------------------------------------------- per-citation boundary

def test_citation_boundary_not_blended():
    # the number exists in S2, but the claim cites S1 -> must NOT count as supported
    s = strict_status("Cold-start latency was 210ms [S1].", ["S1"], SOURCES)
    assert s in ({"NUMBER_UNSUPPORTED", "CONTRADICTED"})
    assert s != "NUMBER_SUPPORTED"


def test_decompose_claims_cited_extracts_markers():
    pairs = decompose_claims_cited("Recall@5 reached 0.82 [S1]. Overhead was 12% [S2, S3].")
    assert pairs[0][1] == ["S1"] and pairs[1][1] == ["S2", "S3"]


# ---------------------------------------------------------------- end-to-end backend

def test_strict_end_to_end_scoring_and_abstention():
    digest = ("Recall@5 reached 0.82 [S1]. "
              "Ingest overhead was 12% at 40,000 events per second [S2]. "
              "The design is modular and easy to extend [S1].")
    r = faithfulness(digest, SOURCES, backend="strict")
    assert r.backend == "strict" and not r.empty_context
    statuses = [c["status"] for c in r.per_claim]
    assert statuses == ["NUMBER_SUPPORTED", "CONTRADICTED", "UNVERIFIABLE"]
    # abstained claim excluded from denominator: 1 supported / 2 decided
    assert r.total == 2 and r.supported == 1 and r.score == 0.5 and not r.passed
    assert r.per_claim[2]["grounded"] is None


def test_strict_empty_context_auto_fails():
    r = faithfulness("Everything went perfectly and nothing failed.", [], backend="strict")
    assert r.empty_context and r.score == 0.0 and not r.passed


def test_strict_all_abstain_does_not_pass():
    r = faithfulness("The design is modular and clean [S1].", SOURCES, backend="strict")
    assert r.total == 0 and r.score == 0.0 and not r.passed
    assert r.per_claim[0]["status"] == "UNVERIFIABLE"


def test_strict_no_markers_degrades_to_all_sources():
    r = faithfulness("Cold-start latency was 210ms.",
                     ["Cold-start latency measured 210ms in testing."], backend="strict")
    assert r.passed and r.per_claim[0]["status"] == "NUMBER_SUPPORTED"


def test_strict_does_not_change_default_backend():
    r = faithfulness("Recall@5 reached 0.82.", ["Recall@5 was 0.82."])
    assert r.backend == "lexical" and r.passed
    assert "status" not in r.per_claim[0]
