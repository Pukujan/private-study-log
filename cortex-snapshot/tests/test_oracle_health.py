"""Tests for GAP J1 — per-lane oracle *health* monitoring (evals/oracle_health/).

The load-bearing assurance metric is the **false-pass rate**: of known-BAD inputs, the fraction
the checker wrongly PASSED. These tests prove the harness:

  * reports false_pass_rate=1.0 for a checker that passes everything (the assurance nightmare);
  * reports a SANE false-pass (0.0) for a correct checker on a real declared-control lane;
  * reports flakiness=0 / replay_determinism=True for a deterministic checker;
  * emits an HONEST `insufficient_controls` row (metrics = None, never a fabricated 0.0) for a lane
    that exposes no independently-labeled known-good/known-bad control set.

Anti-circular guard under test: ground truth is the lane's DECLARED controls (mutation-seeded
known-bad + reference known-good), never the oracle's own prior verdicts and never a judge.
"""

import pytest

from evals import oracle_health as oh


# --------------------------------------------------------------------------- synthetic adapters
def _const_adapter(lane, verdict, *, good=0, bad=0, mut=0):
    """Adapter whose checker always returns `verdict`, with the given control counts."""
    controls = []
    controls += [oh.Control(f"g{i}", "good", "fixture", (lambda v=verdict: v)) for i in range(good)]
    controls += [oh.Control(f"b{i}", "bad", "fixture", (lambda v=verdict: v)) for i in range(bad)]
    controls += [oh.Control(f"m{i}", "bad", "mutation", (lambda v=verdict: v)) for i in range(mut)]
    return oh.HealthAdapter(lane=lane, controls=controls, replays=4)


# --------------------------------------------------------------------------- false-pass
def test_always_pass_checker_gets_false_pass_rate_1():
    """A checker that passes every known-BAD input is the worst case: false_pass_rate == 1.0."""
    row = oh.compute_lane_health(_const_adapter("always_pass", "pass", good=2, bad=3, mut=2))
    assert row["false_pass_rate"] == 1.0
    # it never fails a known-good, so false_fail_rate is 0.0
    assert row["false_fail_rate"] == 0.0
    # it passes every mutation -> catches none
    assert row["mutation_kill_score"] == 0.0
    assert row["covered"] is True


def test_always_fail_checker_kills_all_mutations_but_fails_goods():
    row = oh.compute_lane_health(_const_adapter("always_fail", "fail", good=2, bad=3, mut=2))
    assert row["false_pass_rate"] == 0.0        # nothing wrongly passed
    assert row["false_fail_rate"] == 1.0        # every known-good wrongly failed
    assert row["mutation_kill_score"] == 1.0    # every mutation caught


# --------------------------------------------------------------------------- flakiness / determinism
def test_flakiness_zero_for_deterministic_checker():
    row = oh.compute_lane_health(_const_adapter("det", "fail", good=1, bad=2, mut=1))
    assert row["flakiness"] == 0.0
    assert row["replay_determinism"] is True


def test_flakiness_detected_for_nondeterministic_checker():
    seq = {"n": 0}

    def flip():
        seq["n"] += 1
        return "pass" if seq["n"] % 2 else "fail"

    adapter = oh.HealthAdapter(lane="flaky", controls=[oh.Control("x", "bad", "fixture", flip)],
                               replays=4)
    row = oh.compute_lane_health(adapter)
    assert row["flakiness"] == 1.0
    assert row["replay_determinism"] is False


# --------------------------------------------------------------------------- honest abstention
def test_all_abstain_leaves_rates_none_not_zero():
    """Abstention on every control must not be scored as a fake 0.0 pass/fail rate."""
    row = oh.compute_lane_health(_const_adapter("abst", "abstain", good=2, bad=2, mut=1))
    assert row["false_pass_rate"] is None
    assert row["false_fail_rate"] is None
    assert row["mutation_kill_score"] is None
    assert row["abstained_bad"] == 3      # 2 bad fixtures + 1 mutation


# --------------------------------------------------------------------------- insufficient controls
def test_insufficient_controls_row_is_honest_none_not_zero():
    row = oh.insufficient_row("objective_gsm_plus", "no declared known-good/known-bad control set")
    assert row["covered"] is False
    assert row["insufficient_controls"] is True
    # every metric must be None, NOT a fabricated 0.0
    for k in ("false_pass_rate", "false_fail_rate", "mutation_kill_score", "flakiness",
              "replay_determinism", "holdout_catch_rate"):
        assert row[k] is None, k


def test_every_objective_lane_is_planned_exactly_once():
    """Every objective_* dir on disk is either covered by an adapter or honestly marked
    insufficient_controls — exactly one, never both, never neither. (Fast: no checker runs.)"""
    from evals.oracle_health.health import _objective_lane_dirs
    on_disk = {d.name for d in _objective_lane_dirs()}
    covered = oh.covered_lane_names()
    insufficient = oh.insufficient_lane_names()
    assert covered & insufficient == set(), "a lane is both covered and insufficient"
    assert covered | insufficient == on_disk, "a lane is neither covered nor insufficient"
    assert covered and insufficient, "expected at least one covered and one insufficient lane"


# --------------------------------------------------------------------------- real declared-control lane
def test_correct_checker_on_crypto_lane_has_zero_false_pass():
    """The crypto-misuse lane's real checker on its DECLARED controls (secure refs known-good,
    vulnerable refs + seeded mutations known-bad) must wrongly-pass none of the known-bad: a
    sane, low false-pass. Ground truth is the lane's declared labels, never the oracle itself."""
    adapters = {a.lane: a for a in oh.build_registry()}
    crypto = adapters["objective_crypto_misuse"]
    row = oh.compute_lane_health(crypto, replays=1)
    assert row["n_known_bad"] > 0 and row["n_known_good"] > 0
    assert row["false_pass_rate"] == 0.0, row.get("false_pass_ids")
    assert row["mutation_kill_score"] == 1.0
    assert row["judge_free"] is True
