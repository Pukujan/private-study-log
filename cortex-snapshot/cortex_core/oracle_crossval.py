"""Oracle cross-validation harness (GAP D1 + D2).

**D1 — oracle strength, not just count.** The objective eval lanes each decide pass/fail with a
deterministic checker. Most lanes are *single-authority*: only tool-calling (BFCL) has a measured
agreement between two independent checker implementations (99.93%, see
`evals/objective_tool_calling/STAGE2A_TOOL_CALLING_REPORT.md`). This harness replicates that dual
cross-validation pattern for the other high-impact deterministic lanes and reports, per lane:

  * mutation_score  — fraction of known-bad candidates the PRIMARY checker kills (fail). A strong
                      oracle kills ~all of them; a weak one lets mutants through.
  * agreement / FP / FN vs an INDEPENDENT second checker — FP = primary passed what the
                      independent failed (too lenient); FN = primary failed what the independent
                      passed (too strict). Disagreements are surfaced, never hidden.

Lanes and their second authority:
  datetime  — JDN/leap-rule reimplementation (`cortex_core/_indep_datetime.py`); tz_convert
              honestly abstains (no independent IANA tz DB in stdlib).
  regex     — from-scratch Thompson-NFA matcher (`cortex_core/_indep_regex.py`).
  ledger    — the lane's own pre-existing integer-cents second impl (`check_ledger_intcents`).
  invoice   — the lane's own pre-existing integer-cents second impl (`check_invoice_intcents`).

(ledger + invoice already shipped a second authority — this harness measures it uniformly and
records that D1 was already partially satisfied there. Security already cross-checks against
`bandit`. The lanes this newly cross-validates are datetime + regex.)

**D2 — hidden holdout actually catching gaming.** `evals/objective_coding/holdout_gaming.py`
constructs *real* gamed attempts (visible-suite memorizers + the fixtures' buggy variants) that an
implementer with no holdout access could write, and proves the hidden holdout catches them
(catch-rate > 0). This module's CLI runs both D1 and D2.

Deterministic, offline, stdlib-only. No judge anywhere in any verdict path.
"""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from cortex_core import _indep_datetime as idt
from cortex_core import _indep_regex as irx

# Primary checkers (the current single authorities).
from evals.objective_datetime_correctness.checker_datetime import check_record as dt_check
from evals.objective_datetime_correctness.fixtures_datetime import FIXTURES as DT_FIXTURES
from evals.objective_regex_correctness.checker_regex import grade_regex, _GUARD as _REGEX_GUARD
from evals.objective_regex_correctness.fixtures_regex import RECORDS as RX_RECORDS
from evals.objective_ledger_balances.checker_ledger import check_ledger, check_ledger_intcents
from evals.objective_ledger_balances.fixtures_ledger import fixtures as ledger_fixtures
from evals.objective_invoice_reconciliation.checker_invoice import (
    check_invoice, check_invoice_intcents)
from evals.objective_invoice_reconciliation.fixtures_invoice import fixtures as invoice_fixtures


# --------------------------------------------------------------------------- data model
@dataclass
class Candidate:
    id: str
    intended: str          # "pass" | "fail" (ground-truth intent of this candidate)
    kind: str              # "fixture" | "mutant"
    payload: dict


@dataclass
class LaneReport:
    name: str
    second_authority: str
    n_fixtures: int = 0
    n_mutants: int = 0
    negatives: int = 0              # intended-fail candidates scored (excl. equivalent mutants)
    killed: int = 0                 # negatives the primary marked fail
    equivalent: int = 0             # generated mutants primary marked pass (equivalent mutant)
    mutation_score: float = 0.0
    comparable: int = 0             # candidates where independent did not abstain
    agree: int = 0
    fp: int = 0                     # primary=pass, independent=fail (primary too lenient)
    fn: int = 0                     # primary=fail, independent=pass (primary too strict)
    abstain: int = 0
    agreement: float = 1.0
    disagreements: list = field(default_factory=list)

    def asdict(self):
        d = self.__dict__.copy()
        return d


