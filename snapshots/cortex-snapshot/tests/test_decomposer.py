"""EXECUTABLE SPEC for the STANDALONE heterogeneous decomposer
(``cortex_core/decomposer.py``) — terra's PARTITION-seam design
(``reviewed/decomposer-research-terra-2026-07-15.md``).

These tests are the TDD contract. They are RED until ``cortex_core/decomposer.py``
exists and turning them GREEN is the definition of "the judge-free heart is correct."

The invariant under test (non-negotiable): a MODEL may only PROPOSE a manifest; a
bounded, PURE-DETERMINISTIC ``validate_manifest`` accepts/rejects it. No model decides
pass/fail or task completion anywhere in this module. ``validate_manifest`` must be
CONSISTENT with the existing mission PARTITION gate
(``state_engine.partition_coverage_gate``, state_engine.py:454-488) so the two cannot
drift: MISSING_COVERAGE, UNIT_DOUBLE_OWNED, FANOUT_EXCEEDED share codes and semantics,
and claim path-exclusivity mirrors ``StateEngine._claim_conflicts`` (bidirectional
``fnmatchcase`` within one ``kind``, state_engine.py:1137-1160).
"""

from __future__ import annotations

import copy

import pytest

from cortex_core import decomposer
from cortex_core.decomposer import validate_manifest, propose_manifest


# --------------------------------------------------------------------------- #
# A well-formed manifest that every rejection test mutates a single field of.  #
# --------------------------------------------------------------------------- #
def _good_manifest() -> dict:
    return {
        "mission_id": "t_parent",
        "coverage_spec": {"required_units": ["api", "ui", "tests"], "max_workers": 3},
        "workers": [
            {
                "key": "api",
                "objective": "Implement the HTTP handlers only",
                "track": "app_build",
                "tier_profile": "code-medium",
                "owns_units": ["api"],
                "claims": [{"kind": "path", "key": "src/api/**"}],
                "depends_on": [],
                "artifact_lane": ".cortex/worktrees/t_parent/api",
                "acceptance": {"kind": "smoke_receipt"},
            },
            {
                "key": "ui",
                "objective": "Build the UI components only",
                "track": "app_build",
                "tier_profile": "code-medium",
                "owns_units": ["ui"],
                "claims": [{"kind": "path", "key": "src/ui/**"}],
                "depends_on": ["api"],
                "artifact_lane": ".cortex/worktrees/t_parent/ui",
                "acceptance": {"kind": "smoke_receipt"},
            },
            {
                "key": "tests",
                "objective": "Write integration tests only",
                "track": "app_build",
                "tier_profile": "code-low",
                "owns_units": ["tests"],
                "claims": [{"kind": "path", "key": "tests/**"}],
                "depends_on": ["api", "ui"],
                "artifact_lane": ".cortex/worktrees/t_parent/tests",
                "acceptance": {"kind": "smoke_receipt"},
            },
        ],
        "reducers": [{"kind": "git_merge", "order": ["api", "ui", "tests"]}],
    }


def _codes(problems) -> set[str]:
    return {p["code"] for p in problems}


# --------------------------------------------------------------------------- #
# GREEN: a well-formed manifest validates.                                     #
# --------------------------------------------------------------------------- #
def test_well_formed_manifest_validates():
    ok, problems = validate_manifest(_good_manifest())
    assert ok is True, problems
    assert problems == []


def test_validate_is_pure_no_mutation():
    """Deterministic + side-effect-free: the input manifest is not mutated."""
    m = _good_manifest()
    before = copy.deepcopy(m)
    validate_manifest(m)
    assert m == before


# --------------------------------------------------------------------------- #
# RED: each failure mode is rejected with a stable code.                       #
# --------------------------------------------------------------------------- #
def test_reject_missing_required_unit():
    m = _good_manifest()
    # drop the worker that owns "tests" -> "tests" is uncovered.
    m["workers"] = [w for w in m["workers"] if w["key"] != "tests"]
    m["reducers"] = [{"kind": "git_merge", "order": ["api", "ui"]}]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "MISSING_COVERAGE" in _codes(problems)


