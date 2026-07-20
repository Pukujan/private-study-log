"""Phase 4.4 judge calibration harness.

Runs a judge over the gold-labeled anchor set (calibration/anchor_set.yaml) and
scores its agreement with the gold verdicts — accuracy + Cohen's kappa + a
confusion matrix. This is how every rung below Fable is measured: a cheap judge
is only trusted for volume as far as its kappa-vs-Fable clears a threshold
(>=0.6 general, >=0.8 high-stakes).

Two judge sources:
  - API / local rungs (GLM, DeepSeek, Qwen, Ollama, OpenRouter): dispatched here
    directly via cortex_core.judge.llm_judge.
  - In-harness rungs (Fable-Max, Opus, Sonnet, Haiku): NOT callable from Python;
    the orchestrator runs them as subagents and feeds their verdicts back in via
    score_verdicts() / a saved verdicts file.

No sklearn/numpy dependency — kappa and the confusion matrix are computed directly.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import make_stdio_encoding_safe, resolve_workspace
from .evaluator import AtomicClaim, Verdict
from . import judge as J

_VERDICTS = [v.value for v in Verdict]  # canonical label order


@dataclass
class AnchorCase:
    id: str
    task_type: str
    claim: str
    evidence: list[dict[str, Any]]
    gold_verdict: str
    gold_source: str = "draft-opus"
    rationale: str = ""
    probes: str = ""


def load_anchor_set(path: str | Path | None = None) -> list[AnchorCase]:
    if path is None:
        path = resolve_workspace() / "calibration" / "anchor_set.yaml"
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = []
    for c in data.get("cases", []):
        cases.append(
            AnchorCase(
                id=c["id"],
                task_type=c["task_type"],
                claim=c["claim"],
                evidence=c.get("evidence", []) or [],
                gold_verdict=c["gold_verdict"],
                gold_source=c.get("gold_source", "draft-opus"),
                rationale=c.get("rationale", ""),
                probes=c.get("probes", ""),
            )
        )
    return cases


def cohens_kappa(gold: list[str], pred: list[str], labels: list[str] | None = None) -> float:
    """Cohen's kappa between two label sequences. Returns 1.0 for perfect agreement,
    0.0 for chance-level, negative for worse-than-chance. Guards the degenerate
    single-label case (pe == 1.0) by returning the raw agreement."""
    if len(gold) != len(pred) or not gold:
        return 0.0
    labels = labels or sorted(set(gold) | set(pred))
    n = len(gold)
    po = sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / n
    pe = 0.0
    for lab in labels:
        pg = sum(1 for g in gold if g == lab) / n
        pp = sum(1 for p in pred if p == lab) / n
        pe += pg * pp
    if pe >= 1.0:
        return po  # all one label — kappa undefined; report agreement
    return (po - pe) / (1.0 - pe)


def confusion_matrix(gold: list[str], pred: list[str], labels: list[str]) -> dict[str, dict[str, int]]:
    m = {g: {p: 0 for p in labels} for g in labels}
    for g, p in zip(gold, pred, strict=False):
        if g in m and p in m[g]:
            m[g][p] += 1
    return m


@dataclass
class JudgeRun:
    tier: str
    rows: list[dict[str, Any]] = field(default_factory=list)  # per-case results

    def golds(self) -> list[str]:
        return [r["gold"] for r in self.rows]

    def preds(self) -> list[str]:
        return [r["pred"] for r in self.rows]

    def summary(self) -> dict[str, Any]:
        gold, pred = self.golds(), self.preds()
        n = len(gold)
        acc = sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / n if n else 0.0
        kappa = cohens_kappa(gold, pred, _VERDICTS)
        # accuracy restricted to the subtle/probe cases (where lexical grading fails)
        probe_rows = [r for r in self.rows if r.get("probes")]
        probe_acc = (
            sum(1 for r in probe_rows if r["gold"] == r["pred"]) / len(probe_rows)
            if probe_rows else None
        )
        return {
            "tier": self.tier,
            "n": n,
            "accuracy": round(acc, 4),
            "cohens_kappa": round(kappa, 4),
            "probe_accuracy": round(probe_acc, 4) if probe_acc is not None else None,
            "probe_n": len(probe_rows),
            "confusion": confusion_matrix(gold, pred, _VERDICTS),
            "disagreements": [
                {"id": r["id"], "gold": r["gold"], "pred": r["pred"],
                 "probes": r.get("probes", ""), "reason": r.get("reason", "")[:200]}
                for r in self.rows if r["gold"] != r["pred"]
            ],
        }


def load_rubric_v2(workspace: str | Path | None = None) -> str | None:
    """Load Fable's improved rubric (rubric_v2) from calibration/fable_gold.yaml."""
    ws = resolve_workspace(workspace) if workspace else resolve_workspace()
    p = ws / "calibration" / "fable_gold.yaml"
    if not p.is_file():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8")).get("rubric_v2")


