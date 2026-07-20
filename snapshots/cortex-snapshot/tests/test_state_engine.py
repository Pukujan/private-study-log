"""EXECUTABLE SPEC for the server-driven state-machine engine (docs/research/
STATE-MACHINE-DESIGN-fable-research-2026-07-06.md). These tests are the TDD success
conditions the engine must satisfy -- they are RED until `cortex_core/state_engine.py` is
built, and making them GREEN is the definition of "the hard core is correct."

The invariants here ARE the contract; the internal implementation is the builder's (Fable's)
choice, but this API surface and these behaviors are fixed. They deliberately concentrate on
the CONCURRENCY/CORRECTNESS core (legality gating, seq-fencing, idempotency, claim exclusivity,
lease/reaper, escalation-always-closes) -- the race-prone part where a cheap implementation
silently fails. Everything else (chart authoring, envelope prose, extra tracks) is periphery.

Contract (minimal API):
    eng = StateEngine(db_path, chart=None, gate=None)
      - chart: track definition; default = the forced-pipeline "build" track
        SEARCH_BRAIN -> RESEARCH -> PLAN -> SPEC -> IMPLEMENT -> REVIEW -> CLOSEOUT -> DONE
      - gate(phase, task, payload) -> {"pass": bool, ...}: pluggable exit-criteria evaluator;
        default permits any well-formed phase report.
    task_id = eng.create_task(intent: dict, track="build", lease_s=600) -> str   # initial state, seq=0
    env = eng.step(task_id, tool, payload=None, seq=..., idem_key=None, actor=None) -> dict
        success -> {"ok": True, "task_id", "state", "seq", "legal_tools": [...], "instruction", ...}
        refusal -> {"ok": False, "code": "ILLEGAL_IN_STATE"|"REJECTED_STALE"|"BOUNDARY_VIOLATION",
                    "legal_tools": [...], ...}  (state/seq UNCHANGED on refusal)
    eng.get(task_id) -> {"state","seq","intent","lease_until","esc_level","rework_count",...}
    eng.acquire_claims(task_id, claims: list[dict], seq) -> {"ok": bool, ...}   # atomic, all-or-nothing
    eng.reap(now_ts=None) -> list[str]   # expired-lease tasks -> STALLED, returns their ids
"""

from __future__ import annotations

import pytest

state_engine = pytest.importorskip(
    "cortex_core.state_engine",
    reason="state_engine not built yet -- this file is the RED spec it must satisfy",
)
StateEngine = state_engine.StateEngine
make_universal_gate = state_engine.make_universal_gate


def _eng(tmp_path, **kw):
    # Use universal gate by default so partition_coverage_gate and review_scope_gate work
    if "gate" not in kw:
        kw["gate"] = make_universal_gate()
    return StateEngine(str(tmp_path / "engine.sqlite"), **kw)


def test_create_task_starts_in_initial_state_with_legal_tools(tmp_path):
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "how X works"})
    row = eng.get(tid)
    assert row["state"] == "SEARCH_BRAIN"
    assert row["seq"] == 0
    # SEARCH phase must expose the brain-consult tools and NOT downstream tools.
    env = eng.step(tid, tool="__peek__", seq=0) if False else None  # (no-op; legality checked below)
    legal = eng.get(tid).get("legal_tools") or []
    assert any("search" in t for t in legal)
    assert not any("submit_patch" in t for t in legal)


def test_illegal_tool_is_refused_without_advancing(tmp_path):
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})
    env = eng.step(tid, tool="cortex_submit_patch", payload={}, seq=0)  # illegal in SEARCH
    assert env["ok"] is False
    assert env["code"] == "ILLEGAL_IN_STATE"
    assert "legal_tools" in env and env["legal_tools"]
    after = eng.get(tid)
    assert after["state"] == "SEARCH_BRAIN" and after["seq"] == 0  # unchanged


def test_legal_tool_advances_state_and_bumps_seq(tmp_path):
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})
    env = eng.step(tid, tool="cortex_report_findings",
                   payload={"evidence": [{"claim": "c", "source": "s"}]}, seq=0)
    assert env["ok"] is True
    assert env["seq"] == 1
    assert env["state"] != "SEARCH_BRAIN"  # advanced along the track


def test_stale_seq_is_rejected_with_fresh_envelope(tmp_path):
    """Seq-fencing = the single-machine defense against two concurrent calls on one task.
    A call carrying a stale seq must apply NOTHING and return the current state so the client
    can resync (the 'reject' double-texting strategy)."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})
    ok = eng.step(tid, tool="cortex_report_findings", payload={"evidence": []}, seq=0)
    assert ok["ok"] is True and ok["seq"] == 1
    stale = eng.step(tid, tool="cortex_report_findings", payload={"evidence": []}, seq=0)  # replayed old seq
    assert stale["ok"] is False
    assert stale["code"] == "REJECTED_STALE"
    assert stale["seq"] == 1  # fresh envelope carries the real seq
    assert eng.get(tid)["seq"] == 1  # nothing double-applied


def test_idempotent_step_applies_once(tmp_path):
    """Retrying CLIs double-submit; the same (task, idem_key) must apply once and replay the
    recorded envelope."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})
    a = eng.step(tid, tool="cortex_report_findings", payload={"evidence": []}, seq=0, idem_key="k1")
    b = eng.step(tid, tool="cortex_report_findings", payload={"evidence": []}, seq=0, idem_key="k1")
    assert a["ok"] is True
    assert b == a  # replayed, not re-applied
    assert eng.get(tid)["seq"] == 1  # only one advance


