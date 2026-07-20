"""RED tests for Tier-0 item 4 — cortex_write_log / cortex_audit naming.

Contract: ``reviewed/phase0-tier0-fix-contract-2026-07-03.md`` (§4).

The protocol docs (``AGENTS.md`` line 7, ``SOUL.md``, ``plugin.py``'s
``SKILLS``) tell agents to "close out with ``cortex-write-log``" but no
such console script exists — ``pyproject.toml`` only ships
``cortex-audit = cortex_core.audit:main`` (finding #5 of
``reviewed/opus-deep-review-2026-07-03.md``). PHASE-GATES 0.13 + the
BUILD-PLAN Fable addendum lock the CLI onto the MCP-mirrored snake_case
names: **``cortex_write_log`` = write path**, **``cortex_audit`` =
read/query path**, so a future MCP tool surface mirrors the CLI 1:1.

Mechanism the contract settles on (see §4 for full reasoning):
  * ``pyproject.toml`` ``[project.scripts]`` exposes both underscore
    names, mapping the write path and a (new, minimal) read/query path to
    *distinct* callables in ``cortex_core``.
  * The ``cortex`` dispatcher mirrors them: ``cortex write-log`` (write,
    already wired) and ``cortex audit`` (read/query, new).

These tests parse ``pyproject.toml`` (no install needed) and drive the
dispatcher directly.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

try:
    import tomllib  # stdlib >=3.11
except ModuleNotFoundError:  # pragma: no cover -- py3.10 CI leg
    import pytest

    tomllib = pytest.importorskip("tomli")  # skip only if neither is available

from cortex_core import __main__ as cortex_main

REPO_ROOT = Path(__file__).resolve().parents[1]


def _project_scripts() -> dict[str, str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data.get("project", {}).get("scripts", {})


def _resolve(target: str):
    module_name, _, attr = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library").mkdir(parents=True)
    (workspace / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _count_closeouts(workspace: Path) -> int:
    return len(list((workspace / "audit").rglob("cortex-closeout__*.md")))


def test_pyproject_defines_cortex_write_log_write_path() -> None:
    """RED: the write path must resolve under the canonical ``cortex_write_log``
    name the protocol docs and MCP surface use. It does not exist today."""
    scripts = _project_scripts()
    assert "cortex_write_log" in scripts, (
        "no 'cortex_write_log' console script; AGENTS.md/SOUL.md tell agents "
        f"to run it. Defined scripts: {sorted(scripts)}"
    )
    target = scripts["cortex_write_log"]
    assert target.startswith("cortex_core."), target
    assert callable(_resolve(target)), f"{target} does not resolve to a callable"


def test_pyproject_defines_cortex_audit_read_query_path() -> None:
    """RED: ``cortex_audit`` must be the read/query path, a distinct entry
    point from the write path. Today only a hyphenated ``cortex-audit``
    exists and it points at the *writer* (``cortex_core.audit:main``)."""
    scripts = _project_scripts()
    assert "cortex_audit" in scripts, (
        "no 'cortex_audit' console script for the read/query path. "
        f"Defined scripts: {sorted(scripts)}"
    )
    read_target = scripts["cortex_audit"]
    assert callable(_resolve(read_target)), f"{read_target} does not resolve to a callable"

    write_target = scripts.get("cortex_write_log")
    assert write_target is not None, "cortex_write_log must exist to pair with cortex_audit"
    assert read_target != write_target, (
        "cortex_audit (read/query) and cortex_write_log (write) must map to "
        f"distinct entry points; both are {read_target!r}"
    )


def test_dispatcher_audit_subcommand_reads_without_writing(tmp_path: Path, monkeypatch) -> None:
    """RED: ``cortex audit`` must run the read/query path — exit 0 and write
    no new closeout. Today the dispatcher has no ``audit`` branch, so the
    argv falls through to the search parser and errors."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    # Seed one existing closeout so "read didn't write" is observable.
    from cortex_core.audit import write_closeout

    write_closeout(workspace, task="seed task", result="seed", status="completed")
    before = _count_closeouts(workspace)

    rc = cortex_main.main(["audit"])

    assert rc == 0, "`cortex audit` (read/query) should exit 0"
    assert _count_closeouts(workspace) == before, "read/query path must not write a closeout"


def test_dispatcher_write_log_subcommand_writes_closeout(tmp_path: Path, monkeypatch) -> None:
    """CONTROL (expected green): the write path via the dispatcher still
    works. Guards against the rename breaking closeout writing."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    workspace = _make_workspace(tmp_path)
    monkeypatch.setenv("CORTEX_WORKSPACE", str(workspace))
    before = _count_closeouts(workspace)

    rc = cortex_main.main(["write-log", "--task", "naming demo", "--result", "ok"])

    assert rc == 0
    assert _count_closeouts(workspace) == before + 1, "write path must create a closeout"
