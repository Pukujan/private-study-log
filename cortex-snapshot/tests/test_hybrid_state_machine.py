"""The hybrid state machine: Director tiers 2+3, APP_BUILD chart, reaction loop, caps.

Verifies every GLM-5.2 review blocker is enforced IN CODE (docs/research/
director-cascade-actionable-plan-2026-07-10.md §10, reviewed/director-cascade-plan-review-2026-07-10.md):

  #1 multi-verb/negation forces tier 4 EVEN when the embedding tier is confident
  #2 an LLM-classified reaction only PROPOSES; mutations need a human binary (confirm)
  #3 trainable iff HUMAN-ACCEPTED — gate-pass alone trains nothing
  #4 (template-injection executor — owned by vague_build/build_skills, exercised via run_chunk)
  #5 project-level rework cap; seeds kept until SEED_LIVE_FLOOR then decay-blended;
     fixed exploration floor that fires even below 20 runs
  #6 WRONG_TRACK only from explicit human feedback or schema mismatch — never token overlap

PLUS the terra (gpt-5.6, xhigh) HIGH/MED fixes as ADVERSARIAL tests (reviewed/
hybrid-state-machine-codex-gpt56-terra-review-2026-07-11.md) — every forgery terra named
must now be BLOCKED:

  terra #1 a caller cannot forge a SMOKE pass; verdicts are server-owned receipts
           (task-bound, artifact-digest-bound, fail-closed); a default-gate engine on
           app_build fails CLOSED
  terra #2 acceptance/relabel/confirm require a server-issued human-approval receipt;
           bool coercion is gone; forged log lines train nothing
  terra #3 rework attempts are reserved transactionally BEFORE execution; concurrency
           cannot exceed the cap; a fresh project_id cannot launder budget
  terra #4 only fresh_build skills can be the primary — at every Director tier and in
           the executor
  terra #5 tiers 2/3 enforce the caller's confidence margin
  terra #6 NaN / dim-mismatched / miscounted embeddings fall through to tier 4
  terra #8 the app_build chart topology + bound gate are frozen against register_track

All offline: embedders, LLMs, and gates are injected fakes. No network, no subprocess.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import build_skills as bs  # noqa: E402
from cortex_core import director as D  # noqa: E402
from cortex_core import hybrid_build as hb  # noqa: E402
from cortex_core import reaction as rx  # noqa: E402
from cortex_core import receipts as rcp  # noqa: E402
from cortex_core import state_engine as se  # noqa: E402
from cortex_core import vague_build as vb  # noqa: E402
from cortex_core.app_contract import CheckResult, GateVerdict  # noqa: E402

VALID_SLOT = ('{"entity":"client","fields":['
              '{"name":"name","type":"text","required":true},'
              '{"name":"paid","type":"bool","required":true}]}')


def _pass_gate(app_dir, checks):
    results = tuple(CheckResult(kind=c["kind"], passed=True, hidden=False, detail="")
                    for c in checks)
    return GateVerdict(passed=True, results=results, failure_class=None,
                       hidden_coverage=False, env_retries=0, seed=0)


def _fail_gate(app_dir, checks):
    results = tuple(CheckResult(kind=c["kind"], passed=False, hidden=False, detail="d",
                                failure_class="X_FAIL") for c in checks)
    return GateVerdict(passed=False, results=results, failure_class="X_FAIL",
                       hidden_coverage=False, env_retries=0, seed=0)


def mk_embed(mapping, default=(9.0, 9.0)):
    """Deterministic fake embedder: known utterances map to fixed 2-d points; everything else
    (e.g. the Fable-authored seeds) lands far away by default."""
    def embed(texts):
        return [list(mapping.get(t, default)) for t in texts]
    return embed


@pytest.fixture
def skills():
    return bs.load_skills(REPO)


@pytest.fixture
def ws(tmp_path):
    """A tmp workspace that resolve_workspace() accepts (docs/ marker) with the repo's
    skill registry copied in, so nothing in the real repo is ever mutated by a test."""
    (tmp_path / "docs").mkdir()
    shutil.copytree(REPO / "skills", tmp_path / "skills")
    return tmp_path


@pytest.fixture(autouse=True)
def _open_test_gate_seam(monkeypatch):
    """terra RE-REVIEW-2 #1: receipts' injected-gate seam is CLOSED by default (production
    posture: only the real app_gates.run_done_checks may mint/validate verdict receipts).
    This offline suite injects fake gates everywhere, so it opens the TEST-ONLY seam. The
    seam-closed adversarial tests below re-close it explicitly via monkeypatch."""
    monkeypatch.setattr(rcp, "_ALLOW_INJECTED_GATE", True)


def _accept(ws, route_id):
    """The human-console act, as tests: mint the approval receipt, then record. This is the
    ONLY way an acceptance can reach the training set (terra #2)."""
    receipt = rcp.mint_approval(route_id, True, workspace=ws)
    D.record_acceptance(route_id, True, receipt=receipt, workspace=ws)
    return receipt


def _acceptance_records(ws, route_id):
    """All acceptance records for a route in the routing log -- the double-apply detector
    (terra RE-REVIEW-2 #2: 'called twice, two acceptance records')."""
    log = ws / "ops-local" / "routing-log.jsonl"
    if not log.is_file():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if rec.get("kind") == "acceptance" and rec.get("route_id") == route_id:
            out.append(rec)
    return out


def _seed_routes(ws, n, skill_id="scaffold-crud-sqlite", utterance="seed filler utterance",
                 accept=False):
    """Append n route records (bumping the deterministic exploration counter) and optionally
    accept them into the training set (via a minted human-approval receipt)."""
    import uuid
    rids = []
    for i in range(n):
        r = D.Route(track="app_build", skill_id=skill_id, tier_used=1, confidence=0.9,
                    route_id=f"r_fill_{uuid.uuid4().hex[:10]}")
        D.log_route(r, utterance, ws)
        if accept:
            _accept(ws, r.route_id)
        rids.append(r.route_id)
    return rids


def _second_fresh_skill(ws, skill_id="scaffold-crud-two"):
    """Clone the scaffold skill under a new id so multi-label tier-2/3 discrimination can be
    exercised with two FRESH skills (follow-ons can no longer be primaries, terra #4). The
    clone reuses the scaffold RENDERER so the terra-#4 capability probe sees it build on an
    empty dir (a fresh_build skill with no renderer would fail the probe, by design)."""
    src = ws / "skills" / "scaffold-crud-sqlite"
    dst = ws / "skills" / skill_id
    shutil.copytree(src, dst)
    sj = dst / "skill.json"
    data = json.loads(sj.read_text(encoding="utf-8"))
    data["skill_id"] = skill_id
    sj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    bs.RENDERERS.setdefault(skill_id, bs.RENDERERS["scaffold-crud-sqlite"])
    return bs.load_skills(ws)


# --------------------------------------------------------------------------------------------
# fix #3 — trainability = human acceptance, NEVER gate-pass
# --------------------------------------------------------------------------------------------

def test_route_without_acceptance_is_not_trainable(ws, skills):
    D.direct("track my clients and who paid", skills, llm=lambda p: "x", workspace=ws)
    assert D.load_trainable(ws) == []          # logged + (gate may even have passed) != trainable


def test_acceptance_makes_route_trainable_and_relabel_removes_it(ws):
    (rid,) = _seed_routes(ws, 1, utterance="track my books")
    assert D.load_trainable(ws) == []
    _accept(ws, rid)
    got = D.load_trainable(ws)
    assert len(got) == 1 and got[0]["route_id"] == rid
    relabel_receipt = rcp.mint_approval(rid, True, workspace=ws)   # bound to the route
    D.record_relabel(rid, source="human_feedback", receipt=relabel_receipt, workspace=ws)
    assert D.load_trainable(ws) == []          # a relabeled route can never train (fix #6 arm)


# --------------------------------------------------------------------------------------------
# tier 2 — embedding router over the route_vec index
# --------------------------------------------------------------------------------------------

def test_tier2_routes_near_neighbor_of_accepted_route(ws, skills):
    train = "somewhere to put all my books"
    query = "i need somewhere to put my books"
    embed = mk_embed({train: (1.0, 0.0), query: (1.05, 0.0)})
    _seed_routes(ws, 3, utterance=train, accept=True)          # counter=3 -> not an exploration run
    assert D.rebuild_route_index(ws, embed=embed) == len(D.SEED_ROUTES) + 3
    r = D.direct(query, skills, llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=embed)
    assert r.tier_used == 2
    assert r.skill_id == "scaffold-crud-sqlite"
    assert r.confidence > 0.8
    assert r.features["seed_decided"] is False


def test_tier2_margin_ambiguity_falls_through_to_tier4(ws):
    skills2 = _second_fresh_skill(ws)
    a, b, q = "put my books here", "put my numbers here", "an ambiguous ask entirely"
    # two labels almost equidistant from the query -> no margin -> escalate
    embed = mk_embed({a: (1.0, 0.0), b: (1.05, 0.0), q: (1.02, 0.0)})
    ra = _seed_routes(ws, 2, utterance=a, accept=True)
    rb = _seed_routes(ws, 1, skill_id="scaffold-crud-two", utterance=b, accept=True)
    assert ra and rb
    D.rebuild_route_index(ws, embed=embed)
    calls = []
    r = D.direct(q, skills2, llm=lambda p: calls.append(p) or "scaffold-crud-two",
                 workspace=ws, embed=embed)
    assert r.tier_used == 4 and len(calls) == 1


def test_tier2_degrades_gracefully_with_no_index_or_data(ws, skills):
    # empty workspace: no index, no log -> straight to tier 4, no crash (small-log degradation)
    r = D.direct("an utterly novel request here", skills,
                 llm=lambda p: "scaffold-crud-sqlite", workspace=ws,
                 embed=mk_embed({}))
    assert r.tier_used == 4 and r.skill_id == "scaffold-crud-sqlite"


# --------------------------------------------------------------------------------------------
# fix #1 — multi-verb / negation forces tier 4 REGARDLESS of embedding confidence
# --------------------------------------------------------------------------------------------

def test_multi_verb_forces_tier4_even_with_confident_embedding(ws, skills):
    trap = "research how to build a tracker for my books"
    train = "somewhere to put all my books"
    embed = mk_embed({train: (1.0, 0.0), trap: (1.0, 0.0)})    # embedding says: IDENTICAL
    _seed_routes(ws, 3, utterance=train, accept=True)
    D.rebuild_route_index(ws, embed=embed)
    calls = []
    r = D.direct(trap, skills, llm=lambda p: calls.append(p) or "scaffold-crud-sqlite",
                 workspace=ws, embed=embed)
    assert r.tier_used == 4 and len(calls) == 1                # guard beat the perfect embedding
    assert r.features["forced_tier4"] == "multi_verb_or_negation"
    assert "neighbors" not in r.features                       # tiers 2/3 were never consulted


def test_negation_forces_tier4_even_with_confident_embedding(ws, skills):
    trap = "keep my books but don't build a dashboard"
    embed = mk_embed({trap: (1.0, 0.0), "somewhere to put all my books": (1.0, 0.0)})
    _seed_routes(ws, 3, utterance="somewhere to put all my books", accept=True)
    D.rebuild_route_index(ws, embed=embed)
    r = D.direct(trap, skills, llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=embed)
    assert r.tier_used == 4


# --------------------------------------------------------------------------------------------
# tier 3 — nearest-centroid from the human-accepted log (no index file needed)
# --------------------------------------------------------------------------------------------

def test_tier3_centroid_classifies_when_no_index_exists(ws):
    skills2 = _second_fresh_skill(ws)
    crud = ["somewhere for my recipes one", "somewhere for my recipes two",
            "somewhere for my recipes three"]
    other = ["overview of my numbers one", "overview of my numbers two",
             "overview of my numbers three"]
    query = "somewhere for my recipes please"
    mapping = {u: (1.0, 0.0) for u in crud}
    mapping.update({u: (0.0, 1.0) for u in other})
    mapping[query] = (0.95, 0.0)
    embed = mk_embed(mapping)
    for u in crud:
        _seed_routes(ws, 1, utterance=u, accept=True)
    for u in other:
        _seed_routes(ws, 1, skill_id="scaffold-crud-two", utterance=u, accept=True)
    # 6 route records logged -> counter=6 -> not an exploration run; no route-index.db on disk
    assert not (ws / "ops-local" / "route-index.db").is_file()
    r = D.direct(query, skills2, llm=lambda p: "scaffold-crud-two", workspace=ws, embed=embed)
    assert r.tier_used == 3
    assert r.skill_id == "scaffold-crud-sqlite"
    assert "centroid_dists" in r.features


def test_tier3_falls_through_below_min_records(ws, skills):
    u = "somewhere for my recipes one"
    embed = mk_embed({u: (1.0, 0.0), "somewhere for my recipes please": (0.95, 0.0)})
    _seed_routes(ws, 3, utterance=u, accept=True)              # 3 < T3_MIN_RECORDS (6)
    r = D.direct("somewhere for my recipes please", skills,
                 llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=embed)
    assert r.tier_used == 4


# --------------------------------------------------------------------------------------------
# fix #5 — exploration floor: fixed deterministic count, fires even at run 0
# --------------------------------------------------------------------------------------------

def test_exploration_floor_reroutes_every_nth_decision_via_tier4(ws):
    skills2 = _second_fresh_skill(ws)                          # 2 fresh labels -> real disagreement
    train = "somewhere to put all my books"
    query = "i need somewhere to put my books"
    embed = mk_embed({train: (1.0, 0.0), query: (1.05, 0.0)})
    rids = _seed_routes(ws, 10, utterance=train)               # counter = 10 -> 10 % 10 == 0
    for rid in rids[:3]:
        _accept(ws, rid)
    D.rebuild_route_index(ws, embed=embed)
    # decision #10: tier 2 has a confident opinion, but the floor demotes it to a logged shadow
    r = D.direct(query, skills2, llm=lambda p: "scaffold-crud-two", workspace=ws, embed=embed)
    assert r.tier_used == 4
    assert r.features["exploration"] is True
    assert r.features["shadow"] == {"tier": 2, "skill_id": "scaffold-crud-sqlite",
                                    "confidence": pytest.approx(0.95)}
    assert r.features["router_disagreement"] is True           # llm said two, shadow said sqlite
    # decision #11: same query now routes at tier 2 (no exploration)
    r2 = D.direct(query, skills2, llm=lambda p: "scaffold-crud-two", workspace=ws, embed=embed)
    assert r2.tier_used == 2 and r2.skill_id == "scaffold-crud-sqlite"


def test_exploration_fires_at_run_zero(ws, skills):
    # n=0 -> 0 % EXPLORATION_EVERY == 0: exploration exists below 20 runs by construction
    assert D._is_exploration_run(ws) is True
    _seed_routes(ws, 1)
    assert D._is_exploration_run(ws) is False


# --------------------------------------------------------------------------------------------
# fix #5 — seeds kept to SEED_LIVE_FLOOR, then decay-blended (never deleted)
# --------------------------------------------------------------------------------------------

def test_seed_constants_match_review_requirements():
    assert D.SEED_LIVE_FLOOR == 50                             # not "delete at 3"
    assert D._seed_penalty(0) == 0.0
    assert D._seed_penalty(49) == 0.0
    assert D._seed_penalty(50) == D.SEED_DECAY_PENALTY > 0


def test_seeds_decay_blend_after_floor_but_are_never_deleted(ws, monkeypatch):
    skills2 = _second_fresh_skill(ws)
    monkeypatch.setattr(D, "SEED_LIVE_FLOOR", 3)               # exercise the mechanism cheaply
    seed_utt = D.SEED_ROUTES[6][0]                             # "a database of my recipes"
    live_utt = "keep things for me somehow"
    query = "hold my things for me"
    embed = mk_embed({seed_utt: (0.4, 0.0), live_utt: (0.44, 0.0), query: (0.0, 0.0)})
    # below the floor (2 live): the closer SEED would decide, but the seed-margin guard sees the
    # nearby different-label live record -> ambiguous -> falls through (cold-start caution)
    _seed_routes(ws, 2, skill_id="scaffold-crud-two", utterance=live_utt, accept=True)
    D.rebuild_route_index(ws, embed=embed)
    assert D._tier2_embed(query, skills2, ws, embed) is None
    # at the floor (3 live): decay penalty pushes the seed back; live evidence decides
    _seed_routes(ws, 1, skill_id="scaffold-crud-two", utterance=live_utt, accept=True)
    D.rebuild_route_index(ws, embed=embed)
    got = D._tier2_embed(query, skills2, ws, embed)
    assert got is not None and got[0] == "scaffold-crud-two"
    # seeds are still IN the index (decayed, not deleted)
    nbrs = D.nearest_routes(ws, query, k=13, embed=embed)
    assert any(n["origin"] == D.SEED_ORIGIN for n in nbrs)


# --------------------------------------------------------------------------------------------
# the APP_BUILD chart — the deterministic engine owns every transition
# --------------------------------------------------------------------------------------------

def _engine(tmp_path, chart=None, gate="universal"):
    g = se.make_universal_gate() if gate == "universal" else gate
    return se.StateEngine(str(tmp_path / "t.db"), chart=chart, gate=g,
                          workspace=str(tmp_path))


_CHECKS = [{"kind": "app_starts"}]


def _mk_app(tmp_path, name, body="print('CORTEX_APP_READY')\n"):
    app_dir = tmp_path / name
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.py").write_text(body, encoding="utf-8")
    return app_dir


def _pass_verdict(app_dir, checks):
    results = tuple(CheckResult(kind=(c.get("kind") if isinstance(c, dict) else "app_starts"),
                                passed=True, hidden=False, detail="")
                    for c in (checks or [{"kind": "app_starts"}]))
    return GateVerdict(passed=True, results=results, failure_class=None,
                       hidden_coverage=False, env_retries=0, seed=0)


def _fail_verdict(fc):
    def g(app_dir, checks):
        return GateVerdict(passed=False, results=(CheckResult("app_starts", False, False, "d", fc),),
                           failure_class=fc, hidden_coverage=False, env_retries=0, seed=0)
    return g


def _mint(tmp_path, tid, app_dir, checks=None, run=_pass_verdict):
    """Mint a verdict receipt the ONLY way it can be minted now (terra RE-REVIEW #1): by
    RUNNING a gate. There is no `passed` param -- the bit comes from the GateVerdict `run`
    returns over the real artifact. Offline tests inject the gate (the legitimate seam)."""
    vid, _ = rcp.run_and_record_smoke_verdict(
        task_id=tid, app_dir=app_dir, checks=_CHECKS if checks is None else checks,
        run_checks=run, workspace=tmp_path)
    return vid


def _scaffold(eng, tid, app_dir, seq, checks=None):
    """Submit the SCAFFOLD artifact WITH its app_dir + checks, so the engine persists the
    server-computed artifact/checks digests the SMOKE receipt is bound to (terra #1)."""
    return eng.step(tid, "cortex_submit_artifact",
                    {"status": "built", "app_dir": str(app_dir),
                     "checks": _CHECKS if checks is None else checks}, seq=seq)


def test_app_build_track_is_registered_and_walks_the_declared_path(tmp_path):
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x", "project_id": "p1"}, track="app_build")
    env = eng.get(tid)
    assert env["state"] == "SCAFFOLD"
    assert se.phase_legal_tools("app_build", "SCAFFOLD") == ["cortex_submit_artifact"]
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, env["seq"])
    assert env["ok"] and env["state"] == "SMOKE"
    vid = _mint(tmp_path, tid, app_dir)
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
    assert env["ok"] and env["state"] == "SHOW" and env["gate"]["smoke"] == "ok"
    env = eng.step(tid, "cortex_submit_reaction", {"reaction": None}, seq=env["seq"])
    assert env["state"] == "CLOSEOUT"
    env = eng.step(tid, "cortex_write_closeout", {"task": "x", "result": "ok"}, seq=env["seq"])
    assert env["state"] == "DONE"
    assert eng.get(tid)["closeout_written"] is True
    eng.close()


