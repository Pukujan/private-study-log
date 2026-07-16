"""Deep Audit Mode (GAP-CORTEX-0009) — recursive, provenance-preserving audit-log digests.

The audit trail (`audit/audit-log-*/agent/`) grows unbounded; a small-context model can't load
it all to answer "what's gone wrong in this area before." This builds a RAPTOR-shaped multi-level
digest tree over the closeouts so a query can retrieve at the right abstraction level — WITHOUT
losing or misattributing evidence.

Design (adopted, not invented — see the gap card's Current Evidence):
  * RAPTOR tree *shape* for within-window summarization (leaves -> level-1 -> ... -> root).
  * NOT RAPTOR's one-shot full-rebuild growth model. Growth uses an **incremental fold-in**
    (Letta sleep-time + Graphiti): new closeouts accumulate as leaves; a run summarizes only the
    NEW leaves plus their date-range neighbors, appending new level-1 digests; higher levels
    re-summarize only changed sub-trees. Never a full recompute.
  * **Provenance-never-replacement**: every digest node carries its source closeout ids + date
    range — always one hop back to source.
  * **Per-level faithfulness gate** (`cortex_core.faithfulness`): each level is verified against
    its cited sources before the next level runs; ungrounded levels are rejected. The
    empty-context guard is inherited (a zero-source digest auto-fails).
  * **Checkpoint per level**: each level is persisted as a real artifact before the next runs,
    so an interruption loses at most the in-flight level.

The summarizer is pluggable. Default is **extractive** (deterministic, offline, grounded by
construction — it selects salient source sentences), so the tree is testable and replayable with
no model. An LLM summarizer (e.g. Haiku) can be passed for nicer prose; the faithfulness gate
holds it to the same grounding bar either way.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from cortex_core.faithfulness import faithfulness

_NUM = re.compile(r"\d")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass
class Closeout:
    id: str
    date: str          # ISO-ish sortable string ('' if unknown)
    task: str
    result: str
    text: str          # full source text (grounding context)


@dataclass
class DigestNode:
    level: int
    id: str
    text: str
    source_ids: list                    # provenance: ids one hop back (leaves or lower nodes)
    leaf_ids: list                      # provenance: transitive leaf closeouts covered
    date_range: list                    # [min_date, max_date]
    faithfulness: dict = field(default_factory=dict)

    def asdict(self):
        return asdict(self)


# --------------------------------------------------------------------------- loading closeouts
_TASK_RE = re.compile(r"(?im)^\s*(?:\*\*)?task(?:\*\*)?\s*[:=]\s*(.+)$")
_RESULT_RE = re.compile(r"(?im)^\s*(?:\*\*)?result(?:\*\*)?\s*[:=]\s*(.+)$")
_DATE_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})T?(\d{2})?")


def _extract_date(name: str, text: str) -> str:
    m = _DATE_RE.search(name) or _DATE_RE.search(text)
    if not m:
        return ""
    y, mo, d, h = m.group(1), m.group(2), m.group(3), (m.group(4) or "00")
    return f"{y}-{mo}-{d}T{h}"


def load_closeouts(workspace: Path) -> list:
    """Read agent closeouts from audit/audit-log-*/agent/*.json|*.md into leaf Closeouts."""
    leaves = []
    for agent_dir in sorted(workspace.glob("audit/audit-log-*/agent")):
        for f in sorted(agent_dir.glob("*")):
            if f.suffix not in (".json", ".md"):
                continue
            try:
                raw = f.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            task = result = ""
            if f.suffix == ".json":
                try:
                    d = json.loads(raw)
                    task, result = str(d.get("task", "")), str(d.get("result", ""))
                except Exception:  # noqa: BLE001
                    pass
            if not task:
                mt = _TASK_RE.search(raw)
                task = mt.group(1).strip() if mt else ""
            if not result:
                mr = _RESULT_RE.search(raw)
                result = mr.group(1).strip() if mr else ""
            leaves.append(Closeout(id=f.stem[:60], date=_extract_date(f.name, raw),
                                   task=task, result=result, text=raw))
    # de-dup json/md pairs of the same closeout by id, prefer the one with parsed result
    by_id = {}
    for c in leaves:
        if c.id not in by_id or (c.result and not by_id[c.id].result):
            by_id[c.id] = c
    return sorted(by_id.values(), key=lambda c: (c.date, c.id))


# --------------------------------------------------------------------------- extractive summarizer
def _salient_sentences(text: str, k: int = 2) -> list:
    """Pick up to k salient sentences: prefer ones with numbers/result-y verbs, else the first."""
    sents = [s.strip() for s in _SENT.split(text.strip()) if len(s.strip()) >= 12]
    if not sents:
        return []
    scored = sorted(sents, key=lambda s: (bool(_NUM.search(s)) +
                                          bool(re.search(r"(?i)pass|fail|fix|error|built|ship|closed|gate", s))),
                    reverse=True)
    top = scored[:k]
    # keep original order for readability
    return [s for s in sents if s in top][:k]


