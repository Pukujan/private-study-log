"""EXPERIMENTAL / throwaway comparison artifact -- lane B of a build-new vs.
fix-existing A/B test (2026-07-07). NOT wired into the MCP tool surface, NOT
a replacement for ``cortex_core/research.py`` / ``deep_research.py`` (lane A's
territory -- this file intentionally does not import or edit either).

Goal: the smallest pipeline that can honestly be called "deep research":

    question -> search this repo's corpus first -> note what the corpus
    can't answer (gaps) -> hand those gaps to a human/agent to fetch external
    sources -> assemble a cited markdown report from corpus hits + fetched
    sources.

Deliberately cut corners vs. the existing pipeline (see docstrings inline
and the write-up in docs/research/AB-TEST-LANE-B-fresh-pipeline-2026-07-07.md):
  - No SSRF-guarded fetcher of its own -- external fetching is left to the
    calling agent's WebSearch/WebFetch tools, not done in-process here.
  - No source registry / trust-tier system.
  - No cite-check / coverage scoring.
  - No LLM framing or summarization step baked in -- the caller (an agent)
    supplies sub-questions and writes the synthesis prose itself, then
    hands finished Finding objects back to this module for report assembly.

What it does do, on purpose, to still count as "a pipeline" rather than
"just some notes": a repeatable two-phase CLI contract (`brief` then
`report`), structured Finding records with mandatory source attribution,
and a single assembled markdown report with a visible gap list so nothing
researched-but-unanswered is silently dropped.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .search import CortexSearchIndex


# ---------------------------------------------------------------------------
# Phase 1: corpus-first brief
# ---------------------------------------------------------------------------


@dataclass
class CorpusHit:
    query: str
    doc: str
    snippet: str
    score: float | None = None


def search_corpus(query: str, workspace: str | Path, limit: int = 8) -> list[CorpusHit]:
    """Reuse `cortex_core.search.CortexSearchIndex` directly (that module is
    fair game -- only `research.py`/`deep_research.py` are off-limits for
    this lane). Reinventing BM25+vector hybrid retrieval from scratch for a
    throwaway comparison artifact would be a different, much bigger project
    than "build a minimal research pipeline"; the fetch/report/gap-detection
    layers below are what's actually new here.
    """
    try:
        index = CortexSearchIndex(str(workspace))
        if index.needs_rebuild():
            index.rebuild()
        results = index.search(query, use_vector=True)
    except Exception as exc:  # pragma: no cover - defensive, not the focus here
        return [CorpusHit(query=query, doc="<error>", snippet=str(exc))]

    hits: list[CorpusHit] = []
    for r in list(results)[:limit]:
        hits.append(
            CorpusHit(
                query=query,
                doc=getattr(r, "path", "?"),
                snippet=(getattr(r, "snippet", "") or "")[:500],
                score=getattr(r, "score", None),
            )
        )
    return hits


@dataclass
class Brief:
    question: str
    sub_questions: list[str]
    corpus_hits: dict[str, list[CorpusHit]] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "sub_questions": self.sub_questions,
            "corpus_hits": {
                q: [h.__dict__ for h in hits] for q, hits in self.corpus_hits.items()
            },
            "gaps": self.gaps,
        }


def build_brief(
    question: str,
    sub_questions: list[str],
    workspace: str | Path,
    min_hits_to_cover: int = 1,
) -> Brief:
    """Search the corpus for each sub-question; any sub-question with fewer
    than ``min_hits_to_cover`` hits is flagged as a gap for external
    research. This is the entire "gap detection" mechanism -- deliberately
    a hit-count threshold, not semantic coverage scoring (the existing
    pipeline's cite_check() is considerably more rigorous; see the write-up).
    """
    brief = Brief(question=question, sub_questions=sub_questions)
    for sq in sub_questions:
        hits = search_corpus(sq, workspace)
        brief.corpus_hits[sq] = hits
        if len(hits) < min_hits_to_cover or hits[0].doc in ("<error>", "<raw-output>"):
            brief.gaps.append(sq)
    return brief


# ---------------------------------------------------------------------------
# Phase 2: findings + report assembly
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    sub_question: str
    claim: str
    source: str  # URL or corpus doc path -- mandatory, never blank
    source_type: str = "external"  # "corpus" | "external"
    note: str = ""

    def __post_init__(self) -> None:
        if not self.source or not self.source.strip():
            raise ValueError(
                f"Finding for sub-question {self.sub_question!r} has no source; "
                "every finding must be attributable to a corpus doc or a fetched URL."
            )


def write_report(
    brief: Brief,
    findings: list[Finding],
    out_path: str | Path,
    title: str | None = None,
) -> Path:
    """Assemble a single markdown report from the brief (corpus coverage +
    gaps) and the findings list (corpus + externally researched claims,
    each with a mandatory source). No cite-check pass, no coverage score --
    just: every claim has a source, and every declared gap that wasn't
    ultimately answered by a finding is surfaced under UNANSWERED rather
    than silently dropped.
    """
    out_path = Path(out_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# {title or brief.question}")
    lines.append("")
    lines.append(f"*Generated {now} by `research_v2_experimental` (lane B, from-scratch pipeline).*")
    lines.append("")
    lines.append("## Sub-questions researched")
    for sq in brief.sub_questions:
        lines.append(f"- {sq}")
    lines.append("")

    answered_sqs = {f.sub_question for f in findings}

    lines.append("## Findings")
    lines.append("")
    for sq in brief.sub_questions:
        lines.append(f"### {sq}")
        sq_findings = [f for f in findings if f.sub_question == sq]
        if not sq_findings:
            lines.append("_UNANSWERED -- no findings gathered for this sub-question._")
        else:
            for f in sq_findings:
                tag = "corpus" if f.source_type == "corpus" else "external"
                lines.append(f"- {f.claim} [{tag}: {f.source}]")
                if f.note:
                    lines.append(f"  - note: {f.note}")
        lines.append("")

    unanswered = [sq for sq in brief.sub_questions if sq not in answered_sqs]
    lines.append("## Coverage")
    lines.append(f"- Sub-questions: {len(brief.sub_questions)}")
    lines.append(f"- Answered: {len(brief.sub_questions) - len(unanswered)}")
    lines.append(f"- Corpus gaps flagged during brief phase: {len(brief.gaps)}")
    if unanswered:
        lines.append(f"- **UNANSWERED ({len(unanswered)}):** " + "; ".join(unanswered))
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="EXPERIMENTAL lane-B research pipeline (brief phase only; "
        "findings + report assembly are driven programmatically by the calling agent)."
    )
    parser.add_argument("question")
    parser.add_argument("--sub-question", action="append", dest="sub_questions", default=[])
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--out", default=None, help="write brief JSON here instead of stdout")
    args = parser.parse_args(argv)

    sub_qs = args.sub_questions or [args.question]
    brief = build_brief(args.question, sub_qs, args.workspace)
    payload = json.dumps(brief.to_dict(), indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
