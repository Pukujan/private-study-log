import json
from pathlib import Path

from cortex_core.kedb_incident import load_incident, validate_incident


REPO_ROOT = Path(__file__).resolve().parent.parent


def valid_incident():
    return {
        "schema_version": 1,
        "id": "DRIVER-20260715-001",
        "failure_class": "DRIVER",
        "driver": "Hermes",
        "model": "umans/umans-glm-5.2",
        "runtime_version": "unknown",
        "cortex_version": "not connected",
        "task": "Build a SalesOps dashboard",
        "starting_state": "No active Cortex run",
        "expected_behavior": "Use the governed product-build route",
        "observed_behavior": "Built against a visible generic fixture",
        "tool_trace": [{"kind": "transcript", "locator": "session:1", "summary": "No Cortex calls"}],
        "artifact_hashes": {"app.py": "a" * 64},
        "severity": "HIGH",
        "why_existing_tests_missed_it": "Tests covered components, not the driver path",
        "root_cause": "MCP configuration and tool-surface failure",
        "fix": "Add external preflight and cross-driver replay",
        "deterministic_reproducer": "Run the frozen driver-path fixture",
        "cross_driver_replay": "pending",
        "oracle_candidate": False,
        "human_decision": "Expected behavior approved",
        "status": "observed",
    }


def test_valid_incident_and_repo_salesops_entry():
    assert validate_incident(valid_incident()) == (True, [])
    path = REPO_ROOT / "kedb/incidents/DRIVER-20260715-001-hermes-salesops.json"
    loaded = load_incident(path)
    assert loaded["failure_class"] == "DRIVER"
    assert loaded["oracle_candidate"] is False

    ops = load_incident(
        REPO_ROOT / "kedb/incidents/OPS-20260715-002-mcp-status-git-hang.json"
    )
    assert ops["failure_class"] == "OPS"
    assert ops["status"] == "fixed"
    assert ops["oracle_candidate"] is False

    research = load_incident(
        REPO_ROOT / "kedb/incidents/RAG-20260715-003-hermes-salesops-research.json"
    )
    assert research["failure_class"] == "RAG"
    assert research["status"] == "expected_behavior_approved"
    assert research["oracle_candidate"] is False


def test_incident_rejects_invalid_hash_and_premature_oracle():
    record = valid_incident()
    record["artifact_hashes"] = {"app.py": "builder-says-pass"}
    record["oracle_candidate"] = True
    ok, problems = validate_incident(record)
    assert not ok
    assert any("sha256" in p for p in problems)
    assert any("oracle_candidate true" in p for p in problems)


def test_incident_rejects_unknown_fields():
    record = valid_incident()
    record["self_certified"] = True
    ok, problems = validate_incident(record)
    assert not ok
    assert any("unknown field" in p for p in problems)
