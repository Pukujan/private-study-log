"""Standalone heterogeneous task-decomposer — terra's PARTITION-seam design
(``reviewed/decomposer-research-terra-2026-07-15.md``,
``docs/design/heterogeneous-decomposer-gap-2026-07-15.md``).

This is the STANDALONE half: a bounded, PURE-DETERMINISTIC manifest **validator**
plus a model-driven manifest **proposer**. It intentionally depends on NOTHING in the
mission/state engine (no ``state_engine``/``plane2_driver``/``receipts``/``fanout``
imports) so it can be built and tested in isolation, and later wired into the mission
``PARTITION`` boundary as a separate follow-up once the per-worker-receipt coupling lands.

Judge-free invariant (NON-NEGOTIABLE)
-------------------------------------
A MODEL may only PROPOSE a decomposition. A model may NOT create tasks, grant claims,
choose transitions, or declare any worker "done"/passed. The ONLY authority over whether
a proposal is acceptable is :func:`validate_manifest` — a deterministic set/graph check,
never a judge. ``propose_manifest`` returns the model's proposal VERBATIM and unblessed;
the caller MUST run ``validate_manifest`` before persisting or spawning anything.

Consistency with the mission PARTITION gate
-------------------------------------------
``validate_manifest`` mirrors the deterministic checks already in
``state_engine.partition_coverage_gate`` (state_engine.py:454-488) and
``StateEngine._claim_conflicts`` / ``_materialize_partition``
(state_engine.py:1137-1208) so the two cannot silently drift:

  * MISSING_COVERAGE   — a ``required_unit`` owned by no worker (exhaustiveness).
  * UNIT_DOUBLE_OWNED  — a ``required_unit`` owned by >1 worker (exclusivity).
  * FANOUT_EXCEEDED    — more workers than ``coverage_spec.max_workers`` (default 8).
  * CLAIM_CONFLICT     — two workers' ``claims`` glob-overlap within one ``kind``
                         (bidirectional ``fnmatchcase``, same rule as ``_claim_conflicts``).
  * CLAIMLESS_WORKER   — a worker with zero claims (can't own a disjoint slice).

The manifest contract (terra's shape)
--------------------------------------
::

    {
      "mission_id": "t_parent",
      "coverage_spec": {"required_units": ["api", "ui", "tests"], "max_workers": 3},
      "workers": [{
        "key": "api",
        "objective": "Implement HTTP handlers only",
        "track": "app_build",
        "tier_profile": "code-medium",
        "owns_units": ["api"],
        "claims": [{"kind": "path", "key": "src/api/**"}],
        "depends_on": [],
        "artifact_lane": ".cortex/worktrees/t_parent/api",
        "acceptance": {"kind": "smoke_receipt"}
      }],
      "reducers": [{"kind": "git_merge", "order": ["api", "ui", "tests"]}]
    }
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from typing import Any

from . import model_dispatch

__all__ = [
    "ALLOWED_TRACKS",
    "ALLOWED_TIER_PROFILES",
    "FREE_TIERS",
    "DEFAULT_MAX_WORKERS",
    "validate_manifest",
    "propose_manifest",
]

# --------------------------------------------------------------------------- #
# Allowlists                                                                   #
# --------------------------------------------------------------------------- #
# Allowlisted worker tracks. These are the engine's REGISTERED, non-mission tracks
# (state_engine.py: "build", "research", "app_build" — a worker is never itself a
# "mission"). terra's v0 recommends restricting heterogeneous slices to independently
# gateable app_build work; "build"/"research" are allowed as the taxonomy grows, but a
# worker's REAL deterministic receipt/evidence type is enforced later at DISPATCH, not here.
ALLOWED_TRACKS: frozenset[str] = frozenset({"app_build", "build", "research"})

# Allowlisted capability profiles the proposer may assign. NOTE (research-first, honest):
# NO prior recorded decision defines a `tier_profile` vocabulary in the corpus/code — this
# concept is introduced by terra's design (which uses "code-medium"). This is therefore a
# DELIBERATE v0 allowlist, not a citation of an existing decision; extend it as real
# profile->dispatch-tier mappings are settled. It is intentionally an abstract capability
# label (NOT a raw dispatch tier from model_dispatch._TIER_ENV) so the proposer cannot pin a
# specific paid model.
ALLOWED_TIER_PROFILES: frozenset[str] = frozenset({
    "code-low", "code-medium", "code-high",
    "research-low", "research-medium", "research-high",
    "review-medium", "review-high",
})

# Default worker cap when coverage_spec omits max_workers — matches
# state_engine.partition_coverage_gate's `spec.get("max_workers", 8)` (state_engine.py:482).
DEFAULT_MAX_WORKERS = 8

# FREE dispatch tiers `propose_manifest` may call. CLAUDE.md rule: the proposer NEVER uses a
# paid/premium tier. ollama is free+local; opencode-zen serves free/stealth promo models;
# the 9Router free pool is rate-limited but free. Sourced from model_dispatch's own catalog.
FREE_TIERS: frozenset[str] = frozenset(
    {"ollama", "opencode-zen", "opencode-zen2"} | set(model_dispatch.NINEROUTER_TIERS)
)

# A propose call needs headroom for a multi-worker JSON manifest + reasoning-model budget.
# model_dispatch.apply_min_max_tokens raises this to the 12000 reasoning FLOOR for the
# zen/9router tiers anyway; ollama has no floor so we ask for a comfortable ceiling here.
_PROPOSE_MAX_TOKENS = 12000


# --------------------------------------------------------------------------- #
# Deterministic validator — the judge-free heart.                             #
# --------------------------------------------------------------------------- #
def _problem(code: str, reason: str) -> dict[str, str]:
    return {"code": code, "reason": reason}


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_manifest(manifest: Any) -> tuple[bool, list[dict[str, str]]]:
    """PURE, DETERMINISTIC accept/reject of a proposed decomposition manifest.

    Returns ``(ok, problems)`` where ``ok`` is ``len(problems) == 0`` and ``problems`` is a
    list of ``{"code", "reason"}`` records. NO model is consulted; the same input always
    yields the same verdict and the input is never mutated. This is the ONLY authority over
    whether a proposal may be persisted/spawned — a model's proposal is meaningless until it
    passes here.

    Collects as many INDEPENDENT problems as it safely can (does not short-circuit on the
    first) so a proposer can fix a batch in one round-trip; structural errors that make deeper
    checks impossible (non-dict manifest, non-list workers) do return early.
    """
    problems: list[dict[str, str]] = []

    # --- structural spine (early-return only when deeper checks are impossible) ---------- #
    if not isinstance(manifest, dict):
        return False, [_problem("BAD_MANIFEST", "manifest must be a JSON object")]

    if not _nonempty_str(manifest.get("mission_id")):
        problems.append(_problem("BAD_MISSION_ID", "mission_id must be a non-empty string"))

    coverage = manifest.get("coverage_spec")
    if not isinstance(coverage, dict):
        coverage = {}
        problems.append(_problem("BAD_COVERAGE_SPEC", "coverage_spec must be a JSON object"))

    workers = manifest.get("workers")
    if not isinstance(workers, list):
        problems.append(_problem("BAD_WORKERS", "workers must be a list"))
        return False, problems
    if not workers:
        problems.append(_problem("NO_WORKERS", "a manifest must declare at least one worker"))
        return False, problems

    required = coverage.get("required_units") or []
    if not isinstance(required, list) or not all(isinstance(u, str) for u in required):
        problems.append(_problem("BAD_REQUIRED_UNITS",
                                 "coverage_spec.required_units must be a list of strings"))
        required = [u for u in (required if isinstance(required, list) else []) if isinstance(u, str)]
    required_set = set(required)

    max_workers = coverage.get("max_workers", DEFAULT_MAX_WORKERS)
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        problems.append(_problem("BAD_MAX_WORKERS", "coverage_spec.max_workers must be an int >= 1"))
        max_workers = DEFAULT_MAX_WORKERS

    # --- per-worker structural checks ---------------------------------------------------- #
    keys: list[str] = []
    owned_sets: list[set[str]] = []
    for i, w in enumerate(workers):
        if not isinstance(w, dict):
            problems.append(_problem("BAD_WORKER", f"worker[{i}] must be a JSON object"))
            owned_sets.append(set())
            continue

        key = w.get("key")
        label = key if _nonempty_str(key) else f"[{i}]"
        if not _nonempty_str(key):
            problems.append(_problem("BAD_WORKER_KEY", f"worker {label}: key must be a non-empty string"))
        else:
            keys.append(key)

        if not _nonempty_str(w.get("objective")):
            problems.append(_problem("EMPTY_OBJECTIVE", f"worker {label}: objective must be non-empty"))

        if w.get("track") not in ALLOWED_TRACKS:
            problems.append(_problem("BAD_TRACK",
                                     f"worker {label}: track {w.get('track')!r} not in "
                                     f"{sorted(ALLOWED_TRACKS)}"))

        if w.get("tier_profile") not in ALLOWED_TIER_PROFILES:
            problems.append(_problem("BAD_TIER_PROFILE",
                                     f"worker {label}: tier_profile {w.get('tier_profile')!r} not in "
                                     f"{sorted(ALLOWED_TIER_PROFILES)}"))

        owns = w.get("owns_units") or []
        if not isinstance(owns, list) or not all(isinstance(u, str) for u in owns):
            problems.append(_problem("BAD_OWNS_UNITS", f"worker {label}: owns_units must be a list of strings"))
            owns = [u for u in (owns if isinstance(owns, list) else []) if isinstance(u, str)]
        owned_sets.append(set(owns))

        claims = w.get("claims") or []
        if not isinstance(claims, list):
            problems.append(_problem("BAD_CLAIMS", f"worker {label}: claims must be a list"))
        elif not claims:
            # Mirrors _materialize_partition CLAIMLESS_WORKER: no claim -> no disjoint slice.
            problems.append(_problem("CLAIMLESS_WORKER",
                                     f"worker {label}: must declare >=1 claim (a claimless worker "
                                     "cannot own a disjoint slice)"))
        else:
            for c in claims:
                if not (isinstance(c, dict) and _nonempty_str(c.get("kind")) and _nonempty_str(c.get("key"))):
                    problems.append(_problem("BAD_CLAIM",
                                             f"worker {label}: each claim needs non-empty 'kind' and 'key'"))
                    break

    # duplicate worker keys
    seen: set[str] = set()
    for k in keys:
        if k in seen:
            problems.append(_problem("DUPLICATE_WORKER_KEY", f"worker key {k!r} used more than once"))
        seen.add(k)
    key_set = set(keys)

    # --- coverage: exhaustiveness + exclusivity (mirror partition_coverage_gate) --------- #
    union = set().union(*owned_sets) if owned_sets else set()
    missing = required_set - union
    if missing:
        problems.append(_problem("MISSING_COVERAGE",
                                 f"units not owned by any worker: {sorted(missing)}"))
    dupes = [u for u in required_set if sum(u in o for o in owned_sets) > 1]
    if dupes:
        problems.append(_problem("UNIT_DOUBLE_OWNED",
                                 f"units owned by >1 worker (duplication risk): {sorted(dupes)}"))

    # --- fan-out guard (mirror partition_coverage_gate) ---------------------------------- #
    if len(workers) > max_workers:
        problems.append(_problem("FANOUT_EXCEEDED",
                                 f"{len(workers)} workers > max_workers {max_workers}"))

    # --- dependency DAG: unknown refs + no cycle ----------------------------------------- #
    deps: dict[str, list[str]] = {}
    for w in workers:
        if not (isinstance(w, dict) and _nonempty_str(w.get("key"))):
            continue
        d = w.get("depends_on") or []
        if not isinstance(d, list):
            problems.append(_problem("BAD_DEPENDS_ON", f"worker {w['key']}: depends_on must be a list"))
            d = []
        deps[w["key"]] = [x for x in d if isinstance(x, str)]

    unknown = sorted({dep for src in deps.values() for dep in src if dep not in key_set})
    if unknown:
        problems.append(_problem("DEP_UNKNOWN",
                                 f"depends_on references unknown worker keys: {unknown}"))

    if _has_cycle(deps, key_set):
        problems.append(_problem("DEP_CYCLE", "depends_on graph contains a cycle (including self-deps)"))

    # --- claim path-exclusivity (mirror _claim_conflicts: bidirectional fnmatch/kind) ---- #
    problems.extend(_claim_conflicts(workers))

    return (len(problems) == 0), problems


def _has_cycle(deps: dict[str, list[str]], key_set: set[str]) -> bool:
    """Detect any cycle (self-deps count) in the depends_on graph via DFS colouring.
    Unknown deps are ignored here — they're reported separately as DEP_UNKNOWN."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in key_set}

    def visit(node: str) -> bool:
        color[node] = GREY
        for nxt in deps.get(node, []):
            if nxt not in color:  # unknown ref: not a cycle edge
                continue
            if color[nxt] == GREY:
                return True
            if color[nxt] == WHITE and visit(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[k] == WHITE and visit(k) for k in key_set)


