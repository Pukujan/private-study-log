"""Minimal driver for the vague-build harness -- spine + skill chaining. Still no smart director
/coach/vision/reaction-loop (later increments).

    vague task -> keyword router picks the fresh-build skill + detects FOLLOW-ON skills
    -> student model fills each skill's ONE json slot (follow-ons get the scaffold's fields as
       context) -> harness renders scaffold then edits it with each follow-on
    -> ONE deterministic gate over the combined, slot-generated checks -> outcome.

So "track members, show how many are active, and let me search them" builds a CRUD app WITH a
metric card AND search -- the skill library composing. The student `llm` and `gate` are injectable
so the loop is unit-testable with zero network / zero subprocess.

CLI:  cortex-build "track my members, count the active ones, let me search them" [--tier opencode]
Tiers (students): opencode=deepseek-v4-flash, opencode-zen=big-pickle, qwen35b, ...
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from cortex_core import app_gates, build_skills as bs
from cortex_core.app_contract import coach_view

# --- Dumb keyword router (placeholder for the embedding director) ---------------------------
_ROUTES: list[tuple[tuple[str, ...], str]] = [
    (("track", "tracker", "crud", "manage", "list", "store", "save", "log",
      "database", "records", "app to", "keep tabs", "inventory", "catalog"),
     "scaffold-crud-sqlite"),
]
_DEFAULT_SKILL = "scaffold-crud-sqlite"

# Follow-on (edit) skills applied ONTO a scaffold, in this order, when the utterance calls for
# them AND the skill has a slot-aware done-check generator. add-conditional-class ("color") is a
# valid skill but is NOT chained yet -- it needs its own generated checks (a quick follow-up).
_FOLLOWONS: list[tuple[str, tuple[str, ...]]] = [
    ("add-summary-metric", ("count", "how many", "number of", "total", "tally", "metric",
                            "how much", "sum of")),
    ("add-search-filter", ("search", "find", "filter", "look up", "look for", "lookup")),
    ("add-edit-record", ("edit", "update", "change an", "change a", "modify", "fix a value", "rename")),
    ("add-delete-with-confirm", ("delete", "remove", "get rid", "erase", "trash", "throw away")),
    ("add-role-gate", ("admin only", "only admin", "admins can", "require login", "log in",
                       "password protect", "password-protect", "authenticate", "authoriz",
                       "restrict access", "admin export", "admin-only")),
    ("add-audit-log", ("audit", "history of", "activity log", "keep a trail", "keep a log",
                       "log every", "record who", "track changes", "change history")),
    ("add-dashboard", ("dashboard", "overview page", "summary page", "stats overview",
                       "stats page", "overview of")),
    ("add-detail-view", ("detail page", "view a single", "view a record", "click into",
                         "open a record", "see the full record", "record detail", "detail view")),
    ("add-second-entity-relation", ("has many", "belongs to", "related", "line items",
                                    "for each", "under each", "child records", "linked to")),
    ("add-status-lifecycle", ("status", "lifecycle", "workflow state", "stage", "mark as active",
                              "state machine", "move to", "transition", "progress through")),
    ("add-soft-delete", ("archive", "soft delete", "soft-delete", "trash bin", "recycle bin",
                         "hide a record", "restore", "unarchive", "mark as deleted")),
    ("add-ownership-assignment", ("assign", "owner", "ownership", "my items", "assigned to",
                                  "responsible", "reassign", "assignee", "who owns")),
    ("add-review-approval", ("approve", "approval", "review and", "reject", "sign off", "sign-off",
                             "pending approval", "needs approval", "reviewer approves")),
]


def route(utterance: str, available: dict[str, Any]) -> str:
    """Utterance -> the fresh-build skill_id."""
    u = (utterance or "").lower()
    for keywords, skill_id in _ROUTES:
        if skill_id in available and any(k in u for k in keywords):
            return skill_id
    return _DEFAULT_SKILL if _DEFAULT_SKILL in available else next(iter(available))


def detect_followons(utterance: str, available: dict[str, Any]) -> list[str]:
    """Ordered follow-on skill_ids the utterance calls for (and that are loaded + chainable)."""
    u = (utterance or "").lower()
    return [sid for sid, kws in _FOLLOWONS if sid in available and any(k in u for k in kws)]


def _scaffold_context(scaffold_slot: dict[str, Any]) -> str:
    fields = ", ".join(f"{f['name']} ({f['type']})" for f in scaffold_slot["fields"])
    return (f"[context: the app already exists with entity '{scaffold_slot['entity']}' and these "
            f"fields: {fields}. Choose ONLY from these field names.]")


def _followon_field(skill_id: str, slot: dict[str, Any]) -> str | None:
    """The scaffold field a follow-on references (must exist in the scaffold)."""
    return slot.get("field") if skill_id in ("add-summary-metric", "add-search-filter") else None


def _is_parameterless(skill: Any) -> bool:
    """A follow-on whose slot is the empty-object schema (no properties, no required) takes no model
    input -- its slot is always `{}`. We can render it WITHOUT a student round-trip: faster (a big
    composite drops from N model calls to just the parameterized ones) and more reliable (no chance
    the model fumbles the trivial `{}`)."""
    schema = getattr(skill.slot, "schema", None) or {}
    return (schema.get("type") == "object"
            and not schema.get("properties") and not schema.get("required"))


def _gen_checks(skill_id: str, scaffold_slot: dict[str, Any], slot: dict[str, Any]) -> list[dict]:
    if skill_id == "add-summary-metric":
        return bs.summary_metric_done_checks(scaffold_slot, slot)
    if skill_id == "add-search-filter":
        return bs.search_filter_done_checks(scaffold_slot, slot)
    if skill_id == "add-delete-with-confirm":
        return bs.delete_done_checks(scaffold_slot)
    if skill_id == "add-edit-record":
        return bs.edit_done_checks(scaffold_slot)
    if skill_id == "add-role-gate":
        return bs.role_gate_done_checks(scaffold_slot)
    if skill_id == "add-audit-log":
        return bs.audit_log_done_checks(scaffold_slot)
    if skill_id == "add-dashboard":
        return bs.dashboard_done_checks(scaffold_slot)
    if skill_id == "add-detail-view":
        return bs.detail_view_done_checks(scaffold_slot)
    if skill_id == "add-second-entity-relation":
        return bs.relation_done_checks(scaffold_slot, slot)
    if skill_id == "add-status-lifecycle":
        return bs.status_lifecycle_done_checks(scaffold_slot)
    if skill_id == "add-soft-delete":
        return bs.soft_delete_done_checks(scaffold_slot)
    if skill_id == "add-ownership-assignment":
        return bs.assignment_done_checks(scaffold_slot)
    if skill_id == "add-review-approval":
        return bs.review_approval_done_checks(scaffold_slot)
    return []


# Checks that verify a once-per-APP property (not a per-skill feature): running them multiple
# times over the same combined app is redundant. Feature checks (buttons_work, derived_value,
# filtered_results, logic_works) are kept per-skill.
_SINGLETON_KINDS = frozenset({"app_starts", "data_persists", "schema_real",
                              "input_handling", "security_controls", "regression"})


def _merge_checks(base: list[dict], extra: list[dict]) -> list[dict]:
    """Concatenate check lists, keeping the FIRST of each once-per-app kind (so a chained app isn't
    gated with 3 redundant data_persists/regression checks) and every distinct feature check."""
    out: list[dict] = []
    seen: set[str] = set()
    for c in list(base) + list(extra):
        k = c.get("kind", "")
        if k in _SINGLETON_KINDS:
            if k in seen:
                continue
            seen.add(k)
        out.append(c)
    return out


# Friendly model name -> (dispatch tier that supplies endpoint+key, model_override id). The dispatch
# already honors OpenRouter Retry-After backoff, so paced OpenRouter free lanes just work. See
# docs/MODELS-TIER-LIST.md + docs/OPERATING-PLAN.md. Only FREE lanes here (+ cheap opencode-go).
_TIER_ALIASES: dict[str, tuple[str, str | None]] = {
    "laguna-m1":     ("openrouter", "poolside/laguna-m.1:free"),        # builder (72.5% SWE-Verified)
    "laguna-xs":     ("openrouter", "poolside/laguna-xs-2.1:free"),     # fast bulk
    "north-mini":    ("openrouter", "cohere/north-mini-code:free"),     # fast bulk / tool calls
    "nemotron-ultra":("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),  # free frontier reviewer
    "nemotron-super":("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    "hy3":           ("openrouter", "tencent/hy3:free"),               # free frontier (agentic)
    "gemma-4":       ("openrouter", "google/gemma-4-31b-it:free"),
    "gpt-oss-120b":  ("openrouter", "openai/gpt-oss-120b:free"),
    "aux":           ("ninerouter", "aux"),                            # 9router free round-robin pool
    "big-pickle":    ("opencode-zen", None),                           # always-on default (no burst limit)
    "deepseek-flash-free": ("opencode-zen", "deepseek-v4-flash-free"),
    "mimo-free":     ("opencode-zen", "mimo-v2.5-free"),
}


def _resolve_tier(tier: str) -> tuple[str, str | None]:
    """Map a friendly model name to (dispatch_tier, model_override); passthrough for raw tiers."""
    return _TIER_ALIASES.get(tier, (tier, None))


def _guard_no_paid_9router(base: str, override: str | None) -> None:
    """User rule: NEVER call a PAID 9router model (only its ~25 free ones). Free = `aux` or any id
    carrying a `:free`/`-free`/`/free` marker. Anything else on 9router is paid -> refuse loudly."""
    if base in ("ninerouter", "9router", "ninerouter-aux") and override:
        free = override == "aux" or ":free" in override or override.endswith("-free") or "/free" in override
        if not free:
            raise ValueError(f"BLOCKED: 9router model {override!r} is PAID; only free 9router lanes "
                             "(aux / *:free) are allowed (user rule).")


def _default_student(tier: str) -> Callable[[str], str]:
    # Dispatch primitives come from the PUBLIC-safe model_dispatch shim, not the private
    # judge module, so this path runs without judge.py present. (2026-07-14 extraction.)
    from cortex_core.model_dispatch import apply_min_max_tokens, llm_complete

    base, override = _resolve_tier(tier)
    _guard_no_paid_9router(base, override)

    def student(prompt: str) -> str:
        return llm_complete(prompt, base, max_tokens=apply_min_max_tokens(base, 6000),
                            model_override=override) or ""

    return student


def _record_run(skill_id: str, passed: bool, workspace: str | Path | None = None) -> None:
    """Append one raw build outcome to a GITIGNORED run log (flywheel telemetry). Never mutates the
    committed skill definition; best-effort."""
    try:
        from cortex_core.config import resolve_workspace
        root = Path(resolve_workspace(workspace))
        out = root / "ops-local" / "skill-outcomes.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"skill_id": skill_id, "passed": bool(passed)}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _fill_slot(skill: Any, base_prompt: str, student: Callable[[str], str], retries: int,
               extra_validate: Callable[[dict], list[str]] | None = None
               ) -> tuple[dict | None, list[str], int]:
    """Ask the student for the skill's slot; validate (+ optional extra check); retry with the
    validator's reason folded into the prompt. Returns (slot|None, last_errors, attempts)."""
    prompt = base_prompt
    errs: list[str] = []
    attempts = 0
    for _ in range(max(1, retries + 1)):
        attempts += 1
        cand = bs.extract_slot_json(student(prompt))
        ok, errs = bs.validate_slot(skill, cand or {})
        if ok and extra_validate is not None:
            more = extra_validate(cand or {})
            if more:
                ok, errs = False, more
        if ok:
            return cand, [], attempts
        prompt = (base_prompt + "\n\n[your previous answer was rejected: " + "; ".join(errs)
                  + ". Output ONE corrected JSON object, nothing else.]")
    return None, errs, attempts


