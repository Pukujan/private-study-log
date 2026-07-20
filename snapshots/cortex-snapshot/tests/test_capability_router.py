from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from cortex_core.capability_router import route_model


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    return tmp_path


def _availability(*rows: dict) -> dict:
    return {"schema": "cortex.model_availability/1", "generated_at": "2026-07-15T00:00:00Z",
            "results": list(rows)}


def _row(tier: str, model: str, *, free: bool, live: bool = True, latency: int = 50) -> dict:
    return {"tier": tier, "model": model, "available": live, "free_to_spend": free,
            "latency_ms": latency}


def _score(ws: Path, model: str, task_type: str, n: int, rate: float) -> None:
    path = ws / "scorecards" / "scorecards.sqlite"
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE model_scorecards(
          model TEXT, provider TEXT, task_type TEXT, n_tasks INTEGER,
          verified_success_rate REAL, self_report_vs_verified_gap REAL,
          avg_cost_usd REAL, p50_latency_s REAL, avg_output_tokens REAL,
          source TEXT, window TEXT, updated_at TEXT,
          PRIMARY KEY(model,task_type,window))""")
        conn.execute("INSERT INTO model_scorecards VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (model, "family", task_type, n, rate, None, None, None, None,
                      "objective", "w1", "2026-07-15T00:00:00Z"))


def test_free_live_least_capable_qualified_executor_is_selected(ws: Path) -> None:
    availability = _availability(
        _row("zen", "big-pickle", free=True, latency=100),
        _row("qwen", "qwen3.6-35b-a3b", free=True, latency=80),
        _row("paid", "glm-5.2", free=False, latency=20),
    )
    result = route_model(
        {"role": "executor", "task_type": "code", "min_tier": "medium",
         "required_capabilities": ["tool_use"], "free_only": True},
        workspace=ws, availability=availability,
        capability_cards={
            "big-pickle": {"family": "pickle", "qualified_tier": "upper-mid",
                           "qualification_evidence": ["eval:tool-use"],
                           "qualified_at": "2026-07-14T00:00:00Z",
                           "expires_at": "2026-08-15T00:00:00Z",
                           "capabilities": ["tool_use"]},
            "qwen3.6-35b-a3b": {"family": "qwen", "qualified_tier": "medium",
                                "qualification_evidence": ["eval:tool-use"],
                                "qualified_at": "2026-07-14T00:00:00Z",
                                "expires_at": "2026-08-15T00:00:00Z",
                                "capabilities": ["tool_use"]},
            "glm-5.2": {"family": "glm", "qualified_tier": "strong",
                        "qualification_evidence": ["eval:tool-use"],
                        "qualified_at": "2026-07-14T00:00:00Z",
                        "expires_at": "2026-08-15T00:00:00Z",
                        "capabilities": ["tool_use"]},
        }, routed_at="2026-07-15T00:00:00+00:00",
    )
    assert result["outcome"] == "ROUTED"
    assert result["selected"]["model"] == "qwen3.6-35b-a3b"
    assert result["dispatch_authorized"] is False


def test_unknown_and_unlive_models_are_never_guessed_into_route(ws: Path) -> None:
    result = route_model(
        {"role": "orchestrator", "task_type": "architecture"}, workspace=ws,
        availability=_availability(
            _row("mystery", "new-magic-model", free=True),
            _row("glm", "glm-5.2", free=True, live=False),
        ), routed_at="2026-07-15T00:00:00+00:00",
    )
    assert result["outcome"] == "UNRESOLVED" and result["selected"] is None
    reasons = {reason for item in result["rejected"] for reason in item["reasons"]}
    assert "capability_card_missing" in reasons and "not_live" in reasons


def test_model_name_containing_known_strong_stem_does_not_self_qualify(ws: Path) -> None:
    result = route_model(
        {"role": "orchestrator", "task_type": "architecture"}, workspace=ws,
        availability=_availability(_row("spoof", "unverified-glm-5.2-clone", free=True)),
        capability_cards={}, routed_at="2026-07-15T00:00:00+00:00",
    )
    assert result["outcome"] == "UNRESOLVED"
    assert "capability_card_missing" in result["rejected"][0]["reasons"]
    assert result["rejected"][0]["name_classifier_advisory"]["tier"] == "strong"


def test_reviewer_requires_declared_independence_and_known_family(ws: Path) -> None:
    with pytest.raises(ValueError, match="independence boundary"):
        route_model({"role": "reviewer", "task_type": "security"}, workspace=ws,
                    availability=_availability())
    result = route_model(
        {"role": "reviewer", "task_type": "security",
         "independent_from_families": ["builder-family"]}, workspace=ws,
        availability=_availability(_row("zen", "big-pickle", free=True)),
        capability_cards={}, routed_at="2026-07-15T00:00:00+00:00",
    )
    assert result["outcome"] == "UNRESOLVED"
    assert "family_unknown" in result["rejected"][0]["reasons"]


def test_objective_scorecard_is_exact_task_type_and_floor_gated(ws: Path) -> None:
    availability = _availability(_row("zen", "big-pickle", free=True))
    cards = {"big-pickle": {"family": "pickle", "qualified_tier": "upper-mid",
                            "qualification_evidence": ["eval:security"],
                            "qualified_at": "2026-07-14T00:00:00Z",
                            "expires_at": "2026-08-15T00:00:00Z",
                            "capabilities": ["security_review"]}}
    requirements = {
        "role": "reviewer", "task_type": "security", "required_capabilities": ["security_review"],
        "independent_from_families": ["builder-family"], "require_objective_scorecard": True,
        "min_tasks": 20, "min_verified_success_rate": 0.9,
    }
    missing = route_model(requirements, workspace=ws, availability=availability,
                          capability_cards=cards, routed_at="2026-07-15T00:00:00+00:00")
    assert missing["outcome"] == "UNRESOLVED"
    assert "task_scorecard_missing" in missing["rejected"][0]["reasons"]

    _score(ws, "big-pickle", "security", 30, 0.95)
    routed = route_model(requirements, workspace=ws, availability=availability,
                         capability_cards=cards, routed_at="2026-07-15T00:00:01+00:00")
    assert routed["outcome"] == "ROUTED"
    assert routed["selected"]["scorecard"]["verified_success_rate"] == 0.95


def test_route_receipt_is_persisted_without_prompt_or_secret(ws: Path) -> None:
    result = route_model(
        {"role": "executor", "task_type": "bounded"}, workspace=ws,
        availability=_availability(_row("qwen", "qwen3:4b", free=True)),
        capability_cards={"qwen3:4b": {"family": "qwen", "qualified_tier": "weak",
                                             "qualification_evidence": ["eval:bounded"],
                                             "qualified_at": "2026-07-14T00:00:00Z",
                                             "expires_at": "2026-08-15T00:00:00Z",
                                             "capabilities": []}},
        routed_at="2026-07-15T00:00:00+00:00",
    )
    db = ws / "ops-local" / "model-routing.db"
    with sqlite3.connect(db) as conn:
        stored = json.loads(conn.execute("SELECT canonical_json FROM route WHERE route_id=?",
                                         (result["route_id"],)).fetchone()[0])
    assert stored["selected"]["model"] == "qwen3:4b"
    assert "prompt" not in stored and "output" not in stored


def test_stale_availability_or_capability_card_cannot_route(ws: Path) -> None:
    card = {"family": "qwen", "qualified_tier": "weak", "capabilities": [],
            "qualification_evidence": ["eval:bounded"],
            "qualified_at": "2026-01-01T00:00:00Z", "expires_at": "2026-02-01T00:00:00Z"}
    result = route_model(
        {"role": "executor", "task_type": "bounded", "max_availability_age_seconds": 60},
        workspace=ws,
        availability={"generated_at": "2026-07-14T00:00:00Z",
                      "results": [_row("qwen", "qwen3:4b", free=True)]},
        capability_cards={"qwen3:4b": card}, routed_at="2026-07-15T00:00:00Z",
    )
    assert result["outcome"] == "UNRESOLVED"
    reasons = result["rejected"][0]["reasons"]
    assert "availability_stale_future_or_invalid" in reasons
    assert "capability_card_stale_future_or_invalid" in reasons