def test_overlapping_claims_refused_disjoint_succeed(tmp_path):
    """Boundaries between parallel workers: claims are exclusive by construction."""
    eng = _eng(tmp_path)
    t1 = eng.create_task(intent={"seeking": "a"})
    t2 = eng.create_task(intent={"seeking": "b"})
    r1 = eng.acquire_claims(t1, [{"kind": "path", "key": "src/auth/**"}], seq=eng.get(t1)["seq"])
    assert r1["ok"] is True
    overlap = eng.acquire_claims(t2, [{"kind": "path", "key": "src/auth/**"}], seq=eng.get(t2)["seq"])
    assert overlap["ok"] is False  # already claimed
    disjoint = eng.acquire_claims(t2, [{"kind": "path", "key": "src/api/**"}], seq=eng.get(t2)["seq"])
    assert disjoint["ok"] is True


def test_expired_lease_is_reaped_to_stalled_with_intent_intact(tmp_path):
    """A worker that goes silent past its lease is reaped; its intent record survives so a
    replacement resumes instead of restarting (Temporal heartbeat-details)."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "keep me"}, lease_s=0)  # already expired
    reaped = eng.reap(now_ts=10_000_000_000)
    assert tid in reaped
    row = eng.get(tid)
    assert row["state"] == "STALLED"
    assert row["intent"]["seeking"] == "keep me"  # not lost


def test_spawn_mission_partitions_disjoint_workers(tmp_path):
    """STAGE 2 supervisor: disjoint workers all spawn under one mission, each holding its claims."""
    eng = _eng(tmp_path)
    res = eng.spawn_mission(
        {"seeking": "build feature"},
        [{"intent": {"seeking": "auth"}, "claims": [{"kind": "path", "key": "src/auth/**"}]},
         {"intent": {"seeking": "api"}, "claims": [{"kind": "path", "key": "src/api/**"}]}],
    )
    assert res["ok"] is True
    assert len(res["worker_ids"]) == 2
    status = eng.mission_status(res["mission_id"])
    assert status["n"] == 2 and status["all_done"] is False
    # each worker starts fresh in the initial state
    for w in status["workers"]:
        assert eng.get(w["task_id"])["state"] == "SEARCH_BRAIN"


def test_spawn_mission_refuses_overlapping_workers_all_or_nothing(tmp_path):
    """Two workers wanting overlapping (glob) claims -> refused, and NOTHING is created."""
    eng = _eng(tmp_path)
    res = eng.spawn_mission(
        {"seeking": "m"},
        [{"intent": {}, "claims": [{"kind": "path", "key": "src/**"}]},
         {"intent": {}, "claims": [{"kind": "path", "key": "src/auth/x.py"}]}],  # inside src/**
    )
    assert res["ok"] is False and res["code"] == "CLAIM_CONFLICT"
    assert res["conflicts"]
    # all-or-nothing: no mission, no workers, no claims leaked
    assert eng._db.execute("SELECT COUNT(*) c FROM task").fetchone()["c"] == 0
    assert eng._db.execute("SELECT COUNT(*) c FROM claim").fetchone()["c"] == 0


def test_spawn_mission_refuses_claim_already_held(tmp_path):
    eng = _eng(tmp_path)
    ok = eng.spawn_mission({"seeking": "m1"},
                           [{"intent": {}, "claims": [{"kind": "path", "key": "src/db/**"}]}])
    assert ok["ok"] is True
    clash = eng.spawn_mission({"seeking": "m2"},
                              [{"intent": {}, "claims": [{"kind": "path", "key": "src/db/schema.py"}]}])
    assert clash["ok"] is False and clash["code"] == "CLAIM_CONFLICT"


def test_concurrent_contention_yields_exactly_one_winner(tmp_path):
    """REAL multi-threaded contention (the acceptance tests above are sequential; this is the
    race the seq-fence + RLock actually defend against): 20 threads all try to advance from
    seq=0 at the same instant -> exactly ONE applies, 19 get REJECTED_STALE, seq bumps once.
    Added by the reviewer -- a passing sequential suite does not prove race-safety."""
    import threading
    from collections import Counter

    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})
    results: list = []
    barrier = threading.Barrier(20)

    def worker(i):
        barrier.wait()  # release all 20 at once for maximum contention
        r = eng.step(tid, tool="cortex_report_findings", payload={"evidence": []},
                     seq=0, idem_key=f"t{i}")
        results.append(r.get("ok"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c = Counter(results)
    assert c[True] == 1, f"expected exactly 1 winner under contention, got {c[True]}"
    assert eng.get(tid)["seq"] == 1  # advanced exactly once


def test_escalation_always_terminates_in_a_closeout(tmp_path):
    """Runaway guard: REVIEW that keeps failing escalates, and past the escalation cap the task
    is ABANDONED -- but it must still transit CLOSEOUT so an audit record ALWAYS exists."""
    fail_gate = lambda phase, task, payload: {"pass": phase != "REVIEW"}  # REVIEW never passes
    eng = _eng(tmp_path, gate=fail_gate)
    tid = eng.create_task(intent={"seeking": "x"})
    # Drive the task to terminal by repeatedly submitting; the engine's rework/escalation caps
    # must converge it, not loop forever.
    for _ in range(50):
        row = eng.get(tid)
        if row["state"] in ("DONE", "ABANDONED"):
            break
        legal = row.get("legal_tools") or ["cortex_report_findings"]
        eng.step(tid, tool=legal[0], payload={"evidence": [], "patch": "x"}, seq=row["seq"])
    final = eng.get(tid)
    assert final["state"] in ("DONE", "ABANDONED")
    assert final.get("closeout_written") is True  # audit record exists even on abandonment


# --- MISSION_TRACK tests (Phase 5.2, 2026-07-08) ---


def test_mission_track_create_task_starts_at_intake(tmp_path):
    """Mission track: task starts in INTAKE state."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "build feature"}, track="mission")
    row = eng.get(tid)
    assert row["state"] == "INTAKE"
    assert row["seq"] == 0
    assert row["track"] == "mission"