def drive(utterance: str, *, tier: str = "opencode", out_dir: str | Path | None = None,
          llm: Callable[[str], str] | None = None,
          gate: Callable[[Path, list[dict]], Any] | None = None,
          retries: int = 1, workspace: str | Path | None = None,
          chain: bool = True, use_director: bool = False,
          primary_skill_id: str | None = None) -> dict[str, Any]:
    """Run one vague task end to end (scaffold + any detected follow-ons). `llm`/`gate` injectable.
    `use_director` routes via the Director cascade (cortex_core/director.py: rules -> LLM fallback,
    multi-verb guard, logged) instead of the bare keyword router. `primary_skill_id` lets an
    OUTER orchestrator (cortex_core/hybrid_build.py) that already routed pass its pick in, so the
    Director's decision is not silently re-routed here."""
    skills = bs.load_skills(workspace)
    if not skills:
        return {"status": "no_skill", "utterance": utterance}
    if primary_skill_id is not None and primary_skill_id in skills:
        primary_id = primary_skill_id
    elif use_director:
        from cortex_core import director
        primary_id = director.direct(utterance, skills, llm=llm, workspace=workspace).skill_id
    else:
        primary_id = route(utterance, skills)
    primary = skills[primary_id]
    if getattr(primary, "role", "follow_on") != "fresh_build":
        # terra HIGH #4: a follow-on skill EDITS an existing scaffold; executing it as the
        # primary on a blank dir is a RenderError waiting to happen. Whatever router chose
        # it (including a tier-4 LLM), the executor refuses honestly instead of crashing.
        return {"status": "bad_primary", "skill_id": primary_id,
                "reason": f"skill {primary_id!r} has role "
                          f"{getattr(primary, 'role', 'follow_on')!r}; only a fresh_build "
                          "skill may be the primary (terra finding #4)",
                "utterance": utterance}
    student = llm or _default_student(tier)
    run_gate = gate or app_gates.run_done_checks

    p_slot, errs, attempts = _fill_slot(primary, bs.build_step_prompt(primary, utterance),
                                        student, retries)
    if p_slot is None:
        return {"status": "bad_slot", "skill_id": primary_id, "errors": errs,
                "attempts": attempts, "utterance": utterance}

    app_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="cortex_build_")) / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    bs.render_skill(primary, p_slot, app_dir)
    checks = bs.resolve_done_checks(primary, p_slot)
    applied = [primary_id]
    skipped: list[dict] = []

    if chain and primary_id == "scaffold-crud-sqlite":
        field_names = {f["name"] for f in p_slot["fields"]}
        for fo_id in detect_followons(utterance, skills):
            fo = skills[fo_id]
            fo_prompt = bs.build_step_prompt(fo, utterance) + "\n\n" + _scaffold_context(p_slot)

            if _is_parameterless(fo):
                # No model input needed -- the slot is always {}. Skip the student round-trip.
                fo_slot = {}
            else:
                def _field_check(s: dict, _sid: str = fo_id) -> list[str]:
                    f = _followon_field(_sid, s)
                    return [] if (f is None or f in field_names) else [
                        f"field {f!r} is not one of the app's fields {sorted(field_names)}"]

                fo_slot, fo_errs, _ = _fill_slot(fo, fo_prompt, student, retries,
                                                 extra_validate=_field_check)
                if fo_slot is None:
                    skipped.append({"skill_id": fo_id, "reason": fo_errs})
                    continue
            bs.render_skill(fo, fo_slot, app_dir)
            checks = _merge_checks(checks, _gen_checks(fo_id, p_slot, fo_slot))
            applied.append(fo_id)

    verdict = run_gate(app_dir, checks)
    for sid in applied:
        _record_run(sid, bool(verdict.passed), workspace)
    # Capture the build trace for the self-improving corpus (fail-open; only gate-verified traces
    # are later distilled). The model authored no code, but the (task -> slot -> gate verdict) triple
    # is the calibration signal.
    try:
        from cortex_core import trace_capture
        trace_capture.capture_build(utterance, tier, json.dumps(p_slot), verdict,
                                    role="builder", workspace=workspace)
    except Exception:  # noqa: BLE001
        pass
    return {
        "status": "built", "skill_id": primary_id, "skills": applied, "skipped": skipped,
        "slot": p_slot, "attempts": attempts, "app_dir": str(app_dir),
        "passed": bool(verdict.passed), "failure_class": verdict.failure_class,
        "verdict": coach_view(verdict),
        "checks": [(r.kind, bool(r.passed)) for r in verdict.results],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cortex-build",
        description="vague task -> a cheap model builds a deterministic-gate-verified app (with chaining)")
    ap.add_argument("utterance", help="the vague task, e.g. 'track my members, count the active ones, let me search'")
    ap.add_argument("--tier", default="opencode",
                    help="student tier: opencode(=deepseek-v4-flash), opencode-zen(=big-pickle), qwen35b, ...")
    ap.add_argument("--out", default=None)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--no-chain", dest="chain", action="store_false")
    a = ap.parse_args(argv)

    r = drive(a.utterance, tier=a.tier, out_dir=a.out, retries=a.retries, chain=a.chain)
    if r["status"] == "no_skill":
        print("no skills are loaded -- nothing to build with.")
        return 2
    if r["status"] == "bad_slot":
        print(f"[bad slot] tier={a.tier} could not produce a valid slot for skill={r['skill_id']} "
              f"after {r['attempts']} attempt(s): {r['errors']}")
        return 3
    status = "PASS" if r["passed"] else f"FAIL ({r['failure_class']})"
    print(f"=> {status} | skills={'+'.join(r['skills'])} | tier={a.tier} | app at: {r['app_dir']}")
    for kind, ok in r["checks"]:
        print(f"   {'PASS' if ok else 'FAIL'}  {kind}")
    if r["skipped"]:
        print("skipped follow-ons:", [s["skill_id"] for s in r["skipped"]])
    print("slot:", json.dumps(r["slot"]))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