def test_model_cannot_invent_a_state_or_tool(tmp_path):
    """Routing-as-data: an undeclared tool is refused with guidance; state and seq unchanged."""
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    env = eng.step(tid, "cortex_jump_to_done", {"state": "DONE"}, seq=0)
    assert env["ok"] is False and env["code"] == "ILLEGAL_IN_STATE"
    after = eng.get(tid)
    assert after["state"] == "SCAFFOLD" and after["seq"] == 0
    eng.close()


def test_smoke_gate_is_fail_closed_and_reworks_to_scaffold(tmp_path):
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, 0)
    # missing verdict receipt -> fail (fail-closed), which is a REWORK back to SCAFFOLD
    env = eng.step(tid, "cortex_submit_smoke", {"nope": True}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["rework_count"] == 1
    # a genuinely FAILING deterministic verdict (server-minted receipt) also reworks
    env = _scaffold(eng, tid, app_dir, env["seq"])
    vid = _mint(tmp_path, tid, app_dir, run=_fail_verdict("SMOKE_HTTP_FAIL"))
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["rework_count"] == 2
    assert "SMOKE_HTTP_FAIL" in env["instruction"]
    eng.close()


def test_app_build_abandons_via_closeout_past_caps(tmp_path):
    tiny = json.loads(json.dumps(se.APP_BUILD_TRACK))
    tiny["rework_cap"] = 0
    tiny["esc_cap"] = 0
    eng = _engine(tmp_path, chart=tiny)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    env = eng.step(tid, "cortex_submit_artifact", {"status": "built"}, seq=0)
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": None}, seq=env["seq"])
    assert env["state"] == "ABANDONED" and env.get("abandoned") is True
    assert eng.get(tid)["closeout_written"] is True            # server-side closeout, always
    eng.close()


def test_register_track_validates_fail_at_load():
    with pytest.raises(ValueError):
        se.register_track({"track": "bogus", "initial": "A",
                           "states": {"A": {"advance_tool": "t", "next": "MISSING",
                                            "instruction": "x"}}})


# --------------------------------------------------------------------------------------------
# terra #1 — ADVERSARIAL: a caller cannot forge a SMOKE pass; verdicts are server-owned
# --------------------------------------------------------------------------------------------

def test_generic_caller_cannot_forge_a_smoke_pass(tmp_path):
    """terra HIGH #1, the headline forgery: cortex_run_start(track=app_build) then
    cortex_submit_smoke({"verdict": {"passed": true}}). The legacy payload shape is now
    refused BY NAME and the step is a rework, never an advance to SHOW."""
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "forge me"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, 0)
    env = eng.step(tid, "cortex_submit_smoke",
                   {"verdict": {"passed": True, "failure_class": None}}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD"                          # reworked, NOT advanced
    assert env["gate"]["pass"] is False
    assert env["gate"]["code"] == "VERDICT_NOT_SERVER_OWNED"
    # a made-up verdict_id fails closed too
    env = _scaffold(eng, tid, app_dir, env["seq"])
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": "sv_forged"}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] == "UNKNOWN_VERDICT"
    eng.close()


def test_a_passing_receipt_cannot_be_minted_without_running_the_gate(tmp_path):
    """terra RE-REVIEW #1 core: there is NO public API that stores passed=True without a gate.
    run_and_record has no `passed` param; the bit is taken from the GateVerdict the gate
    returns. A 'gate' that FAILS mints a FAILING receipt -> SMOKE reworks, never advances."""
    assert not hasattr(rcp, "record_smoke_verdict")           # the caller-`passed` API is gone
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, 0)
    # the strongest a forger can do without a real passing gate is run a FAILING gate:
    vid = _mint(tmp_path, tid, app_dir, run=_fail_verdict("SMOKE_FAIL"))
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["pass"] is False
    eng.close()