def test_mission_track_contract_submission_to_partition(tmp_path):
    """INTAKE -> PARTITION: submit mission contract."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "build feature"}, track="mission")
    contract = {
        "mission_id": tid,
        "mission_task": "build auth module",
        "task_type": "feature",
        "acceptance_criteria": ["auth works"],
        "coverage_spec": {"required_units": ["auth", "db"], "max_workers": 3},
        "reducers": {"output": "append"},
        "evidence_refs": [],
    }
    env = eng.step(tid, tool="cortex_submit_mission_contract",
                   payload={"contract": contract}, seq=0)
    assert env["ok"] is True
    assert env["state"] == "PARTITION"
    row = eng.get(tid)
    stored_intent = row["intent"]
    assert stored_intent.get("contract") == contract


def test_partition_coverage_gate_rejects_missing_units(tmp_path):
    """partition_coverage_gate rejects partition if required_units are not collectively covered."""
    from cortex_core.state_engine import partition_coverage_gate, default_gate
    phase = "PARTITION"
    task = {
        "intent": {
            "coverage_spec": {"required_units": ["auth", "db", "api"], "max_workers": 3}
        },
        "track": "mission",
    }
    payload = {
        "workers": [
            {"owns_units": ["auth"]},
            {"owns_units": ["db"]},
            # missing "api"
        ]
    }
    result = partition_coverage_gate(phase, task, payload, base=default_gate)
    assert result["pass"] is False
    assert result["code"] == "MISSING_COVERAGE"
    assert "api" in str(result["reason"])


def test_partition_coverage_gate_rejects_duplicate_ownership(tmp_path):
    """partition_coverage_gate rejects if a required_unit is owned by >1 worker."""
    from cortex_core.state_engine import partition_coverage_gate, default_gate
    phase = "PARTITION"
    task = {
        "intent": {
            "coverage_spec": {"required_units": ["auth", "db"], "max_workers": 3}
        },
        "track": "mission",
    }
    payload = {
        "workers": [
            {"owns_units": ["auth", "db"]},
            {"owns_units": ["db"]},  # duplicate ownership of "db"
        ]
    }
    result = partition_coverage_gate(phase, task, payload, base=default_gate)
    assert result["pass"] is False
    assert result["code"] == "UNIT_DOUBLE_OWNED"
    assert "db" in str(result["reason"])


def test_partition_coverage_gate_rejects_fanout_exceeded(tmp_path):
    """partition_coverage_gate rejects if worker count exceeds max_workers."""
    from cortex_core.state_engine import partition_coverage_gate, default_gate
    phase = "PARTITION"
    task = {
        "intent": {
            "coverage_spec": {"required_units": ["a", "b"], "max_workers": 2}
        },
        "track": "mission",
    }
    payload = {
        "workers": [
            {"owns_units": ["a"]},
            {"owns_units": ["b"]},
            {"owns_units": []},  # 3 workers > max 2
        ]
    }
    result = partition_coverage_gate(phase, task, payload, base=default_gate)
    assert result["pass"] is False
    assert result["code"] == "FANOUT_EXCEEDED"
    assert "3" in str(result["reason"]) and "2" in str(result["reason"])


def test_partition_coverage_gate_accepts_valid_partition(tmp_path):
    """partition_coverage_gate accepts complete, disjoint, bounded partition."""
    from cortex_core.state_engine import partition_coverage_gate, default_gate
    phase = "PARTITION"
    task = {
        "intent": {
            "coverage_spec": {"required_units": ["auth", "db"], "max_workers": 3}
        },
        "track": "mission",
    }
    payload = {
        "workers": [
            {"owns_units": ["auth"]},
            {"owns_units": ["db"]},
        ]
    }
    result = partition_coverage_gate(phase, task, payload, base=default_gate)
    assert result["pass"] is True
    assert result["coverage"] == "ok"
    assert result["units"] == 2
    assert result["workers"] == 2


def test_mission_contract_round_trip_json(tmp_path):
    """MissionContract round-trips through to_dict/from_dict (stored in intent JSON)."""
    from cortex_core.contract import MissionContract
    import json

    mc = MissionContract(
        mission_id="m1",
        mission_task="build auth",
        task_type="feature",
        acceptance_criteria=["auth works", "tests pass"],
        coverage_spec={"required_units": ["auth", "db"], "max_workers": 2},
        reducers={"output": "append", "artifacts": "union"},
        evidence_refs=["docs/auth-design.md"],
        created_at="2026-07-08T00:00:00Z",
    )
    # Simulate storage in task.intent JSON
    stored_json = json.dumps(mc.to_dict())
    loaded_dict = json.loads(stored_json)
    mc_loaded = MissionContract.from_dict(loaded_dict)
    assert mc_loaded.mission_id == mc.mission_id
    assert mc_loaded.acceptance_criteria == mc.acceptance_criteria
    assert mc_loaded.reducers == mc.reducers


def test_partition_submission_via_step_with_coverage_gate(tmp_path):
    """PARTITION state: cortex_submit_partition runs coverage gate via step()."""
    eng = _eng(tmp_path)
    tid = eng.create_task(
        intent={"coverage_spec": {"required_units": ["auth"], "max_workers": 2}},
        track="mission"
    )
    # Move to PARTITION state first
    eng.step(tid, tool="cortex_submit_mission_contract",
             payload={"contract": {}}, seq=0)
    row = eng.get(tid)
    seq = row["seq"]
    # Now submit partition with complete coverage
    env = eng.step(tid, tool="cortex_submit_partition",
                   payload={"workers": [{"owns_units": ["auth"]}]}, seq=seq)
    assert env["ok"] is True
    assert env["state"] == "DISPATCH"
    # Missing coverage should refuse
    tid2 = eng.create_task(
        intent={"coverage_spec": {"required_units": ["auth", "db"]}},
        track="mission"
    )
    eng.step(tid2, tool="cortex_submit_mission_contract", payload={}, seq=0)
    row2 = eng.get(tid2)
    env2 = eng.step(tid2, tool="cortex_submit_partition",
                    payload={"workers": [{"owns_units": ["auth"]}]}, seq=row2["seq"])
    assert env2["ok"] is True  # refusal returns ok=True with gate failure encoded
    assert not env2.get("gate", {}).get("pass", True)  # gate failed
    assert "MISSING_COVERAGE" in str(env2.get("gate", {}))


def test_mission_merge_requires_all_workers_done_guard(tmp_path):
    """MONITOR->MERGE: merge attempt when workers not all done should be detected.
    This guard is enforced in the MCP tool, not the state engine (the engine doesn't know
    about workers). This test documents the guard requirement."""
    # This is tested via integration tests in the MCP layer; the state engine itself
    # just tracks state transitions. The MCP tool cortex_submit_merge enforces the
    # mission_status().all_done guard before calling step().
    pass


def test_mission_review_scope_gate_on_merged_whole(tmp_path):
    """REVIEW state: review_scope_gate compares merged artifact against mission intent."""
    from cortex_core.state_engine import review_scope_gate, default_gate
    phase = "REVIEW"
    task = {
        "intent": {"seeking": "build authentication system"},
        "track": "mission",
    }
    # Without scope_check: pass but warn
    env1 = review_scope_gate(phase, task, {}, base=default_gate)
    assert env1["pass"] is True
    assert "scope_warning" in env1

    # With matching scope_check: pass
    env2 = review_scope_gate(phase, task,
                             {"scope_check": {"delivered": "authentication implementation",
                                              "matches_request": True}}, base=default_gate)
    assert env2["pass"] is True
    assert env2.get("scope_check") == "ok"

    # With mismatched scope_check: fail
    env3 = review_scope_gate(phase, task,
                             {"scope_check": {"delivered": "something else",
                                              "matches_request": False}}, base=default_gate)
    assert env3["pass"] is False
    assert "does not match" in str(env3.get("reason", ""))


def test_mission_track_full_lifecycle_to_review(tmp_path):
    """End-to-end: INTAKE -> PARTITION -> DISPATCH -> MERGE -> REVIEW -> CLOSEOUT."""
    eng = _eng(tmp_path)
    tid = eng.create_task(
        intent={"seeking": "build auth", "coverage_spec": {"required_units": ["auth"], "max_workers": 1}},
        track="mission"
    )
    assert eng.get(tid)["state"] == "INTAKE"

    # INTAKE -> PARTITION
    env1 = eng.step(tid, tool="cortex_submit_mission_contract", payload={}, seq=0)
    assert env1["ok"] and env1["state"] == "PARTITION"

    # PARTITION -> DISPATCH (valid coverage; worker carries its disjoint claim, persisted)
    env2 = eng.step(tid, tool="cortex_submit_partition",
                    payload={"workers": [{"owns_units": ["auth"],
                                          "intent": {"seeking": "auth"},
                                          "claims": [{"kind": "path", "key": "auth/**"}]}]},
                    seq=env1["seq"])
    assert env2["ok"] and env2["state"] == "DISPATCH"

    # DISPATCH -> MONITOR: the engine ATOMICALLY materializes the build worker from the
    # persisted partition in this same superstep (S4a); the envelope carries its worker_ids.
    env3 = eng.step(tid, tool="cortex_dispatch_mission", payload={}, seq=env2["seq"])
    assert env3["ok"] and env3["state"] == "MONITOR"
    assert len(env3["worker_ids"]) == 1
    child = eng.get(env3["worker_ids"][0])
    assert child["track"] == "build" and child["state"] == "SEARCH_BRAIN"


# --- GAP B2 tests: abstain-default REVIEW exit (2026-07-14) ---
#
# When an AUTO build task reaches REVIEW with NO deterministic oracle and no human on
# call, the state machine must default to ABSTAIN + flag-for-human -- a LOGGED handled
# success, never a confident-but-unverified pass. These are the TDD success conditions.


def _drive_to_review(eng, tid):
    """Step a build task forward (using each phase's advance tool) until it reaches REVIEW."""
    for _ in range(20):
        row = eng.get(tid)
        if row["state"] == "REVIEW":
            return row
        legal = row["legal_tools"]
        assert legal, f"no legal tools in {row['state']}"
        eng.step(tid, tool=legal[0],
                 payload={"evidence": [], "patch": "x", "plan": "x", "spec": "x"},
                 seq=row["seq"])
    raise AssertionError("build task never reached REVIEW")


def test_review_abstains_when_no_oracle_and_no_human(tmp_path):
    """(a) auto task + advisory (non-oracle) gate + no human -> ABSTAIN, not a confident verdict."""
    eng = _eng(tmp_path)  # default make_universal_gate -> REVIEW uses advisory review_scope_gate
    tid = eng.create_task(intent={"seeking": "x", "auto": True})  # auto, no human_available
    row = _drive_to_review(eng, tid)
    env = eng.step(tid, tool="cortex_submit_review", payload={"evidence": []}, seq=row["seq"])
    assert env["ok"] is True                 # handled, not an error
    assert env["outcome"] == "ABSTAIN"       # abstained, not a confident pass
    assert env["flag_human"] is True         # flagged for a human
    assert env["state"] == "ABSTAINED"       # terminal abstain sink, NOT CLOSEOUT/DONE
    assert env["state"] not in ("CLOSEOUT", "DONE")


def test_abstain_is_logged_as_handled_success(tmp_path):
    """(b) the abstain path is logged as a handled success with a server-written closeout."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x", "auto": True})
    row = _drive_to_review(eng, tid)
    env = eng.step(tid, tool="cortex_submit_review", payload={"evidence": []}, seq=row["seq"])
    assert env["ok"] is True
    after = eng.get(tid)
    assert after["state"] == "ABSTAINED"
    assert after["closeout_written"] is True           # audit record ALWAYS exists
    # The event log records the abstention + a server-written closeout (not a gate_failed/abandon).
    kinds = [e["kind"] for e in eng.event_history(tid)]
    assert "abstained" in kinds
    assert kinds.count("closeout") >= 1
    # replay agrees with the folded state -- event-sourcing honesty check.
    assert eng.replay(tid)["state"] == "ABSTAINED"


def test_abstain_advisory_output_is_non_promotable(tmp_path):
    """(c) any advisory verdict that fed the abstain is tagged advisory_semi_gold: hard
    non-trainable, non-promotable."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x", "auto": True})
    row = _drive_to_review(eng, tid)
    env = eng.step(tid, tool="cortex_submit_review", payload={"evidence": []}, seq=row["seq"])
    advisory = env["advisory"]
    assert advisory["data_class"] == "advisory_semi_gold"
    assert advisory["promotable"] is False
    assert advisory["trainable"] is False


