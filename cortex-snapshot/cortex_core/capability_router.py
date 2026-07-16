"""Deterministic, evidence-aware model route planning (no completion dispatch).

Availability, capability, independence, and measured task performance are separate facts. This
router joins them and emits an auditable plan; it never guesses an unknown model into ``medium`` and
never spends money. Dispatch remains a later, explicit action.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .config import resolve_workspace_override
from . import model_tiers


ROLES = {"orchestrator", "executor", "reviewer", "judge"}
TIER_RANK = {"weak": 1, "medium": 2, "upper-mid": 3, "strong": 4}
ROLE_MINIMUM = {"orchestrator": "strong", "executor": "weak",
                "reviewer": "upper-mid", "judge": "upper-mid"}
REQUIREMENT_FIELDS = {
    "role", "task_type", "min_tier", "required_capabilities", "free_only",
    "independent_from_families", "independent_from_models", "require_objective_scorecard",
    "min_verified_success_rate", "min_tasks", "max_candidates",
    "max_availability_age_seconds",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _validate(requirements: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(requirements, dict):
        raise ValueError("requirements must be an object")
    unknown = set(requirements) - REQUIREMENT_FIELDS
    if unknown:
        raise ValueError(f"unknown requirement fields: {sorted(unknown)}")
    role = requirements.get("role")
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    minimum = requirements.get("min_tier") or ROLE_MINIMUM[role]
    if minimum not in TIER_RANK:
        raise ValueError(f"min_tier must be one of {sorted(TIER_RANK)}")
    task_type = requirements.get("task_type")
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task_type must be non-empty")
    caps = requirements.get("required_capabilities", [])
    if not isinstance(caps, list) or any(not isinstance(item, str) or not item for item in caps):
        raise ValueError("required_capabilities must be a list of non-empty strings")
    families = requirements.get("independent_from_families", [])
    models = requirements.get("independent_from_models", [])
    if not isinstance(families, list) or not isinstance(models, list):
        raise ValueError("independence exclusions must be lists")
    if role in {"reviewer", "judge"} and not families and not models:
        raise ValueError("reviewer/judge routing requires an explicit independence boundary")
    min_rate = requirements.get("min_verified_success_rate", 0.0)
    min_tasks = requirements.get("min_tasks", 20)
    max_candidates = requirements.get("max_candidates", 5)
    max_age = requirements.get("max_availability_age_seconds", 3600)
    if isinstance(min_rate, bool) or not isinstance(min_rate, (int, float)) or not 0 <= min_rate <= 1:
        raise ValueError("min_verified_success_rate must be between 0 and 1")
    if isinstance(min_tasks, bool) or not isinstance(min_tasks, int) or min_tasks < 1:
        raise ValueError("min_tasks must be an integer >= 1")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise ValueError("max_candidates must be an integer >= 1")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 1:
        raise ValueError("max_availability_age_seconds must be an integer >= 1")
    return {
        "role": role, "task_type": task_type.strip(), "min_tier": minimum,
        "required_capabilities": sorted(set(caps)),
        "free_only": bool(requirements.get("free_only", False)),
        "independent_from_families": sorted(set(families)),
        "independent_from_models": sorted(set(models)),
        "require_objective_scorecard": bool(requirements.get("require_objective_scorecard", False)),
        "min_verified_success_rate": float(min_rate), "min_tasks": min_tasks,
        "max_candidates": max_candidates,
        "max_availability_age_seconds": max_age,
    }


def _load_availability(workspace: Path) -> dict[str, Any]:
    path = workspace / "model_availability.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"model availability is missing or invalid: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("model availability must contain a results list")
    return data


def _load_cards(workspace: Path) -> dict[str, dict[str, Any]]:
    path = workspace / "model-capabilities.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cards = data.get("models", {}) if isinstance(data, dict) else {}
    return cards if isinstance(cards, dict) else {}


def _scorecard(workspace: Path, model: str, task_type: str) -> dict[str, Any] | None:
    db = workspace / "scorecards" / "scorecards.sqlite"
    if not db.is_file():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT n_tasks,verified_success_rate,source,updated_at FROM model_scorecards "
                "WHERE model=? AND task_type=? ORDER BY updated_at DESC LIMIT 1",
                (model, task_type),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {"n_tasks": row[0], "verified_success_rate": row[1],
            "source": row[2], "updated_at": row[3]}


def route_model(requirements: dict[str, Any], *, workspace: str | Path,
                availability: dict[str, Any] | None = None,
                capability_cards: dict[str, dict[str, Any]] | None = None,
                routed_at: str | None = None) -> dict[str, Any]:
    """Return and persist a route plan. No model call is made."""
    req = _validate(requirements)
    ws = resolve_workspace_override(workspace)
    availability = availability or _load_availability(ws)
    cards = capability_cards if capability_cards is not None else _load_cards(ws)
    routed_at = routed_at or datetime.now(timezone.utc).isoformat()
    try:
        route_time = datetime.fromisoformat(routed_at.replace("Z", "+00:00"))
        availability_time = datetime.fromisoformat(
            str(availability.get("generated_at") or "").replace("Z", "+00:00"))
        if route_time.tzinfo is None or availability_time.tzinfo is None:
            raise ValueError
        route_time = route_time.astimezone(timezone.utc)
        availability_time = availability_time.astimezone(timezone.utc)
        availability_fresh = (
            availability_time <= route_time
            and (route_time - availability_time).total_seconds()
            <= req["max_availability_age_seconds"]
        )
    except ValueError:
        availability_fresh = False
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    min_rank = TIER_RANK[req["min_tier"]]
    excluded_families = set(req["independent_from_families"])
    excluded_models = set(req["independent_from_models"])
    required_caps = set(req["required_capabilities"])

    for raw in availability.get("results", []):
        if not isinstance(raw, dict):
            continue
        dispatch_tier = str(raw.get("tier") or "")
        model = str(raw.get("model") or "")
        identity = model or dispatch_tier
        card = cards.get(model)
        name_tier, name_provenance = model_tiers.tier_and_provenance(model)
        if not isinstance(card, dict):
            card = {}
        tier = card.get("qualified_tier")
        qualification_evidence = card.get("qualification_evidence")
        family = card.get("family")
        capabilities = set(card.get("capabilities") or [])
        reasons: list[str] = []
        if raw.get("available") is not True:
            reasons.append("not_live")
        if not availability_fresh:
            reasons.append("availability_stale_future_or_invalid")
        if not card:
            reasons.append("capability_card_missing")
        if tier not in TIER_RANK:
            reasons.append("qualified_tier_missing_or_invalid")
        elif TIER_RANK[tier] < min_rank:
            reasons.append("below_min_tier")
        if (not isinstance(qualification_evidence, list) or not qualification_evidence
                or any(not isinstance(item, str) or not item for item in qualification_evidence)):
            reasons.append("qualification_evidence_missing")
        try:
            qualified_at = datetime.fromisoformat(
                str(card.get("qualified_at") or "").replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(
                str(card.get("expires_at") or "").replace("Z", "+00:00"))
            if qualified_at.tzinfo is None or expires_at.tzinfo is None:
                raise ValueError
            card_current = (qualified_at.astimezone(timezone.utc) <= route_time
                            < expires_at.astimezone(timezone.utc))
        except ValueError:
            card_current = False
        if not card_current:
            reasons.append("capability_card_stale_future_or_invalid")
        if req["free_only"] and raw.get("free_to_spend") is not True:
            reasons.append("not_free_to_spend")
        if identity in excluded_models or model in excluded_models:
            reasons.append("model_not_independent")
        if excluded_families:
            if not family:
                reasons.append("family_unknown")
            elif family in excluded_families:
                reasons.append("family_not_independent")
        missing_caps = sorted(required_caps - capabilities)
        if missing_caps:
            reasons.append("missing_capabilities:" + ",".join(missing_caps))
        score = _scorecard(ws, model, req["task_type"])
        if req["require_objective_scorecard"]:
            if not score:
                reasons.append("task_scorecard_missing")
            else:
                if score["n_tasks"] < req["min_tasks"]:
                    reasons.append("task_scorecard_too_sparse")
                rate = score.get("verified_success_rate")
                if rate is None or rate < req["min_verified_success_rate"]:
                    reasons.append("verified_success_below_floor")
        candidate = {
            "dispatch_tier": dispatch_tier, "model": model, "family": family,
            "capability_tier": tier, "tier_provenance": "capability_card",
            "name_classifier_advisory": {"tier": name_tier, "provenance": name_provenance},
            "qualification_evidence": qualification_evidence or [],
            "free_to_spend": raw.get("free_to_spend") is True,
            "latency_ms": raw.get("latency_ms"), "scorecard": score,
        }
        if reasons:
            rejected.append({**candidate, "reasons": reasons})
        else:
            accepted.append(candidate)

    def rank(item: dict[str, Any]) -> tuple:
        rate = (item.get("scorecard") or {}).get("verified_success_rate")
        latency = item.get("latency_ms")
        return (not item["free_to_spend"], TIER_RANK[item["capability_tier"]],
                -(rate if isinstance(rate, (int, float)) else -1.0),
                latency if isinstance(latency, int) else 10**12,
                item["dispatch_tier"])

    accepted.sort(key=rank)
    draft = {
        "schema_version": 1, "requirements": req,
        "availability_generated_at": availability.get("generated_at"),
        "qualified": accepted[:req["max_candidates"]], "rejected": rejected,
        "selected": accepted[0] if accepted else None,
        "outcome": "ROUTED" if accepted else "UNRESOLVED",
        "routed_at": routed_at,
    }
    route_id = "mr_" + hashlib.sha256(_canonical(draft).encode()).hexdigest()[:24]
    result = {"route_id": route_id, **draft,
              "dispatch_authorized": False,
              "next": ("explicitly dispatch the selected tier and bind its call to this route_id"
                       if accepted else "probe or qualify additional models; do not guess")}
    _store_route(ws, result)
    return result


def _store_route(workspace: Path, route: dict[str, Any]) -> None:
    path = workspace / "ops-local" / "model-routing.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = _canonical(route)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS route(route_id TEXT PRIMARY KEY, canonical_json TEXT NOT NULL)")
        row = conn.execute("SELECT canonical_json FROM route WHERE route_id=?", (route["route_id"],)).fetchone()
        if row and row[0] != canonical:
            raise ValueError("immutable model route identity collision")
        if not row:
            conn.execute("INSERT INTO route(route_id,canonical_json) VALUES(?,?)",
                         (route["route_id"], canonical))
