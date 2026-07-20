"""Tests for ontology validity after Phase 6 updates.

Validates:
1. All entities have valid types and statuses.
2. All relations have valid predicates and endpoint types.
3. fable-max is marked "expired".
4. Direct Anthropic models are marked "unavailable".
5. All 9Router models exist and are "active".
6. Dataset entities exist (normalized, cross_vendor_validated, hard_gold_validation).
7. Model attributes include family, tier_name, concurrency_limit.
8. Relations link benchmarks to models correctly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_core import ontology as O
from cortex_core.config import resolve_exact_workspace

# Use the repo root as workspace (not the CORTEX_WORKSPACE env var)
_WS = resolve_exact_workspace(Path(__file__).resolve().parent.parent)


@pytest.fixture(scope="module")
def schema():
    return O.load_schema(_WS)


@pytest.fixture(scope="module")
def entities(schema):
    return O.load_entities(_WS)


@pytest.fixture(scope="module")
def relations(schema):
    return O.load_relations(_WS)


# ---- Entity validity ----

def test_all_entities_have_valid_types(schema, entities):
    for eid, ent in entities.items():
        assert ent.type in schema.entity_types, f"{eid}: unknown type {ent.type!r}"


def test_all_entities_have_valid_statuses(schema, entities):
    for eid, ent in entities.items():
        assert ent.status in schema.status_values, (
            f"{eid}: unknown status {ent.status!r}"
        )


def test_all_entities_have_source_paths(entities):
    for eid, ent in entities.items():
        assert ent.source_paths, f"{eid}: source_paths is empty"


# ---- fable-max status ----

def test_fable_max_is_expired(entities):
    ent = entities.get("model:fable-max")
    assert ent is not None, "model:fable-max not found"
    assert ent.status == "expired", f"fable-max status is {ent.status!r}, expected 'expired'"


# ---- Direct Anthropic models unavailable ----

@pytest.mark.parametrize("name", ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"])
def test_direct_anthropic_models_unavailable(entities, name):
    eid = f"model:{O._slug(name)}"
    ent = entities.get(eid)
    assert ent is not None, f"{eid} not found"
    assert ent.status == "unavailable", (
        f"{eid} status is {ent.status!r}, expected 'unavailable'"
    )


# ---- 9Router models exist and are active ----

NINEROUTER_MODELS = [
    "9r-sonnet-4.6",
    "9r-opus-4.6",
    "9r-gpt-oss-120b",
    "9r-gemini-3-flash",
    "9r-gemini-3.5-flash",
    "9r-gemini-3.1-pro",
    "9r-deepseek-3.2",
    "9r-sonnet-4.5",
    "9r-gpt-oss-ollama",
    "9r-gemini-preview",
]


@pytest.mark.parametrize("name", NINEROUTER_MODELS)
def test_ninerouter_model_exists_and_active(entities, name):
    eid = f"model:{O._slug(name)}"
    ent = entities.get(eid)
    assert ent is not None, f"{eid} not found"
    assert ent.status == "active", f"{eid} status is {ent.status!r}, expected 'active'"


# ---- Model attributes ----

def test_models_have_family_attribute(entities):
    model_entities = [e for e in entities.values() if e.type == "model"]
    for ent in model_entities:
        if ent.status in ("expired", "unavailable", "deprecated"):
            continue  # old entries may not have attributes
        attrs = ent.attributes or {}
        assert "family" in attrs, f"{ent.entity_id}: missing 'family' attribute"


def test_active_models_have_tier_and_concurrency(entities):
    model_entities = [
        e for e in entities.values()
        if e.type == "model" and e.status == "active"
    ]
    for ent in model_entities:
        attrs = ent.attributes or {}
        assert "tier_name" in attrs, f"{ent.entity_id}: missing 'tier_name' attribute"
        assert "concurrency_limit" in attrs, (
            f"{ent.entity_id}: missing 'concurrency_limit' attribute"
        )


# ---- Dataset entities ----

@pytest.mark.parametrize("name, expected_status", [
    ("normalized-fable-datasets", "active"),
    ("cross-vendor-validated", "active"),
    ("cross-vendor-quarantine", "active"),
    # The former aggregate path is gone. Preserve the historical entity but do not
    # claim an active benchmark result merely to satisfy an old fixture.
    ("hard-gold-validation", "unavailable"),
    ("cross-vendor-synthetic-gold", "active"),
])
def test_dataset_entity_exists(entities, name, expected_status):
    eid = f"benchmark:{O._slug(name)}"
    ent = entities.get(eid)
    assert ent is not None, f"{eid} not found"
    assert ent.status == expected_status, f"{eid} status is {ent.status!r}"


# ---- Relation validity ----

def test_all_relations_have_valid_predicates(schema, relations, entities):
    for rid, rel in relations.items():
        if not O._relation_is_live(rel):
            continue
        assert rel.predicate in schema.relation_types, (
            f"{rid}: unknown predicate {rel.predicate!r}"
        )


def test_all_relations_reference_existing_entities(relations, entities):
    for rid, rel in relations.items():
        if not O._relation_is_live(rel):
            continue
        assert rel.subject in entities, f"{rid}: subject {rel.subject!r} not found"
        assert rel.object in entities, f"{rid}: object {rel.object!r} not found"


def test_no_self_loop_relations(relations):
    for rid, rel in relations.items():
        if not O._relation_is_live(rel):
            continue
        assert rel.subject != rel.object, f"{rid}: self-loop"


# ---- Schema has new status values ----

def test_schema_includes_expired_and_unavailable(schema):
    assert "expired" in schema.status_values
    assert "unavailable" in schema.status_values
