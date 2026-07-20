"""I4 theater-audit: flag governance-ritual / evidence-theater in closeouts.

`docs/GAP-CLOSURE-PLAN.md` §I4 names the risk: "controls over dead pipes" --
guards and ceremony with no live substance behind them. The closeout trail is
where that shows up first: a closeout that performs the *ceremony* of
completion ("all tests passed", "fully closed", "0 HIGH", "fresh-reviewed")
while citing nothing a reader could actually check.

This module REPORTS those closeouts. It is deliberately **detection-over-
coercion**: it flags, it never blocks, and it exits 0 -- the same policy as
`workspace_sweep` (flag, don't move) and `patterns` (occurrence floor, not a
gate). A human reads the report; the tool never decides a closeout is a lie.

It does NOT re-implement anti-evidence-theater grading. That already lives in
`cortex_core.evaluator` (the MARCH-asymmetric rubric: "evidence present but
none relevant to the claim" -> UNSUPPORTED). This module *calls* the evaluator
for the structured-evidence signal and adds two text-level ceremony signals the
evaluator doesn't cover (a `tests: passed` field that references nothing; prose
that is all closure-ritual and no concrete artifact).

Signals (all deterministic, no LLM in the verdict):
  UNSUPPORTED_CLAIM        -- evaluator grades the claim UNSUPPORTED (evidence
                              theater: right-shaped evidence, none relevant).
  TESTS_CLAIMED_UNREFERENCED
                           -- the closeout asserts tests passed but cites no
                              count, no test-type evidence, no runnable ref.
  CEREMONY_WITHOUT_SUBSTANCE
                           -- a substantive closeout is heavy on closure-ritual
                              phrasing yet cites zero concrete artifact
                              (no evidence, no scripts, no path/number in prose).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .closeout_reconcile import find_recent_closeouts
from .evaluator import (
    Verdict,
    _SUBSTANTIVE_TYPES,
    extract_claims_from_closeout,
    grade_claim,
)


class Signal(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    TESTS_CLAIMED_UNREFERENCED = "tests_claimed_unreferenced"
    CEREMONY_WITHOUT_SUBSTANCE = "ceremony_without_substance"


@dataclass
class Flag:
    signal: Signal
    reason: str


# Words that assert tests were run/passed -- a claim that must be backed by a
# reference to something runnable.
_TEST_PASS_CLAIM = re.compile(
    r"\b(all\s+)?(tests?|pytest|suite|ci)\b[^.]*\b(pass(?:ed|es|ing)?|green|ok|succe)",
    re.IGNORECASE,
)
_BARE_PASS_CLAIM = re.compile(r"\b(all\s+green|all\s+pass(?:ed|ing)?|full\s+suite\s+green)\b", re.IGNORECASE)

# Closure-ritual / ceremony phrasing: language that performs done-ness.
_CEREMONY_PHRASES = re.compile(
    r"\b(fully\s+closed|fully\s+built|0\s+high|zero\s+high|fresh[-\s]?reviewed|"
    r"all\s+green|gate\s+closed|no\s+(tracked\s+)?debt\s+remains|shipped\s+default[-\s]?on|"
    r"everything\s+(works|green)|fully\s+done|closed\.?$)",
    re.IGNORECASE,
)

# A "concrete reference": anything a reader could actually go look at. If a
# closeout has ANY of these it is not *pure* ceremony.
_PATH_REF = re.compile(r"[\w./-]+\.(py|md|json|yaml|yml|txt|jsonl|toml|sh|sql)\b", re.IGNORECASE)
_DIR_REF = re.compile(r"\b\w[\w-]*/\w[\w./-]*")  # a path-like a/b token
_NUMBER_REF = re.compile(r"\b\d+\b")
_TOOL_REF = re.compile(r"\b(pytest|test_\w+|commit|sha|cortex-\w+|def\s+\w+)\b", re.IGNORECASE)


def _has_concrete_reference(text: str) -> bool:
    return bool(
        _PATH_REF.search(text)
        or _DIR_REF.search(text)
        or _NUMBER_REF.search(text)
        or _TOOL_REF.search(text)
    )


def _text_blob(closeout: dict[str, Any]) -> str:
    return " ".join(
        str(closeout.get(k, "")) for k in ("result", "tests", "scripts")
    )


def _evidence_items(closeout: dict[str, Any]) -> list[dict[str, Any]]:
    ev = closeout.get("evidence")
    return ev if isinstance(ev, list) else []


def _check_tests_unreferenced(closeout: dict[str, Any]) -> Flag | None:
    """Flag a 'tests passed' assertion in the ``tests`` field that references
    nothing checkable.

    Keyed on the dedicated ``tests`` field, NOT the free-text ``result`` prose:
    a result that merely mentions "smoke test" or "TDD" while narrating the work
    is not a bare pass-claim, and treating it as one over-flags honest closeouts.
    The theater target is specifically ``tests: "yes"`` / ``"all passed"`` /
    ``"green"`` -- a completion ritual with no count, no path, no runnable ref.
    """
    tests_field = str(closeout.get("tests", "")).strip()
    if not tests_field:
        return None  # no claim in the tests field -> nothing to flag
    claims_pass = bool(_TEST_PASS_CLAIM.search(tests_field) or _BARE_PASS_CLAIM.search(tests_field))
    if not claims_pass:
        return None
    # Backed if: a test-type evidence item, OR a count, OR a runnable ref in the
    # tests field itself (e.g. "pytest 17 passed", "tests/test_x.py").
    has_test_evidence = any(e.get("type") == "test" for e in _evidence_items(closeout))
    has_ref = (
        bool(_NUMBER_REF.search(tests_field))
        or bool(_PATH_REF.search(tests_field))
        or bool(re.search(r"\b(pytest|test_\w+)\b", tests_field, re.IGNORECASE))
    )
    if has_test_evidence or has_ref:
        return None
    return Flag(
        Signal.TESTS_CLAIMED_UNREFERENCED,
        f"tests field claims pass ({tests_field!r}) but cites no count, test-evidence item, or runnable reference",
    )


def _check_ceremony(closeout: dict[str, Any]) -> Flag | None:
    """Flag a substantive closeout that is closure-ritual prose with no artifact."""
    task_type = closeout.get("task_type")
    # Only judge substantive claims. Lenient/unknown types are exempt (mirrors
    # evaluator: chore/explore needn't cite relevant evidence).
    if task_type is not None and task_type not in _SUBSTANTIVE_TYPES:
        return None
    result = str(closeout.get("result", ""))
    ceremony_hits = len(_CEREMONY_PHRASES.findall(result))
    if ceremony_hits < 1:
        return None
    # If there's any evidence, scripts, or a concrete reference in the prose,
    # it's not *pure* ceremony -- leave it alone. Strip the ceremony phrases
    # themselves first so a ritual number like "0 HIGH" doesn't read as a
    # concrete artifact reference.
    if _evidence_items(closeout) or str(closeout.get("scripts", "")).strip():
        return None
    residual = _CEREMONY_PHRASES.sub(" ", result)
    if _has_concrete_reference(residual):
        return None
    return Flag(
        Signal.CEREMONY_WITHOUT_SUBSTANCE,
        f"{ceremony_hits} closure-ritual phrase(s) but no evidence, scripts, or concrete reference in the result",
    )


def _check_unsupported(closeout: dict[str, Any], workspace: Path | None) -> Flag | None:
    """Reuse the evaluator's anti-evidence-theater rubric for structured evidence."""
    claims = extract_claims_from_closeout(closeout)
    if not claims:
        return None
    evidence = _evidence_items(closeout)
    if not evidence:
        # No structured evidence -> the text-level signals cover this; don't
        # double-flag every evidence-less v1 closeout as UNSUPPORTED.
        return None
    for claim in claims:
        grade = grade_claim(claim, evidence, workspace)
        if grade.verdict == Verdict.UNSUPPORTED:
            return Flag(
                Signal.UNSUPPORTED_CLAIM,
                f"evaluator graded the claim UNSUPPORTED: {grade.reasoning}",
            )
    return None