def test_review_does_not_abstain_when_human_available(tmp_path):
    """A human-in-the-loop auto task does NOT abstain -- the human is the reviewer."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x", "auto": True, "human_available": True})
    row = _drive_to_review(eng, tid)
    env = eng.step(tid, tool="cortex_submit_review", payload={"evidence": []}, seq=row["seq"])
    assert env.get("outcome") != "ABSTAIN"
    assert env["state"] == "CLOSEOUT"       # normal advance, human will review at closeout


def test_review_does_not_abstain_with_deterministic_oracle(tmp_path):
    """An oracle-backed pass (verdict.deterministic=True) advances normally even auto+no-human."""
    oracle_gate = lambda phase, task, payload: {"pass": True, "deterministic": True}
    eng = _eng(tmp_path, gate=oracle_gate)
    tid = eng.create_task(intent={"seeking": "x", "auto": True})
    row = _drive_to_review(eng, tid)
    env = eng.step(tid, tool="cortex_submit_review", payload={"evidence": []}, seq=row["seq"])
    assert env.get("outcome") != "ABSTAIN"
    assert env["state"] == "CLOSEOUT"       # deterministic oracle -> trusted advance


def test_non_auto_task_does_not_abstain(tmp_path):
    """A non-auto (interactive) task never abstains -- existing flows are unchanged."""
    eng = _eng(tmp_path)
    tid = eng.create_task(intent={"seeking": "x"})  # no "auto" flag
    row = _drive_to_review(eng, tid)
    env = eng.step(tid, tool="cortex_submit_review", payload={"evidence": []}, seq=row["seq"])
    assert env.get("outcome") != "ABSTAIN"
    assert env["state"] == "CLOSEOUT"


# --- S4 topology: >=3 build-track workers under an EXISTING mission parent -------
# (sol #6 / S4a fix: dispatch_workers attaches disjoint-claim build children to the
# SAME mission task, which spawn_mission's single-`track` design could not express.)


def _drive_build_to_done(eng, tid, *, artifact=None):
    """Drive one build-track task SEARCH_BRAIN..CLOSEOUT -> DONE via its declared
    advance tools (default/universal gate passes a well-formed dict payload). The
    CLOSEOUT payload carries a distinct `result` so 'no work lost' is checkable."""
    build = state_engine.BUILD_TRACK
    for _ in range(30):
        row = eng.get(tid)
        st = row["state"]
        if st in ("DONE", "ABANDONED"):
            return st
        tool = build["states"][st]["advance_tool"]
        payload = {"evidence": [], "patch": "x", "result": artifact} if artifact else \
                  {"evidence": [], "patch": "x"}
        eng.step(tid, tool=tool, payload=payload, seq=row["seq"])
    return eng.get(tid)["state"]


def test_dispatch_workers_three_disjoint_under_existing_mission(tmp_path):
    """(a) >=3 workers atomically CLAIM 3 disjoint sub-tasks under an existing mission task,
    each on its OWN build chart (S4b) hanging off that exact parent (so mission_status sees
    them). This is what spawn_mission's single-`track` topology could not express."""
    eng = _eng(tmp_path)
    mission = eng.create_task(intent={"seeking": "organize corpus"}, track="mission")
    res = eng.dispatch_workers(mission, [
        {"intent": {"seeking": "feature RE research"}, "claims": [{"kind": "path", "key": "research/**"}]},
        {"intent": {"seeking": "corpus ingest"}, "claims": [{"kind": "path", "key": "library/**"}]},
        {"intent": {"seeking": "index verify"}, "claims": [{"kind": "path", "key": "index/**"}]},
    ])
    assert res["ok"] is True
    assert res["mission_id"] == mission and len(res["worker_ids"]) == 3
    # Each worker is a build-track child of THIS mission, in its own build initial state.
    for wid in res["worker_ids"]:
        w = eng.get(wid)
        assert w["track"] == "build" and w["state"] == "SEARCH_BRAIN"
        assert eng._db.execute("SELECT parent_id FROM task WHERE id=?", (wid,)
                               ).fetchone()["parent_id"] == mission
    # The mission (still on its own chart) sees exactly its 3 children.
    status = eng.mission_status(mission)
    assert status["n"] == 3 and status["done"] == 0 and status["all_done"] is False
    # Each claim is held by exactly one worker (disjoint, exclusive, auditable).
    claims = eng._db.execute("SELECT key, task_id FROM claim ORDER BY key").fetchall()
    assert len(claims) == 3
    assert len({c["task_id"] for c in claims}) == 3  # no two workers share a claim row


