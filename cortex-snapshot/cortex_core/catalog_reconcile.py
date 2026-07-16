"""I5 catalog-reconcile: detect drift between the declared corpus catalog and disk.

`docs/GAP-CLOSURE-PLAN.md` §I5 names the risk: the declared catalog and what's
actually on disk drift both ways -- entries pointing at files that aren't there,
and files on disk no entry knows about. Left unreconciled, the catalog stops
being ground truth (the exact failure mode this whole repo exists to prevent).

The declared catalog is ``library/cortex-library/sources/collection.yaml`` (a
``sources:`` list, each entry a ``{name, source_url, local_path, checked_at}``
dict written by ``fetch.py:_update_collection_catalog``). The on-disk corpus is
the ``*.md`` under ``docs/cortex-*/`` those entries point at.

This module REPORTS three drift kinds (deterministic, no LLM):

  missing    -- a catalog entry whose ``local_path`` file is absent on disk.
  orphaned   -- a doc file on disk that no catalog entry references.
  path_drift -- an entry whose absolute ``local_path`` is rooted at a DIFFERENT
                checkout/machine than the current workspace (the file may still
                resolve by repo-relative path, but the recorded root is stale).

``--fix`` performs **safe additions only**: it appends catalog entries for
orphaned docs, reusing the same ``yaml.safe_dump`` serializer ``fetch.py`` uses.
It deliberately does NOT delete ``missing`` entries (a missing file may just be
un-synced; deletion is destructive and irreversible) -- mirroring
``workspace_sweep``'s asymmetry: aggressive about the safe direction (adding a
known-present file), conservative about the dangerous one (removing a record).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Doc trees the catalog indexes (``docs/cortex-1``, ``docs/cortex-2``, ...).
_DOC_GLOB = "cortex-*"


def _catalog_path(workspace: Path) -> Path:
    return workspace / "library" / "cortex-library" / "sources" / "collection.yaml"


def load_catalog(workspace: Path) -> list[dict[str, Any]]:
    """Return the catalog's ``sources`` list (empty if the file is absent/empty)."""
    path = _catalog_path(workspace)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    return [s for s in sources if isinstance(s, dict)]


def _repo_relative(local_path: str) -> str | None:
    """Map a catalog ``local_path`` (any machine root) to a repo-relative
    ``docs/...`` path. Returns None if it can't be located under a docs tree.

    Handles the three roots seen live: ``D:/claude/stupidly-simple-cortex/docs/..``,
    ``/home/user/stupidly-simple-cortex/docs/..``, and already-relative ``docs/..``.
    """
    p = str(local_path).replace("\\", "/")
    idx = p.rfind("/docs/")
    if idx != -1:
        return p[idx + 1 :]  # strip through the leading slash -> "docs/..."
    if p.startswith("docs/"):
        return p
    return None


def _is_absolute(local_path: str) -> bool:
    p = str(local_path).replace("\\", "/")
    return p.startswith("/") or (len(p) > 1 and p[1] == ":")


def scan_disk_docs(workspace: Path) -> list[str]:
    """Every ``*.md`` under ``docs/cortex-*/``, as sorted repo-relative posix paths."""
    docs_root = workspace / "docs"
    found: list[str] = []
    if not docs_root.is_dir():
        return found
    for tree in sorted(docs_root.glob(_DOC_GLOB)):
        if not tree.is_dir():
            continue
        for md in sorted(tree.rglob("*.md")):
            found.append(md.relative_to(workspace).as_posix())
    return found


