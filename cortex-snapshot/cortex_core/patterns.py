"""Phase 5 gate 5.1: the failure/success **pattern library** (a KEDB -- Known
Error Database, ITIL/SRE-postmortem shaped).

A pattern is a first-class corpus artifact promoted out of closeouts when a
failure class *repeats*: `symptom, root_cause, detection_recipe, fix,
evidence_links, occurrence_count, first/last_seen, last_verified`. The
non-negotiable field is the **detection recipe** -- a symptom-only entry is a
horoscope; a pattern must say how to *detect/reproduce* the class, or it isn't
promoted (`validate_pattern` enforces this). Patterns live in `patterns/` and
are indexed like the rest of the corpus, so they're searchable and served back
as guidance -- closing the self-learning loop.

`promote_candidates` is the librarian's detector: it clusters existing closeouts
by task_type + shared terms and surfaces classes that recur >= a threshold, so a
human/agent can author them into patterns with real detection recipes. Authoring
is deliberately curated, not auto-generated -- a wrong detection recipe served
confidently is worse than none.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uuid6

from .config import resolve_workspace
from .contract import TASK_TYPES  # ONE shared task-type vocabulary (gate 5.3)

PATTERNS_DIRNAME = "patterns"

# A class must recur at least this many times before it's promotable -- no
# generalizing from n=1 (gate 5.1 pitfall).
DEFAULT_MIN_OCCURRENCE = 2

_STOPWORDS = frozenset(
    "the a an and or of to in on for with from is are was were be been this that "
    "it its at by as fix bug issue error when after before into not no yes has have "
    "cortex phase gate test tests real also only per via etc".split()
)


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    title: str
    symptom: str
    root_cause: str
    detection_recipe: str  # REQUIRED -- how to detect/reproduce (no horoscopes)
    fix: str
    evidence_links: list[str]  # workspace-relative paths that must resolve
    task_type: str
    occurrence_count: int
    first_seen: str
    last_seen: str
    last_verified: str
    status: str = "active"  # active | superseded
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pattern":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        return cls(**known)  # type: ignore[arg-type]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_pattern_id() -> str:
    return str(uuid6.uuid7())


def validate_pattern(pattern: Pattern, workspace: str | Path | None = None) -> tuple[bool, list[str]]:
    """A pattern is well-formed only if it has a real detection recipe (not a
    symptom-only horoscope), the required substance fields, a known task_type, a
    recurrence that cleared the promotion floor, and evidence links that resolve
    to real files."""
    ws = resolve_workspace(workspace)
    errors: list[str] = []
    for field_name in ("title", "symptom", "root_cause", "fix"):
        if not str(getattr(pattern, field_name)).strip():
            errors.append(f"{field_name} is empty")
    if not pattern.detection_recipe.strip():
        errors.append("detection_recipe is empty (a symptom without a detection recipe is a horoscope)")
    if pattern.task_type not in TASK_TYPES:
        errors.append(f"task_type {pattern.task_type!r} not in {TASK_TYPES}")
    if pattern.occurrence_count < DEFAULT_MIN_OCCURRENCE:
        errors.append(f"occurrence_count {pattern.occurrence_count} below promotion floor {DEFAULT_MIN_OCCURRENCE} (don't generalize from n=1)")
    if not pattern.evidence_links:
        errors.append("evidence_links is empty (a pattern must cite the closeouts/reviews it was distilled from)")
    for ref in pattern.evidence_links:
        target = (ws / ref).resolve()
        if not target.is_relative_to(ws.resolve()):
            errors.append(f"evidence_link escapes the workspace: {ref}")
        elif not target.is_file():
            errors.append(f"evidence_link does not resolve to a real file: {ref}")
    return (not errors, errors)


def patterns_dir(workspace: str | Path | None = None) -> Path:
    return resolve_workspace(workspace) / PATTERNS_DIRNAME


def save_pattern(pattern: Pattern, workspace: str | Path | None = None) -> Path:
    """Persist a pattern as a searchable markdown artifact (frontmatter + body)
    plus a JSON sidecar, so the index picks it up as first-class corpus content."""
    d = patterns_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", pattern.title.lower()).strip("-")[:60] or "pattern"
    path = d / f"{slug}__{pattern.pattern_id}.md"
    fm = {
        "schema_version": pattern.schema_version,
        "pattern_id": pattern.pattern_id,
        "title": pattern.title,
        "task_type": pattern.task_type,
        "status": pattern.status,
        "occurrence_count": pattern.occurrence_count,
        "first_seen": pattern.first_seen,
        "last_seen": pattern.last_seen,
        "last_verified": pattern.last_verified,
        "evidence_links": pattern.evidence_links,
    }
    body = ["---"]
    for k, v in fm.items():
        body.append(f"{k}: {json.dumps(v) if isinstance(v, (str, list, dict)) else v}")
    body.append("---")
    body += [
        f"# {pattern.title}",
        "",
        "## Symptom",
        pattern.symptom,
        "",
        "## Root cause",
        pattern.root_cause,
        "",
        "## Detection recipe",
        pattern.detection_recipe,
        "",
        "## Fix",
        pattern.fix,
        "",
        "## Evidence",
    ]
    body += [f"- {ref}" for ref in pattern.evidence_links]
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(pattern.to_dict(), indent=2), encoding="utf-8")
    return path


def load_patterns(workspace: str | Path | None = None) -> list[Pattern]:
    d = patterns_dir(workspace)
    if not d.is_dir():
        return []
    out: list[Pattern] = []
    for j in sorted(d.glob("*.json")):
        try:
            out.append(Pattern.from_dict(json.loads(j.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def promote_candidates(
    workspace: str | Path | None = None, min_occurrence: int = DEFAULT_MIN_OCCURRENCE
) -> list[dict[str, Any]]:
    """The librarian's repeat-class detector. Reads closeouts and surfaces
    candidate recurring classes (shared distinctive terms across >= min_occurrence
    closeouts) for a human/agent to author into patterns. This finds *where to
    look*; it deliberately does NOT auto-write patterns (a wrong detection recipe
    served confidently is worse than none)."""
    ws = resolve_workspace(workspace)
    closeouts: list[tuple[str, set[str]]] = []
    for j in sorted((ws / "audit").glob("audit-log-*/agent/*.json")):
        try:
            data = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = f"{data.get('task', '')} {data.get('result', '')}"
        closeouts.append((j.stem, _terms(text)))

    term_docs: dict[str, list[str]] = {}
    for stem, terms in closeouts:
        for t in terms:
            term_docs.setdefault(t, []).append(stem)

    candidates = [
        {"term": t, "occurrences": len(stems), "closeouts": stems}
        for t, stems in term_docs.items()
        if len(stems) >= min_occurrence
    ]
    candidates.sort(key=lambda c: c["occurrences"], reverse=True)
    return candidates


# --------------------------------------------------------------------------- auto-minted candidates (GAP G1)
# Auto-wiring quarantine sink. A CANDIDATE is a machine-minted *suggestion* that a
# recurring failure/fix class may deserve an authored pattern -- it is NOT an active
# pattern (which still requires a human-authored detection recipe via `validate_pattern`).
# It lives under audit/self-learning/ (non-indexed quarantine), NOT in patterns/ (the
# indexed corpus), so unverified suggestions never masquerade as served guidance. This is
# the deliberate boundary G1 asks for: a closeout/gate failure auto-mints a candidate,
# gated by a deterministic regression oracle (self_learning.classify), but promotion to a
# real pattern stays human-gated -- never a free-form self-edit.
PATTERN_CANDIDATES_REL = ("audit", "self-learning", "pattern_candidates.jsonl")


def pattern_candidates_path(workspace: str | Path | None = None) -> Path:
    return resolve_workspace(workspace).joinpath(*PATTERN_CANDIDATES_REL)


def load_pattern_candidates(workspace: str | Path | None = None) -> list[dict[str, Any]]:
    """Load the quarantined auto-minted pattern candidates (last write per task_key wins)."""
    path = pattern_candidates_path(workspace)
    if not path.is_file():
        return []
    state: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn final line (crash mid-append) must not poison reads
        key = rec.get("task_key")
        if key:
            state[key] = rec
    return list(state.values())


def mint_pattern_candidate(
    candidate: dict[str, Any], workspace: str | Path | None = None
) -> dict[str, Any]:
    """UPSERT one quarantined pattern candidate by ``task_key`` (idempotent -- a later,
    more-decisive verdict for the same task replaces the earlier row rather than appending a
    duplicate). Deterministic and stdlib-only: the caller (``self_learning``) supplies a
    verdict already decided by the deterministic oracle; this only persists it. Always stamps
    ``promoted=False`` / ``promotion_status='quarantined'`` -- nothing here becomes an active
    pattern. Returns the stored record."""
    rec = dict(candidate)
    rec.setdefault("task_key", "untitled")
    rec["promoted"] = False
    rec["promotion_status"] = "quarantined"
    rec.setdefault("kind", "pattern_candidate")
    rec["minted_at"] = _now()

    path = pattern_candidates_path(workspace)
    existing = {c["task_key"]: c for c in load_pattern_candidates(workspace)}
    existing[rec["task_key"]] = rec  # upsert
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for c in existing.values():
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return rec


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import make_stdio_encoding_safe

    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex pattern library / KEDB (Phase 5.1)")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--list", action="store_true", help="list existing patterns")
    parser.add_argument("--candidates", action="store_true", help="show repeat-class candidates from closeouts")
    parser.add_argument("--min-occurrence", type=int, default=DEFAULT_MIN_OCCURRENCE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.candidates:
        cands = promote_candidates(args.workspace, min_occurrence=args.min_occurrence)
        if args.json:
            print(json.dumps(cands, indent=2))
        else:
            print(f"repeat-class candidates (>= {args.min_occurrence} closeouts):")
            for c in cands[:30]:
                print(f"  {c['occurrences']:3}x  {c['term']}")
        return 0

    pats = load_patterns(args.workspace)
    if args.json:
        print(json.dumps([p.to_dict() for p in pats], indent=2))
    else:
        print(f"{len(pats)} patterns:")
        for pat in pats:
            print(f"  [{pat.task_type}] {pat.title}  (x{pat.occurrence_count}, {pat.status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
