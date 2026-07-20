from __future__ import annotations

import json
from pathlib import Path

from cortex_core.audit import choose_audit_dir, write_closeout


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return workspace


def test_choose_audit_dir_uses_first_agent_directory(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    audit_dir = choose_audit_dir(workspace)
    assert audit_dir == workspace / "audit" / "audit-log-1" / "agent"


def test_write_closeout_creates_markdown_and_json(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    path = write_closeout(workspace, task="index search", result="done", status="completed", tests="pytest", scripts="search.py")

    assert path.exists()
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["task"] == "index search"
    assert data["status"] == "completed"
    assert data["result"] == "done"
    assert "index search" in path.read_text(encoding="utf-8")


def test_write_closeout_records_the_resolved_workspace(tmp_path: Path) -> None:
    """2026-07-07: a closeout must record which `workspace=` it was actually written
    to (the RESOLVED path, not the raw possibly-None argument) so a routing mystery
    (a closeout landing in the wrong workspace) can be diagnosed by reading the
    closeout file directly instead of correlating event logs."""
    workspace = _make_workspace(tmp_path)
    path = write_closeout(workspace, task="record workspace", result="done")
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["workspace"] == str(workspace.resolve())


def test_closeout_v2_carries_contract_id_evidence_and_version(tmp_path: Path) -> None:
    """Phase 4.3: a v2 closeout records schema_version, the contract_id it was
    done under, and structured evidence -- rendered in both the JSON sidecar and
    the searchable markdown body."""
    from cortex_core.audit import CLOSEOUT_SCHEMA_VERSION

    workspace = _make_workspace(tmp_path)
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "docs" / "real.md").write_text("# Real\n", encoding="utf-8")
    ev = [
        {"type": "test", "ref": "exit=0", "detail": "pytest -q, 42 passed"},
        {"type": "file", "ref": "docs/real.md:1", "detail": "the change"},
    ]
    path = write_closeout(
        workspace, task="ship the thing", result="done",
        contract_id="contract-xyz", evidence=ev,
    )
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["schema_version"] == CLOSEOUT_SCHEMA_VERSION == 4
    assert data["contract_id"] == "contract-xyz"
    assert data["evidence"] == ev
    body = path.read_text(encoding="utf-8")
    assert "## Evidence" in body
    assert "exit=0" in body and "docs/real.md:1" in body  # searchable


def test_closeout_v1_without_version_still_reads(tmp_path: Path) -> None:
    """Gate 4.3: v1 entries (no schema_version, no contract_id/evidence) must
    still be valid, readable closeouts -- backward compatible."""
    workspace = _make_workspace(tmp_path)
    path = write_closeout(workspace, task="old style", result="done")
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    # A fresh write is v2, but reading tolerates a missing version as v1:
    legacy = {k: v for k, v in data.items() if k not in ("schema_version", "contract_id", "evidence")}
    assert legacy["task"] == "old style"
    assert legacy.get("schema_version", 1) == 1  # default when absent


def test_validate_evidence_flags_unresolvable_file_refs(tmp_path: Path) -> None:
    from cortex_core.audit import validate_evidence

    workspace = _make_workspace(tmp_path)
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "docs" / "real.md").write_text("# Real\n", encoding="utf-8")
    ev = [
        {"type": "file", "ref": "docs/real.md:3"},        # resolves
        {"type": "file", "ref": "docs/ghost.md"},          # does not
        {"type": "file", "ref": "../escape.md"},           # escapes ws
        {"type": "test", "ref": "exit=1"},                 # not a file, ignored
    ]
    bad = validate_evidence(ev, workspace)
    assert "docs/ghost.md" in bad
    assert "../escape.md" in bad
    assert "docs/real.md:3" not in bad
    assert "exit=1" not in bad