def test_reject_double_owned_unit():
    m = _good_manifest()
    # make the ui worker ALSO own "api" -> "api" double-owned.
    m["workers"][1]["owns_units"] = ["ui", "api"]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "UNIT_DOUBLE_OWNED" in _codes(problems)


def test_reject_dependency_cycle():
    m = _good_manifest()
    # api -> tests -> ... and tests already depends on api: make api depend on tests -> cycle.
    m["workers"][0]["depends_on"] = ["tests"]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "DEP_CYCLE" in _codes(problems)


def test_reject_self_dependency_is_a_cycle():
    m = _good_manifest()
    m["workers"][0]["depends_on"] = ["api"]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "DEP_CYCLE" in _codes(problems)


def test_reject_dependency_on_unknown_worker():
    m = _good_manifest()
    m["workers"][1]["depends_on"] = ["ghost"]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "DEP_UNKNOWN" in _codes(problems)


def test_reject_more_workers_than_max():
    m = _good_manifest()
    m["coverage_spec"]["max_workers"] = 2  # 3 workers > 2
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "FANOUT_EXCEEDED" in _codes(problems)


def test_reject_unlisted_track():
    m = _good_manifest()
    m["workers"][0]["track"] = "totally_made_up_track"
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "BAD_TRACK" in _codes(problems)


def test_reject_unlisted_tier_profile():
    m = _good_manifest()
    m["workers"][0]["tier_profile"] = "gpt-9-ultra-max"
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "BAD_TIER_PROFILE" in _codes(problems)


def test_reject_overlapping_claim_globs():
    m = _good_manifest()
    # ui now claims src/** which overlaps api's src/api/** (bidirectional fnmatch).
    m["workers"][1]["claims"] = [{"kind": "path", "key": "src/**"}]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "CLAIM_CONFLICT" in _codes(problems)


def test_reject_identical_claim_globs():
    m = _good_manifest()
    m["workers"][1]["claims"] = [{"kind": "path", "key": "src/api/**"}]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "CLAIM_CONFLICT" in _codes(problems)


def test_claims_of_different_kind_do_not_conflict():
    """Overlap is only checked WITHIN one claim kind (mirrors _claim_conflicts)."""
    m = _good_manifest()
    m["workers"][0]["claims"] = [{"kind": "path", "key": "shared"}]
    m["workers"][1]["claims"] = [{"kind": "lock", "key": "shared"}]
    ok, problems = validate_manifest(m)
    assert "CLAIM_CONFLICT" not in _codes(problems)


def test_reject_empty_objective():
    m = _good_manifest()
    m["workers"][0]["objective"] = "   "
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "EMPTY_OBJECTIVE" in _codes(problems)


def test_reject_claimless_worker():
    """A claimless worker cannot own a disjoint slice (mirrors _materialize_partition
    CLAIMLESS_WORKER)."""
    m = _good_manifest()
    m["workers"][0]["claims"] = []
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "CLAIMLESS_WORKER" in _codes(problems)


def test_reject_duplicate_worker_key():
    m = _good_manifest()
    m["workers"][1]["key"] = "api"
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "DUPLICATE_WORKER_KEY" in _codes(problems)


def test_reject_non_dict_manifest():
    ok, problems = validate_manifest(["not", "a", "dict"])
    assert ok is False
    assert "BAD_MANIFEST" in _codes(problems)


def test_reject_missing_mission_id():
    m = _good_manifest()
    del m["mission_id"]
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "BAD_MISSION_ID" in _codes(problems)


def test_reject_no_workers():
    m = _good_manifest()
    m["workers"] = []
    ok, problems = validate_manifest(m)
    assert ok is False
    assert "NO_WORKERS" in _codes(problems)


def test_multiple_problems_are_all_reported():
    """Not short-circuit: independent failures surface together for one round-trip fix."""
    m = _good_manifest()
    m["workers"][0]["track"] = "bogus"
    m["workers"][1]["objective"] = ""
    ok, problems = validate_manifest(m)
    assert ok is False
    assert {"BAD_TRACK", "EMPTY_OBJECTIVE"} <= _codes(problems)


