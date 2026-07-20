from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from cortex_core import model_catalog


def _write_probe(tmp_path, generated_at: str) -> None:
    (tmp_path / "model_availability.json").write_text(json.dumps({
        "schema": "cortex.model_availability/1",
        "generated_at": generated_at,
        "results": [
            {
                "tier": "ninerouter",
                "model": "umans/umans-glm-5.2",
                "configured": True,
                "available": True,
                "method": "models_list",
                "role": "reviewer",
                "latency_ms": 42,
                "free_to_spend": False,
            },
            {
                "tier": "opencode-zen",
                "model": "big-pickle",
                "configured": True,
                "available": True,
                "method": "models_list",
                "role": "executor",
                "latency_ms": 10,
                "free_to_spend": True,
            },
        ],
    }), encoding="utf-8")


def test_catalog_is_available_without_probe_and_never_guesses(tmp_path):
    result = model_catalog.build_model_catalog(tmp_path)
    assert result["summary"]["known_lanes"] > 0
    ninerouter = next(r for r in result["models"] if r["lane"] == "ninerouter")
    assert ninerouter["availability"] == "UNPROBED"
    assert ninerouter["model"] is None
    assert ninerouter["base_route_ready"] is False


def test_catalog_joins_fresh_probe_and_preserves_card_blocker(tmp_path, monkeypatch):
    now = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(model_catalog, "_utcnow", lambda: now)
    _write_probe(tmp_path, (now - timedelta(minutes=2)).isoformat())
    result = model_catalog.build_model_catalog(tmp_path)
    glm = next(r for r in result["models"] if r["lane"] == "ninerouter")
    assert glm["availability"] == "LIVE"
    assert glm["capability_tier"] == "strong"
    assert glm["workflow_capacity_tier"] == "strong"
    assert "exact_capability_card_missing" in glm["route_blockers"]
    pickle = next(r for r in result["models"] if r["lane"] == "opencode-zen")
    assert pickle["capability_tier"] == "upper-mid"
    assert pickle["workflow_capacity_tier"] == "mid"
    assert "implement" in pickle["stage_fit"]["within_band"]


def test_catalog_marks_old_success_stale(tmp_path, monkeypatch):
    now = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(model_catalog, "_utcnow", lambda: now)
    _write_probe(tmp_path, (now - timedelta(hours=2)).isoformat())
    result = model_catalog.build_model_catalog(tmp_path, max_availability_age_seconds=3600)
    glm = next(r for r in result["models"] if r["lane"] == "ninerouter")
    assert glm["availability"] == "STALE"
    assert "availability_stale" in glm["route_blockers"]


def test_compact_catalog_keeps_every_lane_visible(tmp_path):
    full = model_catalog.build_model_catalog(tmp_path)
    compact = model_catalog.compact_model_catalog(full)
    assert len(compact["roster"]) == full["summary"]["known_lanes"]
    assert compact["details"].startswith("cortex_dispatch_tier")


def test_existing_provider_roster_is_visible_but_never_route_authority(tmp_path, monkeypatch):
    now = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(model_catalog, "_utcnow", lambda: now)
    roster = tmp_path / "models.tiers.md"
    roster.write_text(
        "| model_id | tier | allow | status | notes |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| `umans/umans-glm-5.2` | strong | yes | live·250ms | desired driver |\n"
        "| `mystery-new-model` | medium | yes | live·20ms | unmeasured |\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_MODEL_ROSTER_PATH", str(roster))
    result = model_catalog.build_model_catalog(tmp_path, max_availability_age_seconds=10**9)
    inventory = result["provider_inventory"]
    assert inventory["summary"]["listed"] == 2
    glm = next(row for row in inventory["models"] if "glm" in row["model"])
    assert glm["capability_tier"] == "strong"
    assert glm["route_ready"] is False
    assert "exact_capability_card_required" in glm["route_blockers"]
    unknown = next(row for row in inventory["models"] if row["model"] == "mystery-new-model")
    assert unknown["capability_tier"] == "UNKNOWN"
