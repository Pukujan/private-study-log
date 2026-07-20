"""Self-maintaining seed for the living ontology.

This is the "self-maintaining" half of the ontology: instead of hand-authoring
`entities.jsonl`, ``seed`` SCANS the corpus's own structured sources -- phase
gates, the gap registry, the rubric library, the objective-eval lanes, the
pattern KEDB, the code modules, the docs -- and upserts one entity per real
artifact, with the source file as provenance. Because entity ids are derived
from type+name (``cortex_core/ontology.py``), re-running it after the corpus
changes UPSERTS rather than duplicates: the graph tracks the corpus.

It deliberately only materializes relations it can derive **structurally and
correctly** (a phase depends on the previous phase; a versioned rubric covers
its domain; calibration rubrics are fable-authored) -- it does not guess
semantic edges a wrong one of which would be worse than none, the same
discipline the pattern librarian follows (``patterns.promote_candidates`` finds
where to look but never auto-writes a detection recipe).

Everything seeded here is ``author_model: claude-opus`` (the scan wrote it);
the fable-authorship of the rubrics themselves is recorded as an ``authored_by``
edge, not by mislabeling the scan.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .config import make_stdio_encoding_safe, resolve_workspace
from . import ontology as o

SEED_AUTHOR = "claude-opus"

# The frontier author of the calibration rubric library (CLAUDE.md: "Seven
# Fable-authored rubric domains" + the *_rubrics.jsonl families). Recorded as an
# authored_by edge, never by mislabeling the scan's own author_model.
RUBRIC_AUTHOR = "fable"

# A markdown reference to another doc: an optional `docs/` prefix + a filename
# stem + `.md`. Used to derive the structural doc->doc `references` citation
# graph. The captured group is the stem, matched back against the doc entities.
_MD_LINK_RE = re.compile(r"(?:docs/)?([A-Za-z0-9][A-Za-z0-9_.-]*)\.md\b")

# Curated model pool -- parsing MODEL-ROLES prose is fragile, so the pool is
# listed explicitly and cites that doc as provenance. (name, summary).
_MODELS: list[tuple[str, str]] = [
    ("fable-max", "Frontier author (expires ~2026-07-07); authorship only, never gold."),
    ("claude-opus-4-8", "Opus 4.8 -- builder/reviewer in the harness."),
    ("claude-sonnet-5", "Sonnet 5 -- capable judge under a well-specified rubric."),
    ("claude-haiku-4-5", "Haiku 4.5 -- cheap judge/framing/summarization."),
    ("glm-5.2", "Zhipu GLM -- non-Claude judge/panel lane (bias audit)."),
    ("deepseek-v4-flash", "DeepSeek -- non-Claude worker/panel lane."),
    ("qwen3-4b", "Local 4B judge -- ties Haiku at v1 rubric; regresses on v2."),
    ("prometheus-eval", "Judge-panel/synthetic-gold gate; native template only."),
]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rubric_domain(stem: str) -> str:
    """Normalize a rubric filename stem to its domain: strip a version suffix
    (test_quality.v1 -> test_quality) and a _rubrics suffix
    (code_quality_rubrics -> code_quality)."""
    stem = re.sub(r"\.v\d+$", "", stem)
    stem = re.sub(r"_rubrics$", "", stem)
    return stem


def derive_references(
    workspace: str | Path | None = None,
    *,
    schema: "o.Schema | None" = None,
    stem_to_id: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Derive the structural doc->doc ``references`` citation graph and upsert it.

    Document A references document B iff A's markdown literally links/cites B
    (``docs/B.md`` or a bare ``B.md`` resolving to an existing docs/ file). This
    is a citation FACT, verifiable by reading A -- not a semantic guess. Each
    edge gets a deterministic ``rel-ref-<subj>-<obj>`` id so re-derivation
    UPSERTS rather than duplicating (idempotent). Returns (edges_written, errors).

    Runnable standalone (``python -m cortex_core.ontology_seed --references``)
    to add the citation graph to an already-seeded corpus WITHOUT re-running the
    full seed (which would duplicate the non-deterministic-id relations).
    """
    ws = resolve_workspace(workspace)
    sch = schema or o.load_schema(ws)
    docs_dir = ws / "docs"
    if not docs_dir.is_dir():
        return (0, [])
    if stem_to_id is None:
        # Rebuild the stem->doc-entity-id map from the current graph so a
        # standalone run does not depend on seed() having just run.
        stem_to_id = {
            e.name: e.entity_id
            for e in o.load_entities(ws).values()
            if e.type == "doc"
        }
    written = 0
    errors: list[str] = []
    for df in sorted(docs_dir.glob("*.md")):
        subj_id = stem_to_id.get(df.stem)
        if not subj_id:
            continue
        try:
            text = df.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        targets = {
            stem_to_id[m.group(1)]
            for m in _MD_LINK_RE.finditer(text)
            if m.group(1) in stem_to_id and stem_to_id[m.group(1)] != subj_id
        }
        for obj_id in sorted(targets):
            res = o.assert_relation(
                subj_id, "references", obj_id, source_paths=[f"docs/{df.name}"],
                summary="doc cites doc (structural)", author_model=SEED_AUTHOR,
                workspace=ws, schema=sch,
                relation_id=f"rel-ref-{o._slug(subj_id)}-{o._slug(obj_id)}",
            )
            if res["ok"]:
                written += 1
            else:
                errors.append(f"{subj_id} references {obj_id}: {res['errors']}")
    return (written, errors)