def test_default_gate_engine_on_app_build_fails_closed(tmp_path):
    """terra HIGH #1 fail-open default: StateEngine() with NO gate used to run default_gate
    at SMOKE (any well-formed dict passed). The chart-bound gate now runs regardless of
    engine construction, so the forged pass is refused."""
    eng = se.StateEngine(str(tmp_path / "d.db"), workspace=str(tmp_path))  # default_gate!
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, 0)
    assert env["state"] == "SMOKE"
    env = eng.step(tid, "cortex_submit_smoke",
                   {"verdict": {"passed": True, "failure_class": None}}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["pass"] is False
    # and a legitimate server-minted receipt still passes through the default-gate engine
    env = _scaffold(eng, tid, app_dir, env["seq"])
    vid = _mint(tmp_path, tid, app_dir)
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
    assert env["state"] == "SHOW"
    eng.close()


def test_smoke_receipt_is_task_bound_and_artifact_bound(tmp_path):
    """A receipt minted for ANOTHER task is refused; a receipt whose artifact was modified
    after grading is refused (digest re-validation at SMOKE)."""
    eng = _engine(tmp_path)
    victim = eng.create_task({"seeking": "victim"}, track="app_build")
    thief = eng.create_task({"seeking": "thief"}, track="app_build")
    v_app = _mk_app(tmp_path, "victim_app")
    t_app = _mk_app(tmp_path, "thief_app", body="print('other')\n")
    vid_victim = _mint(tmp_path, victim, v_app)
    # thief scaffolds its own artifact, then tries to advance on the VICTIM's receipt
    env = _scaffold(eng, thief, t_app, 0)
    env = eng.step(thief, "cortex_submit_smoke", {"verdict_id": vid_victim}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] == "VERDICT_TASK_MISMATCH"
    # victim's own receipt over a TAMPERED artifact is refused
    env = _scaffold(eng, victim, v_app, 0)
    (v_app / "app.py").write_text("print('malware')\n", encoding="utf-8")
    env = eng.step(victim, "cortex_submit_smoke", {"verdict_id": vid_victim}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] in ("ARTIFACT_TAMPERED",
                                                                  "ARTIFACT_TASK_MISMATCH")
    eng.close()


def test_smoke_receipt_replayed_for_a_different_artifact_is_rejected(tmp_path):
    """terra RE-REVIEW #1: a GENUINE passing receipt minted over artifact A cannot pass a
    SMOKE whose task submitted artifact B. The receipt is bound to the task's SCAFFOLD digest."""
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_a = _mk_app(tmp_path, "artifact_a", body="print('AAA')\n")
    app_b = _mk_app(tmp_path, "artifact_b", body="print('BBB')\n")
    # the task's SCAFFOLD submits artifact B (engine stores digest(B))
    env = _scaffold(eng, tid, app_b, 0)
    # a real PASSING receipt is minted over artifact A (different bytes)
    vid_a = _mint(tmp_path, tid, app_a)
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid_a}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] == "ARTIFACT_TASK_MISMATCH"
    eng.close()


def test_smoke_receipt_for_different_checks_is_rejected(tmp_path):
    """terra RE-REVIEW #1: the receipt's checks digest must match the task's required checks.
    A receipt minted over a WEAKER/other checks set cannot pass this task's SMOKE."""
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    # task requires the real checks
    env = _scaffold(eng, tid, app_dir, 0, checks=[{"kind": "data_persists"}])
    # receipt minted over a DIFFERENT (empty) checks set
    vid = _mint(tmp_path, tid, app_dir, checks=[])
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] == "CHECKS_MISMATCH"
    eng.close()


