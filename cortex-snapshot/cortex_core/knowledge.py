"""Composite, bounded local-knowledge retrieval for Cortex.

The normal search index is intentionally optimized for Markdown corpus documents. Production
research decisions also need to know whether the canonical Brain, tenant corpus, KEDB incidents,
reviewed gold catalog, and oracle catalog were actually queried. This module searches those stores
without pretending that an empty store is evidence, and returns a coverage record for every source.

It is read-only, stdlib-only, and deliberately does not decide research sufficiency. The separate
research-sufficiency authority consumes these coverage records alongside source metadata and human
or domain review.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any, Callable

from .config import resolve_exact_workspace
from .search import CortexSearchIndex, MAX_SEARCH_LIMIT, MIN_SEARCH_LIMIT

MAX_STRUCTURED_FILES_PER_STORE = 256
MAX_STRUCTURED_FILE_BYTES = 512 * 1024
MAX_STRUCTURED_HITS_PER_STORE = 8
_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{1,}")


@dataclass(frozen=True)
class KnowledgeHit:
    path: str
    relative_path: str
    plane: str
    store: str
    title: str
    snippet: str
    rank: float
    chunk_index: int | None = None
    shard: str = ""


@dataclass(frozen=True)
class CoverageRecord:
    source: str
    plane: str
    store: str
    workspace: str
    status: str
    files_considered: int
    hits: int
    freshest_mtime: str | None
    detail: str = ""


_STRUCTURED_SPECS: dict[str, tuple[str, ...]] = {
    "kedb": (
        "kedb/incidents/**/*.json",
        "kedb/incidents/**/*.md",
    ),
    "gold": (
        "docs/OBJECTIVE-GOLD-CATALOG.md",
        "evals/*/PROMOTION.jsonl",
        "evals/promotion_decisions/*.jsonl",
    ),
    "oracle": (
        "docs/OBJECTIVE-GOLD-CATALOG.md",
        "docs/GENERATIVE-ORACLE-DESIGN.md",
        "evals/reports/ORACLE_HEALTH.md",
        "evals/reports/ORACLE_CROSSVAL_D1_D2.json",
        "registry/artifacts.jsonl",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _terms(query: str) -> set[str]:
    return {m.group(0).lower() for m in _TERM_RE.finditer(query or "")}


def _relative(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _title(text: str, path: Path) -> str:
    for line in text.splitlines()[:20]:
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:160]
    return path.stem


def _matching_snippet(text: str, wanted: set[str], *, width: int = 420) -> tuple[str, int]:
    lowered = text.lower()
    positions = [lowered.find(term) for term in wanted if lowered.find(term) >= 0]
    if not positions:
        return "", 0
    overlap = sum(1 for term in wanted if term in lowered)
    start = max(0, min(positions) - width // 4)
    end = min(len(text), start + width)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return snippet, overlap


def _paths_for_store(workspace: Path, store: str) -> list[Path]:
    seen: set[Path] = set()
    paths: list[Path] = []
    for pattern in _STRUCTURED_SPECS[store]:
        for path in sorted(workspace.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                paths.append(path)
                if len(paths) >= MAX_STRUCTURED_FILES_PER_STORE:
                    return paths
    return paths


def _scan_structured_store(
    workspace: Path, plane: str, store: str, query: str
) -> tuple[list[KnowledgeHit], CoverageRecord]:
    paths = _paths_for_store(workspace, store)
    workspace_resolved = workspace.resolve()
    wanted = _terms(query)
    scored: list[tuple[int, float, KnowledgeHit]] = []
    newest: str | None = None
    unreadable = 0
    escaped = 0
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
            # A matching file may be a symlink/junction to data outside the tenant or Brain.
            # Never follow it across the workspace boundary merely because its link name matched
            # one of our safe globs.
            resolved.relative_to(workspace_resolved)
            with resolved.open("rb") as stream:
                raw = stream.read(MAX_STRUCTURED_FILE_BYTES + 1)
            if len(raw) > MAX_STRUCTURED_FILE_BYTES:
                continue
            stat = resolved.stat()
            text = raw.decode("utf-8", errors="replace")
            modified = _iso_mtime(path)
            newest = max(newest, modified) if newest else modified
        except ValueError:
            escaped += 1
            continue
        except OSError:
            unreadable += 1
            continue
        snippet, overlap = _matching_snippet(text, wanted)
        if not snippet:
            continue
        rel = _relative(path, workspace)
        # More distinct query terms is better; newer breaks ties without claiming authority.
        score = float(overlap) / max(1, len(wanted))
        scored.append((overlap, stat.st_mtime, KnowledgeHit(
            path=str(path.resolve()),
            relative_path=rel,
            plane=plane,
            store=store,
            title=_title(text, path),
            snippet=snippet,
            rank=score,
        )))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].relative_path))
    hits = [item[2] for item in scored[:MAX_STRUCTURED_HITS_PER_STORE]]
    if not paths:
        status = "absent"
        detail = "no configured files exist"
    elif hits:
        status = "hits"
        detail = "bounded lexical scan"
    else:
        status = "no_hits"
        detail = "configured files queried; no query-term overlap"
    if unreadable:
        detail += f"; unreadable_files={unreadable}"
    if escaped:
        detail += f"; escaped_links_refused={escaped}"
    return hits, CoverageRecord(
        source=f"{plane}_{store}", plane=plane, store=store,
        workspace=str(workspace), status=status, files_considered=len(paths),
        hits=len(hits), freshest_mtime=newest, detail=detail,
    )


def _search_corpus(
    workspace: Path, plane: str, query: str, limit: int, *, log_telemetry: bool,
    index_factory: Callable[[Path], CortexSearchIndex],
) -> tuple[list[KnowledgeHit], CoverageRecord]:
    index = index_factory(workspace)
    rebuilt = False
    if index.needs_rebuild():
        index.rebuild()
        rebuilt = True
    results = index.search(query, limit=limit, use_vector=True, log_telemetry=log_telemetry)
    hits = [KnowledgeHit(
        path=str(Path(result.path).resolve()),
        relative_path=_relative(Path(result.path), workspace),
        plane=plane,
        store="corpus",
        title=result.title,
        snippet=result.snippet,
        # Preserve the index rank for existing callers; result order is used for fusion below.
        rank=float(result.rank),
        chunk_index=result.chunk_index,
        shard=result.shard,
    ) for result in results]
    newest: str | None = None
    try:
        documents = index.discover_documents()
        if documents:
            newest = datetime.fromtimestamp(
                max(doc.mtime_ns for doc in documents) / 1_000_000_000, timezone.utc
            ).isoformat()
    except OSError:
        pass
    return hits, CoverageRecord(
        source=f"{plane}_corpus", plane=plane, store="corpus",
        workspace=str(workspace), status="hits" if hits else "no_hits",
        files_considered=len(index._iter_document_paths()), hits=len(hits),
        freshest_mtime=newest, detail="hybrid BM25/vector search" + ("; index rebuilt" if rebuilt else ""),
    )


def _unique_workspaces(brain_workspace: str | Path, tenant_workspace: str | Path) -> list[tuple[str, Path]]:
    brain = resolve_exact_workspace(brain_workspace)
    tenant = resolve_exact_workspace(tenant_workspace)
    if brain == tenant:
        return [("shared", brain)]
    return [("brain", brain), ("tenant", tenant)]


def composite_search(
    query: str,
    *,
    brain_workspace: str | Path,
    tenant_workspace: str | Path,
    limit: int = 20,
    include_structured: bool = True,
    log_telemetry: bool = True,
    index_factory: Callable[[Path], CortexSearchIndex] | None = None,
) -> dict[str, Any]:
    """Search canonical and tenant knowledge with per-store coverage.

    Results use bounded reciprocal-rank fusion across sources. Coverage is never inferred from a
    hit: every queried store reports `hits`, `no_hits`, `absent`, or `error` independently.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    try:
        bounded_limit = max(MIN_SEARCH_LIMIT, min(MAX_SEARCH_LIMIT, int(limit)))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc

    source_hits: list[tuple[str, list[KnowledgeHit]]] = []
    coverage: list[CoverageRecord] = []
    factory = index_factory or CortexSearchIndex
    workspaces = _unique_workspaces(brain_workspace, tenant_workspace)
    for plane, workspace in workspaces:
        try:
            hits, record = _search_corpus(
                workspace, plane, query, bounded_limit, log_telemetry=log_telemetry,
                index_factory=factory,
            )
        except Exception as exc:  # a broken source is surfaced; other stores still run
            hits = []
            record = CoverageRecord(
                source=f"{plane}_corpus", plane=plane, store="corpus",
                workspace=str(workspace), status="error", files_considered=0, hits=0,
                freshest_mtime=None, detail=f"{type(exc).__name__}: {exc}"[:300],
            )
        source_hits.append((record.source, hits))
        coverage.append(record)
        if include_structured:
            for store in _STRUCTURED_SPECS:
                try:
                    structured_hits, structured_record = _scan_structured_store(
                        workspace, plane, store, query
                    )
                except Exception as exc:
                    structured_hits = []
                    structured_record = CoverageRecord(
                        source=f"{plane}_{store}", plane=plane, store=store,
                        workspace=str(workspace), status="error", files_considered=0, hits=0,
                        freshest_mtime=None, detail=f"{type(exc).__name__}: {exc}"[:300],
                    )
                source_hits.append((structured_record.source, structured_hits))
                coverage.append(structured_record)

    fused: dict[tuple[str, int | None], tuple[float, KnowledgeHit, set[str]]] = {}
    for source, hits in source_hits:
        for position, hit in enumerate(hits, start=1):
            key = (str(Path(hit.path).resolve()).lower(), hit.chunk_index)
            rrf = 1.0 / (60 + position)
            if key in fused:
                prior, prior_hit, sources = fused[key]
                sources.add(source)
                fused[key] = (prior + rrf, prior_hit, sources)
            else:
                fused[key] = (rrf, hit, {source})
    ranked = sorted(fused.values(), key=lambda item: (-item[0], item[1].relative_path))
    results = []
    for score, hit, sources in ranked[:bounded_limit]:
        item = asdict(hit)
        item["fusion_score"] = round(score, 8)
        item["matched_sources"] = sorted(sources)
        results.append(item)

    coverage_dicts = [asdict(record) for record in coverage]
    gaps = [
        record["source"] for record in coverage_dicts
        if record["status"] in {"absent", "no_hits", "error"}
    ]
    return {
        "query": query,
        "queried_at": _now(),
        "composite": len(workspaces) > 1,
        "workspaces": {plane: str(path) for plane, path in workspaces},
        "coverage": coverage_dicts,
        "gaps": gaps,
        "hits": len(results),
        "results": results,
    }