def load_rubric(variant: str, workspace: str | Path | None = None) -> str | None:
    """Resolve a judge rubric (system-prompt) variant.

    v1       -> None (judge.py's built-in _SYSTEM_PROMPT is used)
    v2       -> Fable's full ordered-stopping-rule rubric (fable_gold.yaml)
    v2-lite  -> stripped version for small models (calibration/rubrics/prompts/v2_lite.txt)
    """
    if variant == "v1":
        return None
    if variant == "v2":
        return load_rubric_v2(workspace)
    ws = resolve_workspace(workspace) if workspace else resolve_workspace()
    p = ws / "calibration" / "rubrics" / "prompts" / f"{variant.replace('-', '_')}.txt"
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return None


def run_api_judge(
    tier: str,
    cases: list[AnchorCase],
    workspace: str | Path | None = None,
    *,
    timeout: float = 150.0,
    retries: int = 1,
    verbose: bool = True,
    system_prompt: str | None = None,
    rubric_label: str = "v1",
    prompt_style: str = "direct",
    model_override: str | None = None,
    label_override: str | None = None,
) -> JudgeRun:
    """Run one dispatchable (API/local) judge tier over every anchor case."""
    ws = workspace if workspace is not None else resolve_workspace()
    label = label_override or tier
    if rubric_label != "v1":
        label += f"@{rubric_label}"
    if prompt_style != "direct":
        label += "+rf"  # reasoning-first prompt variant
    run = JudgeRun(tier=label)
    for i, c in enumerate(cases, 1):
        claim = AtomicClaim(claim_id=c.id, task_type=c.task_type, description=c.claim)
        grade = J.llm_judge(
            claim, c.evidence, tier=tier, workspace=ws, timeout=timeout, retries=retries,
            system_prompt=system_prompt, prompt_style=prompt_style, model_override=model_override,
        )
        row = {
            "id": c.id, "gold": c.gold_verdict, "pred": grade.verdict.value,
            "confidence": grade.confidence, "reason": grade.reasoning,
            "probes": c.probes, "task_type": c.task_type,
        }
        run.rows.append(row)
        if verbose:
            mark = "OK " if row["gold"] == row["pred"] else "XX "
            print(f"  [{i:2d}/{len(cases)}] {c.id} {mark} gold={c.gold_verdict:20s} pred={row['pred']}")
    return run


def score_verdicts(tier: str, cases: list[AnchorCase], verdicts: dict[str, str]) -> JudgeRun:
    """Score externally-produced verdicts (e.g. from an in-harness subagent judge).

    ``verdicts`` maps case id -> predicted verdict string. Missing ids are scored as
    'unverifiable' (a non-answer counts against the judge, honestly)."""
    run = JudgeRun(tier=tier)
    for c in cases:
        pred = verdicts.get(c.id, "unverifiable")
        run.rows.append({
            "id": c.id, "gold": c.gold_verdict, "pred": pred,
            "confidence": None, "reason": "(subagent verdict)",
            "probes": c.probes, "task_type": c.task_type,
        })
    return run