def test_smoke_without_bound_scaffold_artifact_fails_closed(tmp_path):
    """terra RE-REVIEW-2 #1, the exact repro: SCAFFOLD advanced WITHOUT an app_dir, so the
    task stored scaffold_artifact_digest=None -- and validate_smoke_receipt used to SKIP the
    digest comparison on None, letting a real passing receipt over an UNSUBMITTED artifact
    advance SMOKE -> SHOW. A None/absent expected digest now FAILS the comparison
    (NO_ARTIFACT): missing artifact = fail CLOSED, never open."""
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    env = eng.step(tid, "cortex_submit_artifact", {"status": "built"}, seq=0)  # NO app_dir
    assert env["ok"] and env["state"] == "SMOKE"
    # a REAL passing receipt for THIS task, minted over an artifact the task never submitted
    app_dir = _mk_app(tmp_path, "unsubmitted_artifact")
    vid = _mint(tmp_path, tid, app_dir)
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD"                          # reworked -- NEVER SHOW
    assert env["gate"]["pass"] is False
    assert env["gate"]["code"] == "NO_ARTIFACT"
    # an app_dir that does not exist binds nothing either (digest_dir -> None): same refusal
    env = eng.step(tid, "cortex_submit_artifact",
                   {"status": "built", "app_dir": str(tmp_path / "does_not_exist"),
                    "checks": _CHECKS}, seq=env["seq"])
    assert env["state"] == "SMOKE"
    vid2 = _mint(tmp_path, tid, app_dir)
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid2}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] == "NO_ARTIFACT"
    eng.close()


def test_fake_gate_cannot_mint_or_validate_receipts_in_production(ws, tmp_path, monkeypatch):
    """terra RE-REVIEW-2 #1, the callback mint seam: run_and_record_smoke_verdict used to
    accept ANY caller-supplied run_checks and trust its `passed`. With the TEST-ONLY seam
    CLOSED (the production posture), (a) minting with a non-real callback raises, (b) a
    receipt minted through the test seam does NOT validate, and (c) run_chunk(gate=fake)
    cannot forge a pass end to end."""
    eng = _engine(tmp_path)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, 0)
    vid_seam = _mint(tmp_path, tid, app_dir)                   # minted while the seam is OPEN
    monkeypatch.setattr(rcp, "_ALLOW_INJECTED_GATE", False)    # -> production posture
    # (a) a fake-gate callback cannot mint at all
    with pytest.raises(PermissionError):
        rcp.run_and_record_smoke_verdict(task_id=tid, app_dir=app_dir, checks=_CHECKS,
                                         run_checks=_pass_verdict, workspace=tmp_path)
    # (b) the seam-minted receipt is refused at SMOKE: the gate identity is not the real gate
    env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid_seam}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["code"] == "GATE_NOT_AUTHENTIC"
    eng.close()
    # (c) the declared model-reachable surface: run_chunk with an injected fake gate refuses
    # to mint (PermissionError propagates) -- it can never reach a forged DONE
    with pytest.raises(PermissionError):
        hb.run_chunk("track my clients and who paid", project_id="p_seam", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_pass_gate)


def test_app_build_chart_topology_is_frozen():
    """terra MED #8: register_track cannot swap in an app_build chart that drops SMOKE or
    unbinds the server-owned gate."""
    hollow = {"track": "app_build", "initial": "SCAFFOLD",
              "states": {"SCAFFOLD": {"advance_tool": "cortex_submit_artifact",
                                      "next": "DONE", "instruction": "x"}}}
    with pytest.raises(ValueError, match="immutable|topology"):
        se.register_track(hollow)
    # unbinding just the gate marker is refused too
    unbound = json.loads(json.dumps(se.APP_BUILD_TRACK))
    del unbound["states"]["SMOKE"]["bound_gate"]
    with pytest.raises(ValueError, match="bound_gate"):
        se.register_track(unbound)
    # engine-instance registration enforces the same freeze
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        eng = se.StateEngine(str(Path(td) / "x.db"))
        with pytest.raises(ValueError):
            eng.register_track(hollow)
        eng.close()
    # a conforming variant (tunable caps, topology intact) still loads
    tiny = json.loads(json.dumps(se.APP_BUILD_TRACK))
    tiny["rework_cap"] = 0
    assert se._validate_chart(tiny)["rework_cap"] == 0


def test_registered_chart_is_immutable_and_cannot_disarm_the_engine(tmp_path):
    """terra RE-REVIEW #8: the returned/stored chart is DEEP-FROZEN, so
    `del registered['states']['SMOKE']['bound_gate']` raises TypeError and cannot disarm a
    running engine. Even if a caller could reach the marker, the engine still fail-closes."""
    import copy
    eng = se.StateEngine(str(tmp_path / "x.db"), workspace=str(tmp_path))
    registered = eng.register_track(copy.deepcopy(se.APP_BUILD_TRACK))
    # the returned chart is immutable -- the disarm mutation raises instead of succeeding
    with pytest.raises(TypeError):
        del registered["states"]["SMOKE"]["bound_gate"]
    with pytest.raises(TypeError):
        registered["states"]["SMOKE"]["bound_gate"] = "off"    # can't overwrite it either
    # and the engine still fail-closes a legacy boolean at SMOKE (the marker is intact)
    tid = eng.create_task({"seeking": "x"}, track="app_build")
    app_dir = _mk_app(tmp_path, "app")
    env = _scaffold(eng, tid, app_dir, 0)
    env = eng.step(tid, "cortex_submit_smoke",
                   {"verdict": {"passed": True}}, seq=env["seq"])
    assert env["state"] == "SCAFFOLD" and env["gate"]["pass"] is False
    eng.close()


# --------------------------------------------------------------------------------------------
# reaction loop — fix #2: LLM proposes, human disposes; fix #6: no token-overlap WRONG_TRACK
# --------------------------------------------------------------------------------------------

def test_reaction_tier1_rules_classify_deterministically(ws):
    cases = {
        "perfect, done": "done",
        "the save button is broken": "bug",
        "undo that please": "undo",
        "no, the late ones should be red instead": "refine",
        "also add a csv export": "new_feature",
        "this is the wrong thing entirely": "wrong_track",
    }
    for text, want in cases.items():
        r = rx.classify_reaction(text, llm=lambda p: "unclear", workspace=ws)
        assert (r.classified_as, r.tier_used) == (want, 1), text


def test_ambiguous_reaction_uses_bounded_llm_and_junk_is_unclear(ws):
    # bug + new_feature phrases -> ambiguous -> tier 4, bounded to the declared classes
    r = rx.classify_reaction("it crashes, but also add a chart", llm=lambda p: "bug", workspace=ws)
    assert r.classified_as == "bug" and r.tier_used == 4
    r2 = rx.classify_reaction("hmm interesting", llm=lambda p: "not-a-class!", workspace=ws)
    assert r2.classified_as == "unclear" and r2.confidence == 0.0


def test_llm_reaction_only_proposes_never_mutates(ws):
    """Fix #2 end to end: a bug classification queues a pass_count decrement; the skill.json is
    untouched until — and unless — a human confirms with an explicit binary + receipt."""
    sj = ws / "skills" / "scaffold-crud-sqlite" / "skill.json"
    before = json.loads(sj.read_text(encoding="utf-8"))["pass_count"]
    reaction = rx.classify_reaction("the form is broken", llm=None, workspace=ws)  # tier 1: bug
    pids = rx.proposals_from_reaction(reaction, route_id="r_1",
                                      skill_ids=["scaffold-crud-sqlite"], workspace=ws)
    assert len(pids) == 1
    assert json.loads(sj.read_text(encoding="utf-8"))["pass_count"] == before  # NOT mutated
    # human says NO -> still not mutated, resolution recorded
    out = rx.confirm(pids[0], False, receipt=rx.mint_confirmation(pids[0], False, workspace=ws),
                     workspace=ws)
    assert out["accepted"] is False and out["applied"] is False
    assert json.loads(sj.read_text(encoding="utf-8"))["pass_count"] == before
    # a fresh proposal + human YES -> applied (floor 0)
    pids2 = rx.proposals_from_reaction(reaction, route_id="r_1",
                                       skill_ids=["scaffold-crud-sqlite"], workspace=ws)
    out2 = rx.confirm(pids2[0], True, receipt=rx.mint_confirmation(pids2[0], True, workspace=ws),
                      workspace=ws)
    assert out2["applied"] is True
    assert json.loads(sj.read_text(encoding="utf-8"))["pass_count"] == max(0, before - 1)


def test_confirm_requires_an_explicit_binary_bool(ws):
    pid = rx.propose("mark_trainable", {"route_id": "r_x"}, workspace=ws)
    with pytest.raises(TypeError):
        rx.confirm(pid, "yes", receipt="whatever", workspace=ws)  # an LLM string can never confirm
    rx.confirm(pid, True, receipt=rx.mint_confirmation(pid, True, workspace=ws), workspace=ws)
    with pytest.raises(ValueError):                             # already resolved
        rx.confirm(pid, True, receipt=rx.mint_confirmation(pid, True, workspace=ws), workspace=ws)


# --------------------------------------------------------------------------------------------
# terra #2 — ADVERSARIAL: acceptance / relabel / confirm are un-forgeable by a model
# --------------------------------------------------------------------------------------------

def test_record_acceptance_rejects_string_coercion(ws):
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    with pytest.raises(TypeError):
        D.record_acceptance(rid, "yes", receipt="r", workspace=ws)   # bool("yes") forgery: DEAD
    assert D.load_trainable(ws) == []


def test_record_acceptance_requires_a_real_receipt(ws):
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    with pytest.raises(TypeError):
        D.record_acceptance(rid, True, workspace=ws)             # type: ignore[call-arg]
    with pytest.raises(PermissionError):
        D.record_acceptance(rid, True, receipt="ap_forged", workspace=ws)
    # a receipt whose DECISION doesn't match is refused too
    wrong_decision = rcp.mint_approval(rid, False, workspace=ws)
    with pytest.raises(PermissionError):
        D.record_acceptance(rid, True, receipt=wrong_decision, workspace=ws)
    assert D.load_trainable(ws) == []


