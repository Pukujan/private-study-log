"""cortex-repo-audit -- deterministic-detection repo-health auditor (Phases 0-1).

Design + full phased plan: docs/research/repo-audit-design-research-2026-07-06.md (Fable research),
seeded by reviewed/repo-self-audit-2026-07-06.md + reviewed/deep-dive-code-failures-2026-07-06.md.
DISTINCT from cortex_core/deep_audit.py: that digests the audit *log*; this audits repo *health*.

Phase 0: finding schema + the code-health F821 JSON-literal lane (C1) + committed baseline + exit codes.
Phase 1 (this file): **symbol-anchored, occurrence-indexed fingerprints** (a finding keeps its identity
when its function moves, and two identical lines in one symbol stay distinct -- fixing the Phase-0
dedup that reported 8 files instead of 14 sites); a **per-path severity policy** (the mess is at the
edges: `cortex_core/` strict, `evals/` warn, `deprecated/` report-only); and a **--ratchet** mode where
total debt can only monotonically decrease. Detection stays deterministic (`ast`, no LLM in the verdict
path). Still TODO in Phase 1: inline suppression-with-justification (SUPPRESSION-MISSING/EXPIRED).
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BLOCKING = {"blocker", "high"}
_SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", "node_modules", "external"}
_JSON_LITERALS = {"true": "True", "false": "False", "null": "None"}
_BASELINE_VERSION = 2

# Per-path severity policy: (glob, mode). First match wins; `**` is the fallback. `strict` findings
# block at their severity; `warn`/`report` never block (report is quieter). Encodes the empirical
# reality that the core is clean and the mess lives at the edges.
DEFAULT_POLICY: list[tuple[str, str]] = [
    ("cortex_core/**", "strict"),
    ("ops/**", "strict"),
    ("tests/**", "strict"),
    ("evals/**", "warn"),
    ("deprecated/**", "report"),
    ("**", "strict"),
]


@dataclass
class Finding:
    lane: str
    rule_id: str
    path: str
    span: dict
    message: str
    severity: str
    fingerprint: str
    evidence: str
    symbol: str = "<module>"
    autofix_hint: str = ""
    detector: str = ""
    triage: dict = field(default_factory=lambda: {"cluster_id": None, "rationale": None,
                                                  "suggested_priority": None})
    suppressed: bool = False   # runtime: fingerprint is in the baseline
    policy_mode: str = "strict"  # runtime: effective per-path mode (strict|warn|report)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_fingerprint(rule_id: str, normalized_path: str, symbol: str,
                        normalized_evidence: str, occurrence: int) -> str:
    """Symbol-anchored, occurrence-indexed identity (semgrep match_based_id model): stable when the
    enclosing function moves (no line number in it), yet distinct for two identical lines in one
    symbol (the occurrence index). sha256(rule \0 path \0 symbol \0 evidence \0 occurrence)[:16]."""
    parts = (rule_id, normalized_path, symbol, normalized_evidence, str(occurrence))
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]


def _relpath(file: Path, root: Path) -> str:
    try:
        return file.relative_to(root).as_posix()
    except ValueError:
        return file.name


def _iter_py_files(root: Path):
    for f in root.rglob("*.py"):
        if any(p in _SKIP_DIRS or (p.startswith(".") and p != ".") for p in f.parts[:-1]):
            continue
        yield f


def _symbol_spans(tree: ast.AST) -> list[tuple[int, int, str]]:
    spans = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            spans.append((n.lineno, getattr(n, "end_lineno", n.lineno), n.name))
    return spans


def _enclosing(spans: list[tuple[int, int, str]], lineno: int) -> str:
    """Name of the innermost (smallest-span) def/class containing `lineno`, else '<module>'."""
    best = None
    for start, end, name in spans:
        if start <= lineno <= end and (best is None or (end - start) < (best[1] - best[0])):
            best = (start, end, name)
    return best[2] if best else "<module>"


# --------------------------------------------------------------------- lane: code-health (C1 F821)
def scan_json_literals(file: Path, root: Path) -> list[Finding]:
    """Deterministic AST detector for C1: a bare `true`/`false`/`null` value (a Name in Load ctx),
    almost always JSON pasted into Python -> a runtime NameError. Two-pass so each site gets an
    occurrence index within its (symbol, evidence) group."""
    try:
        src = file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    rel = _relpath(file, root)
    spans = _symbol_spans(tree)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in _JSON_LITERALS
                and isinstance(node.ctx, ast.Load)):
            raw = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else node.id
            hits.append((node.lineno, node, " ".join(raw.split()), _enclosing(spans, node.lineno)))
    hits.sort(key=lambda h: (h[0], h[1].col_offset))
    occ: dict[tuple, int] = {}
    out: list[Finding] = []
    for lineno, node, evidence, symbol in hits:
        key = ("F821-json-literal", symbol, evidence)
        i = occ.get(key, 0)
        occ[key] = i + 1
        py = _JSON_LITERALS[node.id]
        out.append(Finding(
            lane="code-health", rule_id="F821-json-literal", path=rel,
            span={"start_line": lineno, "end_line": getattr(node, "end_lineno", lineno),
                  "start_col": node.col_offset, "end_col": getattr(node, "end_col_offset", node.col_offset)},
            message=f"Undefined name '{node.id}' -- JSON literal in Python (use {py})",
            severity="high",
            fingerprint=compute_fingerprint("F821-json-literal", rel, symbol, evidence, i),
            evidence=evidence[:200], symbol=symbol,
            autofix_hint=f"Replace `{node.id}` with `{py}`", detector="ast:json-literal"))
    return out


_LANES = {"code-health": [scan_json_literals]}


def scan_paths(paths, lanes=None) -> list[Finding]:
    detectors = [d for name, ds in _LANES.items()
                 if lanes is None or name in lanes for d in ds]
    seen: dict[str, Finding] = {}
    for p in paths:
        p = Path(p)
        if p.is_file() and p.suffix == ".py":
            targets = [(p, p.parent)]
        elif p.is_dir():
            targets = [(f, p) for f in _iter_py_files(p)]
        else:
            targets = []
        for file, root in targets:
            for detector in detectors:
                for finding in detector(file, root):
                    seen.setdefault(finding.fingerprint, finding)
    return list(seen.values())


# --------------------------------------------------------------------- per-path policy
def policy_mode(path: str, policy=None) -> str:
    for glob, mode in (policy or DEFAULT_POLICY):
        if fnmatch.fnmatch(path, glob):
            return mode
    return "strict"


def apply_policy(findings: list[Finding], policy=None) -> None:
    for f in findings:
        f.policy_mode = policy_mode(f.path, policy)


# --------------------------------------------------------------------- baseline + counts
def counts_by_lane_severity(findings: list[Finding]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for f in findings:
        counts.setdefault(f.lane, {}).setdefault(f.severity, 0)
        counts[f.lane][f.severity] += 1
    return counts


def load_baseline(path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"fingerprints": set(), "counts": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fingerprints": set(), "counts": {}}
    return {"fingerprints": set((data.get("findings") or {}).keys()),
            "counts": data.get("counts_by_lane_severity") or {}}


def write_baseline_file(findings: list[Finding], path, counts=None) -> None:
    data = {
        "version": _BASELINE_VERSION,
        "counts_by_lane_severity": counts if counts is not None else counts_by_lane_severity(findings),
        "findings": {f.fingerprint: {"lane": f.lane, "rule_id": f.rule_id, "path": f.path,
                                     "severity": f.severity, "symbol": f.symbol}
                     for f in findings},
    }
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _update_counts(path, counts) -> None:
    """Tighten the ratchet: rewrite ONLY counts_by_lane_severity, keep the fingerprint set."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"version": _BASELINE_VERSION}
    data["counts_by_lane_severity"] = counts
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _counts_exceeded(current: dict, ceilings: dict) -> bool:
    if not ceilings:
        return False
    for lane, sevs in current.items():
        for sev, n in sevs.items():
            if n > ceilings.get(lane, {}).get(sev, float("inf")):
                return True
    return False