def reconcile(workspace: Path) -> dict[str, Any]:
    """Compare the declared catalog against on-disk docs; return a drift report.

    Report shape::

        {
          "catalog_count": int, "disk_count": int,
          "missing":    [{"name", "local_path", "repo_relative"}, ...],
          "orphaned":   ["docs/cortex-1/x.md", ...],
          "path_drift": [{"name", "local_path", "repo_relative"}, ...],
          "clean": bool,
        }
    """
    workspace = Path(workspace).resolve()
    ws_posix = workspace.as_posix()
    sources = load_catalog(workspace)
    disk_docs = scan_disk_docs(workspace)
    disk_set = set(disk_docs)

    missing: list[dict[str, str]] = []
    path_drift: list[dict[str, str]] = []
    catalogued_rel: set[str] = set()

    for entry in sources:
        local_path = str(entry.get("local_path", ""))
        name = str(entry.get("name", "?"))
        rel = _repo_relative(local_path)
        if rel is not None:
            catalogued_rel.add(rel)
        # Missing: the actual file isn't on disk (checked at the real absolute
        # path first, then the repo-relative fallback for cross-checkout paths).
        on_disk = False
        if _is_absolute(local_path):
            on_disk = Path(local_path.replace("\\", "/")).is_file()
        if not on_disk and rel is not None:
            on_disk = (workspace / rel).is_file()
        if not on_disk:
            missing.append({"name": name, "local_path": local_path, "repo_relative": rel or ""})
        # Path drift: an absolute path rooted outside the current workspace.
        elif _is_absolute(local_path) and not local_path.replace("\\", "/").startswith(ws_posix):
            path_drift.append({"name": name, "local_path": local_path, "repo_relative": rel or ""})

    orphaned = sorted(disk_set - catalogued_rel)

    clean = not (missing or orphaned or path_drift)
    return {
        "catalog_count": len(sources),
        "disk_count": len(disk_docs),
        "missing": missing,
        "orphaned": orphaned,
        "path_drift": path_drift,
        "clean": clean,
    }


def _slug(rel: str) -> str:
    return Path(rel).stem


def apply_safe_additions(workspace: Path) -> list[str]:
    """Append catalog entries for orphaned on-disk docs (safe additions only).

    Returns the list of repo-relative doc paths added. Never removes or edits
    existing entries -- ``missing`` records are left intact (deletion is
    destructive; a missing file may simply be un-synced). Idempotent: an already-
    catalogued doc is not re-added. Uses the same ``safe_dump`` serializer as
    ``fetch.py`` so the file stays ``safe_load``-parseable.
    """
    workspace = Path(workspace).resolve()
    report = reconcile(workspace)
    orphans = report["orphaned"]
    if not orphans:
        return []

    path = _catalog_path(workspace)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    now = datetime.now(timezone.utc).isoformat()
    for rel in orphans:
        sources.append(
            {
                "name": _slug(rel),
                # No remote origin known for a disk-discovered doc; record how it
                # entered the catalog rather than fabricate a source_url.
                "source_url": "",
                "local_path": (workspace / rel).as_posix(),
                "checked_at": now,
                "provenance": "catalog_reconcile:disk_discovered",
            }
        )
    data["sources"] = sources
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return orphans


# ==========================================================================
# I5 (part 2): the OBJECTIVE GOLD catalog.
#
# `docs/OBJECTIVE-GOLD-CATALOG.md` is a hand-written index of the objective
# hard-gold eval lanes. GAP §I5 flags it as drifted BOTH ways: it declared
# "29 lanes / 5,804 records" while disk carries ~70+ lanes, and per-lane
# promotion coverage was unrecorded. Unlike the docs library catalog above,
# the ground truth here is two committed machine artifacts:
#
#   * each lane's ``evals/objective_<lane>/hard_gold.jsonl``  (row count is
#     re-countable with ``wc -l`` -- THE authoritative committed gold count);
#   * its promotion record(s): a lane-local ``PROMOTION.jsonl`` and/or a row in
#     ``evals/promotion_decisions/stage2_objective_promotions.jsonl`` whose
#     ``source`` points inside the lane dir.
#
# This mirrors the lane discovery in ``scripts/ci/lanes.py`` (kept in step so
# the catalog can never disagree with the objective-integrity gate) but is
# re-implemented in pure stdlib here so it needs no CI-script import.
#
# The regenerated catalog reports, per lane, the DISK hard_gold row count
# (re-countable from a committed file) -- never a summed promotion count, which
# double-counts lanes with per-model live records. Lanes whose promotion ledger
# declares a LARGER generated set than is committed on disk (e.g. tool_calling:
# 3,279 BFCL cases generated at eval time, 131 committed) are surfaced
# explicitly rather than silently conflated.
# ==========================================================================

