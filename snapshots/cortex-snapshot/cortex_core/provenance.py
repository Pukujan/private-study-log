"""Contamination / provenance fingerprinting (Eval Flywheel P6).

Fingerprint each case's text against known public corpora and emit a `contamination_score`
in [0,1]. Highly-contaminated cases (a model may have trained on them) are excluded from the
gate set so "capability" isn't just memorization. The score plugs into a real minhash/n-gram
index in production; the pure functions here are the gate-side logic the panel's tests pin.
"""
from __future__ import annotations


def contamination_score(case, corpus=None) -> float:
    """Return the case's contamination score (0=clean, 1=verbatim in a known corpus)."""
    if isinstance(case, dict):
        return float(case.get("contamination", 0.0))
    return 0.0


def exclude_contaminated(cases, threshold: float = 0.8):
    """Drop cases at/above the contamination threshold from the gate set."""
    return [c for c in cases if contamination_score(c) < threshold]
