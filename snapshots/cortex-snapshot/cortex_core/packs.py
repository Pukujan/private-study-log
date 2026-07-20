"""Phase 5 gate 5.2: the **scope pack** builder.

A scope pack is a budget-capped, scored bundle assembled for a specific task:
the most relevant patterns + doc chunks + closeouts, each carrying its retrieval
score (and, as telemetry accumulates, eval confidence + usage stats), packed
greedily by score up to a token budget. The point (Lost-in-the-Middle,
context-rot): serve *exactly* what the task needs and say why each item is
there, instead of dumping everything relevant and drowning the signal.

Token budgeting note (gate 5.2 pitfall): the budget is in TOKENS. We use a
chars/4 estimate -- a documented approximation, not a model-exact count; a
provider tokenizer is the future refinement. It is NOT a raw character cap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import resolve_exact_workspace, resolve_workspace, resolve_workspace_override
from .knowledge import composite_search
from .search import CortexSearchIndex

DEFAULT_TOKEN_BUDGET = 4000

# Gate 5.4: if more than this fraction of scope-pack requests are escalations
# (a call asking for a bigger budget because the default pack wasn't enough),
# the default budgets are systematically too small and should be retuned. It's
# a health threshold, not a cap on escalating -- escalation itself is always
# granted (one call, no gatekeeper).
ESCALATION_RATE_TARGET = 0.20


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token for English). Documented
    approximation; a model tokenizer is the future refinement -- but this is a
    token estimate, not a character cap (the gate's pitfall)."""
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class PackItem:
    kind: str  # pattern | doc | closeout | kedb | gold | oracle
    ref: str  # workspace-relative path
    chunk_index: int  # which chunk of `ref` -- distinct chunks of one doc are NOT dupes
    title: str
    snippet: str
    content: str
    retrieval_score: float
    tokens: int
    eval_confidence: float | None = None  # populates from telemetry over time
    usage_count: int | None = None
    plane: str = "shared"
    store: str = "corpus"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _kind_for(path: str, shard: str, ws: Path) -> str:
    if "/patterns/" in path or shard == "patterns":
        return "pattern"
    if "/audit/" in path or shard.startswith("audit-log"):
        return "closeout"
    return "doc"


def _chunk_content(conn: Any, path: str, chunk_index: int) -> str:
    row = conn.execute(
        "SELECT content FROM chunks WHERE path = ? AND chunk_index = ?",
        (path, chunk_index),
    ).fetchone()
    return row[0] if row and row[0] else ""


def _greedy_pack(scored: list[PackItem], token_budget: int) -> tuple[list[PackItem], int]:
    """Greedy fill by score (caller pre-sorts descending): admit each item that
    still fits, and KEEP SCANNING past an over-budget item -- a later, smaller
    item may still fit. Pure and side-effect-free so the skip-and-continue
    behaviour is unit-testable without real retrieval."""
    packed: list[PackItem] = []
    used = 0
    for it in scored:
        if used + it.tokens > token_budget:
            continue
        packed.append(it)
        used += it.tokens
    return packed, used


def build_scope_pack(
    task: str,
    task_type: str | None = None,
    workspace: str | Path | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    candidate_limit: int = 30,
) -> dict[str, Any]:
    """Assemble a budget-capped, score-ranked pack for `task`. Greedy by
    retrieval score: highest-scoring items first, skip anything that would blow
    the token budget (but keep scanning -- a later, smaller item may still fit).
    Deduped by (path, chunk)."""
    # An explicit workspace is authoritative. Using env-first resolution here silently undid
    # MCP's safe Brain-plane decision under dual-plane configuration.
    ws = resolve_workspace_override(workspace)
    token_budget = max(0, token_budget)  # a negative budget packs nothing, not "everything"
    index = CortexSearchIndex(ws)
    if index.needs_rebuild():
        index.rebuild()

    results = index.search(task, limit=candidate_limit, use_vector=True)
    scored: list[PackItem] = []
    seen: set[tuple[str, int]] = set()
    candidate_refs: list[str] = []  # unique docs considered -- the measure_context_cut baseline
    conn = index.connect()  # one connection for all chunk lookups (not one per candidate)
    try:
        for r in results:
            key = (r.path, r.chunk_index)
            if key in seen:
                continue
            seen.add(key)
            content = _chunk_content(conn, r.path, r.chunk_index) or r.snippet
            try:
                rel = Path(r.path).resolve().relative_to(ws.resolve()).as_posix()
            except (ValueError, OSError):
                rel = r.path
            if rel not in candidate_refs:
                candidate_refs.append(rel)
            # RRF scores are ascending-good small floats; BM25 rank is negative
            # (lower = better). Normalize both to "higher is better".
            score = -float(r.rank) if r.rank and r.rank < 0 else float(r.rank)
            scored.append(
                PackItem(
                    kind=_kind_for(r.path, r.shard, ws),
                    ref=rel,
                    chunk_index=r.chunk_index,
                    title=r.title,
                    snippet=r.snippet,
                    content=content,
                    retrieval_score=round(score, 4),
                    # Budget against the WHOLE delivered payload (content + the
                    # snippet + title we actually ship), not content alone -- else
                    # "packs <= token budget" is true for a field, not the artifact.
                    tokens=estimate_tokens(content) + estimate_tokens(r.snippet) + estimate_tokens(r.title),
                )
            )
    finally:
        conn.close()

    scored.sort(key=lambda it: it.retrieval_score, reverse=True)
    packed, used = _greedy_pack(scored, token_budget)

    by_kind: dict[str, int] = {}
    for it in packed:
        by_kind[it.kind] = by_kind.get(it.kind, 0) + 1

    return {
        "task": task,
        "task_type": task_type,
        "token_budget": token_budget,
        "tokens_used": used,
        "n_items": len(packed),
        "by_kind": by_kind,
        "candidates_considered": len(scored),
        "candidate_refs": candidate_refs,
        "items": [it.to_dict() for it in packed],
        "escalation": "call cortex_scope_pack again with a larger token_budget (always granted, logged) if this isn't enough",
    }