def seed(workspace: str | Path | None = None) -> dict[str, Any]:
    """Scan the corpus and upsert entities + structural relations. Idempotent:
    safe to re-run after the corpus changes. Returns per-category counts and any
    errors encountered (a missing source is skipped, not fatal)."""
    ws = resolve_workspace(workspace)
    schema = o.load_schema(ws)
    counts: dict[str, int] = {}
    errors: list[str] = []

    def _entity(etype: str, name: str, source: str, **kw: Any) -> str | None:
        res = o.upsert_entity(etype, name, source_paths=[source], author_model=SEED_AUTHOR,
                              workspace=ws, schema=schema, **kw)
        if not res["ok"]:
            errors.append(f"{etype}:{name}: {res['errors']}")
            return None
        counts[etype] = counts.get(etype, 0) + 1
        return res["entity_id"]

    def _relation(subj: str, pred: str, obj: str, source: str, summary: str = "") -> None:
        res = o.assert_relation(subj, pred, obj, source_paths=[source], summary=summary,
                                author_model=SEED_AUTHOR, workspace=ws, schema=schema)
        if not res["ok"]:
            errors.append(f"{subj} {pred} {obj}: {res['errors']}")
        else:
            counts["_relations"] = counts.get("_relations", 0) + 1

    # --- models --------------------------------------------------------
    models_src = "docs/MODEL-ROLES.md"
    if (ws / models_src).exists():
        for name, summary in _MODELS:
            _entity("model", name, models_src, summary=summary)
    fable_id = o.make_entity_id("model", "fable-max")

    # --- phases (+ depends_on chain) -----------------------------------
    gates = ws / "docs" / "PHASE-GATES.md"
    phase_ids: dict[int, str] = {}
    if gates.is_file():
        for m in re.finditer(r"^## Phase (\d+)\s*[—\-]\s*(.+)$", gates.read_text(encoding="utf-8"), re.M):
            num = int(m.group(1))
            title = m.group(2).strip()
            eid = _entity("phase", f"Phase {num}", "docs/PHASE-GATES.md", summary=title,
                          attributes={"number": num})
            if eid:
                phase_ids[num] = eid
        for num, eid in phase_ids.items():
            if num - 1 in phase_ids:
                _relation(eid, "depends_on", phase_ids[num - 1], "docs/PHASE-GATES.md",
                          summary="phase gates are sequential")

    # --- gaps ----------------------------------------------------------
    registry = ws / "templates" / "workspace-control-plane" / "gaps" / "registry.md"
    reg_rel = "templates/workspace-control-plane/gaps/registry.md"
    if registry.is_file():
        for m in re.finditer(r"\|\s*`(GAP-CORTEX-\d+)`\s*\|\s*([^|]+?)\s*\|", registry.read_text(encoding="utf-8")):
            _entity("gap", m.group(1), reg_rel, summary=m.group(2).strip())

    # --- rubrics (+ domain + covers + authored_by fable) ---------------
    rubrics_dir = ws / "calibration" / "rubrics"
    if rubrics_dir.is_dir() and fable_id in o.load_entities(ws):
        domains_seen: set[str] = set()
        for rf in sorted(rubrics_dir.glob("*.yaml")) + sorted(rubrics_dir.glob("*.jsonl")):
            rel = f"calibration/rubrics/{rf.name}"
            rubric_id = _entity("rubric", rf.stem, rel)
            domain = _rubric_domain(rf.stem)
            if domain not in domains_seen:
                _entity("rubric_domain", domain, rel, summary=f"{domain} scoring domain")
                domains_seen.add(domain)
            domain_id = o.make_entity_id("rubric_domain", domain)
            if rubric_id:
                _relation(rubric_id, "covers", domain_id, rel, summary="rubric scores this domain")
                _relation(rubric_id, "authored_by", fable_id, rel, summary="fable-authored rubric")

    # --- benchmarks (objective-eval lanes) -----------------------------
    lane_benchmarks = [
        ("BFCL v4", "evals/objective_tool_calling", "Berkeley Function-Calling Leaderboard; ast_checker gold."),
        ("SWE-bench", "evals/objective_coding", "Real repo patches graded by test execution."),
        ("SecurityEval", "evals/objective_security", "Defensive vuln/secure pairs; AST + bandit cross-check."),
        ("RAGAS-faithfulness", "evals/objective_research", "Citation/quote/number + contradiction verification."),
        ("import-graph-arch", "evals/objective_architecture", "Static import-graph / AST layering & cycle checks."),
        ("tenant-isolation", "evals/objective_tenant_isolation", "Multi-tenant row isolation; runtime oracle + AST detector cross-check."),
        ("ledger-balances", "evals/objective_ledger_balances", "Double-entry accounting invariants; Decimal-exact arithmetic checker."),
    ]
    for name, lane, summary in lane_benchmarks:
        if (ws / lane).is_dir():
            _entity("benchmark", name, lane, summary=summary)

    # --- patterns (KEDB) -----------------------------------------------
    patterns_dir = ws / "patterns"
    if patterns_dir.is_dir():
        for pf in sorted(patterns_dir.glob("*.json")):
            data = _read_json(pf)
            title = data.get("title")
            if title:
                _entity("pattern", title, f"patterns/{pf.name}",
                        summary=data.get("symptom", "")[:200])

    # --- code modules --------------------------------------------------
    core = ws / "cortex_core"
    if core.is_dir():
        for mf in sorted(core.glob("*.py")):
            if mf.stem.startswith("_"):
                continue
            _entity("module", mf.stem, f"cortex_core/{mf.name}")

    # --- docs (+ references citation graph) ----------------------------
    # Each docs/*.md becomes a doc entity; then the doc->doc `references` edge
    # is derived STRUCTURALLY (doc A literally links doc B in its markdown) --
    # a citation fact, not a semantic guess, the same discipline every other
    # seeded relation follows. This is the cross-document graph the ontology
    # retrieval leg walks (docs/ONTOLOGY-RETRIEVAL-SPEC.md); without it the doc
    # nodes are isolated and graph fusion is a no-op by construction.
    docs_dir = ws / "docs"
    if docs_dir.is_dir():
        doc_files = sorted(docs_dir.glob("*.md"))
        stem_to_id: dict[str, str] = {}
        for df in doc_files:
            eid = _entity("doc", df.stem, f"docs/{df.name}")
            if eid:
                stem_to_id[df.stem] = eid
        n_ref, ref_errors = derive_references(ws, schema=schema, stem_to_id=stem_to_id)
        counts["_relations"] = counts.get("_relations", 0) + n_ref
        errors.extend(ref_errors)

    return {"counts": counts, "errors": errors,
            "total_entities": sum(v for k, v in counts.items() if not k.startswith("_")),
            "total_relations": counts.get("_relations", 0)}


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Seed the living ontology from the corpus")
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--references",
        action="store_true",
        help="derive ONLY the doc->doc references citation graph (idempotent upsert), "
        "without re-running the full seed -- use to add the retrieval-fusion graph to "
        "an already-seeded corpus.",
    )
    args = parser.parse_args(argv)
    if args.references:
        written, errors = derive_references(args.workspace)
        print(json.dumps({"references_written": written, "errors": errors}, indent=2, ensure_ascii=False))
        return 0
    result = seed(args.workspace)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
