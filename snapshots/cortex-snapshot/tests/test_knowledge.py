import json
from pathlib import Path

import pytest

from cortex_core.knowledge import composite_search


def _workspace(root: Path, name: str) -> Path:
    ws = root / name
    (ws / "library" / "cortex-library" / "search").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return ws


def test_composite_search_queries_brain_tenant_and_structured_stores(tmp_path):
    brain = _workspace(tmp_path, "brain")
    tenant = _workspace(tmp_path, "tenant")
    (brain / "docs" / "cortex-1").mkdir(parents=True)
    (brain / "docs" / "cortex-1" / "caseos.md").write_text(
        "# CaseOS patterns\n\nLitigation matter workflow and deadline provenance.", encoding="utf-8"
    )
    (tenant / "docs" / "cortex-1").mkdir(parents=True)
    (tenant / "docs" / "cortex-1" / "matter.md").write_text(
        "# Tenant matter\n\nCaseOS client workflow requirements.", encoding="utf-8"
    )
    (tenant / "kedb" / "incidents").mkdir(parents=True)
    (tenant / "kedb" / "incidents" / "caseos.json").write_text(
        json.dumps({"title": "CaseOS authorization incident", "symptom": "cross matter access"}),
        encoding="utf-8",
    )
    (brain / "docs" / "OBJECTIVE-GOLD-CATALOG.md").write_text(
        "# Objective gold\n\nCaseOS tenant isolation checker.", encoding="utf-8"
    )
    (brain / "registry").mkdir()
    (brain / "registry" / "artifacts.jsonl").write_text(
        json.dumps({"name": "caseos_authz_oracle", "kind": "oracle"}) + "\n", encoding="utf-8"
    )

    result = composite_search(
        "CaseOS matter authorization", brain_workspace=brain, tenant_workspace=tenant, limit=20
    )

    assert result["composite"] is True
    assert {item["plane"] for item in result["results"]} >= {"brain", "tenant"}
    coverage = {item["source"]: item for item in result["coverage"]}
    assert coverage["brain_corpus"]["status"] == "hits"
    assert coverage["tenant_corpus"]["status"] == "hits"
    assert coverage["tenant_kedb"]["status"] == "hits"
    assert coverage["brain_gold"]["status"] == "hits"
    assert coverage["brain_oracle"]["status"] == "hits"


def test_same_workspace_is_queried_once_and_absent_stores_are_explicit(tmp_path):
    ws = _workspace(tmp_path, "shared")
    (ws / "docs" / "cortex-1").mkdir(parents=True)
    (ws / "docs" / "cortex-1" / "x.md").write_text("# Alpha\n\nalpha beta", encoding="utf-8")

    result = composite_search("alpha", brain_workspace=ws, tenant_workspace=ws)

    assert result["composite"] is False
    assert set(result["workspaces"]) == {"shared"}
    coverage = {item["source"]: item for item in result["coverage"]}
    assert coverage["shared_corpus"]["status"] == "hits"
    assert coverage["shared_kedb"]["status"] == "absent"
    assert coverage["shared_gold"]["status"] == "absent"
    assert coverage["shared_oracle"]["status"] == "absent"
    assert "shared_kedb" in result["gaps"]


def test_composite_search_rejects_empty_query_and_bounds_limit(tmp_path):
    ws = _workspace(tmp_path, "ws")
    with pytest.raises(ValueError, match="non-empty"):
        composite_search(" ", brain_workspace=ws, tenant_workspace=ws)
    with pytest.raises(ValueError, match="integer"):
        composite_search("x", brain_workspace=ws, tenant_workspace=ws, limit="many")


def test_structured_store_refuses_symlink_escape(tmp_path):
    ws = _workspace(tmp_path, "ws")
    incidents = ws / "kedb" / "incidents"
    incidents.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"secret": "caseos external target"}), encoding="utf-8")
    link = incidents / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    result = composite_search("caseos", brain_workspace=ws, tenant_workspace=ws)

    coverage = {item["source"]: item for item in result["coverage"]}
    assert coverage["shared_kedb"]["hits"] == 0
    assert "escaped_links_refused=1" in coverage["shared_kedb"]["detail"]
    assert all(str(outside) not in item["path"] for item in result["results"])