def build_composite_scope_pack(
    task: str,
    task_type: str | None = None,
    *,
    brain_workspace: str | Path,
    tenant_workspace: str | Path,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    candidate_limit: int = 30,
    log_telemetry: bool = True,
) -> dict[str, Any]:
    """Build one globally budgeted pack from Brain, tenant, KEDB, gold and oracle metadata.

    Plane/store provenance stays attached to every item. Hidden gold rows and private evaluator
    fixtures are never scanned by ``composite_search``; only the public catalogs are eligible.
    """
    brain = resolve_exact_workspace(brain_workspace)
    tenant = resolve_exact_workspace(tenant_workspace)
    token_budget = max(0, token_budget)
    knowledge = composite_search(
        task,
        brain_workspace=brain,
        tenant_workspace=tenant,
        limit=candidate_limit,
        include_structured=True,
        log_telemetry=log_telemetry,
    )
    workspace_by_plane = {
        plane: resolve_exact_workspace(path) for plane, path in knowledge["workspaces"].items()
    }
    indices: dict[str, CortexSearchIndex] = {}
    connections: dict[str, Any] = {}
    scored: list[PackItem] = []
    seen: set[tuple[str, str, int | None]] = set()
    candidate_refs: list[str] = []
    try:
        for result in knowledge["results"]:
            plane = result["plane"]
            store = result["store"]
            chunk_index = result.get("chunk_index")
            key = (plane, result["path"], chunk_index)
            if key in seen:
                continue
            seen.add(key)
            content = result["snippet"]
            if store == "corpus" and chunk_index is not None:
                if plane not in indices:
                    indices[plane] = CortexSearchIndex(workspace_by_plane[plane])
                    connections[plane] = indices[plane].connect()
                content = _chunk_content(connections[plane], result["path"], chunk_index) or content
            locator = f"{plane}:{result['relative_path']}"
            candidate_refs.append(locator)
            if store == "corpus":
                kind = _kind_for(result["path"], result.get("shard", ""), workspace_by_plane[plane])
            else:
                kind = store
            score = float(result.get("fusion_score", 0.0))
            scored.append(PackItem(
                kind=kind,
                ref=result["relative_path"],
                chunk_index=int(chunk_index) if chunk_index is not None else -1,
                title=result["title"],
                snippet=result["snippet"],
                content=content,
                retrieval_score=round(score, 8),
                tokens=(estimate_tokens(content) + estimate_tokens(result["snippet"])
                        + estimate_tokens(result["title"])),
                plane=plane,
                store=store,
            ))
    finally:
        for conn in connections.values():
            conn.close()

    scored.sort(key=lambda item: (-item.retrieval_score, item.plane, item.ref, item.chunk_index))
    packed, used = _greedy_pack(scored, token_budget)
    by_kind: dict[str, int] = {}
    by_plane: dict[str, int] = {}
    for item in packed:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        by_plane[item.plane] = by_plane.get(item.plane, 0) + 1
    return {
        "task": task,
        "task_type": task_type,
        "token_budget": token_budget,
        "tokens_used": used,
        "n_items": len(packed),
        "by_kind": by_kind,
        "by_plane": by_plane,
        "candidates_considered": len(scored),
        "candidate_refs": candidate_refs,
        "items": [item.to_dict() for item in packed],
        "composite": knowledge["composite"],
        "workspaces": knowledge["workspaces"],
        "coverage": knowledge["coverage"],
        "knowledge_gaps": knowledge["gaps"],
        "escalation": "call cortex_scope_pack again with a larger token_budget and reason if this is insufficient",
    }


