"""Positive + negative tests for the retrieval-health checkers
(`cortex_core/retrieval_health.py`) -- the guards for the silent "search returns
nothing while the answer is indexed" failure class."""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core.retrieval_health import (
    CHECKER_VERSION,
    CanaryCase,
    fts5_safe,
    retrieval_canary,
)
from cortex_core.search import CortexSearchIndex


# ---- fts5_safe -------------------------------------------------------------

def test_fts5_safe_positive_version_token() -> None:
    # POSITIVE: a version-token query (the exact regression trigger) is now safe,
    # because the normalizer strips the '.' before it reaches FTS5.
    assert fts5_safe("GLM-5.2 provenance Umans opencode") is True
    assert fts5_safe("sqlite-vec vs txtai") is True
    assert fts5_safe("plain multi word query") is True


def test_fts5_safe_negative_malformed_operator() -> None:
    # NEGATIVE: a query with an explicit but malformed FTS5 operator (unbalanced
    # quote) is passed through verbatim and DOES throw -> the checker must flag it.
    assert fts5_safe('foo "unterminated') is False


# ---- retrieval_canary ------------------------------------------------------

def _make_index(tmp_path: Path) -> CortexSearchIndex:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library" / "search").mkdir(parents=True)
    (ws / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    shard = ws / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "model-roles.md").write_text(
        "# Model Roles\n\nProvenance rule: GLM-5.2 ONLY via Umans or opencode-go.\n",
        encoding="utf-8",
    )
    idx = CortexSearchIndex(ws)
    idx.rebuild()
    return idx


def test_canary_positive_all_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    idx = _make_index(tmp_path)
    cases = [
        CanaryCase("GLM-5.2 provenance", expect_nonzero=True, expect_path_substr="model-roles"),
        CanaryCase("Umans opencode", expect_nonzero=True),
    ]
    report = retrieval_canary(idx, cases)
    assert report.ok is True
    assert report.passed == 2 and report.failures == []
    assert report.checker_version == CHECKER_VERSION


def test_canary_negative_catches_silent_zero_and_wrong_doc(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    idx = _make_index(tmp_path)
    cases = [
        # a term that is genuinely NOT in the corpus -> must be caught as a silent zero
        CanaryCase("kubernetes helm chart", expect_nonzero=True),
        # a real hit but the WRONG expected doc -> must be caught
        CanaryCase("GLM-5.2 provenance", expect_nonzero=True, expect_path_substr="does-not-exist"),
    ]
    report = retrieval_canary(idx, cases)
    assert report.ok is False
    assert report.passed == 0 and len(report.failures) == 2
    reasons = " ".join(f["reason"] for f in report.failures)
    assert "SILENT ZERO" in reasons and "not surfaced" in reasons
