"""cortex_core/pack_experiment.py — the pack-falsification experiment harness.

Decides (later, with real data) ONE question and only that one: does a *same-family*
coach pack beat an equally engineered *generic* pack for a weak student, behind an
identical deterministic gate? Design: docs/research/BUILD-01-sdd-tdd-design-2026-07-10.md §5;
pre-registered criterion: docs/research/vendor-lane-FINAL-synthesis-2026-07-10.md §7.

Non-negotiables this module enforces (test-covered):
  * Pre-registration BEFORE data. The retention criterion is frozen and sha256-stamped;
    a post-hoc edit to the criterion is detectable at scoring time.
  * IDENTICAL gates across arms. For a given task, the SAME visible checks, the SAME
    hidden-holdout checks, and the SAME gate seed go to every arm's gate run. Varying the
    gate between arms would rig the experiment; a test captures gate-runner args and proves
    they are byte-identical across the three arms.
  * Pack firewall. Pack coaching text flows ONLY into the student prompt. Nothing
    pack-derived reaches the gate runner. Retry prompts carry only coach_view(verdict) =
    {"pass", "failure_class"} — never a hidden payload, never per-check detail.
  * Blinded acceptance. Human acceptance is recorded against opaque hex labels with NO pack
    provenance; the label->arm mapping is sealed and read only by scoring.
  * No LLM in any verdict path. The verdict is the deterministic gate's; retention is a pure
    function of aggregated numbers. LLM dispatch (the student) is injected and lives behind
    make_student(), imported lazily; unit tests use fakes with zero network / zero subprocess.

Windows-safe: every read/decode uses encoding="utf-8", errors="replace".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .app_contract import GateVerdict, coach_view
# app_gates is stdlib-only (no LLM); safe to import for the default gate runner + context.
from .app_gates import GateContext, load_hidden_checks, run_done_checks

PACK_ARMS = ("generic", "same_family", "cross_vendor")
GENERIC_ARM = "generic"
SAME_FAMILY_ARM = "same_family"

# Default retention criterion (also lives in PREREGISTRATION.template.json — the file is
# authoritative once frozen; these are only fallbacks for a spec that omits them).
DEFAULT_CRITERION = {"min_lift": 0.10, "cost_tolerance": 0.15, "min_cell_n": 20}

DEFAULT_RETRY_HINT = (
    "Attempt failed with gate class {failure_class}. Re-read the slot schema and emit "
    "ONE corrected JSON object. Do not add fields the schema does not list."
)

_REQUIRED_PREREG_KEYS = ("arms", "student", "task_ids", "gate_version", "criterion")


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class PreregError(RuntimeError):
    """Raised when a pre-registration is missing, malformed, or edited post-hoc."""


# --------------------------------------------------------------------------- #
# Data                                                                         #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PromptPack:
    """One experiment arm's coaching text. `provenance` is SEALED — loaded here but never
    written into any blind-review artifact."""
    pack_id: str
    arm: str
    system_addendum: str
    retry_hint: str = DEFAULT_RETRY_HINT
    version: str = "v1"
    provenance: str = ""
    slot_example: dict | None = None

    @classmethod
    def load(cls, path: str | Path) -> "PromptPack":
        p = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix == ".json":
            d = json.loads(text)
            return cls(
                pack_id=str(d.get("pack_id", p.stem)),
                arm=str(d["arm"]),
                system_addendum=str(d.get("system_addendum", "")),
                retry_hint=str(d.get("retry_hint", DEFAULT_RETRY_HINT)),
                version=str(d.get("version", "v1")),
                provenance=str(d.get("provenance", "")),
                slot_example=d.get("slot_example"),
            )
        meta, body = _parse_frontmatter(text)
        arm = meta.get("arm")
        if arm not in PACK_ARMS:
            raise ValueError(f"pack {p.name}: arm {arm!r} not in {PACK_ARMS}")
        return cls(
            pack_id=meta.get("pack_id", p.stem),
            arm=arm,
            system_addendum=body.strip(),
            retry_hint=meta.get("retry_hint", DEFAULT_RETRY_HINT),
            version=meta.get("version", "v1"),
            provenance=meta.get("provenance", ""),
        )


@dataclass(frozen=True)
class ExperimentTask:
    task_id: str
    utterance: str
    skill_id: str
    checks: list[dict]                 # visible done_checks (may contain @hidden: placeholders)
    holdout_family: str = ""           # -> load_hidden_checks(holdout_dir, family)


@dataclass
class CellResult:
    """One (task, pack) cell. `blind_label` is the ONLY id a reviewer sees; `pack_id`/`arm`
    are provenance and are sealed away from the review sheet."""
    task_id: str
    pack_id: str
    arm: str
    attempts: int
    slot_valid: bool
    visible_pass: bool
    hidden_pass: bool
    overall_pass: bool
    failure_class: str | None
    prompt_chars: int
    completion_chars: int
    artifact_dir: str
    blind_label: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "pack_id": self.pack_id,
            "arm": self.arm,
            "attempts": self.attempts,
            "slot_valid": self.slot_valid,
            "visible_pass": self.visible_pass,
            "hidden_pass": self.hidden_pass,
            "overall_pass": self.overall_pass,
            "failure_class": self.failure_class,
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "artifact_dir": self.artifact_dir,
            "blind_label": self.blind_label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CellResult":
        return cls(**{k: d[k] for k in (
            "task_id", "pack_id", "arm", "attempts", "slot_valid", "visible_pass",
            "hidden_pass", "overall_pass", "failure_class", "prompt_chars",
            "completion_chars", "artifact_dir", "blind_label")})


@dataclass(frozen=True)
class PreReg:
    spec: dict
    sha256: str
    path: str | None = None

    @property
    def criterion(self) -> dict:
        c = dict(DEFAULT_CRITERION)
        c.update(self.spec.get("criterion") or {})
        return c


# --------------------------------------------------------------------------- #
# Frontmatter + canonical hashing                                             #
# --------------------------------------------------------------------------- #
def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Tiny `--- key: value ---` frontmatter parser. Values may be quoted. Returns
    (metadata, body). No YAML dependency."""
    meta: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta, text
    body_start = len(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        line = lines[i]
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            meta[key.strip()] = val
    return meta, "\n".join(lines[body_start:])


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization for hashing (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def prereg_hash(spec: dict) -> str:
    """sha256 of the canonicalized pre-registration spec. Any change to the criterion (or
    any other frozen field) changes this hash — that is how a post-hoc edit is detected."""
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Pre-registration                                                            #
# --------------------------------------------------------------------------- #
def _validate_prereg_spec(spec: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["pre-registration must be a JSON object"]
    for key in _REQUIRED_PREREG_KEYS:
        if key not in spec:
            errors.append(f"pre-registration missing required key {key!r}")
    crit = spec.get("criterion")
    if isinstance(crit, dict):
        for ck in ("min_lift", "cost_tolerance", "min_cell_n"):
            if ck not in crit:
                errors.append(f"criterion missing {ck!r}")
    elif "criterion" in spec:
        errors.append("criterion must be an object")
    arms = spec.get("arms")
    if isinstance(arms, list) and GENERIC_ARM not in arms:
        errors.append("arms must include the 'generic' baseline")
    return errors


def preregister(spec: dict, exp_dir: str | Path | None = None, *, overwrite: bool = False) -> PreReg:
    """Freeze and (optionally) write a hash-stamped pre-registration BEFORE any data.

    The spec must name the arms, the student, the task ids, the gate version, and the
    retention criterion (min_lift / cost_tolerance / min_cell_n = the sample floor). The
    returned sha256 stamps the frozen criterion; a later edit to the file changes the hash,
    so post-hoc criterion changes are detectable (`score_experiment` re-checks it).
    """
    errors = _validate_prereg_spec(spec)
    if errors:
        raise PreregError("invalid pre-registration: " + "; ".join(errors))
    frozen = dict(spec)
    frozen.setdefault("frozen_at", datetime.now(timezone.utc).isoformat())
    sha = prereg_hash(frozen)
    path = None
    if exp_dir is not None:
        d = Path(exp_dir)
        d.mkdir(parents=True, exist_ok=True)
        dst = d / "PREREGISTRATION.json"
        if dst.exists() and not overwrite:
            raise PreregError(f"refusing to overwrite existing pre-registration at {dst}")
        dst.write_text(json.dumps(frozen, indent=2, sort_keys=True),
                       encoding="utf-8", errors="replace")
        path = str(dst)
    return PreReg(spec=frozen, sha256=sha, path=path)


def load_prereg(exp_dir: str | Path) -> PreReg:
    """Read PREREGISTRATION.json from exp_dir. Raises PreregError if absent/invalid."""
    dst = Path(exp_dir) / "PREREGISTRATION.json"
    if not dst.exists():
        raise PreregError(f"no pre-registration at {dst} (prereg-before-data is mandatory)")
    try:
        spec = json.loads(dst.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise PreregError(f"malformed pre-registration at {dst}: {e}") from e
    errs = _validate_prereg_spec(spec)
    if errs:
        raise PreregError("invalid pre-registration: " + "; ".join(errs))
    return PreReg(spec=spec, sha256=prereg_hash(spec), path=str(dst))


def prereg_sha256(exp_dir: str | Path) -> str:
    return load_prereg(exp_dir).sha256


# --------------------------------------------------------------------------- #
# Deterministic seeds + blind labels                                          #
# --------------------------------------------------------------------------- #
def _stable_int(*parts: Any) -> int:
    """Deterministic 32-bit int from arbitrary parts (stable across processes — unlike
    hash())."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _blind_label(seed: int, task_id: str, pack_id: str) -> str:
    """Opaque 8-hex label for a cell. Deterministic but reveals no provenance."""
    h = hashlib.sha256(f"{seed}|{task_id}|{pack_id}".encode("utf-8")).hexdigest()
    return h[:8]


# --------------------------------------------------------------------------- #
# Prompt construction (the pack firewall lives here)                          #
# --------------------------------------------------------------------------- #
def build_prompt(task: ExperimentTask, pack: PromptPack, coach: dict | None = None) -> str:
    """Assemble the student prompt for one attempt.

    Inputs are ONLY: the task utterance, the pack's coaching text (+ optional worked
    example), and — on a retry — coach_view(verdict) = {"pass", "failure_class"}. No gate
    check spec, no hidden payload, and no per-check detail is ever placed in the prompt.
    """
    parts = [pack.system_addendum.strip(), "", f"TASK: {task.utterance}"]
    if pack.slot_example is not None:
        parts.append("EXAMPLE: " + canonical_json(pack.slot_example))
    if coach is not None:
        # coach is coach_view(verdict) — {"pass", "failure_class"} only, by construction.
        try:
            parts.append(pack.retry_hint.format(**coach))
        except (KeyError, IndexError):
            parts.append(pack.retry_hint)
    return "\n".join(p for p in parts if p is not None)


def _default_extract_slot(text: str) -> dict | None:
    """Tolerantly pull the FIRST JSON object from possibly-noisy model output. Returns None
    (never raises) on garbage. Kept local so the harness has no hard build_skills dependency."""
    if not isinstance(text, str):
        return None
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        start = -1
                        continue
                    return obj if isinstance(obj, dict) else None
    return None


def _default_slot_valid(task: ExperimentTask, slot: dict | None) -> bool:
    """Minimal, dependency-free slot check: a non-empty dict. Real validation is injected
    via slot_validator (build_skills.validate_slot at live/integration time)."""
    return isinstance(slot, dict) and len(slot) > 0


# --------------------------------------------------------------------------- #
# Verdict interpretation                                                       #
# --------------------------------------------------------------------------- #
def _split_pass(verdict: GateVerdict) -> tuple[bool, bool]:
    """(visible_pass, hidden_pass) from a GateVerdict's per-check results."""
    visible = [r for r in verdict.results if not r.hidden]
    hidden = [r for r in verdict.results if r.hidden]
    visible_pass = all(r.passed for r in visible) if visible else verdict.passed
    if hidden:
        hidden_pass = all(r.passed for r in hidden)
    else:
        hidden_pass = bool(verdict.passed and verdict.hidden_coverage)
    return visible_pass, hidden_pass


# --------------------------------------------------------------------------- #
# The runner                                                                   #
# --------------------------------------------------------------------------- #
def run_arm(
    task: ExperimentTask,
    pack: PromptPack,
    student_complete: Callable[[str], str],
    *,
    gate_runner: Callable[..., GateVerdict],
    gate_seed: int,
    hidden_checks: list[dict],
    exp_seed: int,
    attempts: int = 2,
    runs_dir: Path | None = None,
    renderer: Callable[[ExperimentTask, dict, Path], None] | None = None,
    slot_extractor: Callable[[str], dict | None] | None = None,
    slot_validator: Callable[[ExperimentTask, dict | None], bool] | None = None,
) -> CellResult:
    """Run ONE (task, pack) cell through the attempt budget and the SAME gate the other arms
    of this task get (gate_seed + hidden_checks + task.checks are supplied by the caller and
    are identical across arms — the identical-gates guarantee).
    """
    extract = slot_extractor or _default_extract_slot
    validate = slot_validator or _default_slot_valid
    blind = _blind_label(exp_seed, task.task_id, pack.pack_id)
    artifact_dir = (runs_dir / task.task_id / blind) if runs_dir is not None else Path(blind)

    prompt_chars = 0
    completion_chars = 0
    coach: dict | None = None
    used = 0
    slot_valid = False
    verdict: GateVerdict | None = None

    for _ in range(max(1, attempts)):
        used += 1
        prompt = build_prompt(task, pack, coach)
        prompt_chars += len(prompt)
        raw = student_complete(prompt)
        completion_chars += len(raw) if isinstance(raw, str) else 0
        slot = extract(raw)
        slot_valid = bool(validate(task, slot))
        if not slot_valid:
            # Honest failure: no usable slot => no render, no gate, this attempt is a fail.
            verdict = None
            coach = {"pass": False, "failure_class": "ENV_FAIL"}
            continue
        if renderer is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            renderer(task, slot, artifact_dir)
        verdict = gate_runner(
            artifact_dir, task.checks,
            hidden_checks=hidden_checks,
            ctx=GateContext(seed=gate_seed),
        )
        if verdict.passed:
            break
        coach = coach_view(verdict)  # ONLY {"pass", "failure_class"} flows to the retry.

    if verdict is None:
        visible_pass = hidden_pass = overall_pass = False
        failure_class = "ENV_FAIL"
    else:
        visible_pass, hidden_pass = _split_pass(verdict)
        overall_pass = verdict.passed
        failure_class = verdict.failure_class

    return CellResult(
        task_id=task.task_id, pack_id=pack.pack_id, arm=pack.arm,
        attempts=used, slot_valid=slot_valid,
        visible_pass=visible_pass, hidden_pass=hidden_pass, overall_pass=overall_pass,
        failure_class=failure_class,
        prompt_chars=prompt_chars, completion_chars=completion_chars,
        artifact_dir=str(artifact_dir), blind_label=blind,
    )


def run_experiment(
    exp_dir: str | Path,
    packs: list[PromptPack],
    tasks: list[ExperimentTask],
    student_complete: Callable[[str], str],
    *,
    gate_runner: Callable[..., GateVerdict] = run_done_checks,
    holdout_dir: str | Path | None = None,
    attempts: int = 2,
    seed: int = 0,
    renderer: Callable[[ExperimentTask, dict, Path], None] | None = None,
    slot_extractor: Callable[[str], dict | None] | None = None,
    slot_validator: Callable[[ExperimentTask, dict | None], bool] | None = None,
) -> list[CellResult]:
    """Paired, within-subject 3-arm run.

    For each task, the gate configuration is computed ONCE — the same visible `checks`, the
    same hidden-holdout `hidden_checks`, and the same `gate_seed` — and reused for EVERY arm.
    That is the identical-gates guarantee (test-enforced with a gate-runner spy). Arm order is
    shuffled per task by a seeded RNG (paired randomization), but the gate never varies.
    """
    exp_dir = Path(exp_dir)
    prereg = load_prereg(exp_dir)  # refuses to start without a frozen pre-registration.

    exp_id = f"exp_{seed}"
    runs_dir = exp_dir / "runs" / exp_id
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Manifest FIRST — stamps the prereg hash before any data is produced.
    manifest = {
        "exp_id": exp_id,
        "prereg_sha256": prereg.sha256,
        "seed": seed,
        "arms_run": [p.arm for p in packs],
        "pack_ids": [p.pack_id for p in packs],
        "task_ids": [t.task_id for t in tasks],
        "attempts": attempts,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    (runs_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8", errors="replace")

    hdir = Path(holdout_dir) if holdout_dir is not None else None
    cells_path = runs_dir / "cells.jsonl"
    results: list[CellResult] = []

    with cells_path.open("w", encoding="utf-8", errors="replace") as cells_fh:
        for task in tasks:
            # ---- gate config computed ONCE per task, shared across arms ----
            gate_seed = _stable_int(seed, task.task_id)
            if hdir is not None and task.holdout_family:
                hidden_checks = load_hidden_checks(hdir, task.holdout_family)
            else:
                hidden_checks = []

            order = list(packs)
            random.Random(_stable_int(seed, task.task_id, "order")).shuffle(order)

            for pack in order:
                cell = run_arm(
                    task, pack, student_complete,
                    gate_runner=gate_runner, gate_seed=gate_seed,
                    hidden_checks=hidden_checks, exp_seed=seed,
                    attempts=attempts, runs_dir=runs_dir,
                    renderer=renderer, slot_extractor=slot_extractor,
                    slot_validator=slot_validator,
                )
                results.append(cell)
                cells_fh.write(json.dumps(cell.to_dict(), sort_keys=True) + "\n")

    # Seal the blind_label -> provenance map (read only by scoring; gitignored in the repo).
    _seal_blind_map(runs_dir, results)
    return results


# --------------------------------------------------------------------------- #
# Blinded acceptance                                                           #
# --------------------------------------------------------------------------- #
def _runs_dir(exp_dir: Path, exp_id: str | None = None) -> Path:
    runs = exp_dir / "runs"
    if exp_id:
        return runs / exp_id
    subs = sorted([p for p in runs.glob("exp_*") if p.is_dir()])
    if not subs:
        raise PreregError(f"no run found under {runs}")
    return subs[-1]


def _seal_blind_map(runs_dir: Path, cells: list[CellResult]) -> Path:
    sealed = {
        c.blind_label: {"task_id": c.task_id, "pack_id": c.pack_id, "arm": c.arm}
        for c in cells
    }
    dst = runs_dir / "blind_map.sealed.json"
    dst.write_text(json.dumps(sealed, indent=2, sort_keys=True), encoding="utf-8", errors="replace")
    return dst


def export_blind_review(exp_dir: str | Path, exp_id: str | None = None) -> Path:
    """Write review_sheet.json containing ONE entry per cell with its opaque blind label and
    task_id ONLY — no pack_id, no arm, no provenance. Reviewers score acceptance from this.
    """
    exp_dir = Path(exp_dir)
    runs_dir = _runs_dir(exp_dir, exp_id)
    cells = _load_cells(runs_dir)
    sheet = {
        "exp_id": runs_dir.name,
        "instructions": "Score each label's built app for acceptance. You are blind to which "
                        "coaching pack produced it — that is intentional.",
        "items": [
            {"blind_label": c.blind_label, "task_id": c.task_id, "artifact_dir": c.artifact_dir}
            for c in cells
        ],
    }
    dst = runs_dir / "review_sheet.json"
    dst.write_text(json.dumps(sheet, indent=2, sort_keys=True), encoding="utf-8", errors="replace")
    return dst


def record_acceptance(exp_dir: str | Path, blind_label: str, accepted: bool,
                      note: str = "", exp_id: str | None = None) -> None:
    """Append one acceptance verdict, keyed by blind label. The record carries NO pack
    provenance — only the label, the boolean, and an optional note."""
    runs_dir = _runs_dir(Path(exp_dir), exp_id)
    rec = {"blind_label": str(blind_label), "accepted": bool(accepted), "note": str(note)}
    with (runs_dir / "acceptance.jsonl").open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def load_acceptance(runs_dir: Path) -> dict[str, bool]:
    path = runs_dir / "acceptance.jsonl"
    out: dict[str, bool] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out[rec["blind_label"]] = bool(rec["accepted"])
    return out


def _load_cells(runs_dir: Path) -> list[CellResult]:
    path = runs_dir / "cells.jsonl"
    if not path.exists():
        return []
    cells = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            cells.append(CellResult.from_dict(json.loads(line)))
    return cells


def _load_sealed(runs_dir: Path) -> dict[str, dict]:
    path = runs_dir / "blind_map.sealed.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------------------- #
# Metrics + retention                                                          #
# --------------------------------------------------------------------------- #
def aggregate_metrics(cells: list[CellResult],
                      acceptance: dict[str, bool] | None = None) -> dict[str, dict]:
    """Aggregate cell records into per-arm metrics. Keyed by ARM (the experiment compares
    arms). Acceptance maps blind_label -> bool; missing labels count as not-accepted."""
    acceptance = acceptance or {}
    by_arm: dict[str, list[CellResult]] = {}
    for c in cells:
        by_arm.setdefault(c.arm, []).append(c)

    out: dict[str, dict] = {}
    for arm, group in by_arm.items():
        n = len(group)
        accepted = sum(1 for c in group if acceptance.get(c.blind_label, False))
        total_cost = sum((c.prompt_chars + c.completion_chars) for c in group) / 4.0
        regressions = sum(1 for c in group if c.failure_class == "REGRESSION_FAIL")
        out[arm] = {
            "n": n,
            "hidden_pass_rate": (sum(1 for c in group if c.hidden_pass) / n) if n else 0.0,
            "visible_pass_rate": (sum(1 for c in group if c.visible_pass) / n) if n else 0.0,
            "overall_pass_rate": (sum(1 for c in group if c.overall_pass) / n) if n else 0.0,
            "acceptance_rate": (accepted / n) if n else 0.0,
            "mean_attempts": (sum(c.attempts for c in group) / n) if n else 0.0,
            "cost_per_accepted": (total_cost / accepted) if accepted else float("inf"),
            "regression_rate": (regressions / n) if n else 0.0,
            "accepted": accepted,
        }
    return out


def retention_verdict(results: Any, prereg: Any) -> dict:
    """PURE, pre-registered retention rule. `results` is either per-arm metrics
    (aggregate_metrics output) or a list[CellResult]; `prereg` is a PreReg, a full spec dict,
    or a criterion dict. Returns {"retain_same_family": bool, "reason": str, "reasons": [...]}.

    Retain same_family IFF ALL hold, at the pre-registered thresholds:
      1. n(generic) >= min_cell_n AND n(same_family) >= min_cell_n  (the sample floor);
      2. hidden_pass_rate(same_family) - hidden_pass_rate(generic) >= min_lift  (BOTH metrics);
      3. acceptance_rate(same_family) - acceptance_rate(generic)   >= min_lift;
      4. cost_per_accepted(same_family) <= cost_per_accepted(generic) * (1 + cost_tolerance);
      5. regression_rate(same_family) <= regression_rate(generic).
    A shortfall on ANY clause => do not retain, with the reason(s) named.
    """
    per_arm = results if isinstance(results, dict) else aggregate_metrics(list(results))
    crit = _criterion_of(prereg)
    min_lift = float(crit.get("min_lift", DEFAULT_CRITERION["min_lift"]))
    cost_tol = float(crit.get("cost_tolerance", DEFAULT_CRITERION["cost_tolerance"]))
    min_n = int(crit.get("min_cell_n", DEFAULT_CRITERION["min_cell_n"]))

    reasons: list[str] = []
    gen = per_arm.get(GENERIC_ARM)
    sf = per_arm.get(SAME_FAMILY_ARM)
    if gen is None or sf is None:
        reasons.append("missing generic and/or same_family arm — cannot compare")
        return {"retain_same_family": False, "reason": "; ".join(reasons), "reasons": reasons}

    # 1. sample floor
    if gen["n"] < min_n or sf["n"] < min_n:
        reasons.append(
            f"UNDERPOWERED: n(generic)={gen['n']}, n(same_family)={sf['n']} < min_cell_n={min_n}")
        return {"retain_same_family": False, "reason": "; ".join(reasons), "reasons": reasons,
                "underpowered": True}

    # 2. hidden-gate pass-rate lift
    hidden_lift = sf["hidden_pass_rate"] - gen["hidden_pass_rate"]
    if hidden_lift < min_lift:
        reasons.append(
            f"hidden-gate pass rate lift {hidden_lift:+.3f} < min_lift {min_lift} "
            f"(same_family {sf['hidden_pass_rate']:.3f} vs generic {gen['hidden_pass_rate']:.3f})")

    # 3. acceptance lift
    accept_lift = sf["acceptance_rate"] - gen["acceptance_rate"]
    if accept_lift < min_lift:
        reasons.append(
            f"acceptance rate lift {accept_lift:+.3f} < min_lift {min_lift} "
            f"(same_family {sf['acceptance_rate']:.3f} vs generic {gen['acceptance_rate']:.3f})")

    # 4. cost ceiling
    cost_ceiling = gen["cost_per_accepted"] * (1 + cost_tol)
    if sf["cost_per_accepted"] > cost_ceiling:
        reasons.append(
            f"cost_per_accepted {sf['cost_per_accepted']:.1f} exceeds generic ceiling "
            f"{cost_ceiling:.1f} (generic {gen['cost_per_accepted']:.1f} x (1+{cost_tol}))")

    # 5. no worse regressions
    if sf["regression_rate"] > gen["regression_rate"]:
        reasons.append(
            f"regression rate {sf['regression_rate']:.3f} > generic {gen['regression_rate']:.3f}")

    retain = not reasons
    if retain:
        reasons.append(
            "same_family beats generic on BOTH hidden-gate pass rate and acceptance, "
            "within cost tolerance and with no worse regressions, at the sample floor")
    return {"retain_same_family": retain, "reason": "; ".join(reasons), "reasons": reasons}


def _criterion_of(prereg: Any) -> dict:
    if isinstance(prereg, PreReg):
        return prereg.criterion
    if isinstance(prereg, dict):
        if "criterion" in prereg and isinstance(prereg["criterion"], dict):
            merged = dict(DEFAULT_CRITERION)
            merged.update(prereg["criterion"])
            return merged
        if any(k in prereg for k in DEFAULT_CRITERION):
            merged = dict(DEFAULT_CRITERION)
            merged.update(prereg)
            return merged
    return dict(DEFAULT_CRITERION)


# --------------------------------------------------------------------------- #
# Scoring                                                                      #
# --------------------------------------------------------------------------- #
def score_experiment(exp_dir: str | Path, exp_id: str | None = None) -> dict:
    """Aggregate a run and apply the pre-registered retention rule.

    verdicts:
      * PREREG_VIOLATION — the frozen criterion was edited after the run manifest was stamped;
      * REVIEW_INCOMPLETE — a passing cell still lacks a blinded acceptance verdict;
      * UNDERPOWERED — a compared arm has n < min_cell_n;
      * OK — scored cleanly.
    """
    exp_dir = Path(exp_dir)
    runs_dir = _runs_dir(exp_dir, exp_id)
    prereg = load_prereg(exp_dir)

    manifest_path = runs_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    if manifest.get("prereg_sha256") != prereg.sha256:
        return {"verdict": "PREREG_VIOLATION",
                "reason": "PREREGISTRATION.json changed after the run was stamped "
                          f"(manifest {manifest.get('prereg_sha256')!r} != current {prereg.sha256!r})",
                "arms_run": manifest.get("arms_run", [])}

    cells = _load_cells(runs_dir)
    acceptance = load_acceptance(runs_dir)

    # Completeness: every overall-passing cell needs a blinded acceptance verdict.
    missing = [c.blind_label for c in cells if c.overall_pass and c.blind_label not in acceptance]
    if missing:
        return {"verdict": "REVIEW_INCOMPLETE",
                "reason": f"{len(missing)} passing cell(s) lack a blinded acceptance verdict",
                "missing_labels": missing, "arms_run": manifest.get("arms_run", [])}

    per_arm = aggregate_metrics(cells, acceptance)
    retention = retention_verdict(per_arm, prereg)
    verdict = "UNDERPOWERED" if retention.get("underpowered") else "OK"
    return {
        "verdict": verdict,
        "arms_run": sorted(per_arm.keys()),
        "per_pack": per_arm,
        "retention": retention,
        "prereg_sha256": prereg.sha256,
    }


# --------------------------------------------------------------------------- #
# Live student dispatch (lazy; never touched by unit tests)                    #
# --------------------------------------------------------------------------- #
def make_student(tier: str = "qwen35b") -> Callable[[str], str]:
    """Return a live student callable dispatching via research._llm_complete. Raises
    RuntimeError on an unavailable model — an honest failure, never a fabricated slot.
    Imported lazily so unit tests never touch the network."""
    from . import research  # lazy

    def _student(prompt: str) -> str:
        out = research._llm_complete(prompt, model=tier, max_tokens=2000)
        if out is None:
            raise RuntimeError(f"no usable model for student tier {tier!r}")
        return out

    return _student


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    try:
        from .config import make_stdio_encoding_safe
        make_stdio_encoding_safe()
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="cortex-pack-exp",
                                     description="Pack-falsification experiment harness.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="write a PREREGISTRATION.json (refuses overwrite)")
    p_init.add_argument("--exp-dir", required=True)
    p_init.add_argument("--template", default=None,
                        help="path to a prereg template (default: bundled template)")

    p_score = sub.add_parser("score", help="score a completed run")
    p_score.add_argument("--exp-dir", required=True)
    p_score.add_argument("--json", action="store_true")

    p_review = sub.add_parser("review", help="record a blinded acceptance verdict")
    p_review.add_argument("--exp-dir", required=True)
    p_review.add_argument("--label", required=True)
    grp = p_review.add_mutually_exclusive_group(required=True)
    grp.add_argument("--accept", action="store_true")
    grp.add_argument("--reject", action="store_true")
    p_review.add_argument("--note", default="")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        exp_dir = Path(args.exp_dir)
        dst = exp_dir / "PREREGISTRATION.json"
        if dst.exists():
            print(f"refusing to overwrite existing pre-registration at {dst}", file=sys.stderr)
            return 1
        if args.template:
            spec = json.loads(Path(args.template).read_text(encoding="utf-8", errors="replace"))
        else:
            tmpl = Path(__file__).resolve().parents[1] / "evals" / "pack_experiment" / "PREREGISTRATION.template.json"
            spec = json.loads(tmpl.read_text(encoding="utf-8", errors="replace"))
        try:
            reg = preregister(spec, exp_dir)
        except PreregError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"wrote {reg.path} (sha256={reg.sha256})")
        return 0

    if args.cmd == "score":
        try:
            result = score_experiment(args.exp_dir)
        except PreregError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        else:
            print(f"verdict: {result['verdict']}")
            ret = result.get("retention")
            if ret:
                print(f"retain_same_family: {ret['retain_same_family']}")
                print(f"reason: {ret['reason']}")
        return 0 if result["verdict"] == "OK" else 2

    if args.cmd == "review":
        record_acceptance(args.exp_dir, args.label, accepted=bool(args.accept), note=args.note)
        print(f"recorded {'ACCEPT' if args.accept else 'REJECT'} for label {args.label}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
