"""GAP J5 — arbitration rigor: metrics BEYOND Cohen's kappa.

`cortex_core/calibration.py` scores a judge with κ vs a single-order,
Anthropic-authored gold. Real, but it HIDES three confounds the 2026-07-14
review named (GAP-CLOSURE-PLAN §J row J5):

  1. position bias        -> single-order κ cannot see a judge that prefers
                             whatever it reads first.
  2. family self-preference -> a single family-mean κ conflates vendor with
                             capability (the BIAS-AUDIT.md caveat). You need the
                             ERROR TYPES broken out per family to see it.
  3. punt bias            -> the one vendor-independent skew the runs found: weak
                             judges over-use `unverifiable` (BIAS-AUDIT.md: +0.56
                             qwen35b, +0.83 prometheus). κ penalises it but does
                             not NAME it; an explicit abstention_rate does.

These live ALONGSIDE κ — the existing κ leaderboard is untouched. See
`docs/ARBITRATION-RIGOR.md`.

Anti-circular guard (load-bearing)
----------------------------------
FP/FN and juror weighting are only trustworthy if the ground truth is INDEPENDENT
of the instrument being measured. `GroundTruth.provenance` distinguishes
`"objective"` (deterministic-oracle / mutation-seeded / reference-control labels —
gate-eligible) from `"judge_referenced"` (e.g. the Fable-authored anchor set —
CIRCULAR). Every metric derived from judge-referenced gold is stamped
`judge_referenced_only=True` and is EXCLUDED from any promotion gate.

Advisory-semi-gold ceiling (enforced)
-------------------------------------
Arbitration output — including this rigor report — is `advisory_semi_gold` and can
NEVER be promoted to hard gold. Every report dict hard-codes the non-gold flags and
`assert_not_promotable()` raises if any is violated. A weighted juror panel is a
decision aid; only a deterministic oracle or an explicit human binary turns a
verdict into state.

Stdlib-only. CLI: `cortex-arbitration-rigor`. No MCP tool (anti-bloat).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Reuse the calibration primitives — do NOT re-implement κ or family mapping.
from .calibration import _family, cohens_kappa, _run_from_results_file, load_anchor_set

#: The ONLY record type this lane emits. Mirrors arbitrate.RECORD_TYPE.
RECORD_TYPE = "advisory_semi_gold"

# Verdict buckets for false-pass / false-fail accounting.
PASS_VERDICTS = frozenset({"supported", "strongly_supported"})
FAIL_VERDICTS = frozenset({"unsupported"})
ABSTAIN_VERDICTS = frozenset({"unverifiable"})


def _bucket(verdict: str) -> str:
    if verdict in PASS_VERDICTS:
        return "pass"
    if verdict in FAIL_VERDICTS:
        return "fail"
    if verdict in ABSTAIN_VERDICTS:
        return "abstain"
    return "middle"  # partially_supported / verifiable_but_flawed


# ---------------------------------------------------------------------------
# Order-reversal consistency + position bias.
# ---------------------------------------------------------------------------

@dataclass
class PairedJudgment:
    """One judge's verdict on the SAME content pair shown in both orders.

    `winner_ab` is the content winner when shown A-then-B; `winner_ba` when shown
    B-then-A. Each is "A" | "B" | "tie" — the CONTENT that won, not the position.
    """

    case_id: str
    judge: str
    family: str
    winner_ab: str
    winner_ba: str


@dataclass
class OrderReversalResult:
    consistency: Optional[float]   # None when there is no paired data (honest)
    n_pairs: int
    coverage: float                # fraction of cases that HAVE both-order data


def order_reversal(pairs: list[PairedJudgment], *, total_cases: Optional[int] = None
                   ) -> OrderReversalResult:
    """Fraction of pairs whose CONTENT winner is unchanged across order.

    1.0 = order-invariant; <1.0 = the judge flips. With no paired data the
    consistency is None and coverage 0 — never a fabricated 1.0.
    """
    n = len(pairs)
    if total_cases is None:
        total_cases = n
    coverage = (n / total_cases) if total_cases else 0.0
    if n == 0:
        return OrderReversalResult(consistency=None, n_pairs=0, coverage=coverage)
    same = sum(1 for p in pairs if p.winner_ab == p.winner_ba)
    return OrderReversalResult(consistency=round(same / n, 4), n_pairs=n, coverage=round(coverage, 4))


def position_bias(pairs: list[PairedJudgment]) -> float:
    """Signed positional preference over both judgments of every pair.

    `(#first-position wins - #second-position wins) / #non-tie judgments`.
    0.0 = no positional preference; +1.0 = always picks whatever it reads first;
    -1.0 = always the second. Ties contribute to neither.

    In A-then-B order, content "A" sits in position 1; in B-then-A order, content
    "B" sits in position 1 — so we score each judgment by whether the position-1
    content won.
    """
    first = second = 0
    for p in pairs:
        # order AB: A is position 1
        if p.winner_ab == "A":
            first += 1
        elif p.winner_ab == "B":
            second += 1
        # order BA: B is position 1
        if p.winner_ba == "B":
            first += 1
        elif p.winner_ba == "A":
            second += 1
    denom = first + second
    if denom == 0:
        return 0.0
    return round((first - second) / denom, 4)


# ---------------------------------------------------------------------------
# per_judge_calibration_weight — not equal votes.
# ---------------------------------------------------------------------------

def calibration_weight(*, accuracy: Optional[float] = None,
                       kappa: Optional[float] = None, floor: float = 0.0) -> float:
    """Non-negative juror weight: 0 at chance, 1 at perfect, monotone rising.

    Prefers κ when supplied (already chance-corrected). Otherwise rescales accuracy
    above the 0.5 chance line: (acc - 0.5) / 0.5. Exposed so the weighting is
    auditable — it is a REPORTING aid, never an auto-promotion mechanism.
    """
    if kappa is not None:
        base = max(0.0, kappa)
    elif accuracy is not None:
        base = max(0.0, (accuracy - 0.5) / 0.5)
    else:
        raise ValueError("calibration_weight requires accuracy or kappa")
    return round(max(floor, min(1.0, base)), 4)


def weighted_vote(votes: list[tuple[str, float]]) -> tuple[str, float, dict[str, float]]:
    """Aggregate decisive PASS/FAIL votes by weight (NOT equal votes).

    Returns (decision, margin, buckets) where decision ∈ {"pass","fail","tie"} and
    margin is |pass-weight - fail-weight|. A well-calibrated juror outvotes several
    poorly-calibrated ones.
    """
    buckets = {"pass": 0.0, "fail": 0.0, "abstain": 0.0, "middle": 0.0}
    for verdict, w in votes:
        buckets[_bucket(verdict)] += float(w)
    p, f = buckets["pass"], buckets["fail"]
    if p > f:
        decision = "pass"
    elif f > p:
        decision = "fail"
    else:
        decision = "tie"
    return decision, round(abs(p - f), 6), {k: round(v, 6) for k, v in buckets.items()}


# ---------------------------------------------------------------------------
# Ground truth + beyond-κ per-judge metrics.
# ---------------------------------------------------------------------------

@dataclass
class GroundTruth:
    """Reference labels + their PROVENANCE (the anti-circular carrier).

    provenance == "objective"       -> deterministic-oracle / mutation-seeded /
                                       reference-control labels; gate-eligible.
    provenance == "judge_referenced"-> another model's opinion (e.g. Fable-authored
                                       anchor set); CIRCULAR -> judge_referenced_only.
    """

    labels: dict[str, str]           # case_id -> gold verdict string
    provenance: str = "objective"

    def is_objective(self) -> bool:
        return self.provenance == "objective"


def judge_ground_truth_metrics(judge: str, family: str,
                               verdicts: dict[str, dict[str, Any]],
                               gt: GroundTruth) -> dict[str, Any]:
    """Beyond-κ metrics for one judge vs a ground truth.

    ``verdicts`` maps case_id -> {"verdict": str, "confidence": float?}. Only cases
    present in BOTH verdicts and gt.labels are scored.
    """
    ids = [cid for cid in verdicts if cid in gt.labels]
    n = len(ids)

    gold_pass = [cid for cid in ids if _bucket(gt.labels[cid]) == "pass"]
    gold_fail = [cid for cid in ids if _bucket(gt.labels[cid]) == "fail"]

    def jb(cid: str) -> str:
        return _bucket(verdicts[cid]["verdict"])

    # false_pass: gold FAIL but judge PASS ; false_fail: gold PASS but judge FAIL
    fp = sum(1 for cid in gold_fail if jb(cid) == "pass")
    fn = sum(1 for cid in gold_pass if jb(cid) == "fail")
    abst = sum(1 for cid in ids if jb(cid) == "abstain")

    # calibration error: mean(|confidence - correct|) over cases WITH a confidence.
    conf_ids = [cid for cid in ids if verdicts[cid].get("confidence") is not None]
    cal_err = None
    if conf_ids:
        total = 0.0
        for cid in conf_ids:
            correct = 1.0 if verdicts[cid]["verdict"] == gt.labels[cid] else 0.0
            total += abs(float(verdicts[cid]["confidence"]) - correct)
        cal_err = round(total / len(conf_ids), 4)

    gold = [gt.labels[cid] for cid in ids]
    pred = [verdicts[cid]["verdict"] for cid in ids]
    acc = round(sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / n, 4) if n else None
    kappa = round(cohens_kappa(gold, pred), 4) if n else None

    return {
        "judge": judge,
        "family": family,
        "n": n,
        "cohens_kappa": kappa,                 # reported alongside — never removed
        "accuracy": acc,
        "false_pass_rate": round(fp / len(gold_fail), 4) if gold_fail else None,
        "false_fail_rate": round(fn / len(gold_pass), 4) if gold_pass else None,
        "abstention_rate": round(abst / n, 4) if n else None,
        "calibration_error": cal_err,
        "calibration_weight": calibration_weight(accuracy=acc) if acc is not None else 0.0,
        # anti-circular stamp: derived from a judge-referenced gold => not gate-eligible
        "judge_referenced_only": not gt.is_objective(),
        "gold_provenance": gt.provenance,
    }


def eligible_for_promotion_gate(metric: dict[str, Any]) -> bool:
    """A metric may feed a promotion gate ONLY if its gold was objective. A
    judge-referenced metric is diagnostic, never a trust claim."""
    return not metric.get("judge_referenced_only", True)


# ---------------------------------------------------------------------------
# Report (advisory-semi-gold; per-judge + per-family rollup).
# ---------------------------------------------------------------------------

def _quarantine_flags() -> dict[str, Any]:
    """The frozen non-gold contract, mirrored from arbitrate.AdvisoryRecord."""
    return {
        "record_type": RECORD_TYPE,
        "is_gold": False,
        "is_hard_gold": False,
        "trainable": False,
        "promotable": False,
        "quarantined": True,
    }


def assert_not_promotable(report: dict[str, Any]) -> None:
    """Raise if a report ever claims gold/trainable/promotable status. Belt-and-
    suspenders against a refactor routing arbitration output into a gold sink."""
    if report.get("record_type") != RECORD_TYPE:
        raise RuntimeError(f"arbitration rigor report must be {RECORD_TYPE!r}")
    for flag in ("is_gold", "is_hard_gold", "trainable", "promotable"):
        if report.get(flag):
            raise RuntimeError(f"arbitration rigor report is advisory-only; {flag} must be False")


def _per_family(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_fam: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_fam.setdefault(r["family"], []).append(r)

    def _mean(vals: list[Optional[float]]) -> Optional[float]:
        present = [v for v in vals if v is not None]
        return round(sum(present) / len(present), 4) if present else None

    out = []
    for fam, frows in sorted(by_fam.items()):
        out.append({
            "family": fam,
            "n_judges": len(frows),
            "mean_kappa": _mean([r["cohens_kappa"] for r in frows]),
            "mean_false_pass_rate": _mean([r["false_pass_rate"] for r in frows]),
            "mean_false_fail_rate": _mean([r["false_fail_rate"] for r in frows]),
            "mean_abstention_rate": _mean([r["abstention_rate"] for r in frows]),
            "mean_calibration_error": _mean([r["calibration_error"] for r in frows]),
            "judge_referenced_only": any(r["judge_referenced_only"] for r in frows),
        })
    return out


def build_rigor_report(runs: dict[tuple[str, str], dict[str, dict[str, Any]]],
                       gt: GroundTruth,
                       *, order_pairs: Optional[list[PairedJudgment]] = None,
                       total_cases: Optional[int] = None) -> dict[str, Any]:
    """Assemble the advisory-semi-gold rigor report.

    ``runs`` maps (judge, family) -> {case_id: {verdict, confidence?}}.
    ``order_pairs`` (optional) supplies both-order verdicts for order-reversal; if
    absent, order-reversal coverage is reported as 0 (honest, not fabricated).
    """
    per_judge = [judge_ground_truth_metrics(j, fam, v, gt) for (j, fam), v in sorted(runs.items())]

    order_pairs = order_pairs or []
    by_judge_pairs: dict[str, list[PairedJudgment]] = {}
    for p in order_pairs:
        by_judge_pairs.setdefault(p.judge, []).append(p)
    order_rows = []
    for row in per_judge:
        jp = by_judge_pairs.get(row["judge"], [])
        orr = order_reversal(jp, total_cases=total_cases or row["n"] or None)
        order_rows.append({
            "judge": row["judge"], "family": row["family"],
            "order_reversal_consistency": orr.consistency,
            "position_bias": position_bias(jp) if jp else None,
            "order_coverage": orr.coverage, "n_order_pairs": orr.n_pairs,
        })

    report = {
        **_quarantine_flags(),
        "note": ("SHADOW/QUARANTINE-ONLY arbitration-rigor rollup. Metrics live "
                 "ALONGSIDE Cohen's kappa; FP/FN from judge-referenced gold are "
                 "judge_referenced_only and NOT promotion-eligible."),
        "generated": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "gold_provenance": gt.provenance,
        "gold_n": len(gt.labels),
        "promotion_gate_eligible": gt.is_objective(),
        "per_judge": per_judge,
        "order_reversal": order_rows,
        "per_family": _per_family(per_judge),
    }
    assert_not_promotable(report)  # invariant, checked at construction
    return report


def _fmt(v: Any) -> str:
    return "—" if v is None else (f"{v:g}" if isinstance(v, (int, float)) else str(v))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Arbitration Rigor — beyond κ (advisory_semi_gold)",
        "",
        f"> `record_type={report['record_type']}` · **not gold, not trainable, not "
        "promotable.** Metrics live ALONGSIDE Cohen's κ (they do not replace the "
        "calibration leaderboard).",
        "",
        f"Generated: {report['generated']} · gold provenance: "
        f"**{report['gold_provenance']}** (n={report['gold_n']}) · "
        f"promotion-gate-eligible: **{report['promotion_gate_eligible']}**",
        "",
        "FP-rate — not κ — is the assurance number (a judge crediting broken work). "
        "abstention_rate makes the vendor-independent **punt bias** legible.",
        "",
        "## Per judge (beyond κ)",
        "",
        "| Judge | family | κ | false_pass_rate | false_fail_rate | abstention_rate "
        "| calibration_error | weight | judge_ref_only |",
        "|-------|--------|---|-----------------|-----------------|-----------------"
        "|-------------------|--------|----------------|",
    ]
    for r in report["per_judge"]:
        lines.append(
            f"| {r['judge']} | {r['family']} | {_fmt(r['cohens_kappa'])} "
            f"| {_fmt(r['false_pass_rate'])} | {_fmt(r['false_fail_rate'])} "
            f"| {_fmt(r['abstention_rate'])} | {_fmt(r['calibration_error'])} "
            f"| {_fmt(r['calibration_weight'])} | {r['judge_referenced_only']} |"
        )
    lines += [
        "",
        "## Order-reversal (position bias)",
        "",
        "| Judge | family | order_reversal_consistency | position_bias | coverage | n_pairs |",
        "|-------|--------|----------------------------|---------------|----------|---------|",
    ]
    for r in report["order_reversal"]:
        lines.append(
            f"| {r['judge']} | {r['family']} | {_fmt(r['order_reversal_consistency'])} "
            f"| {_fmt(r['position_bias'])} | {_fmt(r['order_coverage'])} | {r['n_order_pairs']} |"
        )
    lines += [
        "",
        "## Per family (self-preference / punt-bias breakout)",
        "",
        "| family | n_judges | mean κ | mean_false_pass | mean_false_fail | "
        "mean_abstention | mean_calib_err |",
        "|--------|----------|--------|-----------------|-----------------|"
        "-----------------|----------------|",
    ]
    for f in report["per_family"]:
        lines.append(
            f"| {f['family']} | {f['n_judges']} | {_fmt(f['mean_kappa'])} "
            f"| {_fmt(f['mean_false_pass_rate'])} | {_fmt(f['mean_false_fail_rate'])} "
            f"| {_fmt(f['mean_abstention_rate'])} | {_fmt(f['mean_calibration_error'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_rigor_report(runs: dict[tuple[str, str], dict[str, dict[str, Any]]],
                       gt: GroundTruth, *, workspace: str | Path,
                       order_pairs: Optional[list[PairedJudgment]] = None,
                       ) -> tuple[Path, Path]:
    """Render + persist. Writes the markdown rollup AND appends per-judge rows to a
    committed jsonl ledger (mirrors how calibration writes its leaderboard). Returns
    (markdown_path, jsonl_path)."""
    report = build_rigor_report(runs, gt, order_pairs=order_pairs)
    out_dir = Path(workspace) / "calibration" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "ARBITRATION-RIGOR-REPORT.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")

    jsonl_path = out_dir / "arbitration_rigor.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as f:
        for r in report["per_judge"]:
            row = {**_quarantine_flags(), "generated": report["generated"],
                   "gold_provenance": gt.provenance, **r}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return md_path, jsonl_path


# ---------------------------------------------------------------------------
# Real-data rollup over the committed calibration store (demonstrable, honest).
# ---------------------------------------------------------------------------

def rollup_from_calibration(workspace: str | Path | None = None,
                            anchor: str | Path | None = None) -> tuple[Path, Path]:
    """Build the rigor report over the committed calibration verdict store.

    The anchor-set gold is Fable/Anthropic-authored -> provenance is
    `judge_referenced`, so every derived FP/FN/abstention is `judge_referenced_only`
    (correct per the anti-circular rule; NOT promotion-eligible). Wiring an
    `evals/objective_*` deterministic label set through the SAME engine (its
    `provenance="objective"` path) is the follow-up that yields gate-eligible numbers.
    """
    from .config import resolve_workspace
    ws = Path(workspace) if workspace else resolve_workspace()
    cases = load_anchor_set(anchor)
    gt = GroundTruth(labels={c.id: c.gold_verdict for c in cases},
                     provenance="judge_referenced")

    results_dir = ws / "calibration" / "results"
    runs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    best_kappa: dict[str, float] = {}
    for path in sorted(results_dir.glob("*.json")):
        run = _run_from_results_file(path, cases)
        if not run or not run.rows:
            continue
        k = cohens_kappa(run.golds(), run.preds())
        if run.tier in best_kappa and best_kappa[run.tier] >= k:
            continue  # keep the best run per tier (matches leaderboard de-dupe)
        best_kappa[run.tier] = k
        verdicts = {r["id"]: {"verdict": r["pred"], "confidence": r.get("confidence")}
                    for r in run.rows}
        runs[(run.tier, _family(run.tier))] = verdicts
    return write_rigor_report(runs, gt, workspace=ws)


def main(argv: Optional[list[str]] = None) -> int:
    from .config import make_stdio_encoding_safe
    make_stdio_encoding_safe()
    p = argparse.ArgumentParser(
        prog="cortex-arbitration-rigor",
        description=("Arbitration rigor (beyond κ): order-reversal/position-bias, "
                     "per-judge calibration weight, per-family FP/FN/abstention/"
                     "calibration-error. Output is advisory_semi_gold — never gold."),
    )
    p.add_argument("--anchor", default=None, help="anchor set path (gold labels)")
    p.add_argument("--workspace", default=None, help="workspace root")
    args = p.parse_args(argv)
    try:
        md_path, jsonl_path = rollup_from_calibration(workspace=args.workspace, anchor=args.anchor)
    except Exception as exc:  # noqa: BLE001
        print(f"arbitration-rigor rollup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {md_path}")
    print(f"appended {jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