# --------------------------------------------------------------------------- #
# Consistency with the state_engine partition gate: same verdict on the same   #
# coverage input, so the two implementations cannot silently drift.            #
# --------------------------------------------------------------------------- #
def test_coverage_agrees_with_state_engine_partition_gate():
    from cortex_core import state_engine

    m = _good_manifest()
    task = {"intent": {"coverage_spec": m["coverage_spec"]}}
    payload = {"workers": m["workers"]}
    gate = state_engine.partition_coverage_gate("PARTITION", task, payload)
    ok, _ = validate_manifest(m)
    assert gate["pass"] is True and ok is True

    # a double-owned unit must fail BOTH.
    m2 = _good_manifest()
    m2["workers"][1]["owns_units"] = ["ui", "api"]
    task2 = {"intent": {"coverage_spec": m2["coverage_spec"]}}
    payload2 = {"workers": m2["workers"]}
    gate2 = state_engine.partition_coverage_gate("PARTITION", task2, payload2)
    ok2, problems2 = validate_manifest(m2)
    assert gate2["pass"] is False and ok2 is False
    assert gate2["code"] == "UNIT_DOUBLE_OWNED"
    assert "UNIT_DOUBLE_OWNED" in _codes(problems2)


# --------------------------------------------------------------------------- #
# propose_manifest: model PROPOSES only. Mock the dispatch — no live calls.    #
# --------------------------------------------------------------------------- #
def test_propose_manifest_parses_model_json(monkeypatch):
    import json

    proposed = _good_manifest()

    def fake_complete(prompt, tier, max_tokens, **kwargs):
        assert tier in decomposer.FREE_TIERS  # never a paid tier
        return json.dumps(proposed)

    monkeypatch.setattr(decomposer.model_dispatch, "llm_complete", fake_complete)
    out = propose_manifest("build a small web app", tier="ollama")
    assert out == proposed


def test_propose_manifest_extracts_json_from_fenced_block(monkeypatch):
    import json

    proposed = _good_manifest()

    def fake_complete(prompt, tier, max_tokens, **kwargs):
        return "Here is the plan:\n```json\n" + json.dumps(proposed) + "\n```\nDone."

    monkeypatch.setattr(decomposer.model_dispatch, "llm_complete", fake_complete)
    out = propose_manifest("build a small web app")
    assert out == proposed


def test_propose_manifest_returns_none_when_model_unavailable(monkeypatch):
    def fake_complete(prompt, tier, max_tokens, **kwargs):
        return None  # unconfigured/unreachable tier degrades to None

    monkeypatch.setattr(decomposer.model_dispatch, "llm_complete", fake_complete)
    assert propose_manifest("anything") is None


def test_propose_manifest_returns_none_on_unparseable(monkeypatch):
    def fake_complete(prompt, tier, max_tokens, **kwargs):
        return "I'm afraid I can't do that."

    monkeypatch.setattr(decomposer.model_dispatch, "llm_complete", fake_complete)
    assert propose_manifest("anything") is None


def test_propose_manifest_refuses_non_free_tier():
    """Structural judge-free/paid-tier guard: a paid/premium tier is refused before any call."""
    with pytest.raises(ValueError):
        propose_manifest("anything", tier="opus")


def test_propose_output_is_not_trusted_until_validated(monkeypatch):
    """A model may PROPOSE an INVALID manifest; propose_manifest does NOT bless it —
    validate_manifest is the only authority."""
    import json

    bad = _good_manifest()
    bad["workers"][0]["track"] = "bogus"  # model proposes something invalid

    def fake_complete(prompt, tier, max_tokens, **kwargs):
        return json.dumps(bad)

    monkeypatch.setattr(decomposer.model_dispatch, "llm_complete", fake_complete)
    out = propose_manifest("anything")
    assert out == bad  # returned verbatim, unblessed
    ok, problems = validate_manifest(out)  # the deterministic gate rejects it
    assert ok is False
    assert "BAD_TRACK" in _codes(problems)
