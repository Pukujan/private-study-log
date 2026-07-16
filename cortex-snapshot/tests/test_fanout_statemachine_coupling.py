"""Fan-out <-> state-machine coupling (2026-07-15, gap I3 wired).

Two parts, both fully offline + JUDGE-FREE (dispatch injected via `student_factory`; the
verdict path is the deterministic gate + rank_passers, never an LLM):

  Part 1  the fan-in receipt race is DEAD: each parallel fanout executor mints its OWN server
          receipt over its OWN candidate artifact (no shared holder), and the winner's
          verdict_id is carried forward. A loser's receipt cannot cross-validate as the winner.

  Part 2  the state machine AUTO-fans-out when the route supports it AND >=2 executors are live;
          it falls back to a single vb.drive() when the task isn't fan-out-eligible or <2
          executors -- and the single-worker path is behavior-preserving.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import shutil  # noqa: E402

from cortex_core import build_skills as bs  # noqa: E402
from cortex_core import fanout as fo  # noqa: E402
from cortex_core import hybrid_build as hb  # noqa: E402
from cortex_core import receipts as rcp  # noqa: E402
from cortex_core.app_contract import CheckResult, GateVerdict  # noqa: E402


def _slot(entity: str) -> str:
    return ('{"entity":"%s","fields":['
            '{"name":"name","type":"text","required":true},'
            '{"name":"active","type":"bool","required":true}]}') % entity


VALID_SLOT = _slot("member")

# Distinct slots per executor -> distinct rendered artifacts -> distinct digests, so the
# per-executor receipt binding is provable (not just distinct paths).
_ENTITY_BY_EXEC = {"laguna-m.1": "alpha", "big-pickle": "beta",
                   "north-mini": "gamma", "aux": "delta"}


def _distinct_factory(spec):
    return lambda prompt: _slot(_ENTITY_BY_EXEC.get(spec.name, "member"))


def _fixed_factory(text):
    return lambda spec: (lambda prompt: text)


# --- deterministic 2-arg gates (the receipt run_checks shape; NO model in any verdict path) ---
def _pass_gate(app_dir, checks):
    results = tuple(CheckResult(kind=(c.get("kind") if isinstance(c, dict) else "app_starts"),
                                passed=True, hidden=False, detail="")
                    for c in (checks or [{"kind": "app_starts"}]))
    return GateVerdict(passed=True, results=results, failure_class=None,
                       hidden_coverage=False, env_retries=0, seed=0)


@pytest.fixture(autouse=True)
def _open_test_gate_seam(monkeypatch):
    """Offline suite injects fake gates, so open receipts' TEST-ONLY injected-gate seam."""
    monkeypatch.setattr(rcp, "_ALLOW_INJECTED_GATE", True)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "docs").mkdir()
    shutil.copytree(REPO / "skills", tmp_path / "skills")
    return tmp_path


# ============================================================================================
# Part 1 -- per-executor receipts; no shared-holder race; winner carried; judge-free
# ============================================================================================

def test_each_executor_mints_its_own_receipt_no_shared_holder(ws):
    """N parallel executors each mint their OWN server receipt over their OWN artifact. The
    verdict_ids are DISTINCT and each is bound (in the server store) to that executor's own
    artifact digest -- exactly what a single shared `holder` could NOT produce."""
    execs = ["laguna-m.1", "big-pickle", "north-mini"]
    r = fo.fanout("track my members, count the active ones", executors=execs,
                  student_factory=_distinct_factory, receipt_task_id="t_race",
                  receipt_run_checks=_pass_gate, workspace=ws, reviewer=None, sink=ws)

    assert len(r.attempts) == 3 and all(a.passed for a in r.attempts)
    vids = [a.verdict_id for a in r.attempts]
    assert all(vids) and len(set(vids)) == 3            # a distinct receipt PER executor

    for a in r.attempts:
        rec = rcp.lookup_smoke_verdict(a.verdict_id, workspace=ws)
        assert rec is not None
        assert rec["task_id"] == "t_race"
        assert rec["app_dir"] == a.app_dir             # bound to THIS executor's artifact
        assert rec["artifact_digest"] == rcp.digest_dir(a.app_dir)
        assert rec["passed"] is True


def test_winner_verdict_id_validates_and_losers_cannot_cross_validate(ws):
    """The fan-in carries the WINNER's verdict_id as the task's SCAFFOLD verdict: it validates
    against the winner's own digests. A loser's genuine receipt, replayed against the winner's
    artifact/checks, is rejected (ARTIFACT_TASK_MISMATCH) -- receipts do not cross-validate."""
    execs = ["laguna-m.1", "big-pickle"]
    r = fo.fanout("track my members", executors=execs, student_factory=_distinct_factory,
                  receipt_task_id="t_win", receipt_run_checks=_pass_gate,
                  workspace=ws, reviewer=None, sink=ws)
    assert r.winner is not None
    win = r.winner
    ok = rcp.validate_smoke_receipt(
        win.verdict_id, task_id="t_win",
        expected_artifact_digest=rcp.digest_dir(win.app_dir),
        expected_checks_digest=rcp.digest_checks(win.check_specs), workspace=ws)
    assert ok["ok"] is True and ok["passed"] is True

    loser = next(a for a in r.attempts if a.verdict_id != win.verdict_id)
    bad = rcp.validate_smoke_receipt(
        loser.verdict_id, task_id="t_win",
        expected_artifact_digest=rcp.digest_dir(win.app_dir),
        expected_checks_digest=rcp.digest_checks(win.check_specs), workspace=ws)
    assert bad["ok"] is False and bad["code"] == "ARTIFACT_TASK_MISMATCH"