def run_audit(paths, baseline_path=None, write_baseline=False, ratchet=False,
              lanes=None, policy=None) -> tuple[list[Finding], int]:
    """Scan -> apply per-path policy -> apply baseline -> gate. Deterministic verdict (no LLM).
    Exit: 0 clean/within-baseline, 1 new blocking findings (or ratchet regression)."""
    findings = scan_paths(paths, lanes=lanes)
    apply_policy(findings, policy)
    bl = load_baseline(baseline_path) if baseline_path else {"fingerprints": set(), "counts": {}}
    for f in findings:
        f.suppressed = f.fingerprint in bl["fingerprints"]
    current_counts = counts_by_lane_severity(findings)

    if write_baseline and baseline_path is not None:
        write_baseline_file(findings, baseline_path, current_counts)
        for f in findings:
            f.suppressed = True
        return findings, 0

    blocking = [f for f in findings
                if not f.suppressed and f.policy_mode == "strict" and f.severity in BLOCKING]

    if ratchet:
        if blocking or _counts_exceeded(current_counts, bl["counts"]):
            return findings, 1
        if baseline_path is not None:  # success: tighten total-debt ceilings downward
            _update_counts(baseline_path, current_counts)
        return findings, 0

    return findings, (1 if blocking else 0)


# --------------------------------------------------------------------- report + CLI
def _console_report(findings: list[Finding]) -> str:
    active = [f for f in findings if not f.suppressed and f.policy_mode == "strict"]
    warned = [f for f in findings if not f.suppressed and f.policy_mode == "warn"]
    if not active and not warned:
        return f"repo-audit: clean ({len(findings)} finding(s), all within baseline/report-only)."
    out = [f"repo-audit: {len(active)} blocking, {len(warned)} warn "
           f"({len(findings) - len(active) - len(warned)} baselined/report):", ""]
    for f in sorted(active + warned, key=lambda x: (x.policy_mode, x.severity, x.path)):
        tag = f.severity if f.policy_mode == "strict" else f"warn:{f.severity}"
        out.append(f"  [{tag}] {f.path}:{f.span['start_line']}  {f.rule_id}  {f.message}")
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse
    from cortex_core.config import make_stdio_encoding_safe
    make_stdio_encoding_safe()
    p = argparse.ArgumentParser(prog="cortex-repo-audit",
                                description="Deterministic repo-health auditor (Phases 0-1).")
    p.add_argument("paths", nargs="*", default=["."], help="files/dirs to scan (default: .)")
    p.add_argument("--baseline", action="store_true", help="write/refresh the baseline file")
    p.add_argument("--baseline-file", default=".repo_audit_baseline.json")
    p.add_argument("--ratchet", action="store_true", help="fail if total debt exceeds baseline; tighten on success")
    p.add_argument("--format", choices=["console", "json"], default="console")
    p.add_argument("--lanes", default=None, help="comma-separated lane subset")
    try:
        a = p.parse_args(argv)
        lanes = a.lanes.split(",") if a.lanes else None
        findings, code = run_audit(a.paths or ["."], baseline_path=Path(a.baseline_file),
                                   write_baseline=a.baseline, ratchet=a.ratchet, lanes=lanes)
        if a.format == "json":
            print(json.dumps([f.to_dict() for f in findings], indent=2))
        else:
            print(_console_report(findings))
        return code
    except Exception as e:  # never a silent 0
        print(f"repo-audit: internal error: {type(e).__name__}: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
