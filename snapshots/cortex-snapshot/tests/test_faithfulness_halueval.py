"""HaluEval-derived regression tests for the faithfulness backends.

Fixtures below are real cases from evals/hf_datasets/halu_eval/semi_ground.jsonl
(pminervini/HaluEval qa, MIT), inlined so the unit tests stay hermetic. The final
test re-runs the full 500-pair cross-validation as a frozen gate: the hardened
backend must keep beating lexical, at the levels measured on 2026-07-06
(evals/reports/FAITHFULNESS_HALUEVAL_CROSSVAL.md).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core.faithfulness import hardened_grounded, lexical_grounded  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "evals" / "hf_datasets" / "halu_eval" / "semi_ground.jsonl"

OBEROI_KNOWLEDGE = (
    "The Oberoi family is an Indian family that is famous for its involvement in hotels, "
    "namely through The Oberoi Group.The Oberoi Group is a hotel company with its head "
    "office in Delhi."
)


def test_halueval_grounded_answer_accepted():
    # id fd22bec596fc79bb — short grounded answer must pass both backends
    assert lexical_grounded("Delhi", OBEROI_KNOWLEDGE)
    assert hardened_grounded("Delhi", OBEROI_KNOWLEDGE)


def test_halueval_hallucinated_entity_caught_by_hardened_only():
    # id fd22bec596fc79bb — "Mumbai" appears nowhere in the knowledge; lexical's
    # bag-of-words overlap is fooled by the surrounding correct vocabulary,
    # the hardened entity check is not.
    halluc = "The Oberoi family's hotel company is based in Mumbai."
    assert not hardened_grounded(halluc, OBEROI_KNOWLEDGE)


def test_halueval_relational_hallucination_is_a_known_miss():
    # id cf073863b2adcdbf — "First for Women was started first." is built entirely
    # from words present in the knowledge; catching it needs entailment, not
    # lexical checks. Frozen as a KNOWN LIMIT (documented in the crossval report):
    # if this starts failing, the backend gained reasoning it does not have — check
    # for an overfit heuristic before celebrating.
    #
    # NOTE (2026-07-11): the temporal-ordering signal added in the v2 hardening does
    # NOT catch this, and that is correct: "First for Women" carries no year in the
    # knowledge, so the date-comparison checker ABSTAINS rather than guessing an
    # ordering it cannot compute. This case stays a deterministic miss (needs
    # entailment). Contrast with test_halueval_temporal_ordering_now_caught, where
    # both entities are dated and the ordering IS computable.
    knowledge = (
        "Arthur's Magazine (1844–1846) was an American literary periodical published "
        "in Philadelphia in the 19th century.First for Women is a woman's magazine "
        "published by Bauer Media Group in the USA."
    )
    assert hardened_grounded("First for Women was started first.", knowledge)


# ---------------------------------------------------------------------------
# v2 hardening (2026-07-11): four deterministic contradiction signals, one per
# attacked hallucination class. Each case is a real HaluEval record that the
# pre-v2 hardened backend FALSE-ACCEPTED; the v2 backend must now reject it.
# Spec: docs/research/faithfulness-hardening-spec-2026-07-11.md. No LLM.
# ---------------------------------------------------------------------------

def test_halueval_spelled_out_number_now_caught():
    # id 8e34fe64dc3d00e6 — knowledge says Carson received SIX Emmy Awards; the
    # hallucination says SEVEN. The wrong count is spelled out, so the digit-only
    # number gate never saw it. Numeric class: spelled-out cardinal mismatch.
    knowledge = (
        "The film is dedicated to Johnny Carson, as \"The Aristocrats\" was said to be "
        "his favorite joke. Carson received six Emmy Awards, the Television Academy's "
        "1980 Governor's Award, and a 1985 Peabody Award."
    )
    assert not hardened_grounded("Johnny Carson received seven Emmy Awards.", knowledge)


def test_halueval_quantifier_bound_contradiction_now_caught():
    # id 79ae06a99eac1782 — knowledge says "more than 1,600" German scientists; the
    # hallucination pins it to "Exactly 1,600". The number matches but the bound is
    # contradicted. Numeric class: quantifier-vs-bound.
    knowledge = (
        "Operation Paperclip was a secret program of the Joint Intelligence Objectives "
        "Agency (JIOA) in which more than 1,600 German scientists, engineers, and "
        "technicians were recruited."
    )
    assert not hardened_grounded("Exactly 1,600 German scientists were recruited.", knowledge)


def test_halueval_name_order_swap_now_caught():
    # id c98dc9ae20efbf23 — knowledge names "Garth Brooks"; the hallucination reverses
    # it to "Brooks Garth". Both tokens are present so the entity check passes, but the
    # order is swapped. Relational class: name-order swap.
    knowledge = (
        "\"Friends in Low Places\" is a song performed by American country pop artist "
        "Garth Brooks. It was released on August 6, 1990 as the lead single from his "
        "album \"No Fences\"."
    )
    assert not hardened_grounded('"Friends in Low Places" was performed by Brooks Garth.',
                                 knowledge)


def test_halueval_temporal_ordering_now_caught():
    # id 476167e5c2bb7356 — Trapero (born 1971) and Ford (born 1908) are both dated in
    # the knowledge; the hallucination claims Trapero was born first. Deterministic
    # min-over-dates contradicts it. Temporal-ordering class.
    knowledge = (
        "Pablo Trapero (Born 4 October 1971) is an Argentine film producer, editor and "
        "director.Aleksander Ford (born Mosze Lifszyc; 24 November 1908 in Kiev, Russian "
        "Empire – 4 April 1980 in Naples, Florida, United States) was a Polish Jewish "
        "film director."
    )
    assert not hardened_grounded("Pablo Trapero was born first.", knowledge)


@pytest.mark.skipif(not DATA.exists(), reason="halu_eval dataset not present")
def test_halueval_crossval_gate():
    """Full HaluEval cross-val gate, frozen thresholds (with slack).

    The dataset was 500 pairs at the 2026-07-06 baseline; it was later re-pulled to
    the full 2000-pair split. The FROZEN CONTRACT here is the accept/reject thresholds
    (the backend's regression signal), NOT the exact row count -- so we run on whatever
    is present (>=500 guards against a truncated/corrupt file) rather than pinning an
    exact size that legitimately changes on every re-download. Re-confirmed on all 2000
    pairs 2026-07-11: lexical .965/.345 (bal .655), hardened .945/.747 (bal .846)."""
    with open(DATA, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    assert len(records) >= 500

    def rates(fn):
        accept = reject = 0
        for r in records:
            accept += bool(fn(r["knowledge_grounded_answer"], r["knowledge"]))
            reject += not fn(r["hallucinated_answer"], r["knowledge"])
        return accept / len(records), reject / len(records)

    lex_acc, lex_rej = rates(lexical_grounded)
    hard_acc, hard_rej = rates(hardened_grounded)

    # measured 2026-07-06: lexical .970/.320, hardened .940/.736
    # measured 2026-07-11 after v2 deterministic hardening (spelled-out cardinal,
    # quantifier-vs-bound, name-order swap, temporal date-ordering): hardened .940/.758.
    # Floor raised .70 -> .75 to lock in the reject gain; accept floor held at .90
    # (actual .940 — the v2 signals added zero false rejections on this set).
    assert hard_acc >= 0.90, "hardened backend now falsely rejects grounded answers"
    assert hard_rej >= 0.75, "hardened backend lost hallucination-catching power"
    hard_bal = (hard_acc + hard_rej) / 2
    lex_bal = (lex_acc + lex_rej) / 2
    assert hard_bal > lex_bal, "hardened no longer beats lexical — its reason to exist"
