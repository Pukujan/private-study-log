"""Committed, typed results ledger for Cortex eval numbers.

Closes GAP-CLOSURE-PLAN C1/C2/C3: every headline eval number becomes one
append-only row in ``evals/results.jsonl``, tagged with the file it came from
and whether it is ``recomputed`` / ``committed-artifact`` / ``prose-only`` /
``reconciled``. A stdlib rollup (:func:`render_scorecard`) regenerates the
scorecard tables FROM the ledger, so every number is re-countable from a
committed file instead of hand-typed prose.

SPEC: ``evals/RESULTS-LEDGER-SPEC.md``. Stdlib ``json`` only -- no third-party
deps, so the ledger is readable anywhere.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from cortex_core.evidence_schema import validate_evidence_bundle

# The one committed file the SPEC names.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = _REPO_ROOT / "evals" / "results.jsonl"

REQUIRED_FIELDS = (
    "run_id", "ts", "lane", "metric", "value", "n",
    "decision", "source_file", "commit", "provenance",
)
OPTIONAL_FIELDS = ("note", "evidence")

PROVENANCE_GRADES = ("recomputed", "committed-artifact", "prose-only", "reconciled")
DECISIONS = ("SHIPPED", "REJECTED", "BASELINE", "MEASURED", "RECORDED")

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def validate_row(row: dict) -> dict:
    """Validate one ledger row against the SPEC. Returns a normalized dict
    (only known fields, in canonical key order). Raises ``ValueError`` on any
    violation -- fail loud, a malformed row poisons the ledger.
    """
    if not isinstance(row, dict):
        raise ValueError(f"row must be a dict, got {type(row).__name__}")

    missing = [f for f in REQUIRED_FIELDS if f not in row]
    if missing:
        raise ValueError(f"row missing required field(s): {sorted(missing)}")

    unknown = set(row) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)
    if unknown:
        raise ValueError(f"row has unknown field(s): {sorted(unknown)}")

    for f in ("run_id", "lane", "metric", "decision", "source_file", "provenance"):
        if not isinstance(row[f], str) or not row[f].strip():
            raise ValueError(f"field {f!r} must be a non-empty string, got {row[f]!r}")

    if not isinstance(row["ts"], str) or not _TS_RE.match(row["ts"]):
        raise ValueError(f"ts must be ISO-8601 UTC 'YYYY-MM-DDTHH:MM:SSZ', got {row['ts']!r}")

    if row["value"] is None:
        raise ValueError("value must not be null")
    if not isinstance(row["value"], (int, float, str)) or isinstance(row["value"], bool):
        raise ValueError(f"value must be a number or string, got {type(row['value']).__name__}")

    if row["n"] is not None and (not isinstance(row["n"], int) or isinstance(row["n"], bool)):
        raise ValueError(f"n must be an integer or null, got {row['n']!r}")

    if row["commit"] is not None and not isinstance(row["commit"], str):
        raise ValueError(f"commit must be a string or null, got {row['commit']!r}")

    if row["decision"] not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {row['decision']!r}")

    if row["provenance"] not in PROVENANCE_GRADES:
        raise ValueError(f"provenance must be one of {PROVENANCE_GRADES}, got {row['provenance']!r}")

    if "note" in row and not isinstance(row["note"], str):
        raise ValueError("note must be a string when present")

    # Optional universal run-evidence bundle (GAP-CLOSURE J2). When present it
    # must validate against cortex_core.evidence_schema -- binding the row's
    # value to the exact instrument (oracle_version + fixture sha256) that
    # produced it. A malformed bundle poisons the row, so fail loud.
    if "evidence" in row:
        ok, problems = validate_evidence_bundle(row["evidence"])
        if not ok:
            raise ValueError(f"evidence bundle is invalid: {problems}")

    ordered = {f: row[f] for f in REQUIRED_FIELDS}
    if "note" in row:
        ordered["note"] = row["note"]
    if "evidence" in row:
        ordered["evidence"] = row["evidence"]
    return ordered


def load_results(*, ledger_path: Path | str = DEFAULT_LEDGER) -> list[dict]:
    """Return every ledger row as a list of dicts, in file order.

    Missing file -> ``[]``. Blank lines are skipped; a malformed line raises
    (the ledger must stay clean).
    """
    path = Path(ledger_path)
    if not path.exists():
        return []
    out: list[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{i}: malformed JSON line: {exc}") from exc
    return out


def append_result(row: dict, *, ledger_path: Path | str = DEFAULT_LEDGER) -> bool:
    """Append one validated row to the ledger.

    Returns ``True`` if a new line was written; ``False`` if an identical row
    with the same ``run_id`` already exists (idempotent no-op). Raises
    ``ValueError`` if the row is invalid, or if the ``run_id`` already exists
    with DIFFERENT content (a run_id is a stable identity -- a changed value is
    a new run_id, never a silent overwrite).
    """
    path = Path(ledger_path)
    normalized = validate_row(row)

    existing = load_results(ledger_path=path)
    for prior in existing:
        if prior.get("run_id") == normalized["run_id"]:
            # Compare against the normalized prior so key order / extra keys
            # don't cause false conflicts.
            prior_norm = validate_row(prior)
            if prior_norm == normalized:
                return False  # idempotent no-op
            raise ValueError(
                f"run_id {normalized['run_id']!r} already exists with different content; "
                f"use a new run_id for a changed value (append-only, no overwrite)"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    return True


# --------------------------------------------------------------------------
# Rollup: regenerate the scorecard §1-§3 tables FROM the ledger.
# --------------------------------------------------------------------------

def _fmt_value(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def render_scorecard(rows: list[dict] | None = None) -> str:
    """Regenerate the §1-§3 tables of ``docs/HARNESS-SCORECARD-CONSOLIDATED.md``
    from the ledger rows. Pure function -- no I/O when ``rows`` is passed.
    """
    if rows is None:
        rows = load_results()

    lines: list[str] = []
    lines.append("# Cortex results scorecard (regenerated from `evals/results.jsonl`)")
    lines.append("")
    lines.append(
        "_Auto-generated by `cortex_core.results_ledger.render_scorecard` -- do NOT "
        "hand-edit. Every number below is re-countable from the committed ledger._"
    )
    lines.append("")

    # §1 decision log (SHIPPED / REJECTED gates -- "the eval learned to say no")
    lines.append("## 1. Improvement-over-time decision log (gated decisions)")
    lines.append("")
    lines.append("| lane | metric | value | n | decision | source | provenance |")
    lines.append("|---|---|---|---|---|---|---|")
    decision_rows = [r for r in rows if r.get("decision") in ("SHIPPED", "REJECTED")]
    for r in decision_rows:
        lines.append(
            f"| {r.get('lane','')} | {r.get('metric','')} | {_fmt_value(r.get('value',''))} "
            f"| {r.get('n')} | {r.get('decision','')} | `{r.get('source_file','')}` "
            f"| {r.get('provenance','')} |"
        )
    if not decision_rows:
        lines.append("| _(none)_ | | | | | | |")
    lines.append("")

    # §2 quantitative results, grouped by lane
    lines.append("## 2. Quantitative results (all rows, grouped by lane)")
    lines.append("")
    lines.append("| lane | metric | value | n | decision | source | provenance | note |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (x.get("lane", ""), x.get("metric", ""), x.get("run_id", ""))):
        note = (r.get("note", "") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r.get('lane','')} | {r.get('metric','')} | {_fmt_value(r.get('value',''))} "
            f"| {r.get('n')} | {r.get('decision','')} | `{r.get('source_file','')}` "
            f"| {r.get('provenance','')} | {note} |"
        )
    if not rows:
        lines.append("| _(ledger empty)_ | | | | | | | |")
    lines.append("")

    # §3 provenance summary -- how much is still prose-only
    lines.append("## 3. Provenance summary (honesty axis)")
    lines.append("")
    counts = {g: 0 for g in PROVENANCE_GRADES}
    for r in rows:
        g = r.get("provenance")
        if g in counts:
            counts[g] += 1
    total = len(rows)
    lines.append(f"**{total}** total rows.")
    lines.append("")
    lines.append("| provenance | count | meaning |")
    lines.append("|---|---|---|")
    meanings = {
        "recomputed": "re-runnable from committed code now (strongest)",
        "committed-artifact": "read from a committed machine-generated file",
        "prose-only": "exists only in prose markdown -- flagged gap",
        "reconciled": "canonical pick among divergent restatements",
    }
    for g in PROVENANCE_GRADES:
        lines.append(f"| {g} | {counts[g]} | {meanings[g]} |")
    lines.append("")
    prose = counts["prose-only"]
    lines.append(
        f"> {prose}/{total} headline rows are still `prose-only` "
        f"(no committed machine artifact reproduces them)."
        if total else "> Ledger is empty."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cortex results ledger: rollup + append.")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="path to results.jsonl")
    p.add_argument("--json", action="store_true", help="dump raw rows as JSON")
    p.add_argument("--append", metavar="JSON", default=None, help="append one row (JSON object)")
    args = p.parse_args(argv)

    ledger = Path(args.ledger)
    if args.append:
        row = json.loads(args.append)
        wrote = append_result(row, ledger_path=ledger)
        print("appended" if wrote else "no-op (identical row already present)")
        return 0

    rows = load_results(ledger_path=ledger)
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print(render_scorecard(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
