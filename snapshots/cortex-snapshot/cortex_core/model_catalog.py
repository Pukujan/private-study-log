"""Immediate, key-free model roster and availability view.

The routing data in Cortex has several independent axes: dispatch lanes,
measured capability classes, workflow capacity bands, live probes, and exact
capability cards.  This module joins those axes for visibility only.  It never
calls a model, never resolves or returns credentials, and never upgrades stale
or unknown evidence into a routable claim.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capacity import load_policy, model_tier as workflow_model_tier, tier_rank
from .model_dispatch import dispatch_lane_names
from .model_tiers import tier_and_provenance


SCHEMA = "cortex.model_catalog/1"
_CAPABILITY_TO_WORKFLOW = {
    "strong": "strong",
    "upper-mid": "mid",
    "medium": "mid",
    "weak": "small",
}
_ROSTER_ROW = re.compile(
    r"^\|\s*`(?P<model>[^`]+)`\s*\|\s*(?P<tier>[^|]+?)\s*\|\s*"
    r"(?P<allow>[^|]+?)\s*\|\s*(?P<status>[^|]+?)\s*\|\s*(?P<notes>.*?)\s*\|\s*$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _workflow_fit(model: str, capability_tier: str) -> tuple[str | None, str]:
    exact = workflow_model_tier(model)
    if exact:
        return exact, "capacity_policy_exact_model_match"
    translated = _CAPABILITY_TO_WORKFLOW.get(capability_tier)
    if translated:
        return translated, "explicit_capability_to_capacity_translation"
    return None, "unknown"


def _stage_fit(workflow_tier: str | None) -> dict[str, list[str]]:
    fit = {"within_band": [], "below_floor": [], "above_ceiling": []}
    if not workflow_tier:
        return fit
    policy = load_policy()
    rank = tier_rank(workflow_tier, policy)
    for stage, band in policy["stages"].items():
        if rank < tier_rank(band["min"], policy):
            fit["below_floor"].append(stage)
        elif rank > tier_rank(band["max"], policy):
            fit["above_ceiling"].append(stage)
        else:
            fit["within_band"].append(stage)
    return fit


def _provider_roster(workspace: Path, now: datetime, max_age: int) -> dict[str, Any]:
    explicit = os.environ.get("CORTEX_MODEL_ROSTER_PATH", "").strip()
    candidates = [Path(explicit)] if explicit else []
    candidates.extend((workspace / ".cortex" / "models.tiers.md", workspace / "models.tiers.md"))
    path = next((item.expanduser().resolve() for item in candidates if item.is_file()), None)
    if path is None:
        return {
            "present": False,
            "source": None,
            "fresh": False,
            "age_seconds": None,
            "summary": {"listed": 0, "live_reported": 0, "route_ready": 0},
            "models": [],
        }
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = max(0, int((now - modified).total_seconds()))
    fresh = modified <= now and age <= max_age
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        match = _ROSTER_ROW.match(line)
        if not match:
            continue
        raw = {key: value.strip() for key, value in match.groupdict().items()}
        capability_tier, provenance = tier_and_provenance(raw["model"])
        allow = raw["allow"].lower() == "yes"
        reported_live = raw["status"].lower().startswith("live")
        blockers: list[str] = []
        if not fresh:
            blockers.append("provider_roster_stale")
        if not allow:
            blockers.append("provider_roster_disallowed")
        if not reported_live:
            blockers.append("provider_model_not_live_in_snapshot")
        if capability_tier in {"UNKNOWN", "utility"}:
            blockers.append("dispatch_capability_not_qualified")
        # Provider inventory never substitutes for an exact route capability card.
        blockers.append("exact_capability_card_required")
        rows.append({
            "model": raw["model"],
            "declared_tier": raw["tier"],
            "declared_tier_authority": "provider_roster_advisory",
            "allow": allow,
            "reported_status": raw["status"],
            "reported_live": reported_live,
            "capability_tier": capability_tier,
            "capability_provenance": provenance,
            "route_ready": False,
            "route_blockers": blockers,
        })
    return {
        "present": True,
        "source": str(path),
        "modified_at": modified.isoformat(),
        "fresh": fresh,
        "age_seconds": age,
        "summary": {
            "listed": len(rows),
            "live_reported": sum(1 for row in rows if row["reported_live"]),
            "allowed": sum(1 for row in rows if row["allow"]),
            "evidence_tier_known": sum(
                1 for row in rows if row["capability_tier"] != "UNKNOWN"),
            "route_ready": 0,
        },
        "models": rows,
        "authority_note": (
            "This is the existing provider-discovery roster, not route authority. Its declared "
            "tier/status remain advisory; fresh exact probes, cards, scorecards, and call binding "
            "are still required."
        ),
    }


def build_model_catalog(
    workspace: str | Path,
    *,
    max_availability_age_seconds: int = 3600,
) -> dict[str, Any]:
    """Join the durable roster to the latest local probe snapshot.

    The roster is always returned even when the workspace has never been
    probed.  In that case every lane is explicitly ``UNPROBED``.  A probe older
    than ``max_availability_age_seconds`` is ``STALE`` even if it once passed.
    """
    if max_availability_age_seconds < 1:
        raise ValueError("max_availability_age_seconds must be >= 1")
    ws = Path(workspace).expanduser().resolve()
    availability = _read_json(ws / "model_availability.json")
    generated = _parse_time(availability.get("generated_at"))
    now = _utcnow()
    age_seconds: int | None = None
    snapshot_fresh = False
    if generated is not None:
        age_seconds = max(0, int((now - generated).total_seconds()))
        snapshot_fresh = generated <= now and age_seconds <= max_availability_age_seconds

    raw_results = availability.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    by_lane = {
        str(item.get("tier")): item
        for item in results
        if isinstance(item, dict) and item.get("tier")
    }
    lanes = sorted(set(dispatch_lane_names()) | set(by_lane))
    cards_doc = _read_json(ws / "model-capabilities.json")
    raw_cards = cards_doc.get("models")
    cards = raw_cards if isinstance(raw_cards, dict) else {}

    rows: list[dict[str, Any]] = []
    for lane in lanes:
        probe = by_lane.get(lane, {})
        model = str(probe.get("model") or "")
        capability_tier, capability_provenance = tier_and_provenance(model or lane)
        workflow_tier, workflow_provenance = _workflow_fit(model or lane, capability_tier)
        if not probe:
            availability_state = "UNPROBED"
        elif not snapshot_fresh:
            availability_state = "STALE"
        elif probe.get("available") is True:
            availability_state = "LIVE"
        else:
            availability_state = "UNAVAILABLE"

        card = cards.get(model) if model else None
        blockers: list[str] = []
        if availability_state != "LIVE":
            blockers.append("availability_" + availability_state.lower())
        if capability_tier in {"UNKNOWN", "utility"}:
            blockers.append("dispatch_capability_not_qualified")
        if not isinstance(card, dict):
            blockers.append("exact_capability_card_missing")

        rows.append({
            "lane": lane,
            "model": model or None,
            "role": probe.get("role"),
            "configured": probe.get("configured") if probe else None,
            "availability": availability_state,
            "availability_method": probe.get("method"),
            "latency_ms": probe.get("latency_ms"),
            "free_to_spend": probe.get("free_to_spend"),
            "capability_tier": capability_tier,
            "capability_provenance": capability_provenance,
            "workflow_capacity_tier": workflow_tier,
            "workflow_capacity_provenance": workflow_provenance,
            "stage_fit": _stage_fit(workflow_tier),
            "exact_capability_card_present": isinstance(card, dict),
            "base_route_ready": not blockers,
            "route_blockers": blockers,
        })

    counts: dict[str, int] = {}
    for row in rows:
        state = row["availability"]
        counts[state] = counts.get(state, 0) + 1
    provider_inventory = _provider_roster(ws, now, max_availability_age_seconds)
    return {
        "schema": SCHEMA,
        "catalog_generated_at": now.isoformat(),
        "availability_snapshot": {
            "generated_at": availability.get("generated_at"),
            "age_seconds": age_seconds,
            "fresh": snapshot_fresh,
            "max_age_seconds": max_availability_age_seconds,
        },
        "summary": {
            "known_lanes": len(rows),
            "availability_counts": counts,
            "base_route_ready": sum(1 for row in rows if row["base_route_ready"]),
        },
        "models": rows,
        "provider_inventory": provider_inventory,
        "authority_note": (
            "This catalog is visibility, not dispatch authority. LIVE means the last probe is "
            "fresh; routing still applies exact capability cards, task scorecards, independence, "
            "and route-to-call binding. UNKNOWN and stale evidence are never guessed upward."
        ),
    }


def compact_model_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Small status payload that still makes every lane immediately visible."""
    return {
        "schema": catalog["schema"],
        "availability_snapshot": catalog["availability_snapshot"],
        "summary": catalog["summary"],
        "roster": [
            {
                "lane": row["lane"],
                "model": row["model"],
                "availability": row["availability"],
                "capability_tier": row["capability_tier"],
                "free_to_spend": row["free_to_spend"],
                "base_route_ready": row["base_route_ready"],
            }
            for row in catalog["models"]
        ],
        "provider_inventory": {
            key: catalog["provider_inventory"].get(key)
            for key in ("present", "source", "modified_at", "fresh", "age_seconds", "summary")
        },
        "details": "cortex_dispatch_tier(action='catalog')",
    }