def audit_one(closeout: dict[str, Any], workspace: Path | None = None) -> list[Flag]:
    """Return every theater signal a single closeout trips (may be empty)."""
    if closeout.get("status") != "completed":
        return []
    flags: list[Flag] = []
    for check in (_check_unsupported,):
        f = check(closeout, workspace)
        if f is not None:
            flags.append(f)
    for check in (_check_tests_unreferenced, _check_ceremony):
        f = check(closeout)
        if f is not None:
            flags.append(f)
    return flags


def audit_closeouts(
    workspaces: list[str | Path], workspace: Path | None = None
) -> dict[str, Any]:
    """Scan every closeout under each workspace root and report the theatrical ones.

    ``workspace`` (for evaluator file-evidence resolution) defaults to the first
    scanned workspace root. Returns a report dict; never raises on a bad closeout.
    """
    records = find_recent_closeouts(workspaces)
    resolve_ws = workspace or (Path(workspaces[0]) if workspaces else None)
    flagged: list[dict[str, Any]] = []
    for record in records:
        try:
            flags = audit_one(record, resolve_ws)
        except Exception as exc:  # noqa: BLE001 -- a bad closeout must never abort the audit
            flagged.append(
                {
                    "task": record.get("task", "?"),
                    "timestamp": record.get("timestamp"),
                    "file": record.get("_file"),
                    "error": f"audit failed: {exc}",
                    "signals": [],
                }
            )
            continue
        if flags:
            flagged.append(
                {
                    "task": record.get("task", "?"),
                    "timestamp": record.get("timestamp"),
                    "file": record.get("_file"),
                    "signals": [{"signal": f.signal.value, "reason": f.reason} for f in flags],
                }
            )
    return {"scanned": len(records), "flagged_count": len(flagged), "flagged": flagged}


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Flag governance-ritual / evidence-theater in closeouts (report, never block)."
    )
    parser.add_argument("workspaces", nargs="+", help="Workspace root(s) to scan")
    parser.add_argument("--json", action="store_true", help="Emit the raw report as JSON")
    args = parser.parse_args(argv)

    report = audit_closeouts([Path(w) for w in args.workspaces])
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Scanned {report['scanned']} closeout(s); flagged {report['flagged_count']}.")
    for item in report["flagged"]:
        print(f"\n[{item.get('timestamp', '?')}] {item['task']}")
        if item.get("error"):
            print(f"  ! {item['error']}")
        for sig in item.get("signals", []):
            print(f"  - {sig['signal']}: {sig['reason']}")
    if report["flagged_count"]:
        print(
            f"\n{report['flagged_count']} closeout(s) show theater signals. "
            "This is a REPORT (detection-over-coercion) -- nothing was blocked."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