def test_stolen_receipt_for_another_route_cannot_accept_this_route(ws):
    """terra RE-REVIEW #2 concrete bypass: a REAL (even consumed) approval receipt for route X
    cannot be replayed to accept route Y. record_acceptance binds the receipt to route_id."""
    (r_x,) = _seed_routes(ws, 1, utterance="track X")
    (r_y,) = _seed_routes(ws, 1, utterance="track Y")
    real_for_x = rcp.mint_approval(r_x, True, workspace=ws)      # a genuine receipt, but for X
    with pytest.raises(PermissionError):
        D.record_acceptance(r_y, True, receipt=real_for_x, workspace=ws)   # replay onto Y: DEAD
    # even after X's receipt is consumed, it still can't accept Y
    rcp.consume_approval(real_for_x, ws)
    with pytest.raises(PermissionError):
        D.record_acceptance(r_y, True, receipt=real_for_x, workspace=ws)
    assert D.load_trainable(ws) == []


def test_confirm_without_valid_receipt_is_refused_even_with_true_bool(ws):
    """terra HIGH #2 headline: confirm(pid, True, by='model') used to be recorded as human.
    The `by` label is gone; a bool without a live receipt BOUND TO THE PROPOSAL is refused."""
    pid = rx.propose("mark_trainable", {"route_id": "r_y"}, workspace=ws)
    with pytest.raises(TypeError):
        rx.confirm(pid, True, workspace=ws)                      # type: ignore[call-arg]
    with pytest.raises(PermissionError):
        rx.confirm(pid, True, receipt="ap_model_made_this_up", workspace=ws)
    # a receipt for a DIFFERENT proposal/route cannot be replayed onto this one
    other = rcp.mint_approval("r_other", True, workspace=ws)
    with pytest.raises(PermissionError):
        rx.confirm(pid, True, receipt=other, workspace=ws)
    assert pid in {p["proposal_id"] for p in rx.pending(ws)}     # still pending, nothing applied
    assert D.load_trainable(ws) == []


def test_consumed_receipt_cannot_be_reused(ws):
    """terra RE-REVIEW #2: single-use. A receipt spent on one confirm cannot be replayed."""
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    p1 = rx.propose("mark_trainable", {"route_id": rid}, workspace=ws)
    receipt = rx.mint_confirmation(p1, True, workspace=ws)
    rx.confirm(p1, True, receipt=receipt, workspace=ws)         # consumes the receipt
    # a second proposal for the same route, trying to REUSE the spent receipt -> refused
    p2 = rx.propose("mark_trainable", {"route_id": rid}, workspace=ws)
    with pytest.raises(PermissionError):
        rx.confirm(p2, True, receipt=receipt, workspace=ws)
    assert p2 in {p["proposal_id"] for p in rx.pending(ws)}     # p2 still pending


def test_concurrent_confirm_applies_exactly_once(ws):
    """terra RE-REVIEW #2 race: two confirm() calls on one proposal with one receipt -- only
    the thread whose atomic consume wins applies; the loser raises and mutates nothing. A
    skill_pass_decrement therefore cannot be applied twice."""
    import threading
    sj = ws / "skills" / "scaffold-crud-sqlite" / "skill.json"
    before = json.loads(sj.read_text(encoding="utf-8"))["pass_count"]
    pid = rx.propose("skill_pass_decrement",
                     {"skill_id": "scaffold-crud-sqlite", "route_id": "r_c"}, workspace=ws)
    receipt = rx.mint_confirmation(pid, True, workspace=ws)
    applied, refused, barrier = [], [], threading.Barrier(2)

    def worker():
        barrier.wait()
        try:
            out = rx.confirm(pid, True, receipt=receipt, workspace=ws)
            applied.append(out.get("applied"))
        except (PermissionError, ValueError):
            refused.append(True)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert applied.count(True) == 1 and len(refused) == 1      # EXACTLY one applied
    assert json.loads(sj.read_text(encoding="utf-8"))["pass_count"] == max(0, before - 1)


def test_direct_apply_rejects_a_fabricated_proposal(ws):
    """terra RE-REVIEW #2: _apply loads the proposal FROM THE STORE by id and cannot be
    handed a fabricated dict. Even a real receipt for one proposal can't drive a made-up one."""
    real_pid = rx.propose("mark_trainable", {"route_id": "r_real"}, workspace=ws)
    receipt = rx.mint_confirmation(real_pid, True, workspace=ws)
    # _apply now takes a proposal_id, not a dict -- a fabricated id is not in the store
    with pytest.raises(KeyError):
        rx._apply("p_fabricated_id", ws, receipt=receipt)
    # a receipt for r_real cannot be used to _apply a different (unknown) proposal
    with pytest.raises(KeyError):
        rx._apply("p_target_that_does_not_exist", ws, receipt=receipt)
    assert D.load_trainable(ws) == []


def test_direct_apply_consumes_the_receipt_and_applies_once(ws):
    """terra RE-REVIEW-2 #2 exact repro: reaction._apply(real_pid, receipt=live_receipt)
    applied WITHOUT consuming -- called twice it wrote TWO acceptance records with the
    receipt still live. _apply now claims the proposal's one-time application slot and the
    write path consumes the receipt atomically: the second call refuses, exactly one
    acceptance record exists, and the receipt is spent."""
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    pid = rx.propose("mark_trainable", {"route_id": rid}, workspace=ws)
    receipt = rx.mint_confirmation(pid, True, workspace=ws)
    out = rx._apply(pid, ws, receipt=receipt)
    assert out["applied"] is True
    # the receipt is now CONSUMED (single-use across the WHOLE write path) ...
    assert rcp.check_approval(receipt, subject_id=rid, decision=True,
                              require_unconsumed=True, workspace=ws) is None
    # ... so terra's second direct _apply refuses and writes nothing
    with pytest.raises((PermissionError, ValueError)):
        rx._apply(pid, ws, receipt=receipt)
    assert len(_acceptance_records(ws, rid)) == 1              # ONE record, not two
    assert len(D.load_trainable(ws)) == 1
    # and even a FRESH receipt cannot re-apply the already-applied proposal
    fresh = rx.mint_confirmation(pid, True, workspace=ws)
    with pytest.raises(ValueError):
        rx._apply(pid, ws, receipt=fresh)
    assert len(_acceptance_records(ws, rid)) == 1


def test_record_acceptance_and_relabel_reject_consumed_receipts(ws):
    """terra RE-REVIEW-2 #2: director.record_acceptance / record_relabel used
    require_unconsumed=False, so a spent receipt could be replayed straight into the ledger
    writers. Both now require an UNCONSUMED receipt and consume it atomically themselves."""
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    spent = rcp.mint_approval(rid, True, workspace=ws)
    assert rcp.consume_approval(spent, ws) is True
    with pytest.raises(PermissionError):
        D.record_acceptance(rid, True, receipt=spent, workspace=ws)     # terra's repro: DEAD
    with pytest.raises(PermissionError):
        D.record_relabel(rid, source="human_feedback", receipt=spent, workspace=ws)
    assert D.load_trainable(ws) == []
    # a live receipt works exactly once -- the writer itself consumes it at the write
    live = rcp.mint_approval(rid, True, workspace=ws)
    D.record_acceptance(rid, True, receipt=live, workspace=ws)
    with pytest.raises(PermissionError):
        D.record_acceptance(rid, True, receipt=live, workspace=ws)      # replay: DEAD
    assert len(_acceptance_records(ws, rid)) == 1


def test_two_distinct_receipts_for_one_proposal_apply_once(ws):
    """terra RE-REVIEW-2 #2 exact repro: two concurrent confirm()s holding two DISTINCT
    valid receipts for the SAME proposal both applied (each consumed only its own receipt)
    -- per-receipt single-use protected the receipt, not the proposal. Application is now
    one-time at the PROPOSAL level (atomic claim_proposal_resolution, PK proposal_id):
    exactly one confirms, the loser refuses, one acceptance record exists."""
    import threading
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    pid = rx.propose("mark_trainable", {"route_id": rid}, workspace=ws)
    r1 = rx.mint_confirmation(pid, True, workspace=ws)
    r2 = rx.mint_confirmation(pid, True, workspace=ws)
    assert r1 != r2                                            # genuinely distinct receipts
    applied, refused, barrier = [], [], threading.Barrier(2)

    def worker(receipt):
        barrier.wait()
        try:
            out = rx.confirm(pid, True, receipt=receipt, workspace=ws)
            applied.append(out.get("applied"))
        except (PermissionError, ValueError):
            refused.append(True)

    threads = [threading.Thread(target=worker, args=(r,)) for r in (r1, r2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert applied.count(True) == 1 and len(refused) == 1      # EXACTLY one applied
    assert len(_acceptance_records(ws, rid)) == 1              # one record, not two
    assert len(D.load_trainable(ws)) == 1
    # a third, sequential attempt with yet another fresh receipt is refused too
    r3 = rx.mint_confirmation(pid, True, workspace=ws)
    with pytest.raises(ValueError):
        rx.confirm(pid, True, receipt=r3, workspace=ws)
    assert len(_acceptance_records(ws, rid)) == 1


def test_relabel_provenance_and_receipt_are_enforced(ws):
    """_apply and record_relabel were directly callable (terra #2). Arbitrary relabel
    provenance ('token_overlap') is refused; human_feedback needs a route-bound receipt."""
    with pytest.raises(ValueError):
        D.record_relabel("r_z", source="token_overlap", workspace=ws)  # the killed heuristic
    with pytest.raises(PermissionError):
        D.record_relabel("r_z", source="human_feedback", workspace=ws)  # no receipt, no write
    # a receipt for a DIFFERENT route can't relabel r_z either
    other = rcp.mint_approval("r_other", True, workspace=ws)
    with pytest.raises(PermissionError):
        D.record_relabel("r_z", source="human_feedback", receipt=other, workspace=ws)


def test_forged_acceptance_log_line_trains_nothing(ws):
    """load_trainable trusts ONLY receipt-backed acceptance records: a hand-appended
    accepted:true line (what a model with file access could write) is inert."""
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    log = ws / "ops-local" / "routing-log.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "acceptance", "route_id": rid, "accepted": True,
                             "by": "human", "ts": 0}) + "\n")           # legacy/forged: no receipt
        fh.write(json.dumps({"kind": "acceptance", "route_id": rid, "accepted": True,
                             "receipt": "ap_invented", "ts": 0}) + "\n")  # forged receipt id
    assert D.load_trainable(ws) == []
    # and the genuine console path still works
    _accept(ws, rid)
    assert [r["route_id"] for r in D.load_trainable(ws)] == [rid]