def save_run(run: JudgeRun, workspace: str | Path | None = None, stamp: str | None = None) -> Path:
    ws = resolve_workspace(workspace) if workspace else resolve_workspace()
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ws / "calibration" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run.tier}-{stamp}.json"
    payload = {"summary": run.summary(), "rows": run.rows, "generated": stamp}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_subagent_verdicts(path: str | Path) -> tuple[str, dict[str, str]]:
    """Load an in-harness (subagent) judge's verdict file -> (judge_name, {id: verdict})."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    judge = data.get("judge", Path(path).stem.replace("_verdicts", ""))
    verdicts = {cid: v.get("verdict", "unverifiable") for cid, v in data.get("verdicts", {}).items()}
    return judge, verdicts


def _run_from_results_file(path: Path, cases: list[AnchorCase]) -> JudgeRun | None:
    """Rebuild a JudgeRun from any results file (harness rows OR subagent verdicts),
    scoring against the CURRENT gold so the leaderboard is uniform."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "rows" in data:  # harness format — re-score rows against current gold
        tier = data.get("summary", {}).get("tier", path.stem)
        gold_by_id = {c.id: c for c in cases}
        run = JudgeRun(tier=tier)
        for r in data["rows"]:
            c = gold_by_id.get(r["id"])
            if not c:
                continue
            run.rows.append({**r, "gold": c.gold_verdict, "probes": c.probes})
        return run
    if "verdicts" in data:  # subagent format
        judge, verdicts = load_subagent_verdicts(path)
        return score_verdicts(judge, cases, verdicts)
    return None


def build_leaderboard(workspace: str | Path | None = None, anchor: str | Path | None = None) -> str:
    """Aggregate every results file into one κ-ranked leaderboard (markdown)."""
    ws = resolve_workspace(workspace) if workspace else resolve_workspace()
    cases = load_anchor_set(anchor)
    results_dir = ws / "calibration" / "results"
    summaries: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.json")):
        run = _run_from_results_file(path, cases)
        if run and run.rows:
            summaries.append(run.summary())
    # de-dupe by tier keeping the highest-kappa run (latest rubric usually)
    best: dict[str, dict[str, Any]] = {}
    for s in summaries:
        if s["tier"] not in best or s["cohens_kappa"] > best[s["tier"]]["cohens_kappa"]:
            best[s["tier"]] = s
    ranked = sorted(best.values(), key=lambda s: s["cohens_kappa"], reverse=True)

    lines = [
        "# Judge Calibration Leaderboard",
        "",
        f"Anchor set: {len(cases)} cases, gold_source: {cases[0].gold_source if cases else 'n/a'} "
        "(Fable-Max authoritative, 18/18 with Opus draft).",
        "Ranked by Cohen's kappa vs gold. Trust bar: κ≥0.6 general, κ≥0.8 high-stakes.",
        "",
        "| Rank | Judge | Accuracy | Cohen κ | Probe acc | Trust |",
        "|------|-------|----------|---------|-----------|-------|",
    ]
    for i, s in enumerate(ranked, 1):
        k = s["cohens_kappa"]
        trust = "high-stakes ✓" if k >= 0.8 else ("general ✓" if k >= 0.6 else "not yet")
        pa = s["probe_accuracy"]
        lines.append(
            f"| {i} | {s['tier']} | {s['accuracy']} | {k} | {pa if pa is not None else '—'} | {trust} |"
        )
    lines.append("")
    return "\n".join(lines)


_ANTHROPIC = {"sonnet", "haiku", "opus", "fable-max"}
_NINEROUTER_ANTHROPIC = {"9r-sonnet-4.6", "9r-opus-4.6", "9r-sonnet-4.5"}
_NINEROUTER_OPENAI = {"9r-gpt-oss-120b", "9r-gpt-oss-ollama"}
_NINEROUTER_GOOGLE = {"9r-gemini-3-flash", "9r-gemini-3.5-flash", "9r-gemini-3.1-pro", "9r-gemini-preview"}