def _claim_conflicts(workers: list[Any]) -> list[dict[str, str]]:
    """Return CLAIM_CONFLICT problems for any glob-overlap between two workers' claims within
    one ``kind``. Mirrors StateEngine._claim_conflicts (state_engine.py:1137-1160): overlap is
    tested in BOTH directions with fnmatchcase, and ONLY between claims of the same kind."""
    problems: list[dict[str, str]] = []
    pending: list[tuple[str, str]] = []  # (kind, key) accumulated across earlier workers
    for w in workers:
        if not isinstance(w, dict):
            continue
        wc = sorted({
            (str(c["kind"]), str(c["key"]))
            for c in (w.get("claims") or [])
            if isinstance(c, dict) and c.get("kind") and c.get("key")
        })
        for kind, key in wc:
            for pk, pkey in pending:
                if pk == kind and (key == pkey or fnmatchcase(key, pkey) or fnmatchcase(pkey, key)):
                    problems.append(_problem(
                        "CLAIM_CONFLICT",
                        f"claim ({kind},{key}) overlaps another worker's ({pk},{pkey})"))
        pending.extend(wc)
    return problems


# --------------------------------------------------------------------------- #
# Model-driven proposer — PROPOSES only; never decides.                       #
# --------------------------------------------------------------------------- #
_PROPOSE_PROMPT = """You are a task PLANNER. Decompose the goal below into a set of \
DISJOINT worker sub-tasks and return ONE JSON object — nothing else.

You may ONLY PROPOSE a decomposition and a capability tier for each worker. You may NOT \
create tasks, grant file claims, choose any state transition, or declare any worker done — \
a separate deterministic validator decides whether your proposal is acceptable.

Return exactly this shape:
{{
  "mission_id": "<id or a placeholder>",
  "coverage_spec": {{"required_units": ["..."], "max_workers": <int>}},
  "workers": [{{
    "key": "<short-slug>",
    "objective": "<one imperative sentence>",
    "track": <one of {tracks}>,
    "tier_profile": <one of {profiles}>,
    "owns_units": ["<subset of required_units>"],
    "claims": [{{"kind": "path", "key": "<disjoint glob, e.g. src/api/**>"}}],
    "depends_on": ["<other worker key>"],
    "artifact_lane": "<path>",
    "acceptance": {{"kind": "smoke_receipt"}}
  }}],
  "reducers": [{{"kind": "git_merge", "order": ["<worker keys in merge order>"]}}]
}}

Rules: required_units must be COLLECTIVELY covered and each owned by EXACTLY ONE worker; \
claims must not overlap; depends_on must form a DAG (no cycles).

GOAL: {goal}
"""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort: pull the first JSON object out of a model reply. Tries a ```json fence,
    then the outermost {...} span, then the whole string. Returns None if nothing parses to a
    dict — the caller then degrades gracefully (never fabricates a manifest)."""
    if not text:
        return None
    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])
    candidates.append(text)
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def propose_manifest(goal: str, *, tier: str = "ollama",
                     max_tokens: int = _PROPOSE_MAX_TOKENS) -> dict[str, Any] | None:
    """Ask a FREE model to PROPOSE a decomposition manifest for ``goal``.

    Judge-free boundary (structural): the model MAY propose a decomposition + per-worker tier
    assignment. It may NOT create tasks, grant claims, choose transitions, or declare any
    worker done — this function only RETURNS a proposal, and that proposal is meaningless
    until it passes :func:`validate_manifest`. The returned dict is the model's raw suggestion,
    UNBLESSED and unvalidated; callers MUST validate before persisting or spawning.

    ``tier`` must be a FREE tier (``FREE_TIERS``); a paid/premium tier raises ``ValueError``
    (CLAUDE.md: the proposer never uses a paid model). Returns ``None`` when the tier is
    unconfigured/unreachable or the reply doesn't contain a parseable JSON object, so callers
    degrade gracefully instead of acting on a fabricated plan.
    """
    if tier not in FREE_TIERS:
        raise ValueError(
            f"propose_manifest refuses non-free tier {tier!r}; allowed FREE_TIERS: {sorted(FREE_TIERS)}"
        )
    prompt = _PROPOSE_PROMPT.format(
        goal=goal,
        tracks=sorted(ALLOWED_TRACKS),
        profiles=sorted(ALLOWED_TIER_PROFILES),
    )
    reply = model_dispatch.llm_complete(prompt, tier=tier, max_tokens=max_tokens)
    if reply is None:
        return None
    return _extract_json_object(reply)