def test_acceptance_reaches_training_set_only_through_confirm(ws):
    """Fix #3 + #2 composed: done-reaction -> mark_trainable proposal -> only confirm(True)
    with a human-console receipt writes the acceptance record load_trainable reads."""
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    reaction = rx.classify_reaction("looks great, all set", llm=None, workspace=ws)
    assert reaction.classified_as == "done" and reaction.tier_used == 1
    pids = rx.proposals_from_reaction(reaction, route_id=rid, workspace=ws)
    kinds = [p["proposal_kind"] for p in rx.pending(ws)]
    assert "mark_trainable" in kinds and "project_done" in kinds
    assert D.load_trainable(ws) == []                          # queued != trained
    trainable_pid = next(p["proposal_id"] for p in rx.pending(ws)
                         if p["proposal_kind"] == "mark_trainable")
    rx.confirm(trainable_pid, True,
               receipt=rx.mint_confirmation(trainable_pid, True, workspace=ws), workspace=ws)
    assert [r["route_id"] for r in D.load_trainable(ws)] == [rid]
    assert pids


def test_bug_and_refine_reactions_never_propose_trainability(ws):
    (rid,) = _seed_routes(ws, 1, utterance="track my plants")
    for text in ("the page is broken", "no, make the late ones red instead"):
        reaction = rx.classify_reaction(text, llm=None, workspace=ws)
        rx.proposals_from_reaction(reaction, route_id=rid, skill_ids=[], workspace=ws)
    assert all(p["proposal_kind"] != "mark_trainable" for p in rx.pending(ws))


def test_wrong_track_only_from_explicit_feedback_or_schema_mismatch(ws):
    # (a) explicit human feedback, deterministically matched -> True
    explicit = rx.classify_reaction("this is the wrong thing entirely", llm=None, workspace=ws)
    assert rx.infer_wrong_track(explicit) is True
    # (b) deterministic schema mismatch -> True, reaction irrelevant
    assert rx.infer_wrong_track(None, schema_mismatch=True) is True
    # (c) an LLM's wrong_track classification does NOT infer (it may only propose)
    llm_wrong = rx.classify_reaction("hmm not sure about this direction",
                                     llm=lambda p: "wrong_track", workspace=ws)
    assert llm_wrong.tier_used == 4
    assert rx.infer_wrong_track(llm_wrong) is False
    # (d) zero token overlap between reaction and artifact must NOT trigger (the killed heuristic):
    # "make it red" shares no tokens with app.py — and classifies as refine, not wrong_track
    zero_overlap = rx.classify_reaction("make it red", llm=None, workspace=ws)
    assert zero_overlap.classified_as == "refine"
    assert rx.infer_wrong_track(zero_overlap) is False


# --------------------------------------------------------------------------------------------
# terra #4 — ADVERSARIAL: a follow-on can never be the primary
# --------------------------------------------------------------------------------------------

def test_tier4_cannot_return_a_follow_on_as_primary(ws, skills):
    """The LLM answers 'add-dashboard' (a real, declared app_build skill — but a follow-on).
    The Director's tier-4 decision space is fresh_build ONLY, so the answer cannot bind and
    the route falls back to a fresh skill."""
    r = D.direct("give me a dashboard overview thing", skills,
                 llm=lambda p: "add-dashboard", workspace=ws, embed=mk_embed({}))
    assert r.tier_used == 4
    assert r.skill_id == "scaffold-crud-sqlite"                 # never the follow-on
    # and the tier-4 PROMPT never even offers follow-ons
    seen = []
    D.direct("another dashboard ask", skills, llm=lambda p: seen.append(p) or "x",
             workspace=ws, embed=mk_embed({}))
    assert "add-dashboard" not in seen[0]


def test_executor_rejects_a_non_fresh_primary_honestly(ws):
    """drive() refuses a follow-on primary with an honest error instead of a RenderError on
    a blank dir — even if some router bug (or a caller) forces it through."""
    r = vb.drive("track my clients", llm=lambda p: VALID_SLOT, gate=_pass_gate,
                 workspace=ws, primary_skill_id="add-dashboard")
    assert r["status"] == "bad_primary" and r["skill_id"] == "add-dashboard"
    assert "fresh_build" in r["reason"]


def test_skill_role_metadata_is_declared_and_validated(skills):
    assert skills["scaffold-crud-sqlite"].role == "fresh_build"
    assert all(sk.role == "follow_on" for sid, sk in skills.items()
               if sid.startswith("add-"))
    # an undeclared role is refused at load (fail-at-load, never a silent default to primary)
    bad = bs.BuildSkill.from_dict({**skills["scaffold-crud-sqlite"].to_dict(),
                                   "role": "super_primary"})
    ok, errors = bs.validate_skill(bad)
    assert not ok and any("role" in e for e in errors)


def test_role_flipped_to_fresh_build_fails_the_renderer_probe(skills):
    """terra RE-REVIEW #4: primary-eligibility is DERIVED from the renderer, not the manifest
    string. Flipping a follow-on's declared role to 'fresh_build' no longer makes it
    primary-eligible: the capability probe renders it on an empty dir, it fails (needs a
    scaffold), and the declared/renderer MISMATCH is a load error."""
    flipped = bs.BuildSkill.from_dict({**skills["add-dashboard"].to_dict(),
                                       "role": "fresh_build"})
    ok, errors = bs.validate_skill(flipped)
    assert not ok
    assert any("renderer capability probe" in e or "builds_on_empty_dir" in e for e in errors)
    # the probe itself: the real fresh skill builds on empty, the follow-on does not
    assert bs._fresh_build_capable(skills["scaffold-crud-sqlite"]) is True
    assert bs._fresh_build_capable(skills["add-dashboard"]) is False


# --------------------------------------------------------------------------------------------
# terra #5 / #6 — ADVERSARIAL: margin enforcement + embedding validation
# --------------------------------------------------------------------------------------------

def test_low_confidence_tier2_below_margin_escalates_to_tier4(ws, skills):
    """terra MED #5: a neighbor at dist 0.90 clears T2_MAX_DIST (0.95) but yields
    confidence 0.10 < margin 0.5 — it must escalate, not decide."""
    train = "somewhere to put all my books"
    query = "a distant odd request"
    mid_query = "a moderately close request"
    embed = mk_embed({train: (1.0, 0.0),
                      query: (1.90, 0.0),        # dist 0.90: inside T2_MAX_DIST, conf 0.10
                      mid_query: (1.55, 0.0)})   # dist 0.55: conf 0.45
    _seed_routes(ws, 3, utterance=train, accept=True)
    D.rebuild_route_index(ws, embed=embed)
    calls = []
    r = D.direct(query, skills, llm=lambda p: calls.append(p) or "scaffold-crud-sqlite",
                 workspace=ws, embed=embed)
    assert r.tier_used == 4 and len(calls) == 1
    # a conf-0.45 neighbor: escalates under the default margin 0.5 ...
    r2 = D.direct(mid_query, skills, llm=lambda p: "scaffold-crud-sqlite",
                  workspace=ws, embed=embed)
    assert r2.tier_used == 4
    # ... but DOES decide under an explicitly laxer margin (0.4) that tier 1 (conf 0.3) misses
    r3 = D.direct(mid_query, skills, margin=0.4,
                  llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=embed)
    assert r3.tier_used == 2 and r3.confidence == pytest.approx(0.45)