# --------------------------------------------------------------------------- Lane base
class Lane:
    name = ""
    second_authority = ""

    def candidates(self):
        raise NotImplementedError

    def primary(self, payload) -> str:
        raise NotImplementedError

    def independent(self, payload) -> str:
        raise NotImplementedError


# --------------------------------------------------------------------------- datetime lane
def _shift_date_str(s: str, days: int) -> str:
    return idt.add_days(s, days)


class DatetimeLane(Lane):
    name = "datetime"
    second_authority = "cortex_core._indep_datetime (JDN/leap-rule reimplementation)"

    def candidates(self):
        cands = []
        for fx in DT_FIXTURES:
            intended = "pass" if fx["expected_label"] == "CORRECT" else "fail"
            cands.append(Candidate(fx["id"], intended, "fixture",
                                   {"op": fx["op"], "inputs": fx["inputs"],
                                    "answer": fx["candidate_answer"]}))
        # generated mutants: perturb each CORRECT fixture's answer into a definitely-wrong one.
        for fx in DT_FIXTURES:
            if fx["expected_label"] != "CORRECT":
                continue
            mut = self._perturb(fx["op"], fx["inputs"], fx["candidate_answer"])
            if mut is None or mut == fx["candidate_answer"]:
                continue
            cands.append(Candidate(fx["id"] + "__gen_mutant", "fail", "mutant",
                                   {"op": fx["op"], "inputs": fx["inputs"], "answer": mut}))
        return cands

    @staticmethod
    def _perturb(op, inputs, ans):
        if op in ("add_months", "add_days"):
            return _shift_date_str(ans, 1)
        if op == "is_leap_year":
            return "not_leap" if ans == "leap" else "leap"
        if op == "weekday":
            order = list(idt._WEEKDAYS)
            return order[(order.index(ans) + 1) % 7]
        if op == "day_diff":
            return ans + 1
        if op == "iso_week":
            yr, wk = ans.split("-W")
            return f"{yr}-W{int(wk) + 1:02d}"
        if op == "tz_convert":
            # shift the hour by one; keeps format, changes the instant
            return ans.replace("T0", "T1", 1) if "T0" in ans else ans[:11] + "00" + ans[13:]
        return None

    def primary(self, payload) -> str:
        r = dt_check(payload["op"], payload["inputs"], payload["answer"])
        return "pass" if r.objective_label == "CORRECT" else "fail"

    def independent(self, payload) -> str:
        return idt.independent_verdict(payload["op"], payload["inputs"], payload["answer"])


# --------------------------------------------------------------------------- regex lane
_RX_MUTATORS = [
    ("drop_start_anchor", lambda p: p[1:] if p.startswith("^") else None),
    ("drop_end_anchor", lambda p: p[:-1] if p.endswith("$") else None),
    ("digit_to_word", lambda p: p.replace(r"\d", r"\w", 1) if r"\d" in p else None),
    ("unescape_dot", lambda p: p.replace(r"\.", ".", 1) if r"\." in p else None),
]


