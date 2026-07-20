"""Frozen tests for the faithfulness interface (cortex_core/faithfulness.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core.faithfulness import faithfulness, decompose_claims, lexical_grounded  # noqa: E402


def test_grounded_digest_passes():
    src = ["The rebuild lock uses an atomic O_CREAT flag. Recall@5 was 0.82."]
    r = faithfulness("The rebuild lock uses an atomic O_CREAT flag. Recall@5 reached 0.82.", src)
    assert r.passed and r.score == 1.0 and r.supported == r.total


def test_hallucinated_claim_fails():
    src = ["Recall@5 was 0.82 on the benchmark."]
    r = faithfulness("Recall@5 reached 0.95. It also added a Redis queue.", src)
    assert not r.passed and r.score < 0.8


def test_wrong_number_is_ungrounded():
    assert not lexical_grounded("latency was 12ms", "the latency was 30ms under load")
    assert lexical_grounded("latency was 30ms", "the latency was 30ms under load")


def test_empty_context_auto_fails_not_near_one():
    # the documented artifact: an ungrounded digest must NOT score ~1.0
    r = faithfulness("Everything went perfectly and nothing failed.", [])
    assert r.empty_context and r.score == 0.0 and not r.passed


def test_no_claims_does_not_pass():
    r = faithfulness("", ["some source text here"])
    assert not r.passed and r.total == 0


def test_decompose_claims_splits_sentences():
    cl = decompose_claims("The lock is atomic. Recall rose to 0.82. Tests passed.")
    assert len(cl) == 3


def test_threshold_is_a_parameter_not_hardcoded():
    src = ["Alpha beta gamma delta appears in the source."]
    # two claims: first grounded, second not -> score 0.5. Passes at 0.4, fails at 0.8.
    dig = "Alpha beta gamma delta. Zeta eta theta iota kappa omicron."
    r4 = faithfulness(dig, src, threshold=0.4)
    assert r4.total == 2 and r4.score == 0.5 and r4.passed
    assert not faithfulness(dig, src, threshold=0.8).passed