def _family(tier: str) -> str:
    base = tier.split("@")[0]
    if base in _ANTHROPIC or base in _NINEROUTER_ANTHROPIC:
        return "anthropic"
    if base == "prometheus":
        return "independent-eval"
    if base == "glm5.2":
        return "zhipu"
    if base == "qwen35b":
        return "qwen"
    if base == "ollama":
        return "qwen-local"
    if base in _NINEROUTER_OPENAI:
        return "openai"
    if base in _NINEROUTER_GOOGLE:
        return "google"
    if base == "9r-deepseek-3.2":
        return "deepseek"
    return "other-vendor"


def bias_audit(workspace: str | Path | None = None, anchor: str | Path | None = None) -> str:
    """Audit judge biases against the (Anthropic/Fable-authored) gold.

    Reports, per judge run: verdict-distribution skew vs gold (leniency = over-crediting
    'supported'; unverifiable-overuse = punting), the dominant directional error, and
    kappa. Then a FAMILY summary — because the gold is Anthropic-authored, if
    Anthropic-family judges agree far more than non-Anthropic ones (at matched rubric
    and comparable capability) that is family/self-preference bias. Prometheus (a
    purpose-built non-Anthropic evaluator) is the independent anchor: high Prometheus
    agreement with the gold is evidence the gold is NOT just Anthropic-family bias.
    """
    ws = resolve_workspace(workspace) if workspace else resolve_workspace()
    cases = load_anchor_set(anchor)
    n = len(cases)
    gold = [c.gold_verdict for c in cases]
    gold_dist = {v: gold.count(v) / n for v in _VERDICTS}

    runs: list[JudgeRun] = []
    for path in sorted((ws / "calibration" / "results").glob("*.json")):
        r = _run_from_results_file(path, cases)
        if r and r.rows:
            runs.append(r)
    # de-dupe by tier, best kappa
    best: dict[str, JudgeRun] = {}
    for r in runs:
        s = r.summary()
        if r.tier not in best or s["cohens_kappa"] > best[r.tier].summary()["cohens_kappa"]:
            best[r.tier] = r

    lines = [
        "# Judge Bias Audit",
        "",
        f"Gold: {n} cases, Fable-Max authored (Anthropic family). Gold verdict mix: "
        + ", ".join(f"{v.split('_')[0]}={gold_dist[v]:.2f}" for v in _VERDICTS),
        "",
        "**Skew** = judge_rate − gold_rate for each verdict (+ = over-uses it).",
        "leniency = supported skew (over-credits); punt = unverifiable skew (won't judge).",
        "",
        "| Judge | family | κ | leniency | severity(unsup) | punt(unverif) | top error |",
        "|-------|--------|-----|----------|-----------------|---------------|-----------|",
    ]
    fam_kappa: dict[str, list[float]] = {}
    for tier, run in sorted(best.items(), key=lambda kv: kv[1].summary()["cohens_kappa"], reverse=True):
        s = run.summary()
        preds = run.preds()
        dist = {v: preds.count(v) / n for v in _VERDICTS}
        leniency = dist["supported"] - gold_dist["supported"]
        severity = dist["unsupported"] - gold_dist["unsupported"]
        punt = dist["unverifiable"] - gold_dist["unverifiable"]
        # dominant directional error
        errs: dict[tuple[str, str], int] = {}
        for row in run.rows:
            if row["gold"] != row["pred"]:
                errs[(row["gold"], row["pred"])] = errs.get((row["gold"], row["pred"]), 0) + 1
        top = max(errs.items(), key=lambda kv: kv[1])[0] if errs else None
        top_s = f"{top[0].split('_')[0]}→{top[1].split('_')[0]}" if top else "—"
        fam = _family(tier)
        fam_kappa.setdefault(f"{fam}", []).append(s["cohens_kappa"])
        lines.append(
            f"| {tier} | {fam} | {s['cohens_kappa']:.3f} | {leniency:+.2f} | {severity:+.2f} "
            f"| {punt:+.2f} | {top_s} |"
        )

    lines += ["", "## Family summary (bias check)", ""]
    for fam, ks in sorted(fam_kappa.items()):
        lines.append(f"- **{fam}**: mean κ {sum(ks)/len(ks):.3f} over {len(ks)} run(s)")
    lines += [
        "",
        "> Caveat: family κ conflates VENDOR with CAPABILITY (Anthropic judges here also "
        "happen to be the most capable). The clean family-bias test is whether the "
        "purpose-built non-Anthropic **prometheus** agrees with the Anthropic gold as much "
        "as a capability-matched Anthropic judge — read its row above, not just the family mean.",
    ]
    return "\n".join(lines)


