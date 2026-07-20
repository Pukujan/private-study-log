"""Frozen tests for the live cross-vendor generation harness itself
(`evals/live_gen/`), not for any one lane's checker (that lane already has its own frozen
tests — see `tests/test_objective_prompt_injection_trace.py`).

Proves, per the harness's own contract:
  1. Record schema — every field `generate.py` promises exists and is well-typed.
  2. Anti-distillation guard — Anthropic tiers/model-ids are NEVER stamped trainable=true,
     even if a caller passes an Anthropic tier by mistake.
  3. Dedup — identical (case, tier, model, trace) content hashes to the same key and is
     dropped on a second pass; a real behavioral difference (different trace) is kept.
  4. Quarantine handling — malformed/empty traces produce a quarantine record, never a
     guessed pass/fail label.
  5. Fan-out scheduling — every scenario gets >=1 assignment, overlap scenarios get every
     tier, disjoint scenarios get a bounded subset (not the full fleet).
  6. Agent-loop step parsing tolerates the same CoT-prefix/markdown-fence noise the
     existing BFCL live-gen script already tolerates, and quarantines pure noise rather
     than fabricating a trace.

  7. Verified provenance (terra Finding 4, 2026-07-14) — the RESPONSE's own reported model
     id is captured (client.py), threaded through the agent loop (agent_loop.py) and into
     the record (schema.py), never just the requested tier's configured model.

No network calls — everything here is pure-function / offline.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.live_gen import schema  # noqa: E402
from evals.live_gen import client as C  # noqa: E402
from evals.live_gen.agent_loop import AgentRunResult, _parse_step, _render_history, run_agent  # noqa: E402
from evals.live_gen.generate import build_assignments, rebalance_assignments  # noqa: E402


# ---- 1. record schema -------------------------------------------------------------------

_SCENARIO = {"id": "case_x", "class": "indirect_rag_injection", "provenance": {"dataset": "bipia"}}
_TRACE_PASS = [
    {"type": "tool_call", "tool": "read_email", "args": {"id": "0"}},
    {"type": "tool_result", "tool": "read_email", "result": "hi"},
    {"type": "final_output", "text": "Sent by Ops."},
]
_VERDICT_PASS = {"case_id": "case_x", "verdict": "pass", "invariants": {}, "violations": [], "note": ""}
_VERDICT_FAIL = {"case_id": "case_x", "verdict": "fail", "invariants": {},
                 "violations": ["FORBIDDEN_TOOL_CALLED"], "note": ""}


def _rec(tier="ninerouter-aux", model="big-pickle", checker_verdict=None, quarantine_reason=None,
         trace=None, served_models=None):
    return schema.build_record(
        record_id="r1", case_id="case_x", class_="indirect_rag_injection", scenario=_SCENARIO,
        trace=trace if trace is not None else _TRACE_PASS, checker_verdict=checker_verdict,
        tier=tier, model_id=model, seed=1, attempt=0, latency_s=1.0, cost_usd=0.0,
        prompt_sha256="abc", quarantine_reason=quarantine_reason, provenance={"group": "overlap"},
        served_models=served_models)


def test_record_has_all_promised_fields():
    r = _rec(checker_verdict=_VERDICT_PASS, served_models=["ag/big-pickle"])
    required = {"id", "case_id", "class_", "scenario_id", "trace", "checker_verdict",
                "objective_verdict", "failure_class", "all_violations", "executor_model",
                "served_model", "served_models", "provenance_verified",
                "provenance_verification_note",
                "seed", "attempt", "steps_taken", "reasoning_chars", "latency_s", "cost_usd",
                "prompt_sha256", "content_sha256", "trainable", "non_trainable_reason",
                "provenance", "candidate_origin", "label_authority", "quarantine_reason"}
    assert required <= set(r.keys())
    assert r["executor_model"] == {"tier": "ninerouter-aux", "model": "big-pickle", "family": "zhipu-proxy"}


# ---- 0. verified provenance (terra Finding 4, 2026-07-14) --------------------------------

def test_single_served_model_is_provenance_verified_and_trainable():
    r = _rec(checker_verdict=_VERDICT_PASS, served_models=["ag/big-pickle"])
    assert r["provenance_verified"] is True
    assert r["served_model"] == "ag/big-pickle"
    assert r["trainable"] is True


def test_no_served_model_reported_is_unverified_and_never_trainable():
    # The gateway response never echoed a model id -- we cannot claim to know who answered,
    # so this must NEVER be stamped trainable even though the requested tier is non-Anthropic.
    r = _rec(checker_verdict=_VERDICT_PASS, served_models=[])
    assert r["provenance_verified"] is False
    assert r["served_model"] is None
    assert r["trainable"] is False
    assert "provenance_unverified" in r["non_trainable_reason"]


def test_multiple_distinct_served_models_in_one_run_is_unverified():
    # A proxy/round-robin route (aux combo) served a DIFFERENT model per call within the
    # same run -- must never collapse into a single trainable claim about "the" model.
    r = _rec(checker_verdict=_VERDICT_PASS, served_models=["ag/big-pickle", "ag/deepseek-v4-flash"])
    assert r["provenance_verified"] is False
    assert r["served_model"] is None
    assert sorted(r["served_models"]) == ["ag/big-pickle", "ag/deepseek-v4-flash"]
    assert r["trainable"] is False


def test_served_model_resolving_to_anthropic_is_never_trainable_even_if_requested_tier_is_not():
    # The exact silent-reroute scenario Finding 4 names: a proxy route claims a non-Anthropic
    # requested tier, but the RESPONSE says Claude actually answered.
    r = _rec(tier="ninerouter-aux", model="ag/aux-router", checker_verdict=_VERDICT_PASS,
             served_models=["claude-3-5-sonnet-20241022"])
    assert r["trainable"] is False
    assert "anthropic" in r["non_trainable_reason"].lower()


def test_provisional_reason_is_recorded_and_forces_non_trainable():
    # Finding 5: a lane-wide known checker limitation (declared via the lane module's
    # PROVISIONAL_REASON attribute, e.g. objective_prompt_injection_trace) must stamp EVERY
    # record from that lane provisional, and a provisional record is NEVER trainable even if
    # provenance was otherwise fully verified.
    r = _rec(checker_verdict=_VERDICT_PASS, served_models=["ag/big-pickle"])
    r2 = schema.build_record(
        record_id="r2", case_id="case_x", class_="indirect_rag_injection", scenario=_SCENARIO,
        trace=_TRACE_PASS, checker_verdict=_VERDICT_PASS, tier="ninerouter-aux",
        model_id="big-pickle", seed=1, attempt=0, latency_s=1.0, cost_usd=0.0,
        prompt_sha256="abc", quarantine_reason=None, provenance={"group": "overlap"},
        served_models=["ag/big-pickle"], provisional_reason="known checker limitation")
    assert r["provisional"] is False  # no provisional_reason passed -> unaffected
    assert r["trainable"] is True
    assert r2["provisional"] is True
    assert r2["provisional_reason"] == "known checker limitation"
    assert r2["trainable"] is False
    assert "provisional" in r2["non_trainable_reason"]


def test_served_model_is_recorded_even_when_run_is_quarantined():
    r = _rec(checker_verdict=None, quarantine_reason="unparseable_every_step", trace=[],
              served_models=["ag/big-pickle"])
    assert r["served_model"] == "ag/big-pickle"
    assert r["provenance_verified"] is True
    # quarantined runs are never trainable regardless of provenance (existing invariant,
    # exercised together here so the two checks don't silently interact badly).
    assert r["trainable"] is False


def test_pass_record_failure_class_is_pass():
    r = _rec(checker_verdict=_VERDICT_PASS)
    assert r["failure_class"] == "PASS"
    assert r["objective_verdict"] == "pass"
    assert r["quarantine_reason"] is None


def test_fail_record_failure_class_is_first_violation():
    r = _rec(checker_verdict=_VERDICT_FAIL)
    assert r["failure_class"] == "FORBIDDEN_TOOL_CALLED"
    assert r["objective_verdict"] == "fail"


def test_quarantined_record_never_gets_a_pass_fail_label():
    r = _rec(checker_verdict=None, quarantine_reason="unparseable_every_step", trace=[])
    assert r["failure_class"] == "QUARANTINED"
    assert r["objective_verdict"] is None
    assert r["quarantine_reason"] == "unparseable_every_step"


# ---- 2. anti-distillation guard ----------------------------------------------------------

def test_anthropic_cli_tier_never_trainable():
    for tier in ("fable-max", "opus", "sonnet", "haiku"):
        trainable, reason = schema.is_trainable(tier, model_id="claude-3-x")
        assert trainable is False
        assert reason


def test_anthropic_routed_ninerouter_tier_never_trainable():
    trainable, reason = schema.is_trainable("9r-sonnet-4.6", model_id="ag/claude-sonnet-4-6")
    assert trainable is False and reason


def test_model_id_containing_claude_never_trainable_even_on_an_unlisted_tier():
    # belt-and-suspenders: a gateway's *declared* model id resolving to Claude must be
    # caught even if the calling tier name itself isn't in ANTHROPIC_TIERS.
    trainable, reason = schema.is_trainable("some-new-9router-lane", model_id="claude-opus-4-8")
    assert trainable is False and "claude" in reason.lower()


def test_non_anthropic_tier_is_trainable():
    trainable, reason = schema.is_trainable("glm5.2", model_id="umans-glm-5.2")
    assert trainable is True and reason is None


def test_build_record_stamps_non_trainable_for_anthropic_tier():
    r = _rec(tier="opus", model="claude-opus-4-8", checker_verdict=_VERDICT_PASS)
    assert r["trainable"] is False
    assert r["non_trainable_reason"]


# ---- 3. dedup --------------------------------------------------------------------------

def test_dedup_drops_identical_content_keeps_distinct():
    r1 = _rec(checker_verdict=_VERDICT_PASS)
    r2 = dict(r1)  # exact duplicate (same content hash)
    r3 = _rec(checker_verdict=_VERDICT_FAIL, trace=_TRACE_PASS + [
        {"type": "tool_call", "tool": "execute_shell", "args": {}}])  # different trace -> different hash
    deduped, dropped = schema.dedup([r1, r2, r3])
    assert dropped == 1
    assert len(deduped) == 2
    assert len({d["content_sha256"] for d in deduped}) == 2


def test_content_hash_is_deterministic_and_order_sensitive_on_inputs():
    h1 = schema.content_hash("c1", "glm5.2", "umans-glm-5.2", _TRACE_PASS)
    h2 = schema.content_hash("c1", "glm5.2", "umans-glm-5.2", _TRACE_PASS)
    h3 = schema.content_hash("c1", "glm5.2", "umans-glm-5.2", list(reversed(_TRACE_PASS)))
    assert h1 == h2
    assert h1 != h3  # a materially different trace must not collide


# ---- 4. failure-class accounting ---------------------------------------------------------

def test_failure_class_counts_excludes_quarantined():
    recs = [
        _rec(checker_verdict=_VERDICT_PASS),
        _rec(checker_verdict=_VERDICT_FAIL, trace=_TRACE_PASS + [{"type": "x"}]),
        _rec(checker_verdict=None, quarantine_reason="no_events_produced", trace=[]),
    ]
    counts = schema.failure_class_counts(recs)
    assert counts.get("QUARANTINED") is None
    assert counts["PASS"] == 1
    assert counts["FORBIDDEN_TOOL_CALLED"] == 1
    assert sum(counts.values()) == 2


# ---- 5. fan-out scheduling ----------------------------------------------------------------

def _case(cid):
    return {"scenario": {"id": cid, "class": "x"}}


def test_overlap_scenarios_get_every_tier():
    import random
    cases = [_case(f"c{i}") for i in range(8)]
    tiers = ["t1", "t2", "t3", "t4"]
    a = build_assignments(tiers, cases, overlap_frac=0.25, disjoint_tiers=2,
                          attempts_overlap=1, attempts_disjoint=1, rng=random.Random(0))
    overlap_ids = {c["scenario"]["id"] for c in cases[:2]}
    for cid in overlap_ids:
        tiers_seen = {x["tier"] for x in a if x["case"]["scenario"]["id"] == cid}
        assert tiers_seen == set(tiers)


def test_disjoint_scenarios_get_bounded_subset_not_full_fleet():
    import random
    cases = [_case(f"c{i}") for i in range(8)]
    tiers = ["t1", "t2", "t3", "t4", "t5"]
    a = build_assignments(tiers, cases, overlap_frac=0.25, disjoint_tiers=2,
                          attempts_overlap=1, attempts_disjoint=1, rng=random.Random(0))
    disjoint_ids = {c["scenario"]["id"] for c in cases[2:]}
    for cid in disjoint_ids:
        tiers_seen = {x["tier"] for x in a if x["case"]["scenario"]["id"] == cid}
        assert 0 < len(tiers_seen) <= 2


def test_every_scenario_gets_at_least_one_assignment():
    import random
    cases = [_case(f"c{i}") for i in range(10)]
    tiers = ["t1", "t2", "t3"]
    a = build_assignments(tiers, cases, overlap_frac=0.2, disjoint_tiers=1,
                          attempts_overlap=1, attempts_disjoint=1, rng=random.Random(0))
    seen = {x["case"]["scenario"]["id"] for x in a}
    assert seen == {c["scenario"]["id"] for c in cases}


def test_rebalance_targets_only_under_covered_classes():
    cases_by_id = {"c0": _case("c0")}
    records = [
        {"case_id": "c0", "executor_model": {"tier": "t1"}, "failure_class": "PASS"},
    ] * 25  # PASS already well above any reasonable target
    records += [
        {"case_id": "c0", "executor_model": {"tier": "t1"}, "failure_class": "FORBIDDEN_TOOL_CALLED"},
    ] * 2  # under-covered
    import random
    extra = rebalance_assignments(records, ["t1", "t2", "t3"], cases_by_id, target_min=20,
                                  extra_attempts=1, extra_tiers=2, round_no=1, rng=random.Random(0))
    assert extra  # something was scheduled
    assert all(x["group"] == "rebalance:FORBIDDEN_TOOL_CALLED" for x in extra)
    # never re-picks a tier already tried for that scenario when an untried one exists
    assert all(x["tier"] != "t1" for x in extra)


def test_rebalance_returns_empty_when_all_classes_covered():
    cases_by_id = {"c0": _case("c0")}
    from evals.live_gen.generate import TRACKED_FAILURE_CLASSES
    records = []
    for fc in TRACKED_FAILURE_CLASSES:
        records += [{"case_id": "c0", "executor_model": {"tier": "t1"}, "failure_class": fc}] * 5
    import random
    extra = rebalance_assignments(records, ["t1"], cases_by_id, target_min=5, extra_attempts=1,
                                  extra_tiers=2, round_no=1, rng=random.Random(0))
    assert extra == []


# ---- 6. agent-loop step parsing (offline, pure-function) ----------------------------------

def test_parse_step_plain_json():
    obj = _parse_step('{"thought": "ok", "final_output": "done"}')
    assert obj == {"thought": "ok", "final_output": "done"}


def test_parse_step_tolerates_markdown_fence():
    obj = _parse_step('```json\n{"action": {"tool": "read_email", "args": {}}}\n```')
    assert obj["action"]["tool"] == "read_email"


def test_parse_step_tolerates_cot_prefix():
    obj = _parse_step('Let me think about this step by step... {"final_output": "42"}')
    assert obj == {"final_output": "42"}


def test_parse_step_returns_none_on_pure_noise():
    assert _parse_step("I am not going to answer in JSON today.") is None


def test_parse_step_returns_none_on_empty():
    assert _parse_step("") is None


def test_render_history_empty_trace():
    assert "first step" in _render_history([])


def test_render_history_renders_all_event_types():
    trace = [
        {"type": "tool_call", "tool": "read_email", "args": {"id": "0"}},
        {"type": "tool_result", "tool": "read_email", "result": "hi"},
        {"type": "final_output", "text": "done"},
    ]
    rendered = _render_history(trace)
    assert "ACTION" in rendered and "OBSERVATION" in rendered and "FINAL" in rendered


# ---- 7. verified provenance (terra Finding 4, 2026-07-14) --------------------------------

def test_client_captures_served_model_from_response_not_request(monkeypatch):
    fake_cfg = SimpleNamespace(tier="faketier", url="http://example.invalid/v1", key="k",
                                model="requested-model-id")
    monkeypatch.setattr(C.J, "get_tier_config", lambda tier, env=None: fake_cfg)
    monkeypatch.setattr(C.J, "apply_min_max_tokens", lambda tier, mt: mt)
    monkeypatch.setattr(C.J, "_chat_completions_url", lambda base: base)
    monkeypatch.setattr(C.J, "CLI_TIERS", frozenset())

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"model": "served-actual-model-id",
                     "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
                     "usage": {}}

    monkeypatch.setattr(C.httpx, "post", lambda *a, **k: FakeResp())

    res = C.call_model("faketier", system_prompt="s", user_prompt="u")
    assert res.ok
    assert res.served_model == "served-actual-model-id"
    assert res.served_model != "requested-model-id"


def test_client_served_model_is_none_when_gateway_omits_it(monkeypatch):
    fake_cfg = SimpleNamespace(tier="faketier", url="http://example.invalid/v1", key="k",
                                model="requested-model-id")
    monkeypatch.setattr(C.J, "get_tier_config", lambda tier, env=None: fake_cfg)
    monkeypatch.setattr(C.J, "apply_min_max_tokens", lambda tier, mt: mt)
    monkeypatch.setattr(C.J, "_chat_completions_url", lambda base: base)
    monkeypatch.setattr(C.J, "CLI_TIERS", frozenset())

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}

    monkeypatch.setattr(C.httpx, "post", lambda *a, **k: FakeResp())
    res = C.call_model("faketier", system_prompt="s", user_prompt="u")
    assert res.served_model is None


class _FakeHook:
    def tool_specs(self, scenario):
        return [{"name": "noop", "description": "does nothing"}]

    def simulate_tool(self, scenario, tool, args):
        return "ok"

    def user_prompt(self, scenario):
        return "do the thing"


def test_run_agent_collects_served_models_across_steps(monkeypatch):
    import evals.live_gen.agent_loop as AL

    responses = iter([
        C.CallResult(content='{"thought": "t", "action": {"tool": "noop", "args": {}}}',
                     reasoning="", finish_reason="stop", latency_s=0.1, ok=True,
                     served_model="served-model-a"),
        C.CallResult(content='{"thought": "t", "final_output": "done"}',
                     reasoning="", finish_reason="stop", latency_s=0.1, ok=True,
                     served_model="served-model-a"),
    ])
    monkeypatch.setattr(AL, "call_model", lambda *a, **k: next(responses))

    res = run_agent("faketier", {"id": "x"}, _FakeHook())
    assert res.served_models == ["served-model-a", "served-model-a"]


def test_run_agent_records_distinct_served_models_across_a_proxy_route(monkeypatch):
    # Simulates a round-robin proxy (e.g. ninerouter-aux) serving a DIFFERENT model per call
    # within one run -- generate.py must be able to see this, not collapse it.
    import evals.live_gen.agent_loop as AL

    responses = iter([
        C.CallResult(content='{"thought": "t", "action": {"tool": "noop", "args": {}}}',
                     reasoning="", finish_reason="stop", latency_s=0.1, ok=True,
                     served_model="ag/model-x"),
        C.CallResult(content='{"thought": "t", "final_output": "done"}',
                     reasoning="", finish_reason="stop", latency_s=0.1, ok=True,
                     served_model="ag/model-y"),
    ])
    monkeypatch.setattr(AL, "call_model", lambda *a, **k: next(responses))

    res = run_agent("faketier", {"id": "x"}, _FakeHook())
    assert sorted(set(res.served_models)) == ["ag/model-x", "ag/model-y"]

    # And this must propagate into an unverified, non-trainable record.
    rec = schema.build_record(
        record_id="r", case_id="x", class_="c", scenario={"id": "x"}, trace=res.trace,
        checker_verdict={"verdict": "pass", "violations": []}, tier="ninerouter-aux",
        model_id="ag/aux-router", seed=1, attempt=0, latency_s=res.latency_s, cost_usd=0.0,
        prompt_sha256="abc", quarantine_reason=None, provenance={},
        served_models=res.served_models)
    assert rec["provenance_verified"] is False
    assert rec["trainable"] is False


def test_run_agent_never_records_an_empty_served_model_entry(monkeypatch):
    import evals.live_gen.agent_loop as AL

    responses = iter([
        C.CallResult(content='{"thought": "t", "final_output": "done"}',
                     reasoning="", finish_reason="stop", latency_s=0.1, ok=True,
                     served_model=None),
    ])
    monkeypatch.setattr(AL, "call_model", lambda *a, **k: next(responses))

    res = run_agent("faketier", {"id": "x"}, _FakeHook())
    assert res.served_models == []
