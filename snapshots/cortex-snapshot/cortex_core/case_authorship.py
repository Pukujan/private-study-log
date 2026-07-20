"""Stamp every eval case with its authorship provenance (Eval Flywheel P0).

Vendor-neutrality lives in WHO AUTHORS THE CASES, not who runs the checker — the
unanimous cross-vendor panel finding (2026-07-09; see docs/EVAL-FLYWHEEL-PLAN.md).
Only ``third_party``-authored cases (the ~26k third-party records under
``evals/hf_datasets/``) may gate model selection. Everything else — the Fable
``*_style`` synthetics, the small hand fixtures, and the ``evals/fable_capture``
oracles — is ``single_vendor_fable`` and routes to the off-gate style report.

No model sits in this path: it is pure classification plus a content hash (never
the filename), so a rename cannot launder authorship and a content edit cannot
hide. Classification fails CLOSED — anything not explicitly under the third-party
allowlist is treated as single-vendor, so a mislabel can never let a vendor gate
itself (Fable's own mitigation).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Only cases under this path prefix are eligible to be third-party gold.
THIRD_PARTY_PREFIX = "evals/hf_datasets/"

THIRD_PARTY = "third_party"
SINGLE_VENDOR_FABLE = "single_vendor_fable"


def _content_hash(content: Any) -> str:
    """SHA-256 over canonicalized content ONLY (never the path), so provenance is
    invariant to renames and always changes when the content changes."""
    canon = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def classify(path: str) -> str:
    """``third_party`` iff the case lives under the hf_datasets allowlist; else
    ``single_vendor_fable`` (fail-closed default)."""
    norm = str(path).replace("\\", "/")
    return THIRD_PARTY if THIRD_PARTY_PREFIX in norm else SINGLE_VENDOR_FABLE


def stamp(record: dict) -> dict:
    """Return ``{case_authorship, source, provenance_hash}`` for one eval case.

    ``record`` carries at least ``path`` (where the case came from) and ``content``
    (the case payload the hash is taken over).
    """
    path = record.get("path", "")
    return {
        "case_authorship": classify(path),
        "source": path,
        "provenance_hash": _content_hash(record.get("content")),
    }