def test_winner_is_gate_selected_judge_free(ws):
    """One executor builds a valid slot (gate PASS), one emits garbage (bad_slot). The winner is
    the deterministic gate's PASSER via rank_passers -- never an LLM -- and with no failures the
    reviewer packet is never even built."""
    def factory(spec):
        if spec.name == "north-mini":
            return lambda prompt: "not json at all"     # -> bad_slot, no receipt
        return lambda prompt: _slot(_ENTITY_BY_EXEC.get(spec.name, "member"))

    r = fo.fanout("track my members", executors=["laguna-m.1", "big-pickle", "north-mini"],
                  student_factory=factory, receipt_task_id="t_judgefree",
                  receipt_run_checks=_pass_gate, workspace=ws, reviewer=None, sink=ws)

    assert r.winner is not None and r.winner.passed
    assert r.winner.executor != "north-mini"
    assert r.winner == r.ranking[0]                    # gate-partitioned + deterministically ranked
    # the bad_slot executor ran no gate -> minted no receipt
    bad = next(a for a in r.attempts if a.executor == "north-mini")
    assert bad.verdict_id is None and not bad.passed


# ============================================================================================
# Part 2 -- the state machine auto-couples fanout; single-worker path preserved
# ============================================================================================

def test_state_machine_auto_fans_out_when_supported_and_two_executors(ws):
    """fanout_supported(scaffold-crud-sqlite) is True and 2 executors are live: run_chunk
    AUTO-fans-out, the winner's server verdict flows through SMOKE, and the chunk reaches DONE."""
    r = hb.run_chunk("track my clients and who paid", project_id="p_fo", workspace=ws,
                     gate=_pass_gate, reaction_text="perfect, done",
                     fanout_executors=["laguna-m.1", "big-pickle"],
                     fanout_student_factory=_fixed_factory(VALID_SLOT))
    assert r["status"] == "done" and r["state"] == "DONE"
    assert r["build"]["passed"] is True
    fanned = r["build"].get("fanout")
    assert fanned is not None                           # the fan-out path ran
    assert fanned["winner"] in ("laguna-m.1", "big-pickle")
    assert set(fanned["attempts"]) == {"laguna-m.1", "big-pickle"}


def test_fewer_than_two_executors_falls_back_to_single_drive(ws):
    """Only ONE executor available -> no fan-out; the state machine uses the single vb.drive()
    path (its injected `llm`), and the build carries NO fanout summary."""
    r = hb.run_chunk("track my clients and who paid", project_id="p_one", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_pass_gate, reaction_text="perfect, done",
                     fanout_executors=["laguna-m.1"],
                     fanout_student_factory=_fixed_factory(VALID_SLOT))
    assert r["status"] == "done" and r["state"] == "DONE"
    assert r["build"]["passed"] is True
    assert "fanout" not in r["build"]                   # single-worker path, unchanged


def test_fanout_disabled_uses_single_drive(ws):
    """fanout_enabled=False keeps the exact single-worker path (behavior-preserving)."""
    r = hb.run_chunk("track my clients and who paid", project_id="p_off", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_pass_gate, reaction_text="perfect, done",
                     fanout_enabled=False)
    assert r["status"] == "done" and r["state"] == "DONE"
    assert "fanout" not in r["build"]


def test_unsupported_route_stays_single_worker(ws, monkeypatch):
    """When the route's skill isn't fan-out-eligible, run_chunk never fans out even with a
    factory + executors present -- the single vb.drive() path fills the slot."""
    monkeypatch.setattr(fo, "fanout_supported", lambda skill_id: False)
    r = hb.run_chunk("track my clients and who paid", project_id="p_uns", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_pass_gate, reaction_text="perfect, done",
                     fanout_executors=["laguna-m.1", "big-pickle"],
                     fanout_student_factory=_fixed_factory(VALID_SLOT))
    assert r["status"] == "done" and "fanout" not in r["build"]


def test_fanout_all_fail_reworks_closed_never_a_waved_pass(ws):
    """Judge-free fail path: every executor's build fails the deterministic gate. No winner ->
    a representative FAILING receipt is carried, SMOKE fails CLOSED, and under tiny caps the
    chunk ABANDONS (via server-side closeout) -- never a forged pass to SHOW."""
    from cortex_core import state_engine as se
    import json
    tiny = json.loads(json.dumps(se.APP_BUILD_TRACK))
    tiny["rework_cap"] = 0
    tiny["esc_cap"] = 0
    eng = se.StateEngine(str(ws / "tiny.db"), chart=tiny, gate=se.make_universal_gate(),
                         workspace=str(ws))

    def _fail_gate(app_dir, checks):
        return GateVerdict(passed=False,
                           results=(CheckResult("app_starts", False, False, "d", "X_FAIL"),),
                           failure_class="X_FAIL", hidden_coverage=False, env_retries=0, seed=0)

    r = hb.run_chunk("track my clients and who paid", project_id="p_fail", workspace=ws,
                     engine=eng, gate=_fail_gate,
                     fanout_executors=["laguna-m.1", "big-pickle"],
                     fanout_student_factory=_fixed_factory(VALID_SLOT))
    eng.close()
    assert r["status"] == "abandoned" and r["state"] == "ABANDONED"
    assert r["build"].get("fanout") is not None
    assert r["build"]["passed"] is False
