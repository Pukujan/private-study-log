"""Universal run-evidence schema (GAP-CLOSURE-PLAN J2).

One canonical ``EvidenceBundle`` that every lane of the harness emits, so a
result/promotion row no longer carries a per-lane ad-hoc shape. Stdlib only
(``json``/``hashlib``/``subprocess``/``datetime``) -- readable and validatable
anywhere, no third-party deps.

Builds ON, does not duplicate: ``cortex_core/results_ledger.py`` (C1 results
ledger) and ``evals/live_gen/schema.py`` (per-record live provenance). This
module owns only the *evidence* sub-object; the ledger row keeps its own
fields and now carries one ``evidence`` bundle validated here.

Anti-circular-validation guard (the property the user required for the J
assurance plane)
-----------------------------------------------------------------------
A verdict is only trustworthy if it is bound to the *exact instrument* that
produced it. Therefore ``oracle_version`` AND ``oracle_fixture_sha256`` are
**REQUIRED** fields of every bundle. A verdict can never be silently
re-attributed to a different or updated checker: change the checker's fixture
and the sha changes, so an old verdict no longer validates against the new
instrument. This is what stops "the number is fine, we just swapped the
oracle underneath it" -- the single failure mode a universal-but-instrument-
blind schema would allow. The oracle identity travels WITH the verdict, in
the same bundle, or the bundle is invalid.

Forward-gating
--------------
Every bundle carries ``evidence_schema_version`` (an int). The CI check
(``scripts/ci/check_evidence_bundles.py``) gates *forward*: it validates only
rows tagged at or above a floor version and LOGS (never silently skips) how
many legacy rows are exempt. Legacy rows are not retroactively failed -- they
predate the schema and are honestly counted, not hidden.
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bump when the required-field set changes. The CI check gates on ``>=`` this.
EVIDENCE_SCHEMA_VERSION = 1

# Required fields -- every bundle MUST carry these (see anti-circular note for
# why oracle_version + oracle_fixture_sha256 are non-negotiable).
REQUIRED_FIELDS = (
    "evidence_schema_version",
    "trace_id",
    "model",
    "model_exact_id",
    "git_commit",
    "dataset_version",
    "holdout_version",
    "oracle_version",
    "oracle_fixture_sha256",
    "artifact_shas",
    "verdict",
    "abstained",
    "ts",
)

# Optional fields -- allowed but not required. Anything else is unknown/rejected.
OPTIONAL_FIELDS = (
    "image_digest",   # docker image digest the run executed under, if containerized
    "confidence",     # judge/model confidence in [0, 1], when meaningful
)

_ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS) | frozenset(OPTIONAL_FIELDS)

# ISO-8601 UTC 'YYYY-MM-DDTHH:MM:SSZ'
_TS_RE = None  # compiled lazily below to keep import cheap
import re as _re  # noqa: E402
_TS_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# A sha256 hex digest.
_SHA256_RE = _re.compile(r"^[0-9a-f]{64}$")


def sha256_hex(data: str | bytes) -> str:
    """sha256 hex of text or bytes (utf-8 for text)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """sha256 hex of a file's bytes (streamed)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_evidence_bundle(bundle: Any) -> tuple[bool, list[str]]:
    """Validate one evidence bundle against the schema.

    Returns ``(ok, problems)``: ``ok`` is True iff ``problems`` is empty.
    Never raises on a bad bundle -- callers that want fail-loud wrap this and
    raise; the CI check wants the full problem list to report.
    """
    problems: list[str] = []

    if not isinstance(bundle, dict):
        return False, [f"bundle must be a dict, got {type(bundle).__name__}"]

    # required present
    for f in REQUIRED_FIELDS:
        if f not in bundle:
            problems.append(f"missing required field {f!r}")

    # unknown rejected (a typo'd required field would otherwise pass silently)
    for k in bundle:
        if k not in _ALLOWED_FIELDS:
            problems.append(f"unknown field {k!r}")

    # If required fields are missing we still type-check the ones present.
    def _is_nonempty_str(v: Any) -> bool:
        return isinstance(v, str) and bool(v.strip())

    if "evidence_schema_version" in bundle:
        v = bundle["evidence_schema_version"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            problems.append(f"evidence_schema_version must be an int >= 1, got {v!r}")

    for f in ("trace_id", "model", "model_exact_id", "git_commit",
              "dataset_version", "holdout_version", "oracle_version", "verdict"):
        if f in bundle and not _is_nonempty_str(bundle[f]):
            problems.append(f"field {f!r} must be a non-empty string, got {bundle.get(f)!r}")

    # anti-circular: the oracle fixture sha must be a real sha256, not a placeholder
    if "oracle_fixture_sha256" in bundle:
        s = bundle["oracle_fixture_sha256"]
        if not isinstance(s, str) or not _SHA256_RE.match(s):
            problems.append(
                "oracle_fixture_sha256 must be a 64-char sha256 hex digest binding the "
                f"verdict to the exact instrument that produced it, got {s!r}"
            )

    if "artifact_shas" in bundle:
        a = bundle["artifact_shas"]
        if not isinstance(a, list) or any(
            not (isinstance(x, str) and _SHA256_RE.match(x)) for x in a
        ):
            problems.append("artifact_shas must be a list of sha256 hex digests")

    if "abstained" in bundle and not isinstance(bundle["abstained"], bool):
        problems.append(f"abstained must be a bool, got {bundle.get('abstained')!r}")

    if "ts" in bundle and not (isinstance(bundle["ts"], str) and _TS_RE.match(bundle["ts"])):
        problems.append(f"ts must be ISO-8601 UTC 'YYYY-MM-DDTHH:MM:SSZ', got {bundle.get('ts')!r}")

    # optional fields, when present
    if "image_digest" in bundle and bundle["image_digest"] is not None:
        if not _is_nonempty_str(bundle["image_digest"]):
            problems.append("image_digest, when present, must be a non-empty string or null")

    if "confidence" in bundle and bundle["confidence"] is not None:
        c = bundle["confidence"]
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not (0.0 <= c <= 1.0):
            problems.append(f"confidence, when present, must be a number in [0, 1] or null, got {c!r}")

    return (not problems), problems


def _git_commit(repo_root: Path | None = None) -> str:
    """Current git commit sha via stdlib subprocess, or 'unknown' if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 -- git absent / not a repo: honest sentinel, never crash a run
        return "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_evidence_bundle(
    *,
    trace_id: str,
    model: str,
    model_exact_id: str,
    dataset_version: str,
    holdout_version: str,
    oracle_version: str,
    oracle_fixture_sha256: str,
    verdict: str,
    abstained: bool = False,
    artifact_shas: list[str] | None = None,
    image_digest: str | None = None,
    confidence: float | None = None,
    git_commit: str | None = None,
    ts: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a schema-valid evidence bundle from a run.

    Fills ``git_commit`` via ``git rev-parse HEAD`` and ``ts`` via UTC now
    when not supplied. Caller supplies the identity + verdict fields. The
    result is validated before return -- a builder that emits an invalid
    bundle is a bug, so it raises rather than hand back garbage.
    """
    bundle: dict[str, Any] = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "model": model,
        "model_exact_id": model_exact_id,
        "git_commit": git_commit if git_commit is not None else _git_commit(repo_root),
        "dataset_version": dataset_version,
        "holdout_version": holdout_version,
        "oracle_version": oracle_version,
        "oracle_fixture_sha256": oracle_fixture_sha256,
        "artifact_shas": list(artifact_shas or []),
        "verdict": verdict,
        "abstained": bool(abstained),
        "ts": ts if ts is not None else _utc_now(),
    }
    if image_digest is not None:
        bundle["image_digest"] = image_digest
    if confidence is not None:
        bundle["confidence"] = confidence

    ok, problems = validate_evidence_bundle(bundle)
    if not ok:
        raise ValueError(f"build_evidence_bundle produced an invalid bundle: {problems}")
    return bundle