class RegexLane(Lane):
    name = "regex"
    second_authority = "cortex_core._indep_regex (from-scratch Thompson-NFA matcher)"

    def candidates(self):
        cands = []
        for fx in RX_RECORDS:
            intended = "pass" if fx["objective_label"] == "correct" else "fail"
            cands.append(Candidate(fx["id"], intended, "fixture", self._pl(fx)))
        # generated mutants from each CORRECT pattern.
        for fx in RX_RECORDS:
            if fx["objective_label"] != "correct":
                continue
            for mname, mfn in _RX_MUTATORS:
                mp = mfn(fx["candidate_regex"])
                if mp is None or mp == fx["candidate_regex"]:
                    continue
                pl = self._pl(fx)
                pl["candidate_regex"] = mp
                cands.append(Candidate(f"{fx['id']}__{mname}", "fail", "mutant", pl))
        return cands

    @staticmethod
    def _pl(fx):
        return {"candidate_regex": fx["candidate_regex"], "match_mode": fx["match_mode"],
                "must_match": list(fx["must_match"]), "must_not_match": list(fx["must_not_match"])}

    def primary(self, payload) -> str:
        r = grade_regex(payload["candidate_regex"], payload["match_mode"],
                        payload["must_match"], payload["must_not_match"])
        return "pass" if r["verdict"] == "correct" else "fail"

    def independent(self, payload) -> str:
        return irx.independent_verdict(payload["candidate_regex"], payload["match_mode"],
                                       payload["must_match"], payload["must_not_match"])


# --------------------------------------------------------------------------- ledger lane
def _bump_dollars(s: str, by: int = 1) -> str:
    neg = s.startswith("-")
    body = s[1:] if neg else s
    if "." in body:
        whole, frac = body.split(".", 1)
    else:
        whole, frac = body, "00"
    whole = str(int(whole) + by)
    out = f"{whole}.{frac}"
    return ("-" + out) if neg else out


class LedgerLane(Lane):
    name = "ledger"
    second_authority = "check_ledger_intcents (pre-existing integer-cents second impl)"

    def candidates(self):
        cands = []
        for fx in ledger_fixtures():
            intended = "pass" if fx["label"] == "BALANCED" else "fail"
            cands.append(Candidate(fx["id"], intended, "fixture", fx))
        # generated mutant: break one BALANCED ledger's reported balance -> unreconciled.
        for fx in ledger_fixtures():
            if fx["label"] != "BALANCED":
                continue
            mut = self._perturb(fx)
            if mut is not None:
                cands.append(Candidate(fx["id"] + "__gen_mutant", "fail", "mutant", mut))
        return cands

    @staticmethod
    def _perturb(fx):
        mut = copy.deepcopy(fx)
        for _acct, meta in mut["accounts"].items():
            if "reported" in meta:
                meta["reported"] = _bump_dollars(str(meta["reported"]))
                return mut
        return None

    def primary(self, payload) -> str:
        return "pass" if check_ledger(payload).label == "BALANCED" else "fail"

    def independent(self, payload) -> str:
        return "pass" if check_ledger_intcents(payload) == "BALANCED" else "fail"


# --------------------------------------------------------------------------- invoice lane
class InvoiceLane(Lane):
    name = "invoice"
    second_authority = "check_invoice_intcents (pre-existing integer-cents second impl)"

    def candidates(self):
        cands = []
        for fx in invoice_fixtures():
            intended = "pass" if fx["label"] == "RECONCILED" else "fail"
            cands.append(Candidate(fx["id"], intended, "fixture", fx))
        for fx in invoice_fixtures():
            if fx["label"] != "RECONCILED":
                continue
            mut = copy.deepcopy(fx)
            mut["total"] = _bump_dollars(str(mut["total"]))   # grand_total_mismatch
            cands.append(Candidate(fx["id"] + "__gen_mutant", "fail", "mutant", mut))
        return cands

    def primary(self, payload) -> str:
        return "pass" if check_invoice(payload).label == "RECONCILED" else "fail"

    def independent(self, payload) -> str:
        return "pass" if check_invoice_intcents(payload) == "RECONCILED" else "fail"


LANES = {L.name: L for L in (DatetimeLane(), RegexLane(), LedgerLane(), InvoiceLane())}

# Candidates whose primary/independent disagreement is a documented POLICY difference, not a bug:
# the regex lane fails catastrophic-backtracking patterns on performance grounds, while the NFA
# (linear-time, no backtracking) reports their string-set correctness. Surfaced, not suppressed.
_POLICY_DISAGREEMENTS = {"word_catastrophic_backtracking"}