def test_dispatch_workers_rejects_double_claim_all_or_nothing(tmp_path):
    """(b) A double-claim / lease collision is rejected and NOTHING is created: no two workers
    can ever hold the same (or a glob-overlapping) claim. Two independent collision routes:
    (i) two workers in the same batch overlap; (ii) a worker overlaps an already-live claim."""
    eng = _eng(tmp_path)
    mission = eng.create_task(intent={"seeking": "m"}, track="mission")
    before_tasks = eng._db.execute("SELECT COUNT(*) c FROM task").fetchone()["c"]
    # (i) intra-batch overlap: src/** vs src/auth/x.py
    clash = eng.dispatch_workers(mission, [
        {"intent": {}, "claims": [{"kind": "path", "key": "src/**"}]},
        {"intent": {}, "claims": [{"kind": "path", "key": "src/auth/x.py"}]},
    ])
    assert clash["ok"] is False and clash["code"] == "CLAIM_CONFLICT" and clash["conflicts"]
    # all-or-nothing: no worker task, no claim leaked (mission row unchanged)
    assert eng._db.execute("SELECT COUNT(*) c FROM task").fetchone()["c"] == before_tasks
    assert eng._db.execute("SELECT COUNT(*) c FROM claim").fetchone()["c"] == 0
    # (ii) collision with an already-live claim held by a first, valid dispatch.
    ok = eng.dispatch_workers(mission, [
        {"intent": {}, "claims": [{"kind": "path", "key": "src/db/**"}]}])
    assert ok["ok"] is True
    clash2 = eng.dispatch_workers(mission, [
        {"intent": {}, "claims": [{"kind": "path", "key": "src/db/schema.py"}]}])  # inside src/db/**
    assert clash2["ok"] is False and clash2["code"] == "CLAIM_CONFLICT"
    # only the first worker's single claim survives
    assert eng._db.execute("SELECT COUNT(*) c FROM claim").fetchone()["c"] == 1