def test_validate_evidence_resolves_refs_on_the_brain_plane(tmp_path: Path, monkeypatch) -> None:
    """Dual-plane fix (2026-07-07): a closeout written to the tenant/write-plane
    workspace can legitimately cite a doc that only exists on the distinct
    CORTEX_BRAIN_WORKSPACE (READ) plane -- e.g. a file `cortex_fetch_doc` just
    landed there. Before the fix, `validate_evidence` only ever checked the
    write-plane root, so this real reference was always flagged as unresolvable.
    Reproduces the live bug: docs/cortex-1/<doc>.md exists under the brain
    workspace but not under the write workspace."""
    from cortex_core.audit import validate_evidence
    from cortex_core.config import BRAIN_WORKSPACE_ENV

    write_workspace = _make_workspace(tmp_path / "tenant")
    brain_workspace = _make_workspace(tmp_path / "brain")
    (brain_workspace / "docs" / "cortex-1").mkdir(parents=True, exist_ok=True)
    (brain_workspace / "docs" / "cortex-1" / "real-brain-doc.md").write_text("# Real\n", encoding="utf-8")

    monkeypatch.setenv(BRAIN_WORKSPACE_ENV, str(brain_workspace))

    ev = [
        {"type": "file", "ref": "docs/cortex-1/real-brain-doc.md"},  # only on the brain plane
        {"type": "file", "ref": "docs/cortex-1/ghost.md"},           # on neither plane
    ]
    bad = validate_evidence(ev, write_workspace)
    assert "docs/cortex-1/real-brain-doc.md" not in bad, "a real brain-plane ref must not warn"
    assert "docs/cortex-1/ghost.md" in bad, "a ref missing from BOTH planes must still warn"


def test_closeout_v3_carries_handoff_and_renders_it(tmp_path: Path) -> None:
    """v3 (2026-07-07 standing rule): a well-formed handoff (locations +
    continuation) is stored in the JSON sidecar and rendered into the searchable
    markdown body."""
    workspace = _make_workspace(tmp_path)
    handoff = {
        "locations": ["cortex_core/audit.py", "tests/test_audit_log.py"],
        "continuation": "done, no follow-up",
    }
    path = write_closeout(workspace, task="add handoff field", result="done", handoff=handoff)
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["handoff"] == handoff
    body = path.read_text(encoding="utf-8")
    assert "## Handoff" in body
    assert "cortex_core/audit.py" in body          # searchable location
    assert "done, no follow-up" in body            # searchable continuation


def test_closeout_preserves_canonical_handoff_and_refreshes_task_view(tmp_path: Path) -> None:
    """A narrow task closeout must not silently replace project-wide authority."""
    workspace = _make_workspace(tmp_path)
    canonical = "# Canonical project continuation\n\nDo not replace this with one task.\n"
    (workspace / "HANDOFF.md").write_text(canonical, encoding="utf-8")

    write_closeout(
        workspace,
        task="small follow-up",
        result="done",
        handoff={"locations": ["artifact.txt"], "continuation": "inspect the artifact"},
    )

    assert (workspace / "HANDOFF.md").read_text(encoding="utf-8") == canonical
    latest = (workspace / "LATEST-CLOSEOUT.md").read_text(encoding="utf-8")
    assert "cortex:generated-closeout-handoff" in latest
    assert "inspect the artifact" in latest


