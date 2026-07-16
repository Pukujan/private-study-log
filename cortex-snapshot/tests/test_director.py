"""The Director cascade: cheap-to-expensive routing with the review's fixes baked in.

Verifies tier-1 rules route confidently, the MULTI-VERB / negation guard (review fix #1) escalates to
tier-4 LLM, the tier-4 model is bounded to declared skills, and every decision is logged to the
flywheel's routing log (the data tiers 2/3 train from) without ever mutating any registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cortex_core import director as D  # noqa: E402
from cortex_core import build_skills as bs  # noqa: E402


@pytest.fixture
def ws(tmp_path):
    return tmp_path


@pytest.fixture
def skills():
    return bs.load_skills(REPO)


def test_tier1_routes_a_clear_build_request(ws, skills):
    r = D.direct("track my clients and who paid", skills, llm=lambda p: "scaffold-crud-sqlite", workspace=ws)
    assert r.tier_used == 1 and r.skill_id == "scaffold-crud-sqlite" and r.confidence >= 0.8
    assert r.track == "app_build"


def test_multi_verb_escalates_to_tier4(ws, skills):
    """review fix #1: 'research how to build a tracker' vs 'build a tracker for research' are
    bag-of-words identical, so any multi-routing-verb utterance is forced to the LLM tier."""
    calls = []
    r = D.direct("research how to build a tracker for my clients", skills,
                 llm=lambda p: calls.append(p) or "scaffold-crud-sqlite", workspace=ws)
    assert r.tier_used == 4 and len(calls) == 1
    assert set(r.features["verbs"]) >= {"build", "research"}


def test_negation_escalates_to_tier4(ws, skills):
    r = D.direct("track clients but don't build a dashboard", skills,
                 llm=lambda p: "scaffold-crud-sqlite", workspace=ws)
    assert r.tier_used == 4


def test_tier4_is_bounded_to_declared_skills(ws, skills):
    # the LLM returns junk -> the router does NOT invent a route; it falls back to a real skill id
    r = D.direct("research and build and analyze something", skills,
                 llm=lambda p: "not-a-real-skill", workspace=ws)
    assert r.skill_id in skills


def test_every_decision_is_logged(ws, skills):
    D.direct("track my books", skills, llm=lambda p: "scaffold-crud-sqlite", workspace=ws)
    D.direct("build and research a thing", skills, llm=lambda p: "scaffold-crud-sqlite", workspace=ws)
    log = ws / "ops-local" / "routing-log.jsonl"
    assert log.is_file()
    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    import json
    rec = json.loads(lines[0])
    assert rec["utterance"] == "track my books" and rec["tier_used"] == 1 and "skill_id" in rec


def test_log_is_fail_open(skills, monkeypatch):
    # a broken log path must never raise out of direct() (fail-open); routing still returns a Route
    monkeypatch.setattr(D, "_routing_log_path", lambda *_: (_ for _ in ()).throw(OSError("x")))
    r = D.direct("track my clients", skills, llm=lambda p: "scaffold-crud-sqlite")
    assert r.skill_id == "scaffold-crud-sqlite"
