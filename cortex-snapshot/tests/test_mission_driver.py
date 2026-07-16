"""run_mission: the native heterogeneous decomposer wired into the state machine PARTITION seam.

Fully offline + JUDGE-FREE:
  * model dispatch (propose_manifest) is injected -> a fixed manifest, no live/paid call.
  * child execution (worker_build) is injected -> writes a tiny artifact + mints a REAL server
    receipt bound to the child's task_id (receipts' TEST-ONLY injected-gate seam). No live call.

The ONLY authorities are deterministic: validate_manifest (the spawn gate) and the per-child
server receipts (the completion gate). No model decides any child's "done".
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import mission_driver as md  # noqa: E402
from cortex_core import receipts as rcp  # noqa: E402
from cortex_core.app_contract import CheckResult, GateVerdict  # noqa: E402


# --- deterministic 2-arg gates (the receipt run_checks shape; NO model in any verdict path) ---
def _pass_gate(app_dir, checks):
    return GateVerdict(passed=True,
                       results=(CheckResult("app_starts", True, False, ""),),
                       failure_class=None, hidden_coverage=False, env_retries=0, seed=0)


def _fail_gate(app_dir, checks):
    return GateVerdict(passed=False,
                       results=(CheckResult("app_starts", False, False, "d", "X_FAIL"),),
                       failure_class="X_FAIL", hidden_coverage=False, env_retries=0, seed=0)


@pytest.fixture(autouse=True)
def _open_test_gate_seam(monkeypatch):
    """Offline suite mints receipts with fake gates, so open receipts' TEST-ONLY seam."""
    monkeypatch.setattr(rcp, "_ALLOW_INJECTED_GATE", True)


def _manifest(tmp_path, *, required=("api", "ui"), missing_coverage=False):
    """A 2-worker heterogeneous manifest with DISJOINT owns_units + DISJOINT path claims."""
    api_owns = ["api"]
    ui_owns = [] if missing_coverage else ["ui"]   # missing_coverage -> "ui" owned by nobody
    return {
        "mission_id": "t_parent",
        "coverage_spec": {"required_units": list(required), "max_workers": 3},
        "workers": [
            {"key": "api", "objective": "build the api handlers slice", "track": "app_build",
             "tier_profile": "code-medium", "owns_units": api_owns,
             "claims": [{"kind": "path", "key": "src/api/**"}], "depends_on": [],
             "artifact_lane": str(tmp_path / "lanes" / "api"),
             "acceptance": {"kind": "smoke_receipt"}},
            {"key": "ui", "objective": "build the ui view slice", "track": "app_build",
             "tier_profile": "code-medium", "owns_units": ui_owns,
             "claims": [{"kind": "path", "key": "src/ui/**"}], "depends_on": [],
             "artifact_lane": str(tmp_path / "lanes" / "ui"),
             "acceptance": {"kind": "smoke_receipt"}},
        ],
        "reducers": [{"kind": "git_merge", "order": ["api", "ui"]}],
    }


def _propose_returning(manifest):
    def _propose(goal, tier=None):
        return manifest
    return _propose


def _make_worker_build(ws, pass_keys, *, calls=None):
    """A fake child executor: writes a DISTINCT artifact per worker (distinct digests) and mints a
    REAL server receipt bound to the child's task_id. `pass_keys` decides which slices pass."""
    def wb(ctx: md.WorkerRunContext) -> md.WorkerBuild:
        if calls is not None:
            calls.append(ctx.key)
        lane = Path(ctx.artifact_lane)
        lane.mkdir(parents=True, exist_ok=True)
        (lane / "app.py").write_text(f"# slice {ctx.key}\nprint('{ctx.key}')\n", encoding="utf-8")
        checks = [{"kind": "app_starts"}]
        gate = _pass_gate if ctx.key in pass_keys else _fail_gate
        vid, v = rcp.run_and_record_smoke_verdict(
            task_id=ctx.task_id, app_dir=str(lane), checks=checks,
            run_checks=gate, workspace=str(ws))
        return md.WorkerBuild(app_dir=str(lane), checks=checks, verdict_id=vid,
                              passed=bool(v.passed), status="built", skills=[])
    return wb


# ============================================================================================
# 1) valid manifest -> N heterogeneous children spawned, each with its OWN receipt -> done
# ============================================================================================

