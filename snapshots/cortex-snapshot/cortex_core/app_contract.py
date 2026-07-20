"""cortex_core/app_contract.py — shared vocabulary between the deterministic app gate,
the fixtures, the template-injection skills, and the pack experiment.

Pure data. Imports: stdlib only. NOTHING here may import an LLM/coaching module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1

# The check vocabulary — verbatim from the final-synthesis section-4 table.
CHECK_KINDS = (
    "app_starts",        # fresh process, bind interface, fail on crash/timeout
    "buttons_work",      # HTTP-drive each action; assert request+response+VISIBLE STATE CHANGE
    "logic_works",       # positive AND negative fixtures
    "data_persists",     # hidden payload -> POST -> KILL -> restart -> row via HTTP AND sqlite file
    "schema_real",       # sqlite metadata, columns, row-count deltas
    "input_handling",    # hidden malformed/empty/dup/oversized/type-invalid -> safe failure
    "security_controls", # RUNTIME negative tests, never a code grep
    "regression",        # all prior accepted checks rerun
    "derived_value",     # a shown aggregate/metric equals the true filtered COUNT (delta-based)
    "filtered_results",  # a search/filter returns EVERY matching row and NO non-matching row
    "deletes_row",       # a guarded per-row delete: unconfirmed rejected; confirmed removes + PERSISTS
    "edits_row",         # a per-row edit changes THE targeted row (only) and the new value PERSISTS
    "auth_required",     # a protected route: no-auth AND wrong-auth rejected + no data; correct-auth serves
    "audit_trail",       # every mutation is recorded to an APPEND-ONLY log (N mutations -> >= N entries)
    "dashboard_metrics", # a /dashboard view whose total card AND each per-field card equal the true counts
    "detail_view",       # GET /<entity>/<id> shows THAT record only (not the list); bogus id -> 404
    "relation_integrity",# a child row REQUIRES a valid parent FK (bogus rejected); a join view shows the link
    "status_lifecycle",  # a status state machine: only allowed transitions persist; illegal ones rejected
    "soft_delete",       # archive HIDES a row from the active view but KEEPS it in sqlite; restore un-hides
    "assignment",        # assign sets an owner; a scoped view returns only that owner's rows; reassign moves it
    "review_approval",   # approve/reject records the approver and is TERMINAL (no second decision)
)

# Coarse failure classes — the ONLY thing a coach may ever see about a failure.
FAILURE_CLASSES = (
    "START_FAIL", "BUTTON_FAIL", "LOGIC_FAIL", "PERSISTENCE_FAIL", "SCHEMA_FAIL",
    "INVALID_INPUT_FAIL", "SECURITY_FAIL", "REGRESSION_FAIL", "DERIVED_FAIL",
    "FILTER_FAIL", "DELETE_FAIL", "EDIT_FAIL", "AUTH_FAIL", "AUDIT_FAIL", "DASHBOARD_FAIL",
    "DETAIL_FAIL", "RELATION_FAIL", "LIFECYCLE_FAIL", "SOFTDELETE_FAIL", "ASSIGN_FAIL",
    "REVIEW_FAIL", "ENV_FAIL",
)

KIND_TO_CLASS = {
    "app_starts": "START_FAIL", "buttons_work": "BUTTON_FAIL",
    "logic_works": "LOGIC_FAIL", "data_persists": "PERSISTENCE_FAIL",
    "schema_real": "SCHEMA_FAIL", "input_handling": "INVALID_INPUT_FAIL",
    "security_controls": "SECURITY_FAIL", "regression": "REGRESSION_FAIL",
    "derived_value": "DERIVED_FAIL", "filtered_results": "FILTER_FAIL",
    "deletes_row": "DELETE_FAIL", "edits_row": "EDIT_FAIL",
    "auth_required": "AUTH_FAIL", "audit_trail": "AUDIT_FAIL",
    "dashboard_metrics": "DASHBOARD_FAIL", "detail_view": "DETAIL_FAIL",
    "relation_integrity": "RELATION_FAIL", "status_lifecycle": "LIFECYCLE_FAIL",
    "soft_delete": "SOFTDELETE_FAIL", "assignment": "ASSIGN_FAIL",
    "review_approval": "REVIEW_FAIL",
}

# validate_skill's mandatory-behavioral-state-check rule (GLM fix: no gate theater).
# An app_build skill's done_checks MUST include "data_persists", OR "schema_real"
# together with a "buttons_work" write action. Otherwise the skill refuses to load.
BEHAVIORAL_STATE_PRIMARY = "data_persists"
BEHAVIORAL_STATE_FALLBACK = ("schema_real", "buttons_work")

# Placeholder prefix: any spec string "@hidden:<name>" is resolved AT GATE RUNTIME
# from the gate's seeded RNG. The concrete value never exists in any spec/prompt file.
HIDDEN_PREFIX = "@hidden:"


@dataclass(frozen=True)
class CheckResult:
    kind: str
    passed: bool
    hidden: bool          # True if this came from the holdout set
    detail: str           # harness-side diagnostics; NEVER shown to coach/student
    failure_class: str | None = None


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    results: tuple[CheckResult, ...]
    failure_class: str | None      # class of the FIRST failing check, else None
    hidden_coverage: bool          # were any hidden holdout checks run
    env_retries: int               # gate-side retries consumed (ENV_FAIL separation)
    seed: int                      # RNG seed used (recorded for reproducibility; not coach-visible)
    schema_version: int = SCHEMA_VERSION


def coach_view(verdict: GateVerdict) -> dict[str, Any]:
    """The ONLY gate output a coach/student/retry-prompt may receive.
    No hidden fixtures, no assertion text, no payload values, no per-check results."""
    return {"pass": verdict.passed, "failure_class": verdict.failure_class}


def validate_check_spec(spec: dict[str, Any]) -> list[str]:
    """Static spec lint. Returns error strings (empty = ok). Enforces, per kind:
    - kind in CHECK_KINDS (fail-closed: unknown kinds are load errors, never skips);
    - buttons_work: every action asserts a state_change (DOM/status-only is rejected);
    - logic_works: at least one positive AND one negative expectation;
    - data_persists: resource has create/read_path/table/column;
    - security_controls: at least one runtime test named (never empty);
    - regression: names a ledger_file."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return [f"check spec must be a dict, got {type(spec).__name__}"]
    kind = spec.get("kind")
    # fail-closed: an unknown/absent kind is a load error, never a silent skip.
    if kind not in CHECK_KINDS:
        return [f"unknown check kind {kind!r} (not in CHECK_KINDS)"]

    if kind == "buttons_work":
        actions = spec.get("actions")
        if not actions:
            errors.append("buttons_work requires a non-empty 'actions' list")
        else:
            for i, action in enumerate(actions):
                sc = action.get("state_change") if isinstance(action, dict) else None
                if not sc:
                    errors.append(
                        f"buttons_work action[{i}] must assert a 'state_change' "
                        f"(DOM/status-only is rejected — the visible-state-change rule)"
                    )
                elif not (sc.get("must_contain") or sc.get("must_not_contain")):
                    errors.append(
                        f"buttons_work action[{i}] 'state_change' must specify "
                        f"'must_contain' (an observable post-action change)"
                    )

    elif kind == "logic_works":
        cases = spec.get("cases") or []
        has_positive = any(isinstance(c, dict) and "has_class" in c for c in cases)
        has_negative = any(isinstance(c, dict) and "not_has_class" in c for c in cases)
        if not has_positive:
            errors.append("logic_works requires at least one positive case (has_class)")
        if not has_negative:
            errors.append("logic_works requires at least one negative case (not_has_class)")

    elif kind == "data_persists":
        resource = spec.get("resource") or {}
        for key in ("create", "read_path", "table", "column"):
            if key not in resource:
                errors.append(f"data_persists resource missing required key {key!r}")

    elif kind == "security_controls":
        tests = spec.get("tests") or []
        protected = spec.get("protected") or []
        if not tests and not protected:
            errors.append(
                "security_controls must name at least one runtime test or protected "
                "route (never empty — no code-grep substitute)"
            )

    elif kind == "regression":
        if not spec.get("ledger_file"):
            errors.append("regression must name a 'ledger_file'")

    elif kind == "derived_value":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "derived_value 'create' must be an object naming a 'method' and 'path' "
                "(the row-create endpoint the metric aggregates over)"
            )
        if not isinstance(spec.get("match_form"), dict):
            errors.append("derived_value requires a 'match_form' object (predicate-satisfying row)")
        if not isinstance(spec.get("nomatch_form"), dict):
            errors.append("derived_value requires a 'nomatch_form' object (predicate-NOT-satisfying row)")
        if not spec.get("marker_attr"):
            errors.append(
                "derived_value requires a non-empty 'marker_attr' (the machine-readable "
                "attribute, e.g. data-cortex-metric, whose integer value the check reads)"
            )

    elif kind == "filtered_results":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "filtered_results 'create' must be an object naming a 'method' and 'path' "
                "(the row-create endpoint whose rows the search filters over)"
            )
        search = spec.get("search")
        if not isinstance(search, dict) or not search.get("get_path") or not search.get("query_param"):
            errors.append(
                "filtered_results 'search' must be an object naming a 'get_path' (the search "
                "endpoint) and a 'query_param' (the query-string term parameter)"
            )
        if not isinstance(spec.get("match_form"), dict):
            errors.append("filtered_results requires a 'match_form' object (row whose searched field CONTAINS the term)")
        if not isinstance(spec.get("nomatch_form"), dict):
            errors.append("filtered_results requires a 'nomatch_form' object (row whose searched field OMITS the term)")

    elif kind == "deletes_row":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "deletes_row 'create' must be an object naming a 'method' and 'path' "
                "(the endpoint that seeds the canary row the check then deletes)"
            )
        delete = spec.get("delete")
        if not isinstance(delete, dict) or not delete.get("path"):
            errors.append(
                "deletes_row 'delete' must be an object naming at least a 'path' (the delete "
                "endpoint); id_param/confirm_param/confirm_value default to id/confirm/yes"
            )
        if not spec.get("table"):
            errors.append("deletes_row requires a 'table' (the sqlite table the row lives in)")
        if not spec.get("column"):
            errors.append("deletes_row requires a 'column' (the text column carrying the canary token)")

    elif kind == "edits_row":
        for key in ("create", "create_b"):
            c = spec.get(key)
            if not isinstance(c, dict) or not c.get("method") or not c.get("path"):
                errors.append(
                    f"edits_row '{key}' must be an object naming a 'method' and 'path' "
                    "(the two canary rows the check seeds: the one edited, and a bystander that "
                    "must stay untouched)"
                )
        edit = spec.get("edit")
        if not isinstance(edit, dict) or not edit.get("path") or not isinstance(edit.get("form"), dict):
            errors.append(
                "edits_row 'edit' must be an object naming a 'path' (the edit endpoint) and a "
                "'form' (the full-row update carrying the NEW value on the text column); "
                "id_param defaults to id"
            )
        if not spec.get("table"):
            errors.append("edits_row requires a 'table' (the sqlite table the row lives in)")
        if not spec.get("column"):
            errors.append("edits_row requires a 'column' (the text column whose value the edit changes)")

    elif kind == "auth_required":
        if not spec.get("protected_path"):
            errors.append("auth_required requires a 'protected_path' (the route that must demand auth)")
        if not spec.get("auth_header"):
            errors.append("auth_required requires an 'auth_header' (the header name carrying the token)")
        if not spec.get("auth_value"):
            errors.append("auth_required requires an 'auth_value' (the correct token value)")
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "auth_required 'create' must be an object naming a 'method' and 'path' "
                "(the open endpoint that seeds a canary row the protected route then exposes)"
            )

    elif kind == "audit_trail":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "audit_trail 'create' must be an object naming a 'method' and 'path' "
                "(the mutation the check performs, then confirms was recorded to the audit log)"
            )
        if not spec.get("audit_table"):
            errors.append("audit_trail requires an 'audit_table' (the append-only log table to read)")
        if not spec.get("detail_column"):
            errors.append("audit_trail requires a 'detail_column' (the column carrying the logged detail)")
        if not spec.get("column"):
            errors.append("audit_trail requires a 'column' (the created row's text column, for the canary)")

    elif kind == "dashboard_metrics":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "dashboard_metrics 'create' must be an object naming a 'method' and 'path' "
                "(the row-create endpoint the dashboard counts aggregate over)"
            )
        if not spec.get("get_path"):
            errors.append("dashboard_metrics requires a 'get_path' (the dashboard route to read)")
        if not spec.get("total_attr"):
            errors.append("dashboard_metrics requires a 'total_attr' (the total-count card attribute)")
        if not isinstance(spec.get("total_form"), dict):
            errors.append("dashboard_metrics requires a 'total_form' (a valid row to seed for the total)")
        cards = spec.get("cards")
        if not isinstance(cards, list):
            errors.append("dashboard_metrics requires a 'cards' list (per-field count cards)")
        else:
            for i, c in enumerate(cards):
                if not isinstance(c, dict) or not c.get("marker_attr") or \
                        not isinstance(c.get("match_form"), dict) or not isinstance(c.get("nomatch_form"), dict):
                    errors.append(f"dashboard_metrics card[{i}] needs marker_attr + match_form + nomatch_form")

    elif kind == "detail_view":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "detail_view 'create' must be an object naming a 'method' and 'path' "
                "(the endpoint that seeds the two canary rows the detail page is checked against)"
            )
        for key in ("table", "column", "detail_path_prefix", "id_marker_attr"):
            if not spec.get(key):
                errors.append(f"detail_view requires a non-empty '{key}'")

    elif kind == "relation_integrity":
        for key in ("parent_create", "child_create"):
            c = spec.get(key)
            if not isinstance(c, dict) or not c.get("method") or not c.get("path"):
                errors.append(f"relation_integrity '{key}' must name a 'method' and 'path'")
        if not isinstance(spec.get("child_valid_form"), dict):
            errors.append("relation_integrity requires a 'child_valid_form' (a valid child row minus the FK)")
        for key in ("parent_table", "parent_column", "child_fk_param", "child_view_path", "child_column"):
            if not spec.get(key):
                errors.append(f"relation_integrity requires a non-empty '{key}'")

    elif kind == "status_lifecycle":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "status_lifecycle 'create' must be an object naming a 'method' and 'path' "
                "(the endpoint that seeds the row whose status the check transitions)"
            )
        transition = spec.get("transition")
        if not isinstance(transition, dict) or not transition.get("path"):
            errors.append(
                "status_lifecycle 'transition' must be an object naming at least a 'path' (the "
                "status-change endpoint); id_param/to_param default to id/to"
            )
        for key in ("table", "column", "status_column"):
            if not spec.get(key):
                errors.append(f"status_lifecycle requires a non-empty '{key}'")
        if not spec.get("initial"):
            errors.append("status_lifecycle requires an 'initial' status (the seeded row's start state)")
        if not spec.get("legal_to"):
            errors.append("status_lifecycle requires a 'legal_to' (a status reachable from 'initial')")
        if not spec.get("illegal_to"):
            errors.append("status_lifecycle requires an 'illegal_to' (a status NOT reachable from 'initial')")

    elif kind == "soft_delete":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "soft_delete 'create' must be an object naming a 'method' and 'path' "
                "(the endpoint that seeds the canary row the check archives)"
            )
        for key in ("archive", "restore"):
            r = spec.get(key)
            if not isinstance(r, dict) or not r.get("path"):
                errors.append(f"soft_delete '{key}' must be an object naming a 'path' (id_param defaults to id)")
        for key in ("table", "column", "archived_column", "active_view_path", "archived_view_path"):
            if not spec.get(key):
                errors.append(f"soft_delete requires a non-empty '{key}'")

    elif kind == "assignment":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "assignment 'create' must be an object naming a 'method' and 'path' "
                "(the endpoint that seeds the rows the check assigns)"
            )
        assign = spec.get("assign")
        if not isinstance(assign, dict) or not assign.get("path"):
            errors.append(
                "assignment 'assign' must be an object naming a 'path' (the assign endpoint); "
                "id_param/assignee_param default to id/assignee"
            )
        scoped = spec.get("scoped_view")
        if not isinstance(scoped, dict) or not scoped.get("get_path") or not scoped.get("query_param"):
            errors.append(
                "assignment 'scoped_view' must be an object naming a 'get_path' and a 'query_param' "
                "(the scoped 'my items' view and its assignee query parameter)"
            )
        for key in ("table", "column", "assignee_column"):
            if not spec.get(key):
                errors.append(f"assignment requires a non-empty '{key}'")

    elif kind == "review_approval":
        create = spec.get("create")
        if not isinstance(create, dict) or not create.get("method") or not create.get("path"):
            errors.append(
                "review_approval 'create' must be an object naming a 'method' and 'path' "
                "(the endpoint that seeds the pending row the check decides on)"
            )
        review = spec.get("review")
        if not isinstance(review, dict) or not review.get("path"):
            errors.append(
                "review_approval 'review' must be an object naming a 'path' (the decision endpoint); "
                "id_param/decision_param/approver_param default to id/decision/approver"
            )
        for key in ("table", "column", "status_column", "approver_column"):
            if not spec.get(key):
                errors.append(f"review_approval requires a non-empty '{key}'")

    return errors