def _print_summary(s: dict[str, Any]) -> None:
    print(f"\n=== {s['tier']} ===")
    print(f"  n={s['n']}  accuracy={s['accuracy']}  cohen_kappa={s['cohens_kappa']}"
          f"  probe_acc={s['probe_accuracy']} (n={s['probe_n']})")
    if s["disagreements"]:
        print("  disagreements:")
        for d in s["disagreements"]:
            tag = f" [{d['probes']}]" if d["probes"] else ""
            print(f"    {d['id']}{tag}: gold={d['gold']} pred={d['pred']}")


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Judge calibration harness")
    parser.add_argument("--tier", help="single dispatchable tier to run")
    parser.add_argument("--all", action="store_true", help="run all dispatchable tiers")
    parser.add_argument("--anchor", default=None, help="anchor set path")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--leaderboard", action="store_true",
                        help="aggregate all results/*.json into a kappa-ranked leaderboard")
    parser.add_argument("--bias", action="store_true",
                        help="audit judge biases (verdict skew, family bias) vs gold")
    parser.add_argument("--rubric", default="v1",
                        help="judge rubric variant: v1 | v2 | v2-lite (or any prompts/<name>.txt)")
    parser.add_argument("--prompt-style", default="direct", choices=["direct", "reasoning-first"],
                        help="prompt-versioning axis (distinct from rubric): output protocol")
    parser.add_argument("--model", default=None, help="override the tier's model (e.g. an ollama tag)")
    parser.add_argument("--label", default=None, help="override the run label (leaderboard row name)")
    args = parser.parse_args(argv)

    if args.leaderboard:
        md = build_leaderboard(anchor=args.anchor)
        out = resolve_workspace() / "calibration" / "results" / "LEADERBOARD.md"
        out.write_text(md, encoding="utf-8")  # UTF-8 file is the source of truth
        print(md)
        print(f"saved -> {out}")
        return 0

    if args.bias:
        md = bias_audit(anchor=args.anchor)
        out = resolve_workspace() / "calibration" / "results" / "BIAS-AUDIT.md"
        out.write_text(md, encoding="utf-8")
        print(md)
        print(f"saved -> {out}")
        return 0

    cases = load_anchor_set(args.anchor)
    print(f"Loaded {len(cases)} anchor cases "
          f"(gold_source: {cases[0].gold_source if cases else 'n/a'})")

    tiers = J.DISPATCHABLE_TIERS if args.all else ([args.tier] if args.tier else [])
    if not tiers:
        parser.error("give --tier <name> or --all")

    system_prompt = None
    if args.rubric != "v1":
        system_prompt = load_rubric(args.rubric)
        if not system_prompt:
            parser.error(f"rubric {args.rubric!r} not found (fable_gold rubric_v2 or "
                         f"calibration/rubrics/prompts/{args.rubric}.txt)")
        print(f"Using rubric variant: {args.rubric}")

    for tier in tiers:
        try:
            J.get_tier_config(tier)
        except Exception as e:  # noqa: BLE001
            print(f"\n=== {tier} === SKIP (not configured: {e})")
            continue
        print(f"\nRunning {tier} over {len(cases)} cases (rubric {args.rubric}, "
              f"prompt {args.prompt_style})...")
        run = run_api_judge(tier, cases, system_prompt=system_prompt, rubric_label=args.rubric,
                            prompt_style=args.prompt_style, model_override=args.model,
                            label_override=args.label)
        _print_summary(run.summary())
        if not args.no_save:
            p = save_run(run)
            print(f"  saved -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