def test_dispatch_workers_run_with_real_overlapping_spans(tmp_path):
    """(c) The workers run CONCURRENTLY with real overlapping execution spans, and each
    advances its OWN task without collision (the seq-fence + per-task claim keep them
    independent). A barrier makes all three overlap deterministically; we assert >=2
    overlapping pairs AND that every worker's step applied exactly once (no lost/collided work)."""
    import threading
    import time as _time
    eng = _eng(tmp_path)
    mission = eng.create_task(intent={"seeking": "m"}, track="mission")
    res = eng.dispatch_workers(mission, [
        {"intent": {"i": k}, "claims": [{"kind": "path", "key": f"w{k}/**"}]} for k in range(3)])
    assert res["ok"] is True
    wids = res["worker_ids"]
    spans: dict[str, tuple[float, float]] = {}
    barrier = threading.Barrier(len(wids))

    def _run(wid):
        barrier.wait()  # force temporal overlap
        start = _time.perf_counter()
        row = eng.get(wid)
        # a real in-phase note (cortex_search is legal in SEARCH_BRAIN) = the worker doing work
        eng.step(wid, tool="cortex_search", payload={"query": wid}, seq=row["seq"])
        _time.sleep(0.1)  # simulated work while siblings run
        spans[wid] = (start, _time.perf_counter())

    threads = [threading.Thread(target=_run, args=(w,)) for w in wids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # >=2 pairs of workers have overlapping [start,end] execution intervals
    pairs = 0
    ids = list(spans)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = spans[ids[i]], spans[ids[j]]
            if a[0] < b[1] and b[0] < a[1]:
                pairs += 1
    assert pairs >= 2, f"expected >=2 overlapping worker spans, got {pairs}"
    # No lost/collided work: each worker advanced exactly once (seq 0 created -> 1 note).
    for wid in wids:
        assert eng.get(wid)["seq"] == 1


def test_dispatch_workers_reconcile_none_lost(tmp_path):
    """(d) All workers reach DONE and reconcile back without collision or lost work: three
    distinct artifacts survive, mission_status reports all_done, done==n==3."""
    eng = _eng(tmp_path)
    mission = eng.create_task(intent={"seeking": "m"}, track="mission")
    res = eng.dispatch_workers(mission, [
        {"intent": {"seeking": f"unit{k}"}, "claims": [{"kind": "path", "key": f"u{k}/**"}]}
        for k in range(3)])
    assert res["ok"] is True
    for k, wid in enumerate(res["worker_ids"]):
        assert _drive_build_to_done(eng, wid, artifact=f"artifact-{k}") == "DONE"
    status = eng.mission_status(mission)
    assert status["n"] == 3 and status["done"] == 3 and status["all_done"] is True
    # Every worker's distinct artifact is preserved in its own closeout event (none lost/merged).
    seen = set()
    for wid in res["worker_ids"]:
        hist = eng.event_history(wid, limit=50)
        # the CLOSEOUT advance recorded this worker's distinct result artifact
        payloads = [eng._db.execute("SELECT payload FROM event WHERE task_id=? AND seq=?",
                                    (wid, e["seq"])).fetchone()["payload"] for e in hist]
        blob = " ".join(payloads)
        assert wid.startswith("t_")
        for k in range(3):
            if f"artifact-{k}" in blob:
                seen.add(k)
    assert seen == {0, 1, 2}  # all three distinct artifacts reconciled, none lost
    # Terminal workers released their claims -> the partition is free again for follow-on work.
    assert eng._db.execute("SELECT COUNT(*) c FROM claim").fetchone()["c"] == 0


def test_worker_failure_releases_claim_for_retry_without_corrupting_siblings(tmp_path):
    """(e) A failed worker (driven to ABANDONED) releases ONLY its own claim; its siblings'
    claims are untouched, and a replacement worker can re-dispatch onto the freed slice."""
    fail_review = lambda phase, task, payload: {"pass": phase != "REVIEW"}  # REVIEW never passes
    eng = _eng(tmp_path, gate=fail_review)
    mission = eng.create_task(intent={"seeking": "m"}, track="mission")
    res = eng.dispatch_workers(mission, [
        {"intent": {"seeking": "a"}, "claims": [{"kind": "path", "key": "a/**"}]},
        {"intent": {"seeking": "b"}, "claims": [{"kind": "path", "key": "b/**"}]},
        {"intent": {"seeking": "c"}, "claims": [{"kind": "path", "key": "c/**"}]},
    ])
    assert res["ok"] is True
    w_fail, w_ok1, w_ok2 = res["worker_ids"]
    # Drive ONLY w_fail: its REVIEW keeps failing -> escalate -> ABANDONED (via CLOSEOUT).
    assert _drive_build_to_done(eng, w_fail) == "ABANDONED"
    # Its claim is released; the siblings (still in SEARCH_BRAIN) keep theirs intact.
    held = {r["key"]: r["task_id"]
            for r in eng._db.execute("SELECT key, task_id FROM claim").fetchall()}
    assert "a/**" not in held  # freed by the terminal transition
    assert held.get("b/**") == w_ok1 and held.get("c/**") == w_ok2  # siblings uncorrupted
    assert eng.get(w_ok1)["state"] == "SEARCH_BRAIN" and eng.get(w_ok2)["state"] == "SEARCH_BRAIN"
    # A replacement worker can now re-claim the freed slice under the same mission -- retry works.
    retry = eng.dispatch_workers(mission, [
        {"intent": {"seeking": "a-retry"}, "claims": [{"kind": "path", "key": "a/**"}]}])
    assert retry["ok"] is True and len(retry["worker_ids"]) == 1
    assert eng._db.execute("SELECT task_id FROM claim WHERE key='a/**'").fetchone()["task_id"] \
        == retry["worker_ids"][0]
    # mission now parents 4 children (3 original incl. the abandoned one + 1 replacement); the
    # abandoned worker's work was not silently dropped from the audit trail.
    assert eng.mission_status(mission)["n"] == 4


# --- S4a GOVERNED path: the atomic mission-chart DISPATCH advance (sol@xhigh must-fixes) ---
# This is what connecting agents actually drive. The DISPATCH advance materializes the workers
# from the PARTITION-validated, server-PERSISTED manifest, inside the SAME superstep txn --
# closing the orphan-children exploit (dispatch was non-atomic w/ the transition), the
# partition->claim drift (claims re-supplied at DISPATCH), and claimless/absent-partition holes.


def _mission_to_dispatch(eng, workers, *, required=None):
    """Drive a mission-track task INTAKE->PARTITION->(gate)->DISPATCH; return (mission_id, seq)."""
    required = required or [f"u{i}" for i in range(len(workers))]
    mid = eng.create_task(intent={"seeking": "m",
                                  "coverage_spec": {"required_units": required, "max_workers": 8}},
                          track="mission")
    e1 = eng.step(mid, tool="cortex_submit_mission_contract", payload={}, seq=0)
    e2 = eng.step(mid, tool="cortex_submit_partition", payload={"workers": workers}, seq=e1["seq"])
    assert e2["state"] == "DISPATCH", e2
    return mid, e2["seq"]


def test_governed_dispatch_materializes_workers_atomically(tmp_path):
    """The DISPATCH advance creates >=3 disjoint-claim build children from the persisted
    partition, records each worker's claims in its created event (durable S4 evidence that
    survives terminal claim deletion), and persists the authoritative cohort on the mission."""
    eng = _eng(tmp_path)
    workers = [{"owns_units": [f"u{i}"], "intent": {"seeking": f"u{i}"},
                "claims": [{"kind": "path", "key": f"u{i}/**"}]} for i in range(3)]
    mid, seq = _mission_to_dispatch(eng, workers)
    env = eng.step(mid, tool="cortex_dispatch_mission", payload={}, seq=seq)
    assert env["ok"] and env["state"] == "MONITOR"
    wids = env["worker_ids"]
    assert len(wids) == 3
    status = eng.mission_status(mid)
    assert status["n"] == 3 and status["cohort_consistent"] is True
    assert set(status["cohort"]) == set(wids)
    for i, wid in enumerate(wids):
        w = eng.get(wid)
        assert w["track"] == "build" and w["state"] == "SEARCH_BRAIN"
        # claim recorded in the created event -> provable post-run even after terminal release
        created = eng._db.execute(
            "SELECT payload FROM event WHERE task_id=? AND seq=0", (wid,)).fetchone()["payload"]
        assert f"u{i}/**" in created
    # exactly 3 disjoint live claims, one per worker
    claims = eng._db.execute("SELECT task_id FROM claim").fetchall()
    assert len({c["task_id"] for c in claims}) == 3 and len(claims) == 3


def test_governed_dispatch_fails_closed_on_overlapping_partition_no_orphans(tmp_path):
    """A partition that passes the owns_units MECE gate but whose CLAIMS overlap must fail the
    DISPATCH advance CLOSED: gate verdict CLAIM_CONFLICT, mission STAYS at DISPATCH, and ZERO
    worker children / claims are left behind (the non-atomic orphan-children exploit is closed)."""
    eng = _eng(tmp_path)
    workers = [{"owns_units": ["a"], "claims": [{"kind": "path", "key": "src/**"}]},
               {"owns_units": ["b"], "claims": [{"kind": "path", "key": "src/db.py"}]}]  # inside src/**
    mid, seq = _mission_to_dispatch(eng, workers, required=["a", "b"])
    env = eng.step(mid, tool="cortex_dispatch_mission", payload={}, seq=seq)
    assert env["state"] == "DISPATCH"  # did NOT advance
    assert env["gate"]["pass"] is False and env["gate"]["code"] == "CLAIM_CONFLICT"
    assert eng.mission_status(mid)["n"] == 0  # no orphan children
    assert eng._db.execute("SELECT COUNT(*) c FROM claim").fetchone()["c"] == 0  # no orphan claims


def test_governed_dispatch_rejects_claimless_and_absent_partition(tmp_path):
    """A claimless worker can't own a disjoint slice, and a mission with no persisted partition
    can't dispatch -- both fail the DISPATCH advance closed."""
    eng = _eng(tmp_path)
    # claimless worker (owns a unit but declares no claim)
    mid, seq = _mission_to_dispatch(eng, [{"owns_units": ["a"]}], required=["a"])
    env = eng.step(mid, tool="cortex_dispatch_mission", payload={}, seq=seq)
    assert env["state"] == "DISPATCH" and env["gate"]["pass"] is False
    assert env["gate"]["code"] == "CLAIMLESS_WORKER"
    assert eng.mission_status(mid)["n"] == 0