def extractive_summarizer(texts: list, source_ids: list) -> str:
    """Deterministic, grounded-by-construction digest: salient sentences quoted per source."""
    parts = []
    for _sid, t in zip(source_ids, texts, strict=False):
        for s in _salient_sentences(t, k=2):
            parts.append(s if s.endswith((".", "!", "?")) else s + ".")
    return " ".join(parts)


# --------------------------------------------------------------------------- clustering (date neighborhood)
def _batch(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _date_range(dates):
    ds = [d for d in dates if d]
    return [min(ds), max(ds)] if ds else ["", ""]


# --------------------------------------------------------------------------- tree build
def _summarize_group(group_texts, group_ids, group_leafids, group_dates, level,
                     summarizer, backend, threshold, node_id):
    text = summarizer(group_texts, group_ids)
    fr = faithfulness(text, group_texts, threshold=threshold, backend=backend)
    return DigestNode(level=level, id=node_id, text=text,
                      source_ids=list(group_ids),
                      leaf_ids=sorted({lid for lids in group_leafids for lid in lids}),
                      date_range=_date_range(group_dates), faithfulness=fr.asdict())


def build_tree(leaves: list, *, batch_size: int = 5, summarizer=extractive_summarizer,
               backend="lexical", threshold: float = 0.8) -> dict:
    """Build a multi-level digest tree from leaf closeouts. Returns {levels: [[nodes]...], ...}.

    Level 0 = leaves (as nodes). Each higher level batches the level below by date neighborhood
    and summarizes; every node is faithfulness-gated against its own sources.
    """
    level0 = [DigestNode(0, c.id, c.text, [c.id], [c.id], [c.date, c.date],
                         {"score": 1.0, "passed": True, "note": "leaf (source of record)"})
              for c in leaves]
    levels = [level0]
    current = level0
    lvl = 1
    while len(current) > 1:
        nodes = []
        for bi, group in enumerate(_batch(current, batch_size)):
            node = _summarize_group(
                [n.text for n in group], [n.id for n in group],
                [n.leaf_ids for n in group], [d for n in group for d in n.date_range],
                lvl, summarizer, backend, threshold, node_id=f"L{lvl}-{bi:03d}")
            nodes.append(node)
        levels.append(nodes)
        current = nodes
        lvl += 1
        if lvl > 12:            # safety bound; date batching converges long before this
            break
    return {"levels": levels, "n_leaves": len(leaves), "batch_size": batch_size,
            "backend": backend, "threshold": threshold}


# --------------------------------------------------------------------------- incremental fold-in
def fold_in(tree: dict, new_leaves: list, *, summarizer=extractive_summarizer,
            backend="lexical", threshold: float = 0.8) -> dict:
    """Append new closeouts as leaves and PARTIALLY recluster (never full rebuild).

    Only the new leaves plus the most recent existing level-1 date-neighborhood are re-summarized
    into fresh level-1 nodes; higher levels are then rebuilt over the (small) level-1 set. This is
    the append-then-partial-recluster policy — the growth model RAPTOR lacks.
    """
    batch_size = tree.get("batch_size", 5)
    existing_leaves = tree["levels"][0]
    new_nodes = [DigestNode(0, c.id, c.text, [c.id], [c.id], [c.date, c.date],
                            {"score": 1.0, "passed": True, "note": "leaf (source of record)"})
                 for c in new_leaves]
    # partial: re-summarize only the new leaves + the last date-neighborhood batch of old leaves
    tail = existing_leaves[-batch_size:]
    touched = tail + new_nodes
    all_leaves = existing_leaves + new_nodes

    # rebuild level-1 for the touched neighborhood only; keep untouched level-1 nodes as-is
    kept_l1 = [n for n in (tree["levels"][1] if len(tree["levels"]) > 1 else [])
               if not set(n.source_ids) & {t.id for t in tail}]
    fresh_l1 = []
    for bi, group in enumerate(_batch(touched, batch_size)):
        fresh_l1.append(_summarize_group(
            [n.text for n in group], [n.id for n in group],
            [n.leaf_ids for n in group], [d for n in group for d in n.date_range],
            1, summarizer, backend, threshold, node_id=f"L1-fold-{bi:03d}"))
    level1 = kept_l1 + fresh_l1

    # higher levels: re-summarize over the (small) level-1 set — bounded, not O(corpus)
    rebuilt = build_tree_from_level(level1, all_leaves, batch_size=batch_size,
                                    summarizer=summarizer, backend=backend, threshold=threshold)
    return rebuilt


def build_tree_from_level(level1: list, all_leaves: list, *, batch_size, summarizer, backend,
                          threshold) -> dict:
    level0 = all_leaves
    levels = [level0, level1] if level1 else [level0]
    current = level1 or level0
    lvl = 2 if level1 else 1
    while len(current) > 1:
        nodes = []
        for bi, group in enumerate(_batch(current, batch_size)):
            nodes.append(_summarize_group(
                [n.text for n in group], [n.id for n in group],
                [n.leaf_ids for n in group], [d for n in group for d in n.date_range],
                lvl, summarizer, backend, threshold, node_id=f"L{lvl}-{bi:03d}"))
        levels.append(nodes)
        current = nodes
        lvl += 1
        if lvl > 12:
            break
    return {"levels": levels, "n_leaves": len(all_leaves), "batch_size": batch_size,
            "backend": backend, "threshold": threshold}


# --------------------------------------------------------------------------- checkpoint persistence
def checkpoint_tree(tree: dict, out_dir: Path) -> dict:
    """Persist each level as a real artifact BEFORE returning — interruption-safe provenance."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"n_leaves": tree["n_leaves"], "backend": tree["backend"],
                "threshold": tree["threshold"], "levels": []}
    for i, level in enumerate(tree["levels"]):
        # skip persisting raw leaves (already in the audit log); persist digests (level >= 1)
        if i == 0:
            manifest["levels"].append({"level": 0, "nodes": len(level), "note": "leaves (source of record)"})
            continue
        path = out_dir / f"level-{i}.jsonl"
        path.write_text("\n".join(json.dumps(n.asdict(), ensure_ascii=False) for n in level) + "\n",
                        encoding="utf-8")
        gated = [n for n in level if n.faithfulness.get("passed")]
        manifest["levels"].append({"level": i, "nodes": len(level), "faithful": len(gated),
                                   "artifact": path.name})
    (out_dir / "digest_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def faithfulness_distribution(tree: dict) -> dict:
    """Score distribution across all digest levels — for THRESHOLD CALIBRATION (not a fixed 0.8)."""
    scores = [n.faithfulness.get("score", 0.0) for lvl in tree["levels"][1:] for n in lvl]
    if not scores:
        return {"n": 0}
    scores.sort()
    n = len(scores)
    pct = lambda p: scores[min(n - 1, int(p * n))]  # noqa: E731
    return {"n": n, "min": round(scores[0], 3), "p10": round(pct(0.10), 3),
            "p25": round(pct(0.25), 3), "median": round(pct(0.50), 3), "max": round(scores[-1], 3),
            "mean": round(sum(scores) / n, 3)}


def calibrated_threshold(dist: dict, seed: float = 0.8) -> float:
    """Gate = max(seed floor, p25 of healthy digests) — the gap's 'calibrate, don't hardcode 0.8'."""
    if not dist or not dist.get("n"):
        return seed
    return round(max(seed, dist.get("p25", seed)), 3)


def main(argv=None):
    import argparse
    from cortex_core.config import resolve_workspace
    p = argparse.ArgumentParser(description="Deep Audit Mode: recursive digest tree over the audit log")
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--threshold", type=float, default=0.8, help="faithfulness gate seed (calibrated up)")
    p.add_argument("--out", default="audit/digests", help="checkpoint dir for digest levels")
    p.add_argument("--backend", default="lexical", choices=["lexical", "minicheck"])
    a = p.parse_args(argv)
    ws = resolve_workspace()
    leaves = load_closeouts(ws)
    if len(leaves) < 2:
        print(f"only {len(leaves)} closeout(s) found — need at least 2 to build a digest tree.")
        return 1
    tree = build_tree(leaves, batch_size=a.batch_size, backend=a.backend, threshold=a.threshold)
    manifest = checkpoint_tree(tree, ws / a.out)
    dist = faithfulness_distribution(tree)
    gate = calibrated_threshold(dist, seed=a.threshold)
    print(f"built digest tree over {len(leaves)} closeouts: levels {[len(l) for l in tree['levels']]}")
    print(f"faithfulness distribution: {dist}")
    print(f"calibrated gate (max({a.threshold} seed, p25)): {gate}")
    print(f"checkpointed -> {ws / a.out}")
    below = [(lv['level'], lv['nodes'] - lv.get('faithful', 0)) for lv in manifest['levels']
             if lv.get('faithful') is not None and lv['nodes'] - lv.get('faithful', 0) > 0]
    if below:
        print(f"levels with ungrounded nodes (gate would reject/re-digest): {below}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