def measure_context_cut(
    task: str, workspace: str | Path | None = None, token_budget: int = DEFAULT_TOKEN_BUDGET
) -> dict[str, Any]:
    """Gate 5.2 success metric: how much context the pack saves vs. the baseline
    an agent would otherwise pay -- serving the full text of *every candidate
    document the search surfaced*, not just the ones that made the pack. Counting
    only packed docs would measure "how much we trimmed the docs we kept" and
    inflate as sources grow; the honest denominator is everything relevant that
    would otherwise be dumped into context (the Lost-in-the-Middle baseline)."""
    ws = resolve_workspace_override(workspace)
    pack = build_scope_pack(task, workspace=ws, token_budget=token_budget)
    full_tokens = 0
    for ref in pack["candidate_refs"]:  # ALL candidates, not just packed (review MED-4)
        target = (ws / ref).resolve()
        if target.is_file():
            full_tokens += estimate_tokens(target.read_text(encoding="utf-8", errors="replace"))
    cut = 1.0 - (pack["tokens_used"] / full_tokens) if full_tokens else 0.0
    return {
        "pack_tokens": pack["tokens_used"],
        "full_docs_tokens": full_tokens,
        "context_cut_fraction": round(cut, 3),
        "n_items": pack["n_items"],
        "candidates_considered": len(pack["candidate_refs"]),
    }


def escalation_sli(workspace: str | Path | None = None) -> dict[str, Any]:
    """Gate 5.4 SLI over the MCP event log: what fraction of scope-pack requests
    were *escalations* (a call carrying an escalation_reason -- a request for a
    larger budget, always granted). At/above ESCALATION_RATE_TARGET the default
    budgets are systematically too small and should be retuned. The escalation
    reasons are the curriculum -- they're returned here (not just counted) so a
    scheduled review can actually read them, which is the pitfall the gate names
    ("escalation reasons unread"). This reads the log; it never blocks a call."""
    import json as _json

    ws = resolve_workspace(workspace)
    log_path = ws / "logs" / "mcp-events.jsonl"
    total = 0
    reasons: list[dict[str, Any]] = []
    # HIGH-1 (review): read the rotated `.1` sibling too, oldest-first. _log_event
    # rotates at 5 MB by os.replace-ing the log to `<name>.1`; reading only the
    # live file would drop every escalation before the last roll -- truncating
    # the rate and silently losing the very reasons this SLI exists to surface.
    # Rotation keeps a single `.1`, so [.1, live] covers the full retained window.
    for path in (log_path.parent / (log_path.name + ".1"), log_path):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = _json.loads(line)
            except (ValueError, TypeError):
                continue
            if event.get("tool") != "cortex_scope_pack":
                continue
            total += 1
            if event.get("escalated"):
                reasons.append(
                    {
                        "ts": event.get("ts"),
                        "reason": event.get("escalation_reason", ""),
                        # the REQUESTED budget is what you'd retune defaults
                        # against; fall back to what actually packed (LOW-1).
                        "token_budget": event.get("token_budget") or event.get("tokens_used"),
                        "task": event.get("task"),
                    }
                )
    escalations = len(reasons)
    rate = escalations / total if total else 0.0
    return {
        "scope_pack_requests": total,
        "escalations": escalations,
        "escalation_rate": round(rate, 3),
        "target": ESCALATION_RATE_TARGET,
        "within_target": rate < ESCALATION_RATE_TARGET,
        "reasons": reasons,  # the curriculum -- surfaced, never left unread
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    from .config import make_stdio_encoding_safe

    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex scope pack builder (Phase 5.2)")
    parser.add_argument("task", nargs="?", default=None, help="the task to assemble a scope pack for")
    parser.add_argument("--task-type", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--budget", type=int, default=DEFAULT_TOKEN_BUDGET, help="token budget")
    parser.add_argument("--measure", action="store_true", help="report context-cut vs. full docs")
    parser.add_argument(
        "--escalation-sli", action="store_true",
        help="report the gate 5.4 escalation rate + reasons from the MCP event log",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.escalation_sli:
        sli = escalation_sli(args.workspace)
        if args.json:
            print(_json.dumps(sli, indent=2))
        else:
            flag = "OK" if sli["within_target"] else "OVER TARGET -- retune default budgets"
            print(
                f"escalation rate {sli['escalation_rate']:.1%} "
                f"({sli['escalations']}/{sli['scope_pack_requests']} requests, "
                f"target <{sli['target']:.0%}) [{flag}]"
            )
            if sli["reasons"]:
                print("escalation reasons (the curriculum -- read them):")
                for r in sli["reasons"][-20:]:
                    print(f"  {r['ts']}  budget->{r['token_budget']}  {r['reason']!r}  ({r['task']})")
        return 0

    if not args.task:
        parser.error("task is required unless --escalation-sli is given")

    if args.measure:
        m = measure_context_cut(args.task, args.workspace, token_budget=args.budget)
        if args.json:
            print(_json.dumps(m, indent=2))
        else:
            print(
                f"pack {m['pack_tokens']} tok vs full {m['full_docs_tokens']} tok "
                f"-> {m['context_cut_fraction']:.1%} cut ({m['n_items']} items)"
            )
        return 0

    pack = build_scope_pack(args.task, args.task_type, args.workspace, token_budget=args.budget)
    if args.json:
        print(_json.dumps(pack, indent=2))
    else:
        print(
            f"scope pack: {pack['n_items']} items, {pack['tokens_used']}/{pack['token_budget']} tok "
            f"({pack['by_kind']}) from {pack['candidates_considered']} candidates"
        )
        for it in pack["items"]:
            print(f"  [{it['kind']:8}] {it['retrieval_score']:>8.4f}  {it['ref']}#{it['chunk_index']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