def test_closeout_refreshes_generated_handoff_for_backward_compatibility(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    write_closeout(
        workspace,
        task="first",
        result="done",
        handoff={"locations": ["first.txt"], "continuation": "first continuation"},
    )
    write_closeout(
        workspace,
        task="second",
        result="done",
        handoff={"locations": ["second.txt"], "continuation": "second continuation"},
    )

    generated = (workspace / "HANDOFF.md").read_text(encoding="utf-8")
    assert "second continuation" in generated
    assert "first continuation" not in generated


def test_validate_handoff_field_flags_missing_and_incomplete() -> None:
    from cortex_core.audit import validate_handoff_field

    # Wholly missing -> one clear problem.
    assert validate_handoff_field(None)
    assert validate_handoff_field({})
    # Empty/placeholder sub-parts each flagged.
    assert validate_handoff_field({"locations": [], "continuation": ""})
    assert validate_handoff_field({"locations": ["   "], "continuation": "x"})   # blank location
    assert validate_handoff_field({"locations": ["real/path"], "continuation": "  "})  # blank continuation
    # A complete handoff passes (no problems).
    assert validate_handoff_field(
        {"locations": ["real/path.py"], "continuation": "feeds into phase 2"}
    ) == []


def test_write_log_mcp_surfaces_handoff_warning(tmp_path: Path) -> None:
    """The MCP tool should still write, but flag a missing handoff so the caller
    (weak or strong) sees the requirement in the response."""
    import asyncio

    from cortex_core.mcp import cortex_write_log

    workspace = _make_workspace(tmp_path)
    ws = str(workspace)
    # Missing handoff -> write succeeds but response carries a handoff_warning.
    out = asyncio.run(cortex_write_log(task="no handoff", result="did a thing", workspace=ws))
    assert "path" in out and Path(out["path"]).exists()
    assert "handoff_warning" in out
    # Complete handoff -> no warning.
    out2 = asyncio.run(
        cortex_write_log(
            task="with handoff", result="did a thing", workspace=ws,
            handoff={"locations": ["cortex_core/audit.py"], "continuation": "done, no follow-up"},
        )
    )
    assert "handoff_warning" not in out2


# --- Anti-evidence-theater WARN (2026-07-07 ledger-mining pass) ---------------------------


def test_evidence_theater_warning_flags_prose_test_claims() -> None:
    from cortex_core.audit import evidence_theater_warning

    # The exact phrasings the 2026-07-07 spot-check found asserted in prose with evidence=[].
    for tests, result in [
        ("", "Built the dashboard; all 6 tests pass and it is verified working."),
        ("telemetry 9/9", "done"),
        ("69 passed", "shipped the feature"),
        ("", "TDD 6/6 green, everything checks out"),
        ("", "verified working via visual inspection"),
    ]:
        w = evidence_theater_warning("completed", result, tests, evidence=[])
        assert w is not None, (tests, result)
        assert "unverifiable" in w

    # Any structured evidence item at all silences it -- the claim is no longer prose-only.
    assert evidence_theater_warning(
        "completed", "all 6 tests pass", "6/6",
        evidence=[{"type": "test", "ref": "exit=0", "detail": "pytest -q"}]) is None

    # No test claim in the prose -> no warning (don't nag honest incomplete closeouts).
    assert evidence_theater_warning(
        "completed", "wrote the input CSV generator, ran out of turns before cleaning", "",
        evidence=[]) is None

    # Non-completed status -> not applicable (an abandoned/in-progress record is honest data).
    assert evidence_theater_warning("abandoned", "all tests pass", "6/6", evidence=[]) is None


def test_write_log_mcp_surfaces_evidence_theater_warning(tmp_path: Path) -> None:
    """A `completed` closeout claiming a test result in prose with empty evidence[] still
    writes (keeps the self-learning fuel) but the response carries the theater warning."""
    import asyncio

    from cortex_core.mcp import cortex_write_log

    workspace = _make_workspace(tmp_path)
    ws = str(workspace)
    out = asyncio.run(cortex_write_log(
        task="dashboard", result="all 6 tests pass, verified working", status="completed",
        workspace=ws,
        handoff={"locations": ["dashboard/index.html"], "continuation": "done"}))
    assert Path(out["path"]).exists()          # still written -- WARN, not a hard block
    assert "evidence_theater_warning" in out

    # With a real evidence item, no theater warning.
    out2 = asyncio.run(cortex_write_log(
        task="dashboard2", result="all 6 tests pass", status="completed", workspace=ws,
        evidence=[{"type": "test", "ref": "exit=0", "detail": "node test_smoke.mjs -> 28/28"}],
        handoff={"locations": ["dashboard/index.html"], "continuation": "done"}))
    assert "evidence_theater_warning" not in out2