def test_nan_and_dim_mismatch_embeddings_fall_through_to_tier4(ws, skills):
    """terra MED #6: NaN distances defeat threshold comparisons; unequal dims were silently
    zip-truncated; 0/2-vector batches raised. All now fall through to tier 4."""
    train = "somewhere to put all my books"
    good = mk_embed({train: (1.0, 0.0)}, default=(1.0, 0.0))
    _seed_routes(ws, 6, utterance=train, accept=True)
    assert D.rebuild_route_index(ws, embed=good) > 0

    def nan_embed(texts):
        return [[float("nan"), 0.0] for _ in texts]

    def wide_embed(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]                # dim 3 vs indexed dim 2

    def double_embed(texts):
        return [[1.0, 0.0] for _ in texts] + [[1.0, 0.0]]      # wrong COUNT

    # NaN and wrong-count batches poison BOTH trained tiers -> tier 4 decides
    for bad in (nan_embed, double_embed):
        r = D.direct("i need somewhere to put my books", skills,
                     llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=bad)
        assert r.tier_used == 4, bad.__name__
        assert r.skill_id == "scaffold-crud-sqlite"
    # a query vector whose dimension mismatches the PERSISTED index dim is refused by
    # tier 2 (no zip-truncated garbage distance); tier 3 has no persisted index and may
    # legitimately classify with an internally-consistent batch
    assert D.nearest_routes(ws, "i need somewhere to put my books", embed=wide_embed) == []
    # and a poisoned batch can't BUILD an index either
    assert D.rebuild_route_index(ws, embed=nan_embed) == 0


def test_embedder_that_raises_falls_through_to_tier4(ws, skills):
    """terra RE-REVIEW #6: an embedder that RAISES (provider timeout / model load error) must
    fall to tier 4, not crash direct()."""
    train = "somewhere to put all my books"
    _seed_routes(ws, 6, utterance=train, accept=True)
    assert D.rebuild_route_index(ws, embed=mk_embed({train: (1.0, 0.0)}, default=(1.0, 0.0))) > 0

    def boom(texts):
        raise TimeoutError("embedder provider timed out")

    r = D.direct("i need somewhere to put my books", skills,
                 llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=boom)
    assert r.tier_used == 4 and r.skill_id == "scaffold-crud-sqlite"
    # and rebuild with a raising embedder is a clean no-op, not a crash
    assert D.rebuild_route_index(ws, embed=boom) == 0


def test_non_finite_margin_fails_safe_to_tier4(ws, skills):
    """terra RE-REVIEW #5: NaN margin (every `conf < margin` is false -> would accept a
    low-confidence route) and None margin (crashes `conf >= margin`) must both fail SAFE:
    escalate to tier 4, never accept under NaN, never crash on None."""
    train = "somewhere to put all my books"
    query = "i need somewhere to put my books"
    embed = mk_embed({train: (1.0, 0.0), query: (1.05, 0.0)})   # a confident tier-2 neighbor
    _seed_routes(ws, 3, utterance=train, accept=True)
    D.rebuild_route_index(ws, embed=embed)
    for bad_margin in (float("nan"), float("inf"), None, "0.5"):
        r = D.direct(query, skills, margin=bad_margin,          # type: ignore[arg-type]
                     llm=lambda p: "scaffold-crud-sqlite", workspace=ws, embed=embed)
        assert r.tier_used == 4, repr(bad_margin)               # never a trained-tier decision
        assert r.features.get("forced_tier4") == "bad_margin"
    # sanity: a valid margin still lets tier 2 decide (guards didn't over-fire)
    ok = D.direct(query, skills, margin=0.5, llm=lambda p: "x", workspace=ws, embed=embed)
    assert ok.tier_used == 2


# --------------------------------------------------------------------------------------------
# hybrid_build.run_chunk — the whole spine, offline
# --------------------------------------------------------------------------------------------

def test_run_chunk_happy_path_reaches_done_and_only_proposes(ws):
    r = hb.run_chunk("track my clients and who paid", project_id="p1", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_pass_gate,
                     reaction_text="perfect, done")
    assert r["status"] == "done" and r["state"] == "DONE"
    assert r["build"]["passed"] is True
    assert r["route"]["tier_used"] == 1
    assert r["reaction"]["classified_as"] == "done"
    assert len(r["proposals"]) == 2                            # mark_trainable + project_done
    assert r["attempts_spent"] == 1
    assert D.load_trainable(ws) == []                          # nothing trained without the human
    log = (ws / "ops-local" / "routing-log.jsonl").read_text(encoding="utf-8")
    assert '"kind": "route"' in log


def test_run_chunk_gate_fail_reworks_then_abandons_under_tiny_caps(ws, tmp_path):
    tiny = json.loads(json.dumps(se.APP_BUILD_TRACK))
    tiny["rework_cap"] = 0
    tiny["esc_cap"] = 0
    eng = se.StateEngine(str(tmp_path / "e.db"), chart=tiny, gate=se.make_universal_gate(),
                         workspace=str(ws))
    r = hb.run_chunk("track my clients and who paid", project_id="p2", workspace=ws,
                     engine=eng, llm=lambda p: VALID_SLOT, gate=_fail_gate)
    eng.close()
    assert r["status"] == "abandoned" and r["state"] == "ABANDONED"
    assert r["attempts_spent"] == 1


def test_project_level_budget_caps_rework_across_the_project(ws, monkeypatch):
    """Fix #5: per-chunk child tasks cannot launder the cap — the PROJECT ledger stops the loop."""
    monkeypatch.setattr(hb, "PROJECT_REWORK_CAP", 2)
    r = hb.run_chunk("track my clients and who paid", project_id="p3", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_fail_gate)
    # default chart caps would allow up to 9 attempts; the project budget stopped it at 2
    assert r["status"] == "project_budget_exhausted"
    assert r["attempts_spent"] == 2
    # a brand-new chunk in the same project is refused OUTRIGHT (no new task, no build)
    r2 = hb.run_chunk("track my books too", project_id="p3", workspace=ws,
                      llm=lambda p: VALID_SLOT, gate=_pass_gate)
    assert r2["status"] == "project_budget_exhausted" and "task_id" not in r2
    # a different project is unaffected
    r3 = hb.run_chunk("track my clients and who paid", project_id="p4", workspace=ws,
                      llm=lambda p: VALID_SLOT, gate=_pass_gate)
    assert r3["status"] == "done"


def test_run_chunk_bug_reaction_queues_decrement_per_applied_skill(ws):
    r = hb.run_chunk("track my clients and who paid", project_id="p5", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=_pass_gate,
                     reaction_text="the save button is broken")
    assert r["status"] == "done"
    kinds = {p["proposal_kind"] for p in rx.pending(ws)}
    assert kinds == {"skill_pass_decrement"}
    sj = ws / "skills" / "scaffold-crud-sqlite" / "skill.json"
    data = json.loads(sj.read_text(encoding="utf-8"))
    # pass_count includes the run's own record_outcome/_record_run increments in ops-local only;
    # the committed-registry decrement is still pending a human binary
    assert data["pass_count"] == bs.load_skills(REPO)["scaffold-crud-sqlite"].pass_count


# --------------------------------------------------------------------------------------------
# terra #3 — ADVERSARIAL: the budget is transactional, concurrent-safe, and un-launderable
# --------------------------------------------------------------------------------------------

def test_concurrent_reservations_cannot_exceed_the_cap(ws, monkeypatch):
    """terra HIGH #3 TOCTOU: two workers both read 7, both append -> 9. Reservations are now
    one BEGIN IMMEDIATE transaction each; hammering from many threads grants exactly cap."""
    import threading
    monkeypatch.setattr(hb, "PROJECT_REWORK_CAP", 5)
    cap = 5
    grants: list[int] = []
    errors: list[BaseException] = []

    def worker():
        try:
            for _ in range(4):
                res = hb.reserve_attempt("p_conc", "t_x", workspace=ws)
                if res.get("ok"):
                    grants.append(res["attempt_seq"])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(grants) == cap                                   # never cap+1
    assert sorted(grants) == list(range(1, cap + 1))            # unique attempt sequence
    assert hb.project_attempts("p_conc", ws) == cap


def test_caller_cannot_widen_the_cap(ws, monkeypatch):
    """terra RE-REVIEW #3: reserve_attempt takes NO caller cap -- it always uses the server
    PROJECT_REWORK_CAP. A caller cannot pass cap=1_000_000 to mint itself a bigger budget."""
    monkeypatch.setattr(hb, "PROJECT_REWORK_CAP", 3)
    # even trying to pass a cap kwarg is a TypeError (the parameter is gone)
    with pytest.raises(TypeError):
        hb.reserve_attempt("p_w", "t_x", cap=1_000_000, workspace=ws)  # type: ignore[call-arg]
    granted = 0
    for _ in range(10):
        if hb.reserve_attempt("p_w", "t_x", workspace=ws).get("ok"):
            granted += 1
    assert granted == 3                                         # server cap, not a caller's cap


def test_reservation_happens_before_execution(ws):
    """A crash DURING the build can no longer leak an un-counted attempt: the unit is spent
    at reservation time (fail-safe: a crash wastes budget, never mints extra)."""
    def exploding_gate(app_dir, checks):
        raise RuntimeError("simulated crash mid-execution")

    with pytest.raises(RuntimeError):
        hb.run_chunk("track my clients and who paid", project_id="p_crash", workspace=ws,
                     llm=lambda p: VALID_SLOT, gate=exploding_gate)
    assert hb.project_attempts("p_crash", ws) == 1              # reserved BEFORE the crash