# --------------------------------------------------------------------------- runner
def run_lane(lane: Lane, primary_fn=None, independent_fn=None) -> LaneReport:
    """Cross-validate one lane. `primary_fn`/`independent_fn` optionally override the lane's
    checkers (callable(payload)->'pass'|'fail'|'abstain') — used to inject a deliberate checker
    disagreement in tests."""
    prim = primary_fn or lane.primary
    indep = independent_fn or lane.independent
    rep = LaneReport(lane.name, lane.second_authority)

    non_equiv_mutants = 0
    for c in lane.candidates():
        pv = prim(c.payload)
        iv = indep(c.payload)

        if c.kind == "fixture":
            rep.n_fixtures += 1
        else:
            rep.n_mutants += 1

        # mutation score is over every intended-negative candidate (authored fixture-fails AND
        # generated mutants) — a strong oracle kills them all.
        if c.intended == "fail":
            if pv == "fail":
                rep.killed += 1
                non_equiv_mutants += 1
            elif c.kind == "mutant" and iv == "pass":
                rep.equivalent += 1          # both authorities agree it's still valid -> equivalent
            else:
                non_equiv_mutants += 1       # primary missed a real negative (counts against score)

        if iv == "abstain":
            rep.abstain += 1
            continue
        rep.comparable += 1
        if pv == iv:
            rep.agree += 1
        else:
            note = "policy" if c.id in _POLICY_DISAGREEMENTS else "checker_disagreement"
            if pv == "pass" and iv == "fail":
                rep.fp += 1
            else:
                rep.fn += 1
            rep.disagreements.append(
                {"id": c.id, "kind": c.kind, "intended": c.intended,
                 "primary": pv, "independent": iv, "note": note})

    rep.negatives = non_equiv_mutants
    rep.mutation_score = round(rep.killed / non_equiv_mutants, 4) if non_equiv_mutants else 1.0
    rep.agreement = round(rep.agree / rep.comparable, 4) if rep.comparable else 1.0
    return rep


def run_all() -> dict:
    return {name: run_lane(lane) for name, lane in LANES.items()}


def shutdown():
    try:
        _REGEX_GUARD.shutdown()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- CLI
def _crossval_summary(reports: dict) -> dict:
    lanes = {}
    for name, r in reports.items():
        lanes[name] = {
            "second_authority": r.second_authority,
            "fixtures": r.n_fixtures, "generated_mutants": r.n_mutants,
            "negatives_scored": r.negatives, "killed": r.killed,
            "mutation_score": r.mutation_score, "equivalent_mutants": r.equivalent,
            "comparable": r.comparable, "agreement": r.agreement,
            "fp_primary_too_lenient": r.fp, "fn_primary_too_strict": r.fn,
            "abstain": r.abstain,
            "disagreements": r.disagreements,
        }
    return {"kind": "oracle_crossval_D1", "lanes": lanes}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="cortex-oracle-crossval",
        description="Cross-validate objective oracles against an independent 2nd checker (D1) "
                    "and prove the hidden holdout catches gaming (D2).")
    ap.add_argument("--lane", choices=sorted(LANES), help="run a single lane (default: all)")
    ap.add_argument("--holdout", action="store_true", help="also run the D2 hidden-holdout catch")
    ap.add_argument("--holdout-only", action="store_true", help="run only the D2 hidden-holdout catch")
    ap.add_argument("--out", help="write the full JSON report to this path")
    args = ap.parse_args(argv)

    result = {}
    try:
        if not args.holdout_only:
            reports = ({args.lane: run_lane(LANES[args.lane])} if args.lane else run_all())
            result["d1_crossval"] = _crossval_summary(reports)
        if args.holdout or args.holdout_only:
            from evals.objective_coding.holdout_gaming import run_holdout
            result["d2_holdout"] = run_holdout()
    finally:
        shutdown()

    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
