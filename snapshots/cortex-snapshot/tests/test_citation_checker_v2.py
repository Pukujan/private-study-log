"""Frozen tests for the Fable-hardened citation checker v2, promoted into the core research lane.

Promotion invariant: v2 must agree with the INDEPENDENT Stage-2D fixtures (authored by us, not
Fable) — that's what makes it a genuinely better checker rather than one tuned to Fable's own
cases. Plus targeted checks of the fixes it claimed (contradiction, number handling).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_research.citation_checker_v2 import status_for_claim  # noqa: E402
from evals.objective_research.claim_extractor import extract_claims  # noqa: E402
from evals.objective_research.fixtures import FIXTURES  # noqa: E402


def test_agrees_with_independent_stage2d_fixtures():
    # the real objectivity test: match ground truth WE authored, not Fable's
    for fx in FIXTURES:
        for c in extract_claims(fx["report"]):
            exp = fx["expected"].get(c.idx)
            if exp is None:
                continue
            got = status_for_claim(c.text, c.citations, fx["sources"])
            assert got == exp, f"{fx['id']}#{c.idx}: v2 got {got}, expected {exp}"


def test_quote_supported_and_unsupported():
    src = {"S1": "the report notes a consistent improvement across queries"}
    c = extract_claims('They found "a consistent improvement across queries" [S1].')[0]
    assert status_for_claim(c.text, c.citations, src) == "QUOTE_SUPPORTED"
    c2 = extract_claims('They found "a dramatic breakthrough" [S1].')[0]
    assert status_for_claim(c2.text, c2.citations, src) == "QUOTE_UNSUPPORTED"


def test_numeric_contradiction_with_units():
    src = {"S1": "benchmarks show a p99 latency of 30ms under load"}
    c = extract_claims("The p99 latency of 12ms was achieved [S1].")[0]
    assert status_for_claim(c.text, c.citations, src) == "CONTRADICTED"


def test_uncited_and_unresolved():
    assert status_for_claim("Retrieval improved a lot.", [], {}) == "UNCITED"
    assert status_for_claim("Vector search hit 0.80 [S9].", ["S9"], {"S1": "x"}) == "UNRESOLVED_CITATION"
