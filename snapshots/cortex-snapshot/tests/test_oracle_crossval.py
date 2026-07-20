"""Tests for GAP D1 (oracle cross-validation) + D2 (hidden-holdout catches gaming).

D1 tests prove:
  * the independent second checker AGREES with the primary on the fixtures (BFCL-style dual
    cross-validation), modulo documented policy disagreements;
  * the harness DETECTS an injected checker disagreement (fp/fn > 0, disagreement surfaced);
  * every lane's mutation_score is high (the oracle kills known-bad candidates).

D2 tests prove:
  * a visible-suite memorizer PASSES the visible checker but the hidden holdout catches it;
  * the real holdout run has catch_rate > 0.
"""

import pytest

from cortex_core import oracle_crossval as ocv
from cortex_core._indep_datetime import independent_verdict as dt_indep
from evals.objective_coding.checker import check_solution
from evals.objective_coding.fixtures import FIXTURES as CODING_FIXTURES
from evals.objective_coding.holdout_gaming import build_memorizer, run_holdout


@pytest.fixture(scope="module", autouse=True)
def _shutdown_regex_guard():
    yield
    ocv.shutdown()


# --------------------------------------------------------------------------- D1
def test_all_lanes_have_a_named_second_authority():
    for name, lane in ocv.LANES.items():
        assert lane.second_authority, f"{name} has no second authority"


def test_datetime_independent_agrees_with_primary_on_fixtures():
    """The JDN reimplementation and the stdlib checker must agree on every comparable datetime
    candidate (tz_convert abstains). Zero false-pos / false-neg."""
    rep = ocv.run_lane(ocv.LANES["datetime"])
    assert rep.comparable > 0
    assert rep.fp == 0 and rep.fn == 0, rep.disagreements
    assert rep.agreement == 1.0


def test_regex_only_policy_disagreement_is_catastrophic_backtracking():
    """The NFA is immune to catastrophic backtracking, so the ONE allowed disagreement with the
    primary is the documented performance/policy case — not a correctness bug."""
    rep = ocv.run_lane(ocv.LANES["regex"])
    ids = {d["id"] for d in rep.disagreements}
    assert ids <= {"word_catastrophic_backtracking"}, rep.disagreements
    for d in rep.disagreements:
        assert d["note"] == "policy"


def test_ledger_and_invoice_second_authority_fully_agrees():
    for name in ("ledger", "invoice"):
        rep = ocv.run_lane(ocv.LANES[name])
        assert rep.comparable > 0
        assert rep.fp == 0 and rep.fn == 0, (name, rep.disagreements)
        assert rep.agreement == 1.0


def test_every_lane_has_high_mutation_score():
    """A strong oracle kills the known-bad candidates. All lanes must kill 100% of non-equivalent
    mutants (equivalent mutants are excluded from the denominator, as in mutation testing)."""
    for name, lane in ocv.LANES.items():
        rep = ocv.run_lane(lane)
        assert rep.n_mutants > 0, name
        assert rep.mutation_score == 1.0, (name, rep.asdict())


def test_crossval_detects_injected_checker_disagreement():
    """Inject a deliberately buggy independent checker (flips one datetime verdict). The harness
    must surface it as a false-neg/false-pos disagreement — the whole point of cross-validation."""
    good = ocv.LANES["datetime"]

    def buggy_independent(payload):
        # off-by-one weekday bug on exactly one input.
        if payload["op"] == "weekday" and payload["inputs"].get("date") == "2000-01-01":
            order = list(__import__("cortex_core._indep_datetime",
                                    fromlist=["_WEEKDAYS"])._WEEKDAYS)
            correct = dt_indep("weekday", payload["inputs"], payload["answer"])
            # return the OPPOSITE verdict for this one case -> a manufactured disagreement
            return "fail" if correct == "pass" else "pass"
        return dt_indep(payload["op"], payload["inputs"], payload["answer"])

    rep = ocv.run_lane(good, independent_fn=buggy_independent)
    assert (rep.fp + rep.fn) > 0, "cross-val failed to detect an injected disagreement"
    assert any(d["id"].startswith("weekday") for d in rep.disagreements), rep.disagreements


def test_injected_faulty_primary_is_also_caught():
    """Symmetry: a too-lenient PRIMARY (passes everything) is caught as false-positives vs the
    honest independent checker."""
    lane = ocv.LANES["datetime"]
    rep = ocv.run_lane(lane, primary_fn=lambda payload: "pass")
    assert rep.fp > 0, "a lenient primary should produce false positives vs the independent checker"


# --------------------------------------------------------------------------- D2
def _fixture(fid):
    return next(f for f in CODING_FIXTURES if f["id"] == fid)


def test_memorizer_passes_visible_but_holdout_catches_it():
    """The crux of D2: a visible-suite memorizer (built with NO holdout knowledge) passes every
    visible assert yet the hidden holdout catches it."""
    fx = _fixture("fizzbuzz_line")
    gamed = build_memorizer(fx["entrypoint"], fx["visible_tests"])
    res = check_solution(gamed, fx["visible_tests"], fx["hidden_tests"], fx["entrypoint"])
    assert res.checks["visible_ok"] is True, "memorizer should pass the visible suite"
    assert res.checks["hidden_ok"] is False, "hidden holdout should catch the memorizer"
    assert res.verdict == "fail" and res.failed_stage == "hidden_tests"


def test_memorizer_is_built_without_touching_hidden_tests():
    """Guard the 'implementer never saw the holdout' property: the generated gamed code contains
    none of the hidden-test inputs."""
    fx = _fixture("fizzbuzz_line")
    gamed = build_memorizer(fx["entrypoint"], fx["visible_tests"])
    # hidden tests exercise 15/30/0 -> none of those literals should appear in the memorizer table
    for hidden_literal in ("15", "30"):
        assert hidden_literal not in gamed


def test_holdout_run_catch_rate_positive():
    """The real end-to-end D2 run must catch at least one gamed attempt (catch_rate > 0)."""
    report = run_holdout()
    assert report["gamed_attempts_passing_visible"] > 0
    assert report["caught_by_holdout"] > 0
    assert report["catch_rate"] > 0
