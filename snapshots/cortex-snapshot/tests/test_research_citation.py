"""Frozen tests for the Stage-2D research citation/evidence checkers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_research.claim_extractor import extract_claims  # noqa: E402
from evals.objective_research.citation_checker import check_claim  # noqa: E402
from evals.objective_research.contradiction_checker import check_contradiction  # noqa: E402
from evals.objective_research.fixtures import FIXTURES  # noqa: E402
from evals.objective_research.run_research import status_for  # noqa: E402


def test_extractor_finds_citations_quotes_numbers():
    claims = extract_claims('Recall rose to 0.82 [S1]. The team called it "a big win" [S2].')
    assert claims[0].citations == ["S1"] and "0.82" in claims[0].numbers
    assert claims[1].citations == ["S2"] and claims[1].quotes == ["a big win"]


def test_quote_supported_vs_unsupported():
    src = {"S1": 'the report says a consistent improvement across queries'}
    c = extract_claims('They found "a consistent improvement across queries" [S1].')[0]
    assert check_claim(c, src).status == "QUOTE_SUPPORTED"
    c2 = extract_claims('They found "a dramatic breakthrough" [S1].')[0]
    assert check_claim(c2, src).status == "QUOTE_UNSUPPORTED"


def test_number_supported_vs_unsupported():
    src = {"S1": "recall@5 reached 0.82 on the benchmark"}
    assert check_claim(extract_claims('Recall@5 was 0.82 [S1].')[0], src).status == "NUMBER_SUPPORTED"
    assert check_claim(extract_claims('Recall@5 was 0.95 [S1].')[0], src).status == "NUMBER_UNSUPPORTED"


def test_uncited_and_unresolved():
    assert check_claim(extract_claims('Retrieval improved a lot.')[0], {}).status == "UNCITED"
    assert check_claim(extract_claims('Vector search hit 0.80 [S9].')[0], {"S1": "x"}).status == "UNRESOLVED_CITATION"


def test_unverifiable_abstains():
    src = {"S1": "the benchmark ran on the corpus"}
    v = check_claim(extract_claims('The design is elegant and maintainable [S1].')[0], src)
    assert v.status == "UNVERIFIABLE" and v.objective is False


def test_numeric_contradiction():
    contra = check_contradiction("the p99 latency of 12ms was achieved",
                                 "benchmarks show a p99 latency of 30ms under load")
    assert contra and contra["claim_value"] == 12.0 and contra["source_value"] == 30.0


def test_all_fixture_expectations_match_checker():
    for fx in FIXTURES:
        for claim in extract_claims(fx["report"]):
            exp = fx["expected"].get(claim.idx)
            if exp is None:
                continue
            status, _, _ = status_for(claim, fx["sources"])
            assert status == exp, f"{fx['id']}#{claim.idx}: got {status}, expected {exp}"
