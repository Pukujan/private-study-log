"""Dual-plane routing (GAP-CORTEX-0015 H2a): reads resolve to the brain, writes to the
tenant. "Read my brain, write your folder" -- the served-product core, proven here without
a network (the transport is H1; this is the routing)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cortex_core import authz
from cortex_core.config import (
    resolve_brain_workspace,
    resolve_brain_workspace_override,
    resolve_exact_workspace,
    resolve_workspace,
)
from cortex_core.search import CortexSearchIndex


def _mk_ws(path: Path, doc: str) -> Path:
    (path / "docs" / "cortex-1").mkdir(parents=True)
    (path / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    (path / "docs" / "cortex-1" / "d.md").write_text(doc, encoding="utf-8")
    return path


def test_resolve_exact_ignores_env(tmp_path: Path, monkeypatch) -> None:
    brain = _mk_ws(tmp_path / "brain", "# Brain\nBRAINFACT unique token.\n")
    tenant = _mk_ws(tmp_path / "tenant", "# Tenant\nTENANTFACT.\n")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))  # ambient tenant env
    # resolve_exact_workspace(brain) must return the BRAIN, not the env tenant
    assert resolve_exact_workspace(brain) == brain.resolve()
    # resolve_workspace(None) still honors the env (tenant) -- writes stay on tenant
    assert resolve_workspace() == tenant.resolve()


def test_brain_workspace_env(tmp_path: Path, monkeypatch) -> None:
    brain = _mk_ws(tmp_path / "brain", "# Brain\n")
    tenant = _mk_ws(tmp_path / "tenant", "# Tenant\n")
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))
    assert resolve_brain_workspace() == brain.resolve()   # READ plane
    assert resolve_workspace() == tenant.resolve()          # WRITE plane


def test_index_reads_explicit_brain_not_env(tmp_path: Path, monkeypatch) -> None:
    # THE bug this guards: CortexSearchIndex(brain) used to re-resolve to the CORTEX_WORKSPACE
    # env (tenant) and silently return the wrong corpus. Explicit path must win.
    brain = _mk_ws(tmp_path / "brain", "# Brain\nBRAINONLYTOKEN appears here.\n")
    tenant = _mk_ws(tmp_path / "tenant", "# Tenant\nnothing relevant.\n")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))
    idx = CortexSearchIndex(str(brain))
    idx.rebuild()
    hits = idx.search("BRAINONLYTOKEN")
    assert hits, "explicit brain path did not resolve to the brain (env overrode it)"
    assert all("brain" in h.path for h in hits)


# --- READ-plane explicit-override precedence (2026-07-08 fix) -------------------------------
# The read-plane twin of the write-plane bug: `.mcp.json` hardcodes CORTEX_WORKSPACE, and
# env-first brain resolution silently overrode an explicit `workspace=` on READ tools -- so an
# explicit `cortex_ontology_query(workspace=<repo>)` read the wrong (empty) corpus. `_read_ws`
# now mirrors `_write_ws`: explicit override wins in owner mode, tenant pin holds on reads too.

import cortex_core.mcp as mcp_mod  # noqa: E402
from cortex_core.mcp import _read_ws, cortex_ontology_query, cortex_scope_pack, cortex_search  # noqa: E402


def _mk_ontology_ws(path: Path, n_entities: int, n_relations: int) -> Path:
    (path / "docs" / "ontology").mkdir(parents=True)
    (path / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    ents = [
        {"entity_id": f"doc:e{i}", "type": "doc", "name": f"e{i}", "status": "active",
         "summary": "", "aliases": [], "source_paths": [], "attributes": {},
         "created_at": "2026-07-08T00:00:00+00:00", "updated_at": "2026-07-08T00:00:00+00:00",
         "event": "create", "schema_version": 1}
        for i in range(n_entities)
    ]
    rels = [
        {"relation_id": f"rel-{i}", "subject": f"doc:e{i}", "predicate": "relates_to",
         "object": f"doc:e{(i + 1) % max(n_entities, 1)}", "status": "active",
         "valid_from": "2026-07-08T00:00:00+00:00", "invalid_from": None, "summary": "",
         "source_paths": [], "created_at": "2026-07-08T00:00:00+00:00",
         "updated_at": "2026-07-08T00:00:00+00:00", "event": "assert", "schema_version": 1}
        for i in range(n_relations)
    ]
    (path / "docs" / "ontology" / "entities.jsonl").write_text(
        "\n".join(json.dumps(e) for e in ents) + "\n", encoding="utf-8")
    (path / "docs" / "ontology" / "relations.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rels) + "\n", encoding="utf-8")
    return path


def test_read_ws_explicit_override_wins_over_env_pin_in_owner_mode(tmp_path: Path, monkeypatch) -> None:
    """Owner mode: an explicit READ workspace= must win over the ambient CORTEX_WORKSPACE pin --
    the confirmed `cortex_ontology_query` bug. No brain env => single-plane owner mode."""
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    monkeypatch.delenv("CORTEX_BRAIN_WORKSPACE", raising=False)
    pinned = _mk_ws(tmp_path / "pinned", "# Pinned\n")
    override = _mk_ws(tmp_path / "override", "# Override\n")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(pinned))
    assert _read_ws(str(override), session_id=None) == str(override.resolve())  # explicit wins
    assert _read_ws(str(override), session_id="anything") == str(override.resolve())


def test_read_ws_omitted_falls_back_to_brain_env(tmp_path: Path, monkeypatch) -> None:
    """Control: with NO explicit workspace, reads resolve env-first to the brain plane -- the
    dual-plane brain routing must NOT regress."""
    brain = _mk_ws(tmp_path / "brain", "# Brain\n")
    tenant = _mk_ws(tmp_path / "tenant", "# Tenant\n")
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))
    assert _read_ws(None, session_id=None) == str(brain.resolve())   # brain, not tenant


def test_read_ws_tenant_pinned_cannot_escape_brain_via_explicit_override(tmp_path: Path, monkeypatch) -> None:
    """SECURITY (GAP-CORTEX-0015 on the READ plane): a served-mode, non-admin TENANT session reads
    the canonical brain and must NOT be able to use an explicit foreign workspace= to escape into
    another corpus. Even with an explicit override, the read must resolve to the brain."""
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("adm"))
    brain = _mk_ws(tmp_path / "brain", "# Brain\n")
    tenant = _mk_ws(tmp_path / "tenant", "# Tenant\n")
    foreign = _mk_ws(tmp_path / "foreign", "# Foreign\n")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))
    reg = mcp_mod.cortex_register(agent_id="rtenant", model="m")   # NO admin token -> tenant
    sid = reg["session_id"]
    # explicit foreign override is IGNORED for the pinned tenant -> resolves to the brain
    assert _read_ws(str(foreign), session_id=sid) == str(brain.resolve())
    assert _read_ws(str(foreign), session_id=sid) != str(foreign.resolve())


def test_read_ws_served_admin_may_override(tmp_path: Path, monkeypatch) -> None:
    """A served-mode ADMIN owns the box and is not tenant-pinned: an explicit read override wins."""
    monkeypatch.setenv(authz.SERVER_MODE_ENV, "served")
    monkeypatch.setenv(authz.ADMIN_HASH_ENV, authz.hash_token("adm"))
    brain = _mk_ws(tmp_path / "brain", "# Brain\n")
    override = _mk_ws(tmp_path / "override", "# Override\n")
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))
    reg = mcp_mod.cortex_register(agent_id="radmin", model="m", admin_token="adm")
    sid = reg["session_id"]
    assert reg.get("is_admin") is True
    assert _read_ws(str(override), session_id=sid) == str(override.resolve())


def test_resolve_brain_workspace_override_direct(tmp_path: Path, monkeypatch) -> None:
    brain = _mk_ws(tmp_path / "brain", "# Brain\n")
    override = _mk_ws(tmp_path / "override", "# Override\n")
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))
    monkeypatch.setenv("CORTEX_WORKSPACE", str(brain))
    assert resolve_brain_workspace_override(str(override)) == override.resolve()   # explicit wins
    assert resolve_brain_workspace_override(None) == brain.resolve()               # omitted -> env-first


def test_ontology_query_explicit_workspace_returns_real_counts(tmp_path: Path, monkeypatch) -> None:
    """End-to-end regression for the confirmed live bug: an explicit
    `cortex_ontology_query(op="stats", workspace=<repo>)` in owner mode must read the REAL
    ontology at that path, not the empty env-pinned corpus. Non-zero, not mocked."""
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    monkeypatch.delenv("CORTEX_BRAIN_WORKSPACE", raising=False)
    real = _mk_ontology_ws(tmp_path / "real", n_entities=7, n_relations=3)
    empty = _mk_ontology_ws(tmp_path / "empty", n_entities=0, n_relations=0)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(empty))   # the .mcp.json-style conflicting pin
    res = asyncio.run(cortex_ontology_query(op="stats", workspace=str(real)))
    assert res["entities"] == 7          # the explicit path, not the empty env pin
    assert res["relations_total"] == 3
    # and omitting the workspace still resolves env-first to the (empty) pin
    res_env = asyncio.run(cortex_ontology_query(op="stats"))
    assert res_env["entities"] == 0


def test_search_and_scope_pack_query_both_brain_and_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(authz.SERVER_MODE_ENV, raising=False)
    brain = _mk_ws(tmp_path / "brain", "# Brain\nCASEOSCOMMON brain authority pattern.\n")
    tenant = _mk_ws(tmp_path / "tenant", "# Tenant\nCASEOSCOMMON tenant matter history.\n")
    monkeypatch.setenv("CORTEX_BRAIN_WORKSPACE", str(brain))
    monkeypatch.setenv("CORTEX_WORKSPACE", str(tenant))

    search = asyncio.run(cortex_search(query="CASEOSCOMMON"))
    pack = asyncio.run(cortex_scope_pack(task="CASEOSCOMMON", token_budget=4000))

    assert search["composite"] is True
    assert {item["plane"] for item in search["results"]} == {"brain", "tenant"}
    assert pack["composite"] is True
    assert set(pack["by_plane"]) == {"brain", "tenant"}
    assert {item["plane"] for item in pack["items"]} == {"brain", "tenant"}