def test_child_chunk_cannot_launder_budget_with_a_fresh_project_id(ws, tmp_path, monkeypatch):
    """terra HIGH #3: a child passing a new project_id used to get a fresh budget. A chunk
    naming a parent task is now bound to the parent's project_id."""
    monkeypatch.setattr(hb, "PROJECT_REWORK_CAP", 1)
    eng = se.StateEngine(str(tmp_path / "b.db"), gate=se.make_universal_gate(),
                         workspace=str(ws))
    try:
        r1 = hb.run_chunk("track my clients and who paid", project_id="p_parent",
                          workspace=ws, engine=eng, llm=lambda p: VALID_SLOT, gate=_pass_gate)
        assert r1["status"] == "done"
        parent_tid = r1["task_id"]
        # parent project is now at its cap; the child invents a fresh project_id -> REFUSED
        r2 = hb.run_chunk("add more stuff", project_id="p_totally_new", workspace=ws,
                          engine=eng, parent_task_id=parent_tid,
                          llm=lambda p: VALID_SLOT, gate=_pass_gate)
        assert r2["status"] == "project_id_mismatch"
        assert r2["parent_project_id"] == "p_parent"
        # honestly naming the parent project hits the exhausted budget instead
        r3 = hb.run_chunk("add more stuff", project_id="p_parent", workspace=ws,
                          engine=eng, parent_task_id=parent_tid,
                          llm=lambda p: VALID_SLOT, gate=_pass_gate)
        assert r3["status"] == "project_budget_exhausted"
    finally:
        eng.close()


def test_empty_project_id_cannot_launder_budget(ws, tmp_path, monkeypatch):
    """terra RE-REVIEW-2 #3 exact repro: a root run with project_id="" exhausted the ""
    budget, then -- because the lineage guard was `if parent_project and ...` and "" is
    falsy -- a declared child with parent_task_id and project_id="laundered" was accepted
    with a FRESH budget. Both arms are now dead: a blank/falsy project_id is refused at
    run_chunk intake AND at the reservation layer, and a declared continuation binds to its
    parent's project_id regardless of falsiness."""
    monkeypatch.setattr(hb, "PROJECT_REWORK_CAP", 1)
    # arm 1: a blank/falsy project_id can never own a budget in the first place
    for bad in ("", "   ", None):
        r = hb.run_chunk("track my clients and who paid", project_id=bad, workspace=ws,
                         llm=lambda p: VALID_SLOT, gate=_pass_gate)
        assert r["status"] == "invalid_project_id", repr(bad)
        assert "task_id" not in r                              # no task, no route, no attempt
    with pytest.raises(ValueError):
        hb.reserve_attempt("", "t_x", workspace=ws)            # server layer refuses too
    with pytest.raises(ValueError):
        hb.reserve_attempt("   ", "t_x", workspace=ws)
    # arm 2: even a hand-created parent task whose intent carries a FALSY project_id cannot
    # hand its declared child a fresh budget under a new name -- the guard no longer skips
    eng = se.StateEngine(str(tmp_path / "l.db"), gate=se.make_universal_gate(),
                         workspace=str(ws))
    try:
        for falsy in ("", None):
            parent_tid = eng.create_task({"seeking": "x", "project_id": falsy},
                                         track="app_build")
            r = hb.run_chunk("add more stuff", project_id="laundered", workspace=ws,
                             engine=eng, parent_task_id=parent_tid,
                             llm=lambda p: VALID_SLOT, gate=_pass_gate)
            assert r["status"] == "project_id_mismatch", repr(falsy)
        assert hb.project_attempts("laundered", ws) == 0       # no budget was ever granted
    finally:
        eng.close()


# --------------------------------------------------------------------------------------------
# research-first app_build: Option B — chain build → app_build
# (reviewed/app-build-research-phase-design-2026-07-15.md)
# --------------------------------------------------------------------------------------------

def test_advance_to_app_build_requires_build_done(tmp_path):
    """advance_to_app_build must refuse a build task that hasn't reached DONE."""
    eng = _engine(tmp_path)
    try:
        build_tid = eng.create_task({"seeking": "x"}, track="build")
        # build task is at SEARCH_BRAIN, not DONE
        with pytest.raises(ValueError, match="has not reached DONE"):
            se.advance_to_app_build(eng, build_tid)
    finally:
        eng.close()


def test_advance_to_app_build_carries_research_evidence(tmp_path):
    """After a build task reaches DONE, advance_to_app_build creates an app_build
    task whose intent carries researched=True and the research evidence."""
    eng = _engine(tmp_path)
    try:
        build_tid = eng.create_task({"seeking": "build a dashboard"}, track="build")
        # Walk the build track to DONE with evidence
        env = eng.step(build_tid, "cortex_report_findings",
                       {"evidence": ["found SaaS dashboard patterns"]}, seq=0)
        env = eng.step(build_tid, "cortex_report_findings",
                       {"evidence": ["found SaaS dashboard patterns", "cited UI research"]},
                       seq=env["seq"])
        env = eng.step(build_tid, "cortex_submit_plan",
                       {"plan": "step 1: build app"}, seq=env["seq"])
        env = eng.step(build_tid, "cortex_submit_spec",
                       {"spec": "must pass 21 checks"}, seq=env["seq"])
        env = eng.step(build_tid, "cortex_submit_patch",
                       {"patch": "app.py written"}, seq=env["seq"])
        env = eng.step(build_tid, "cortex_submit_review",
                       {"review": "looks good"}, seq=env["seq"])
        env = eng.step(build_tid, "cortex_write_closeout",
                       {"task": "build", "result": "done"}, seq=env["seq"])
        assert env["state"] == "DONE"

        app_tid = se.advance_to_app_build(eng, build_tid)
        app_env = eng.get(app_tid)
        assert app_env["state"] == "SCAFFOLD"
        assert app_env["track"] == "app_build"
        assert app_env["parent_id"] == build_tid

        intent = json.loads(app_env["intent"]) if isinstance(app_env["intent"], str) else app_env["intent"]
        assert intent.get("researched") is True
        ev = intent.get("research_evidence")
        assert isinstance(ev, list) and len(ev) >= 1
    finally:
        eng.close()


def test_research_prereq_gate_blocks_scaffold_without_evidence(tmp_path):
    """A task with researched=True but empty research_evidence is blocked at SCAFFOLD."""
    eng = se.StateEngine(
        str(tmp_path / "t.db"),
        gate=se.make_universal_gate(extra=se.research_prereq_gate),
        workspace=str(tmp_path))
    try:
        tid = eng.create_task({"seeking": "x", "researched": True, "research_evidence": []},
                              track="app_build")
        app_dir = _mk_app(tmp_path, "app")
        env = _scaffold(eng, tid, app_dir, 0)
        # SCAFFOLD has no rework_to, so a gate failure returns ok=True but stays at SCAFFOLD
        # with gate.pass=False
        assert env["state"] == "SCAFFOLD"
        assert env["gate"]["pass"] is False
        assert env["gate"]["code"] == "RESEARCH_PREREQ_NOT_MET"
    finally:
        eng.close()


def test_research_prereq_gate_passes_with_evidence(tmp_path):
    """A task with researched=True and meaningful evidence passes the gate."""
    eng = se.StateEngine(
        str(tmp_path / "t.db"),
        gate=se.make_universal_gate(extra=se.research_prereq_gate),
        workspace=str(tmp_path))
    try:
        tid = eng.create_task(
            {"seeking": "x", "researched": True,
             "research_evidence": ["SaaS dashboard UI patterns researched"]},
            track="app_build")
        app_dir = _mk_app(tmp_path, "app")
        env = _scaffold(eng, tid, app_dir, 0)
        assert env["ok"] is True
        assert env["state"] == "SMOKE"
    finally:
        eng.close()


def test_research_prereq_gate_passes_legacy_app_build(tmp_path):
    """A task without the researched flag passes through (backward compatible)."""
    eng = se.StateEngine(
        str(tmp_path / "t.db"),
        gate=se.make_universal_gate(extra=se.research_prereq_gate),
        workspace=str(tmp_path))
    try:
        tid = eng.create_task({"seeking": "x"}, track="app_build")
        app_dir = _mk_app(tmp_path, "app")
        env = _scaffold(eng, tid, app_dir, 0)
        assert env["ok"] is True
        assert env["state"] == "SMOKE"
    finally:
        eng.close()


def test_existing_app_build_tasks_still_walk_unchanged(tmp_path):
    """Existing create_task(track='app_build') still walks the full chart without
    any research requirement."""
    eng = _engine(tmp_path)
    try:
        tid = eng.create_task({"seeking": "x", "project_id": "p1"}, track="app_build")
        env = eng.get(tid)
        assert env["state"] == "SCAFFOLD"
        assert se.phase_legal_tools("app_build", "SCAFFOLD") == ["cortex_submit_artifact"]
        app_dir = _mk_app(tmp_path, "app")
        env = _scaffold(eng, tid, app_dir, env["seq"])
        assert env["ok"] and env["state"] == "SMOKE"
        vid = _mint(tmp_path, tid, app_dir)
        env = eng.step(tid, "cortex_submit_smoke", {"verdict_id": vid}, seq=env["seq"])
        assert env["ok"] and env["state"] == "SHOW"
        env = eng.step(tid, "cortex_submit_reaction", {"reaction": None}, seq=env["seq"])
        assert env["state"] == "CLOSEOUT"
        env = eng.step(tid, "cortex_write_closeout", {"task": "x", "result": "ok"}, seq=env["seq"])
        assert env["state"] == "DONE"
    finally:
        eng.close()
