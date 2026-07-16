"""GAP-CORTEX-0011 (first cut): protected-span registry + fail-closed preservation checker.

Any context compressor MUST guarantee that PROTECTED spans -- the task, the contract, cited
evidence, safety rules, the current `seeking` intent: anything whose loss corrupts the work --
survive compression. The design says to build this safety floor FIRST, before any multi-stage
selection cascade, because a compressor that silently drops a protected span is worse than no
compressor (it produces confident, corrupted context). So: **fail-closed** -- a compression
that loses a protected span is REJECTED, never returned.

This module is deliberately the floor, not the ceiling: `compress` is a simple selection-first
truncator that is *guaranteed* to preserve protected spans; a smarter summarizing cascade can
replace its body later as long as it still passes `preservation_check`.
"""

from __future__ import annotations


def preservation_check(compressed: str, protected: list[str]) -> tuple[bool, list[str]]:
    """`(ok, missing)`: ok iff EVERY protected span appears verbatim in `compressed`. This is the
    gate every compressor output must pass -- fail-closed on any miss."""
    missing = [p for p in protected if p and p not in compressed]
    return (not missing, missing)


def compress(text: str, protected: list[str], max_chars: int, *, sep: str = "\n...\n") -> str:
    """Selection-first compression that GUARANTEES preservation. Every protected span is kept
    verbatim; the remaining budget is filled with the non-protected text (head-first). If the
    protected spans alone exceed `max_chars`, ALL protected content is still kept (preservation
    wins over the budget -- correctness beats size) and the result may exceed max_chars."""
    protected = [p for p in protected if p]
    kept = list(dict.fromkeys(protected))  # de-dup, preserve order
    kept_len = sum(len(k) for k in kept) + len(sep) * max(0, len(kept) - 1)

    remaining = max_chars - kept_len - len(sep)
    if remaining > 0:
        # the non-protected remainder = text with the protected spans blanked out, head-first
        rest = text
        for p in kept:
            rest = rest.replace(p, "")
        rest = rest.strip()
        if rest:
            kept.append(rest[:remaining])
    out = sep.join(kept)
    # fail-closed self-check: a bug in the truncation must never drop a protected span
    ok, missing = preservation_check(out, protected)
    if not ok:
        # correctness beats size: return all protected content joined, budget be damned
        return sep.join(dict.fromkeys(protected))
    return out


def safe_compress(text: str, protected: list[str], max_chars: int) -> tuple[str, bool]:
    """`(compressed, within_budget)`. Always preservation-safe; `within_budget` is False when
    protected content alone forced the result over max_chars (a signal to raise the budget or
    trim what's protected upstream -- never to drop it silently)."""
    out = compress(text, protected, max_chars)
    return out, len(out) <= max_chars