def test_valid_manifest_spawns_children_each_with_own_receipt_and_reconciles_done(tmp_path):
    manifest = _manifest(tmp_path)
    r = md.run_mission(
        "ship an api + ui feature", workspace=str(tmp_path),
        propose=_propose_returning(manifest),
        worker_build=_make_worker_build(tmp_path, {"api", "ui"}))

    assert r["status"] == "done" and r["state"] == "DONE"
    assert len(r["worker_ids"]) == 2                      # one child task per worker

    outcomes = r["outcomes"]
    assert {o.key for o in outcomes} == {"api", "ui"}
    assert all(o.passed and o.final_state == "DONE" for o in outcomes)

    # each child minted its OWN receipt (distinct verdict_ids), bound to its OWN task + artifact.
    vids = [o.verdict_id for o in outcomes]
    assert all(vids) and len(set(vids)) == 2
    for o in outcomes:
        rec = rcp.lookup_smoke_verdict(o.verdict_id, workspace=str(tmp_path))
        assert rec is not None and rec["task_id"] == o.task_id
        assert rec["artifact_digest"] == rcp.digest_dir(o.app_dir)

    recon = r["reconciliation"]
    assert recon["passed"] is True and recon["missing_units"] == []
    assert recon["all_done"] and recon["cohort_consistent"]
    assert r["merge"]["reducer_digest"].startswith("sha256:")


# ============================================================================================
# 2) invalid manifest -> rejected BEFORE any spawn (no children, no child execution)
# ============================================================================================

def test_invalid_manifest_rejected_before_any_spawn(tmp_path):
    calls: list[str] = []
    bad = _manifest(tmp_path, missing_coverage=True)   # "ui" is a required unit owned by nobody
    r = md.run_mission(
        "ship an api + ui feature", workspace=str(tmp_path),
        propose=_propose_returning(bad),
        worker_build=_make_worker_build(tmp_path, {"api", "ui"}, calls=calls))

    assert r["status"] == "rejected"
    assert r["mission_id"] is None
    assert r["worker_ids"] == [] and r["outcomes"] == []
    codes = {p["code"] for p in r["problems"]}
    assert "MISSING_COVERAGE" in codes
    assert calls == []                                  # NOTHING executed -- no model bypass


def test_abstain_when_proposer_returns_no_manifest(tmp_path):
    calls: list[str] = []
    r = md.run_mission(
        "ship a feature", workspace=str(tmp_path),
        propose=lambda goal, tier=None: None,
        worker_build=_make_worker_build(tmp_path, set(), calls=calls))
    assert r["status"] == "abstained"
    assert r["worker_ids"] == [] and calls == []


# ============================================================================================
# 3) one REQUIRED child fails its receipt -> mission fails CLOSED (never a waved pass)
# ============================================================================================

def test_one_required_child_fails_receipt_mission_fails_closed(tmp_path):
    manifest = _manifest(tmp_path)
    r = md.run_mission(
        "ship an api + ui feature", workspace=str(tmp_path),
        propose=_propose_returning(manifest),
        worker_build=_make_worker_build(tmp_path, {"api"}))   # ui FAILS its deterministic gate

    assert r["status"] == "failed_closed"
    assert r["state"] != "DONE"                         # the mission never reached MERGE/DONE
    recon = r["reconciliation"]
    assert recon["passed"] is False
    assert "ui" in recon["missing_units"]

    by_key = {o.key: o for o in r["outcomes"]}
    assert by_key["api"].passed and by_key["api"].final_state == "DONE"
    assert not by_key["ui"].passed                       # the failing child never reached DONE
    assert by_key["ui"].final_state in ("ABANDONED", "SCAFFOLD", "SMOKE")


# ============================================================================================
# 4) a child cannot cross-validate another child's receipt
# ============================================================================================

def test_child_cannot_cross_validate_another_childs_receipt(tmp_path):
    manifest = _manifest(tmp_path)
    r = md.run_mission(
        "ship an api + ui feature", workspace=str(tmp_path),
        propose=_propose_returning(manifest),
        worker_build=_make_worker_build(tmp_path, {"api", "ui"}))
    assert r["status"] == "done"

    by_key = {o.key: o for o in r["outcomes"]}
    api, ui = by_key["api"], by_key["ui"]

    # api's genuine receipt validates against ITS OWN task_id + artifact.
    good = rcp.validate_smoke_receipt(
        api.verdict_id, task_id=api.task_id,
        expected_artifact_digest=rcp.digest_dir(api.app_dir),
        expected_checks_digest=rcp.digest_checks(api.checks), workspace=str(tmp_path))
    assert good["ok"] is True and good["passed"] is True

    # api's receipt CANNOT stand in for ui's task -- server rejects the cross-claim.
    cross = rcp.validate_smoke_receipt(
        api.verdict_id, task_id=ui.task_id,
        expected_artifact_digest=rcp.digest_dir(ui.app_dir),
        expected_checks_digest=rcp.digest_checks(ui.checks), workspace=str(tmp_path))
    assert cross["ok"] is False and cross["code"] == "VERDICT_TASK_MISMATCH"
