"""Unit tests for the fan-out / fan-in parallel executor (cortex_core/fanout.py).

Fully offline + judge-free: student dispatch is injected via `student_factory`, and the
gate is injected via `gate_impl` (a DETERMINISTIC stub, never a model). No live/paid model
call is ever made here. This mirrors the injectable discipline of vague_build.drive.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import fanout as fo  # noqa: E402
from cortex_core.app_contract import CheckResult, GateVerdict  # noqa: E402

VALID_SLOT = ('{"entity":"member","fields":['
              '{"name":"name","type":"text","required":true},'
              '{"name":"active","type":"bool","required":true}]}')


# --- deterministic stub gate (NO model in the verdict path) ---------------------------------
def _gate_all_pass(app_dir, checks, *, seed, hidden_dir=None):
    results = tuple(CheckResult(kind=c["kind"], passed=True, hidden=False, detail="")
                    for c in checks)
    return GateVerdict(passed=True, results=results, failure_class=None,
                       hidden_coverage=False, env_retries=0, seed=seed)


def _gate_fail(app_dir, checks, *, seed, hidden_dir=None):
    results = tuple(CheckResult(kind=c["kind"], passed=(i != 0), hidden=False, detail="d",
                                failure_class=("APP_FAIL" if i == 0 else None))
                    for i, c in enumerate(checks))
    return GateVerdict(passed=False, results=results, failure_class="APP_FAIL",
                       hidden_coverage=False, env_retries=0, seed=seed)


def _fixed_factory(text):
    """A student_factory that returns a fixed-text student for every spec."""
    return lambda spec: (lambda prompt: text)


# --- 1. FREE-ONLY GUARD (the headline fail-closed invariant) --------------------------------
def test_paid_tier_rejected_as_executor():
    """A paid tier as an executor is hard-rejected at entry (fail-closed)."""
    paid = fo.ExecutorSpec("evil", "opencode", "deepseek-v4-flash", "opencode", 0)
    with pytest.raises(fo.BannedExecutorError):
        fo._assert_free_executor(paid)


def test_premium_reviewer_tier_rejected_as_executor():
    """Premium reviewer tiers (opus/sonnet/fable-max/haiku) are reviewer-only, never executors."""
    for tier in ("opus", "sonnet", "fable-max", "haiku"):
        spec = fo.ExecutorSpec("prem", tier, None, tier, 0)
        with pytest.raises(fo.BannedExecutorError):
            fo._assert_free_executor(spec)


def test_paid_9router_umans_rejected_as_executor():
    """The paid umans/glm-5.2 9router connection ('ninerouter') is banned; only aux is free."""
    spec = fo.ExecutorSpec("glm", "ninerouter", None, "ninerouter", 0)
    with pytest.raises(fo.BannedExecutorError):
        fo._assert_free_executor(spec)


def test_all_default_executors_are_free():
    """Re-assert the allowlist: every shipped default executor passes the free-only guard."""
    for name in fo.DEFAULT_EXECUTORS:
        fo._assert_free_executor(fo.EXECUTORS[name])  # must not raise


def test_opencode_zen_model_must_be_in_allowlist():
    """model_override bypasses the tier's own allowlist -> fanout re-asserts it."""
    bad = fo.ExecutorSpec("z", "opencode-zen", "some-paid-zen-model", "opencode", 0)
    with pytest.raises(fo.BannedExecutorError):
        fo._assert_free_executor(bad)
    # the shipped big-pickle spec is fine
    fo._assert_free_executor(fo.EXECUTORS["big-pickle"])


def test_fanout_entry_rejects_banned_executor():
    """fanout() itself fail-closes before any dispatch if handed a banned executor."""
    fo.EXECUTORS["_tmp_paid"] = fo.ExecutorSpec("_tmp_paid", "opus", None, "opus", 0)
    try:
        with pytest.raises(fo.BannedExecutorError):
            fo.fanout("track members", executors=["_tmp_paid"],
                      student_factory=_fixed_factory(VALID_SLOT), gate_impl=_gate_all_pass)
    finally:
        del fo.EXECUTORS["_tmp_paid"]


# --- 2. INDEPENDENCE GUARD (anti-circularity) -----------------------------------------------
def test_independence_guard_rejects_reviewer_in_pool():
    spec = fo.EXECUTORS["laguna-m.1"]
    with pytest.raises(AssertionError):
        fo._assert_independence(spec, reviewer_id="laguna-m.1")


