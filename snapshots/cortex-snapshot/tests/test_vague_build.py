"""Unit tests for the minimal vague-build driver. Fully offline: the student `llm` and the
`gate` are injected, so nothing hits the network and nothing launches a subprocess."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import build_skills as bs  # noqa: E402
from cortex_core import vague_build as vb  # noqa: E402
from cortex_core.app_contract import CheckResult, GateVerdict  # noqa: E402

VALID_SLOT = ('{"entity":"client","fields":['
              '{"name":"name","type":"text","required":true},'
              '{"name":"paid","type":"bool","required":true}]}')


def _pass_gate(app_dir, checks):
    results = tuple(CheckResult(kind=c["kind"], passed=True, hidden=False, detail="") for c in checks)
    return GateVerdict(passed=True, results=results, failure_class=None,
                       hidden_coverage=False, env_retries=0, seed=0)


def _fail_gate_on(kind):
    def gate(app_dir, checks):
        results = tuple(
            CheckResult(kind=c["kind"], passed=(c["kind"] != kind), hidden=False, detail="d",
                        failure_class=("X_FAIL" if c["kind"] == kind else None))
            for c in checks)
        return GateVerdict(passed=False, results=results, failure_class="X_FAIL",
                           hidden_coverage=False, env_retries=0, seed=0)
    return gate


@pytest.fixture(autouse=True)
def _spy_record(monkeypatch):
    """Spy the run-outcome logger so tests never write to ops-local."""
    calls = []
    monkeypatch.setattr(vb, "_record_run", lambda *a, **k: calls.append((a, k)))
    return calls


def test_route_build_task():
    skills = bs.load_skills()
    assert vb.route("track my clients and who has paid", skills) == "scaffold-crud-sqlite"
    assert vb.route("make me an inventory app", skills) == "scaffold-crud-sqlite"


def test_drive_good_slot_built_and_passed():
    r = vb.drive("track my clients", llm=lambda p: VALID_SLOT, gate=_pass_gate)
    assert r["status"] == "built"
    assert r["passed"] is True
    assert r["skill_id"] == "scaffold-crud-sqlite"
    assert r["attempts"] == 1
    assert Path(r["app_dir"], "app.py").is_file()


def test_drive_bad_slot_retries_then_gives_up():
    seen = []
    def bad_llm(p):
        seen.append(p)
        return "definitely not json"
    r = vb.drive("track my clients", llm=bad_llm, gate=_pass_gate, retries=1)
    assert r["status"] == "bad_slot"
    assert r["attempts"] == 2                 # retries + 1
    assert len(seen) == 2
    assert "rejected" in seen[1]              # retry prompt carries the validator's reason


def test_drive_recovers_after_retry():
    seq = iter(["garbage", VALID_SLOT])
    r = vb.drive("track my clients", llm=lambda p: next(seq), gate=_pass_gate, retries=1)
    assert r["status"] == "built" and r["attempts"] == 2 and r["passed"] is True


def test_gate_receives_exact_skill_done_checks():
    skill = bs.load_skills()["scaffold-crud-sqlite"]
    captured = {}
    def gate(app_dir, checks):
        captured["kinds"] = [c["kind"] for c in checks]
        return _pass_gate(app_dir, checks)
    vb.drive("track my clients", llm=lambda p: VALID_SLOT, gate=gate)
    assert captured["kinds"] == [c["kind"] for c in skill.done_checks]


def test_run_outcome_logged_once_on_build(_spy_record):
    vb.drive("track my clients", llm=lambda p: VALID_SLOT, gate=_pass_gate)
    assert len(_spy_record) == 1


def test_fail_gate_surfaces_failure_class():
    r = vb.drive("track my clients", llm=lambda p: VALID_SLOT, gate=_fail_gate_on("input_handling"))
    assert r["status"] == "built"
    assert r["passed"] is False
    assert r["failure_class"] == "X_FAIL"


# --- chaining (BUILD-04) --------------------------------------------------------------------
_SCAFFOLD2 = ('{"entity":"member","fields":['
              '{"name":"name","type":"text","required":true},'
              '{"name":"active","type":"bool","required":true}]}')
_METRIC_SLOT = '{"label":"Active","field":"active","op":"eq","value":"1"}'
_SEARCH_SLOT = '{"field":"name"}'


def test_detect_followons():
    skills = bs.load_skills()
    assert vb.detect_followons("track members, count the active ones, let me search", skills) == \
        ["add-summary-metric", "add-search-filter"]
    assert vb.detect_followons("just track members", skills) == []


def test_chain_applies_metric_and_search():
    seq = iter([_SCAFFOLD2, _METRIC_SLOT, _SEARCH_SLOT])
    cap = {}
    def gate(app_dir, checks):
        cap["kinds"] = [c["kind"] for c in checks]
        cap["dir"] = app_dir
        return _pass_gate(app_dir, checks)
    r = vb.drive("track members, count active, search them", llm=lambda p: next(seq), gate=gate)
    assert r["status"] == "built" and r["passed"] is True
    assert r["skills"] == ["scaffold-crud-sqlite", "add-summary-metric", "add-search-filter"]
    assert "derived_value" in cap["kinds"] and "filtered_results" in cap["kinds"]
    # once-per-app kinds deduped to exactly one; feature checks kept
    for k in ("app_starts", "data_persists", "regression", "schema_real"):
        assert cap["kinds"].count(k) == 1, (k, cap["kinds"])
    src = Path(cap["dir"], "app.py").read_text(encoding="utf-8")
    assert "data-cortex-metric" in src               # metric edit applied
    assert 'name="q"' in src                          # search box applied


def test_chain_skips_followon_with_out_of_scaffold_field():
    bad = '{"label":"X","field":"nonexistent","op":"eq","value":"1"}'
    seq = iter([_SCAFFOLD2, bad, bad])                # metric retried, still bad -> skipped
    r = vb.drive("track members and count them", llm=lambda p: next(seq, bad), gate=_pass_gate)
    assert r["status"] == "built"
    assert "add-summary-metric" not in r["skills"]
    assert any(s["skill_id"] == "add-summary-metric" for s in r["skipped"])


def test_tier_aliases_resolve_and_paid_9router_blocked():
    """The free-model plan wiring: friendly aliases map to (dispatch tier, model_override), and any
    PAID 9router model is refused in code (the user's no-paid-9router rule)."""
    assert vb._resolve_tier("laguna-m1") == ("openrouter", "poolside/laguna-m.1:free")
    assert vb._resolve_tier("big-pickle") == ("opencode-zen", None)
    assert vb._resolve_tier("aux") == ("ninerouter", "aux")
    assert vb._resolve_tier("opencode")[0] == "opencode"          # raw tier passthrough
    # paid 9router blocked; free allowed
    import pytest as _pt
    with _pt.raises(ValueError):
        vb._guard_no_paid_9router("ninerouter", "cx/gpt-5.4-mini")
    with _pt.raises(ValueError):
        vb._guard_no_paid_9router("ninerouter", "ag/claude-opus-4-6-thinking")
    vb._guard_no_paid_9router("ninerouter", "aux")               # ok
    vb._guard_no_paid_9router("openrouter", "poolside/laguna-m.1:free")  # ok (not 9router)


def test_parameterless_followons_skip_the_model():
    """A parameterless follow-on (empty-object slot: delete/edit/role-gate/audit/dashboard/detail)
    is auto-filled `{}` WITHOUT a student round-trip -- a big composite drops from N model calls to
    just the parameterized ones, and the weak model can't fumble the trivial slot."""
    scaffold = ('{"entity":"member","fields":['
                '{"name":"name","type":"text","required":true},'
                '{"name":"active","type":"bool","required":true}]}')
    calls = []
    def llm(p):
        calls.append(p)
        return scaffold                       # only the scaffold slot is ever requested
    r = vb.drive("track members, let me delete them, keep an audit log, add a dashboard, "
                 "and a detail page", llm=llm, gate=_pass_gate)
    assert r["status"] == "built" and r["passed"] is True
    assert r["skills"] == ["scaffold-crud-sqlite", "add-delete-with-confirm",
                           "add-audit-log", "add-dashboard", "add-detail-view"]
    assert len(calls) == 1                    # scaffold only; 4 parameterless skills skipped the model


def test_parameterized_followons_still_call_the_model():
    # metric/search carry a field -> they still need the student
    seq = iter([_SCAFFOLD2, _METRIC_SLOT, _SEARCH_SLOT])
    calls = []
    def llm(p):
        calls.append(p)
        return next(seq)
    r = vb.drive("track members, count active, search them", llm=llm, gate=_pass_gate)
    assert r["status"] == "built" and len(calls) == 3   # scaffold + metric + search


def test_merge_checks_dedups_once_per_app_kinds():
    base = [{"kind": "app_starts"}, {"kind": "data_persists"}, {"kind": "regression", "ledger_file": "g"}]
    extra = [{"kind": "app_starts"}, {"kind": "data_persists"}, {"kind": "derived_value"},
             {"kind": "regression", "ledger_file": "g"}]
    kinds = [c["kind"] for c in vb._merge_checks(base, extra)]
    assert kinds.count("app_starts") == 1
    assert kinds.count("data_persists") == 1        # the redundant follow-on persistence is dropped
    assert kinds.count("regression") == 1
    assert "derived_value" in kinds                 # distinct feature check kept
