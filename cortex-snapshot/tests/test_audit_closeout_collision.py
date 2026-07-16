"""RED tests for Phase-0 KE item 2 — KE-03: audit closeout filename collision.

Contract: ``reviewed/phase0-ke-fixes-contract-2026-07-04.md`` (§2);
PHASE-GATES 0.15; BUILD-PLAN Phase 0 addendum KE-03.

``write_closeout()`` (``cortex_core/audit.py``) names each closeout
``cortex-closeout__<stamp>-<slug>.md`` where ``<stamp>`` is a
**second-granularity** UTC timestamp (``_timestamp_slug`` →
``%Y%m%dT%H%M%SZ``) and ``<slug>`` is the task slug. Two closeouts written in
the same wall-clock second with the same task slug therefore resolve to the
*same* path; the second ``path.write_text()`` silently overwrites the first
(and its ``.json`` sidecar), violating this project's "closeouts are never
lost" invariant in exactly the rapid successive-handoff pattern the
multi-agent flow produces (repro'd 2026-07-04).

Desired (gate 0.15): a **time-sortable, collision-free** suffix (UUIDv7 /
ULID) is appended to the filename so two same-second, same-slug closeouts
land under distinct filenames — *always-unique-by-construction*, with no
read-before-write detect-and-suffix race — and the write is **atomic**
(temp file + ``os.replace()``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import cortex_core.audit as audit
from cortex_core.audit import write_closeout


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _closeout_md_files(workspace: Path) -> list[Path]:
    return sorted((workspace / "audit").glob("audit-log-*/agent/cortex-closeout__*.md"))


def test_two_closeouts_same_second_same_slug_both_persist(tmp_path: Path, monkeypatch) -> None:
    """RED: two closeouts written in the same second with the same task slug
    must both persist under distinct filenames with their own distinct
    content. The same-second collision is forced by pinning the
    second-granularity stamp to a constant; today both calls resolve to one
    filename and the first closeout is silently overwritten."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)
    # Force both closeouts into the same wall-clock second.
    monkeypatch.setattr(audit, "_timestamp_slug", lambda: "20260704T120000Z")

    p1 = write_closeout(workspace, task="Same Task Slug", result="FIRST closeout body")
    p2 = write_closeout(workspace, task="Same Task Slug", result="SECOND closeout body")

    assert p1 != p2, (
        "two same-second same-slug closeouts collided to one filename "
        f"({p1.name}); the second silently overwrote the first"
    )
    assert p1.exists() and p2.exists(), "both closeout files must persist on disk"

    md_files = _closeout_md_files(workspace)
    assert len(md_files) == 2, (
        f"expected 2 distinct closeout files, found {len(md_files)}: "
        f"{[f.name for f in md_files]}"
    )

    body1 = p1.read_text(encoding="utf-8")
    body2 = p2.read_text(encoding="utf-8")
    assert body1 != body2, "the two closeouts must retain their own distinct content"
    assert "FIRST closeout body" in body1, "first closeout content was lost/overwritten"
    assert "SECOND closeout body" in body2, "second closeout content is missing"

    # The .json sidecars must also both survive, distinctly.
    j1 = json.loads(p1.with_suffix(".json").read_text(encoding="utf-8"))
    j2 = json.loads(p2.with_suffix(".json").read_text(encoding="utf-8"))
    assert j1["result"] == "FIRST closeout body"
    assert j2["result"] == "SECOND closeout body"


def test_closeout_write_is_atomic_via_os_replace(tmp_path: Path, monkeypatch) -> None:
    """RED: the closeout ``.md`` must be written atomically (temp file +
    ``os.replace``) so a crash mid-write can never leave a torn/partial
    closeout in the append-only log. Today ``write_closeout`` calls
    ``path.write_text()`` directly and never routes through ``os.replace``.

    ``pathlib.Path.replace`` delegates to the module-level ``os.replace``, so
    this spy catches both the ``os.replace(tmp, final)`` and
    ``tmp.replace(final)`` idioms."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst, *args, **kwargs):
        replace_calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy_replace)

    path = write_closeout(workspace, task="Atomic Task", result="atomic body")

    assert any(str(path) == dst for _src, dst in replace_calls), (
        "closeout .md was not written atomically via os.replace(temp, final); "
        f"os.replace destinations observed: {[dst for _s, dst in replace_calls]}"
    )


def test_single_closeout_still_readable_pair(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): the ordinary single-closeout path must still
    produce a matching ``.md`` / ``.json`` pair whose content round-trips —
    adding a collision-free filename suffix must not break the existing write
    contract. Green today; must stay green after the fix."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    path = write_closeout(
        workspace, task="index search", result="done", status="completed", tests="pytest"
    )

    assert path.exists()
    assert path.name.startswith("cortex-closeout__")
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["task"] == "index search"
    assert data["status"] == "completed"
    assert data["result"] == "done"
    assert "index search" in path.read_text(encoding="utf-8")


def test_long_task_string_does_not_overflow_windows_path_limit(tmp_path: Path, monkeypatch) -> None:
    """RED (repro'd on real Windows 2026-07-04): ``_slugify`` slugified the
    *entire* task string with no length cap, so a long task description
    produced a filename that blew past Windows' ~260-char full-path limit and
    raised ``OSError [Errno 22]`` at write time — the closeout was lost, on
    Windows only (Linux's looser per-component limit hid it). The slug must be
    length-bounded so the write still succeeds and the full task text is
    preserved in the file's own body/JSON (not truncated data, only the
    *filename* is shortened)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    workspace = _make_workspace(tmp_path)

    long_task = (
        "first real windows dogfood pass fixed cross project cortex workspace "
        "env var hijacking found and fixed two genuine windows only bugs eval "
        "path separator mismatch causing recall at five zero console encoding "
        "crash on non ascii corpus content closed phase two gate two point one"
    )
    assert len(long_task) > 200

    path = write_closeout(
        workspace, task=long_task, result="done", status="completed", tests="pytest"
    )

    assert path.exists()
    # The slug portion is bounded; the file still writes successfully.
    assert len(path.name) < 200
    # Full, untruncated task text is preserved where it matters — the payload.
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["task"] == long_task