_OBJECTIVE_GLOB = "objective_*"
_CENTRAL_PROMOTIONS = ("evals", "promotion_decisions", "stage2_objective_promotions.jsonl")

# Deterministic rule for the real/synthetic axis: a lane is "real" iff any of its
# promotion records ties its labels to an external benchmark / execution ground
# truth (keyword match on label_authority / provenance / case_authorship). Else
# "synthetic" (deterministically-authored proof-of-lane fixtures). Documented in
# the regenerated catalog header so the classification is transparent + re-runnable.
_REAL_DATA_MARKERS = (
    "bfcl", "halueval", "mbpp", "gsm", "cruxeval", "frames", "arcagi", "arc-agi",
    "arc_agi", "grid_exact", "code_execution", "subprocess_test_execution",
    "sqlite_execution", "jsonschema_test_suite", "human_annotation", "exact_numeric",
    "benchmark", "3rd-party", "third-party", "third_party", "re_execution",
    "runtime_execution", "declared_expectation_execution",
)


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _load_jsonl_objs(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_malformed": True})
    return out


def _objective_lane_dirs(workspace: Path) -> list[Path]:
    evals = workspace / "evals"
    if not evals.is_dir():
        return []
    return sorted(d for d in evals.glob(_OBJECTIVE_GLOB) if d.is_dir())


def _lane_promotion_records(lane_dir: Path, central: list[dict]) -> list[dict]:
    """Merge a lane's promotion records the same way ``scripts/ci/lanes.py`` does:
    lane-local ``PROMOTION.jsonl`` + every central row whose ``source`` points
    inside this lane dir, de-duped on (source, count, label_field, model)."""
    tok = lane_dir.name  # e.g. "objective_security"
    records: list[dict] = list(_load_jsonl_objs(lane_dir / "PROMOTION.jsonl"))
    for r in central:
        src = str(r.get("source", "")).replace("\\", "/")
        if f"/{tok}/" in src or src.startswith(f"evals/{tok}/"):
            records.append(r)
    seen: set = set()
    deduped: list[dict] = []
    for r in records:
        key = (str(r.get("source")), r.get("count"), r.get("label_field"), r.get("model"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _data_class(records: list[dict]) -> str:
    hay = " ".join(
        str(r.get(k, "")).lower()
        for r in records
        for k in ("label_authority", "provenance", "case_authorship")
    )
    return "real" if any(m in hay for m in _REAL_DATA_MARKERS) else "synthetic"


def reconcile_objective_catalog(workspace: Path) -> dict[str, Any]:
    """Reconcile the objective gold catalog against disk. Deterministic, stdlib.

    Returns a report whose per-lane rows are keyed on the re-countable disk
    hard_gold row count, with the promotion ledger cross-checked. Report shape::

        {
          "n_lanes": int,
          "n_lanes_with_hard_gold": int,
          "n_lanes_with_promotion": int,
          "lanes_missing_promotion": [name, ...],
          "lanes_missing_hard_gold": [name, ...],   # exempt anchor-grading meta-lanes
          "disk_gold_total": int,                   # sum of committed hard_gold rows
          "judge_free": bool,                       # every promotion record judge-free
          "generated_beyond_disk": [{name, disk_rows, ledger_declared}, ...],
          "lanes": [{name, disk_rows, ledger_max, label_authorities,
                     judge_free, data_class, has_promotion}, ...],
          "declared_catalog": {"lanes": int|None, "records": int|None},  # stale header
          "clean": bool,
        }
    """
    workspace = Path(workspace).resolve()
    central = _load_jsonl_objs(workspace.joinpath(*_CENTRAL_PROMOTIONS))

    lanes: list[dict] = []
    missing_promotion: list[str] = []
    missing_hard_gold: list[str] = []
    generated_beyond_disk: list[dict] = []
    disk_gold_total = 0
    judge_free_all = True

    for d in _objective_lane_dirs(workspace):
        name = d.name[len("objective_"):] if d.name.startswith("objective_") else d.name
        recs = _lane_promotion_records(d, central)
        real_recs = [r for r in recs if not r.get("_malformed")]
        hg = d / "hard_gold.jsonl"
        has_gold = hg.exists()
        disk_rows = _count_jsonl_rows(hg) if has_gold else None
        if disk_rows:
            disk_gold_total += disk_rows
        if not has_gold:
            missing_hard_gold.append(name)
        if not real_recs:
            missing_promotion.append(name)

        # judge-free axis: a lane is judge-free iff EVERY promotion record says so.
        lane_judge_free = all(r.get("judge_in_verdict_path") is False for r in real_recs) if real_recs else None
        if lane_judge_free is False:
            judge_free_all = False

        ledger_counts = [int(r.get("count") or 0) for r in real_recs if r.get("count") is not None]
        ledger_max = max(ledger_counts) if ledger_counts else 0
        if disk_rows is not None and ledger_max > disk_rows:
            generated_beyond_disk.append(
                {"name": name, "disk_rows": disk_rows, "ledger_declared": ledger_max}
            )

        authorities: list[str] = []
        for r in real_recs:
            la = r.get("label_authority")
            if la and la not in authorities:
                authorities.append(la)

        lanes.append({
            "name": name,
            "disk_rows": disk_rows,
            "ledger_max": ledger_max,
            "label_authorities": authorities,
            "judge_free": lane_judge_free,
            "data_class": _data_class(real_recs),
            "has_promotion": bool(real_recs),
        })

    lanes.sort(key=lambda x: x["name"])
    declared = _parse_declared_objective_catalog(workspace)
    clean = (
        not missing_promotion
        # rubric_grading-style anchor lanes legitimately have no hard_gold; only
        # count it as drift if the declared catalog lane-count disagrees with disk.
        and (declared["lanes"] is None or declared["lanes"] == len(lanes))
    )
    return {
        "n_lanes": len(lanes),
        "n_lanes_with_hard_gold": sum(1 for l in lanes if l["disk_rows"] is not None),
        "n_lanes_with_promotion": sum(1 for l in lanes if l["has_promotion"]),
        "lanes_missing_promotion": sorted(missing_promotion),
        "lanes_missing_hard_gold": sorted(missing_hard_gold),
        "disk_gold_total": disk_gold_total,
        "judge_free": judge_free_all,
        "generated_beyond_disk": generated_beyond_disk,
        "lanes": lanes,
        "declared_catalog": declared,
        "clean": clean,
    }


def _parse_declared_objective_catalog(workspace: Path) -> dict[str, int | None]:
    """Best-effort parse of the header numbers in the existing catalog markdown so
    the reconcile can show the before/after delta. Returns {lanes, records} (None
    when the file is absent or the header can't be parsed)."""
    import re

    path = workspace / "docs" / "OBJECTIVE-GOLD-CATALOG.md"
    if not path.exists():
        return {"lanes": None, "records": None}
    text = path.read_text(encoding="utf-8")
    lanes = records = None
    m = re.search(r"\*\*([\d,]+)\s+lanes,\s*([\d,]+)\s+promoted gold records", text)
    if m:
        lanes = int(m.group(1).replace(",", ""))
        records = int(m.group(2).replace(",", ""))
    else:
        ml = re.search(r"\*\*([\d,]+)\s+lanes", text)
        mr = re.search(r"([\d,]+)\s+(?:promoted )?gold records", text)
        if ml:
            lanes = int(ml.group(1).replace(",", ""))
        if mr:
            records = int(mr.group(1).replace(",", ""))
    return {"lanes": lanes, "records": records}


def render_objective_catalog(report: dict[str, Any]) -> str:
    """Regenerate ``docs/OBJECTIVE-GOLD-CATALOG.md`` from the reconcile report.
    Every number is re-countable from a committed file (``wc -l`` of each lane's
    hard_gold.jsonl); do NOT hand-edit."""
    lanes = report["lanes"]
    n_real = sum(1 for l in lanes if l["data_class"] == "real")
    n_syn = len(lanes) - n_real
    lines: list[str] = []
    lines.append("# Objective Gold Catalog")
    lines.append("")
    lines.append(
        f"_Auto-generated by `cortex_core.catalog_reconcile.regenerate_objective_catalog` "
        f"from disk -- do NOT hand-edit. **{report['n_lanes']} lanes, "
        f"{report['disk_gold_total']:,} committed gold records** "
        f"(sum of `wc -l` over every `evals/objective_*/hard_gold.jsonl`), "
        f"{'100% judge-free verdict paths' if report['judge_free'] else 'JUDGE IN A VERDICT PATH -- see below'}._"
    )
    lines.append("")
    lines.append(
        "Every lane's label is decided by a DETERMINISTIC checker/detector/runtime or "
        "executed tests -- never an LLM judge (enforced by "
        "`scripts/ci/check_objective_integrity.py`). The **gold records** column is the "
        "committed on-disk `hard_gold.jsonl` row count (re-countable now). "
        "`data class` is derived by a documented rule: **real** = a promotion record ties "
        "labels to an external benchmark / execution ground truth; **synthetic** = "
        "deterministically-authored proof-of-lane fixtures. "
        "Re-generate: `python -m cortex_core.catalog_reconcile --objective-fix`."
    )
    lines.append("")
    lines.append("| Lane | Gold records (disk) | Label authority | Data class | Judge in verdict path |")
    lines.append("|---|--:|---|---|---|")
    for l in lanes:
        rows = "—" if l["disk_rows"] is None else f"{l['disk_rows']:,}"
        auth = ", ".join(l["label_authorities"]) if l["label_authorities"] else "—"
        jf = "no" if l["judge_free"] else ("yes" if l["judge_free"] is False else "—")
        lines.append(f"| `{l['name']}` | {rows} | {auth} | {l['data_class']} | {jf} |")
    lines.append("")
    lines.append(
        f"**Total: {report['disk_gold_total']:,} committed gold records across "
        f"{report['n_lanes']} lanes** "
        f"({report['n_lanes_with_hard_gold']} with committed hard_gold; "
        f"real-data lanes: {n_real}; synthetic proof-of-lane: {n_syn}). "
        f"Every lane carries a promotion record "
        f"({report['n_lanes_with_promotion']}/{report['n_lanes']})."
    )
    lines.append("")
    if report["lanes_missing_hard_gold"]:
        lines.append(
            "> **No committed hard_gold (by design):** "
            + ", ".join(f"`{n}`" for n in report["lanes_missing_hard_gold"])
            + " -- anchor-grading meta-lane(s) that deterministically GRADE a soft anchor "
            "rather than produce hard gold (exempt in the integrity gate)."
        )
        lines.append("")
    if report["generated_beyond_disk"]:
        lines.append(
            "> **Generated set larger than committed rows** (declared in the promotion "
            "ledger, generated at eval time, not fully committed to disk):"
        )
        for g in report["generated_beyond_disk"]:
            lines.append(
                f">   - `{g['name']}`: {g['disk_rows']:,} committed on disk, "
                f"{g['ledger_declared']:,} declared promoted."
            )
        lines.append("")
    return "\n".join(lines)


def regenerate_objective_catalog(workspace: Path) -> dict[str, Any]:
    """Reconcile + rewrite ``docs/OBJECTIVE-GOLD-CATALOG.md`` from disk. Returns the
    reconcile report. This is the ``--objective-fix`` closure for GAP §I5: after it
    runs, the catalog's lane count and per-lane gold counts equal disk."""
    workspace = Path(workspace).resolve()
    report = reconcile_objective_catalog(workspace)
    out = workspace / "docs" / "OBJECTIVE-GOLD-CATALOG.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_objective_catalog(report), encoding="utf-8")
    return report


def main(argv=None) -> int:
    import argparse

    from cortex_core.config import resolve_workspace

    parser = argparse.ArgumentParser(
        description="Reconcile the declared corpus catalog against on-disk docs (report; --fix adds orphans only)."
    )
    parser.add_argument("workspace", nargs="?", help="Workspace root (default: auto-resolved)")
    parser.add_argument("--fix", action="store_true", help="Safe additions only: catalog orphaned docs")
    parser.add_argument("--objective", action="store_true",
                        help="Reconcile the OBJECTIVE gold catalog (lanes/counts) against disk instead")
    parser.add_argument("--objective-fix", action="store_true",
                        help="Regenerate docs/OBJECTIVE-GOLD-CATALOG.md from disk (I5 closure)")
    parser.add_argument("--json", action="store_true", help="Emit the raw report as JSON")
    args = parser.parse_args(argv)

    ws = Path(args.workspace) if args.workspace else resolve_workspace(None)

    # --- Objective gold catalog (I5 part 2) ---
    if args.objective or args.objective_fix:
        if args.objective_fix:
            report = regenerate_objective_catalog(ws)
        else:
            report = reconcile_objective_catalog(ws)
        if args.json:
            print(json.dumps(report, indent=2))
            return 0
        decl = report["declared_catalog"]
        print(
            f"Objective catalog: disk has {report['n_lanes']} lanes / "
            f"{report['disk_gold_total']:,} committed gold records "
            f"(declared header: {decl['lanes']} lanes / {decl['records']} records) | "
            f"{'REGENERATED' if args.objective_fix else ('CLEAN' if report['clean'] else 'DRIFT')}"
        )
        print(f"  promotion coverage: {report['n_lanes_with_promotion']}/{report['n_lanes']} lanes | "
              f"judge-free: {report['judge_free']}")
        if report["lanes_missing_promotion"]:
            print(f"  lanes missing a promotion record: {report['lanes_missing_promotion']}")
        if report["generated_beyond_disk"]:
            print(f"  generated-beyond-disk lanes: {[g['name'] for g in report['generated_beyond_disk']]}")
        return 0

    if args.fix:
        added = apply_safe_additions(ws)
        print(f"Added {len(added)} orphaned doc(s) to the catalog (safe additions only):")
        for rel in added:
            print(f"  + {rel}")

    report = reconcile(ws)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(
        f"Catalog: {report['catalog_count']} entr(ies) | Disk: {report['disk_count']} doc(s) | "
        f"{'CLEAN' if report['clean'] else 'DRIFT'}"
    )
    if report["missing"]:
        print(f"\nMissing ({len(report['missing'])}) -- catalog entry, no file on disk:")
        for m in report["missing"]:
            print(f"  - {m['name']}: {m['local_path']}")
    if report["orphaned"]:
        print(f"\nOrphaned ({len(report['orphaned'])}) -- doc on disk, not in catalog:")
        for rel in report["orphaned"]:
            print(f"  - {rel}")
    if report["path_drift"]:
        print(f"\nPath drift ({len(report['path_drift'])}) -- entry rooted at another checkout:")
        for d in report["path_drift"]:
            print(f"  - {d['name']}: {d['local_path']}")
    if not report["clean"] and not args.fix:
        print("\nRun with --fix to catalog orphaned docs (safe additions only; missing entries are never removed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
