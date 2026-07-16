"""Unified grading runner (Eval Flywheel P2) — the first vendor-neutral scoreboard.

Feeds ANY model's output through `extract.normalize_output` and a checker, aggregating a
`Scoreboard` over the requested lanes. Cases are `(case_authorship, prompt, expected)`; only
lanes explicitly requested are scored, so `single_vendor_fable` cases never leak into a
third-party gate (the panel's core fix). The model is any callable `str -> str` (a live tier
via `research._llm_complete`, or a `FakeModel` in tests) — no network is required to run.

The default checker is a normalized equality compare (the vendor-agnostic baseline). Real
per-lane deterministic checkers (`check_solution`, `check_candidate`, ...) plug in via
`checker=` for the production lanes; the scoreboard shape is identical either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .extract import normalize_output
from .metrics import wilson_ci


@dataclass
class ScoreRow:
    lane: str
    model: str
    n: int
    accuracy: float
    ci_low: float
    ci_high: float
    parse_failure: float = 0.0
    abstain: float = 0.0


@dataclass
class Scoreboard:
    rows: list = field(default_factory=list)


def _normalized_equal(out: str, expected: str) -> bool:
    a = normalize_output(out, "answer_only").answer or ""
    return a.strip().lower() == str(expected).strip().lower()


def run_eval(cases, lanes, model, model_name: str = "fake", checker=None) -> Scoreboard:
    """Grade `model` over `cases` restricted to `lanes`; return a per-lane Scoreboard."""
    wanted = set(lanes)
    check = checker or _normalized_equal
    tally: dict[str, list[int]] = {}
    for authorship, prompt, expected in cases:
        if authorship not in wanted:
            continue
        out = model(prompt)
        correct = bool(check(out, expected))
        succ_n = tally.setdefault(authorship, [0, 0])
        succ_n[1] += 1
        if correct:
            succ_n[0] += 1
    rows = []
    for lane, (succ, n) in tally.items():
        lo, hi = wilson_ci(succ, n)
        rows.append(ScoreRow(lane=lane, model=model_name, n=n,
                             accuracy=(succ / n if n else 0.0), ci_low=lo, ci_high=hi))
    return Scoreboard(rows=rows)