def test_independence_guard_rejects_executor_named_oracle():
    spec = fo.ExecutorSpec(fo.ORACLE_AUTHOR, "openrouter", None, "openrouter", 1)
    with pytest.raises(AssertionError):
        fo._assert_independence(spec, reviewer_id="opus")


# --- 3. FAN-OUT / FAN-IN over injected fakes + deterministic stub gate -----------------------
def test_fanout_partitions_and_deterministic_gate_selects_winner(tmp_path):
    """Two executors build a good slot (gate PASS), one emits garbage (SLOT_FAIL).
    The winner is a PASSER chosen by the deterministic gate + rank, never the failure."""
    def factory(spec):
        if spec.name == "north-mini":
            return lambda prompt: "not json at all"      # -> bad_slot
        return lambda prompt: VALID_SLOT

    r = fo.fanout("track my members, count the active ones",
                  executors=["laguna-m.1", "big-pickle", "north-mini"],
                  student_factory=factory, gate_impl=_gate_all_pass,
                  reviewer=None, sink=tmp_path)

    assert len(r.attempts) == 3
    assert len(r.ranking) == 2                      # two passers
    assert len(r.failures) == 1
    assert r.winner is not None and r.winner.passed
    assert r.winner.executor in ("laguna-m.1", "big-pickle")
    # the SLOT_FAIL executor is never the winner
    assert r.winner.executor != "north-mini"
    bad = next(a for a in r.attempts if a.executor == "north-mini")
    assert bad.status == "bad_slot" and bad.failure_class == "SLOT_FAIL" and not bad.passed


def test_all_fail_gate_yields_no_winner(tmp_path):
    r = fo.fanout("track my members",
                  executors=["laguna-m.1", "big-pickle"],
                  student_factory=_fixed_factory(VALID_SLOT), gate_impl=_gate_fail,
                  reviewer=None, sink=tmp_path)
    assert r.winner is None
    assert len(r.failures) == 2
    # a saved-failure regression fixture is written per gate-caught failure with an app_dir
    assert len(r.regression_paths) == 2
    for p in r.regression_paths:
        assert (Path(p) / "meta.json").is_file()
        assert (Path(p) / "checks.json").is_file()


def test_rank_key_orders_by_cost_then_quality_then_speed():
    a_cheap = fo.ExecAttempt("big-pickle", "built", True, None, "d", None, [], [], 1, 9.0, [], [], 0)
    a_dear = fo.ExecAttempt("laguna-m.1", "built", True, None, "d", None, [], [], 1, 0.1, [], [], 0)
    ranked = fo.rank_passers([a_dear, a_cheap])
    assert ranked[0].executor == "big-pickle"       # cost_weight 0 beats 1 despite being slower


def test_scoreboard_written(tmp_path):
    fo.fanout("track my members", executors=["laguna-m.1"],
              student_factory=_fixed_factory(VALID_SLOT), gate_impl=_gate_all_pass,
              reviewer=None, sink=tmp_path)
    sb = tmp_path / "ops-local" / "fanout-scoreboard.jsonl"
    assert sb.is_file()
    rec = json.loads(sb.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["winner"] == "laguna-m.1" and rec["seed"] is not None


# --- 4. CONCURRENCY: the gate pool semaphore bounds parallel gate execution -----------------
def test_gate_concurrency_cap_respected(tmp_path):
    live = 0
    peak = 0
    lock = threading.Lock()

    def counting_gate(app_dir, checks, *, seed, hidden_dir=None):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return _gate_all_pass(app_dir, checks, seed=seed)

    fo.fanout("track my members",
              executors=["laguna-m.1", "big-pickle", "north-mini", "aux"],
              student_factory=_fixed_factory(VALID_SLOT), gate_impl=counting_gate,
              gate_workers=1, reviewer=None, sink=tmp_path)
    assert peak == 1                                 # gate_sem(1) never lets 2 gates run at once


# --- 5. REGRESSION SAVE/REPLAY ROUND-TRIP (pure gate, no model) -----------------------------
def test_save_and_replay_regression_roundtrip(tmp_path):
    r = fo.fanout("track my members",
                  executors=["laguna-m.1"],
                  student_factory=_fixed_factory(VALID_SLOT), gate_impl=_gate_fail,
                  reviewer=None, sink=tmp_path)
    assert r.regression_paths
    reg_dir = Path(fo._regressions_root(tmp_path))
    # replay with the same deterministic (still-failing) gate -> still_fails + class matches
    out = fo.replay_regressions(reg_dir, gate_impl=_gate_fail)
    assert out and all(x["still_fails"] and x["class_match"] for x in out)
