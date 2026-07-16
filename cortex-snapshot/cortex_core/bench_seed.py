"""Corpus seeding for the UI-benchmark B-vs-C experiment (2026-07-10).

Condition **B** runs an agent against a fresh, empty scoped corpus (the run sandbox has
nothing to retrieve). Condition **C** runs the *same* task/model against a corpus SEEDED
with the UI/UX rubric + exemplars + dashboard patterns. The only variable is the seeded
memory, so a B-vs-C delta measures whether accumulated learning ("the harness") earns its
keep -- the exact question the empty-corpus smoke run could not answer.

Mechanism: the search indexer (`cortex_core.search.SearchIndex`) only indexes ``*.md`` under
a handful of roots (``docs/cortex-*``, ``reviewed``, ``docs/research``, ``inbox``,
``patterns``, ``audit/...``). So we materialize each seed asset as a ``.md`` file under
``<run_dir>/docs/cortex-seed/`` and rebuild the index. Afterwards ``cortex_search(workspace=
run_dir)`` retrieves the seeded guidance; an unseeded run retrieves nothing. The agent's own
build artifacts (index.html etc.) are written to the run_dir *root*, never docs/, so they do
not collide with the seed shard.

Retrieval-hit detection uses the shard label ``cortex-seed`` (or the ``docs/cortex-seed/``
path) appearing in a run's ``cortex_search`` observations -- see the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SEED_SHARD_DIRNAME = "cortex-seed"
# A search result whose path/shard contains this token means the agent actually retrieved
# seeded material -- the orchestrator scans transcripts for it to set the retrieval-hit flag.
SEED_HIT_MARKER = SEED_SHARD_DIRNAME


def _repo_root() -> Path:
    """Repo root = parent of the cortex_core package dir."""
    return Path(__file__).resolve().parent.parent


# (source path relative to repo root, destination .md filename, human title). Missing sources
# are skipped with a note in the manifest rather than crashing -- a partial seed is still a
# valid condition C, and we record exactly what landed so the B-vs-C claim stays honest.
_SEED_SOURCES: list[tuple[str, str, str]] = [
    ("calibration/rubrics/ui_ux.v1.yaml", "ui_ux_rubric.md",
     "UI/UX Quality Rubric (v1)"),
    ("calibration/golden/ui_ux_exemplars.yaml", "ui_ux_exemplars.md",
     "UI/UX Golden Exemplars (slop vs clean pairs)"),
    ("evals/fable_capture/fable_ground_truth_ui_ux_html_design_20260706T210000Z.jsonl",
     "fable_ui_ux_html_design_ground_truth.md",
     "Fable Ground-Truth: UI/UX HTML Design Exemplars"),
    ("evals/fable_capture/fable_ground_truth_ui_ux_v2_20260706T210000Z.jsonl",
     "fable_ui_ux_v2_ground_truth.md",
     "Fable Ground-Truth: UI/UX v2 Exemplars"),
    # Dashboard / real-time / component pattern docs (already markdown -- copied verbatim).
    ("docs/cortex-1/status-indicators-carbon-design-system-111ecf14.md",
     "pattern_status_indicators.md", "Pattern: Status Indicators (Carbon Design System)"),
    ("docs/cortex-1/badge-ui-design-notification-count-and-status-patterns-bc77b466.md",
     "pattern_badges.md", "Pattern: Badge UI (notification count + status)"),
    ("docs/cortex-1/modal-ux-design-patterns-examples-and-best-practices-edfa64d3.md",
     "pattern_modals.md", "Pattern: Modal UX design"),
    ("docs/cortex-1/real-time-architecture-websockets-sse-and-polling-patterns-e-be62d16b.md",
     "pattern_realtime.md", "Pattern: Real-time architecture (WebSockets/SSE/polling)"),
]


def _as_markdown(title: str, source_name: str, body: str) -> str:
    """Wrap arbitrary asset text as an indexable markdown doc. Non-markdown sources
    (.yaml/.jsonl) go in a fenced block; markdown sources are passed through by the caller."""
    return f"# {title}\n\n_Seeded from `{source_name}` for the UI-benchmark C condition._\n\n```\n{body}\n```\n"


def seed_corpus(run_dir: str | Path, repo_root: str | Path | None = None) -> dict[str, Any]:
    """Materialize the UI seed assets into ``run_dir/docs/cortex-seed`` and rebuild the index.

    Returns a manifest: ``{seeded_files, missing_sources, seed_dir, index_result}``. Safe to
    call on a fresh run_dir; creates the docs/cortex-seed tree if absent."""
    run_dir = Path(run_dir)
    root = Path(repo_root) if repo_root is not None else _repo_root()
    seed_dir = run_dir / "docs" / SEED_SHARD_DIRNAME
    seed_dir.mkdir(parents=True, exist_ok=True)

    seeded: list[str] = []
    missing: list[str] = []
    for rel, dest_name, title in _SEED_SOURCES:
        src = root / rel
        if not src.exists():
            missing.append(rel)
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing.append(rel)
            continue
        if src.suffix.lower() == ".md":
            # Already markdown -- prepend a provenance line, keep content verbatim.
            out = f"<!-- seeded from {rel} for UI-benchmark C condition -->\n\n" + text
        else:
            out = _as_markdown(title, rel, text)
        (seed_dir / dest_name).write_text(out, encoding="utf-8")
        seeded.append(dest_name)

    # Rebuild the scoped index so cortex_search(workspace=run_dir) can retrieve the seed shard.
    from cortex_core.search import CortexSearchIndex

    idx = CortexSearchIndex(workspace=str(run_dir))
    index_result = idx.rebuild()

    return {
        "seeded_files": seeded,
        "missing_sources": missing,
        "seed_dir": str(seed_dir),
        "index_result": index_result,
    }


if __name__ == "__main__":  # pragma: no cover -- manual smoke
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "._seed_smoke"
    print(json.dumps(seed_corpus(target), indent=2, default=str))
