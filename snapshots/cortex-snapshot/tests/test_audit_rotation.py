"""RED tests for Tier-0 item 5 — audit shard rotation.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§5).

``choose_audit_dir()`` (``cortex_core/audit.py``) sorts the existing
``audit-log-*/agent`` dirs and unconditionally returns the first one —
there is no size/count check, so ``audit-log-2``/``-3`` (advertised in
``audit/README.md`` and ``cortex.json``'s ``sharding`` block) never
receive anything. Finding #6 of ``reviewed/opus-deep-review-2026-07-03.md``;
PHASE-GATES 0.8 ("Shard rotates at cap; closeouts across shards all
indexed and findable").

Design call (see §5): **wire up** rotation rather than delete the
multi-shard scaffolding, because gate 0.8's success condition explicitly
requires rotation and ``fetch.py``'s ``choose_doc_shard`` is a working
in-repo precedent to mirror. The contract adds a ``max_files`` cap seam to
``choose_audit_dir`` (count-based, mirroring ``choose_doc_shard``'s
byte-based cap but far cheaper to exercise): once the active shard holds
``max_files`` closeouts, selection rolls to the next ``audit-log-N``.
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core.audit import choose_audit_dir
from cortex_core.search import CortexSearchIndex


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def test_choose_audit_dir_rotates_to_next_shard_at_cap(tmp_path: Path, monkeypatch) -> None:
    """RED: with the active shard already at the cap, selection must roll to
    ``audit-log-2`` (creating it). Today ``choose_audit_dir`` has no cap
    seam and always returns ``audit-log-1``."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    agent1 = workspace / "audit" / "audit-log-1" / "agent"
    # Fill shard 1 up to the cap.
    (agent1 / "cortex-closeout__a.md").write_text("x", encoding="utf-8")
    (agent1 / "cortex-closeout__b.md").write_text("x", encoding="utf-8")

    chosen = choose_audit_dir(workspace, max_files=2)

    assert chosen == workspace / "audit" / "audit-log-2" / "agent", (
        f"expected rotation into audit-log-2/agent, got {chosen}"
    )
    assert chosen.exists(), "rotated shard directory should be created"


def test_choose_audit_dir_stays_in_shard_below_cap(tmp_path: Path, monkeypatch) -> None:
    """RED-companion: below the cap, selection must stay in ``audit-log-1``.
    Also fails today (the ``max_files`` seam does not exist), but pins that
    rotation only fires *at* the cap, not before."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    agent1 = workspace / "audit" / "audit-log-1" / "agent"
    (agent1 / "cortex-closeout__a.md").write_text("x", encoding="utf-8")

    chosen = choose_audit_dir(workspace, max_files=2)

    assert chosen == workspace / "audit" / "audit-log-1" / "agent", (
        f"expected to stay in audit-log-1/agent below cap, got {chosen}"
    )


def test_rotated_shard_closeouts_are_discoverable(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): the discovery side already scans every
    ``audit-log-*/agent`` shard, so once rotation writes into ``audit-log-2``
    those closeouts are indexed and findable. This scopes the fix to the
    writer-side rotation only (PHASE-GATES 0.8 second clause already holds)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    agent2 = workspace / "audit" / "audit-log-2" / "agent"
    agent2.mkdir(parents=True)
    (agent2 / "cortex-closeout__rotated.md").write_text(
        "# Closeout\n\nrotatedshardmarker evidence lives in shard two.\n",
        encoding="utf-8",
    )

    index = CortexSearchIndex(workspace)
    index.rebuild()
    results = index.search("rotatedshardmarker")

    assert any(result.filename == "cortex-closeout__rotated.md" for result in results), (
        "closeout in a rotated (audit-log-2) shard is not findable via search"
    )
