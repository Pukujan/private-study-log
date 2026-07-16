"""GAP-CORTEX-0011: the fail-closed preservation floor -- compression never drops protected spans."""

from __future__ import annotations

from cortex_core.compressor import compress, preservation_check, safe_compress


def test_preservation_check_catches_a_dropped_span():
    ok, missing = preservation_check("kept this", ["kept this", "but not this"])
    assert ok is False and missing == ["but not this"]
    assert preservation_check("has A and B", ["A", "B"]) == (True, [])


def test_compress_keeps_all_protected_and_respects_budget():
    text = "PROTECTED-TASK " + ("filler " * 200) + " PROTECTED-RULE"
    protected = ["PROTECTED-TASK", "PROTECTED-RULE"]
    out = compress(text, protected, max_chars=120)
    assert preservation_check(out, protected)[0] is True   # both survive
    assert len(out) <= 120 + 10                            # roughly within budget (+sep slack)


def test_protected_wins_over_budget_when_they_dont_fit():
    protected = ["A" * 100, "B" * 100]
    out, within = safe_compress("A" * 100 + "junk" + "B" * 100, protected, max_chars=50)
    assert preservation_check(out, protected)[0] is True   # correctness beats size
    assert within is False                                 # honestly flags the overflow


def test_non_protected_content_is_truncated_not_the_protected():
    text = "KEEPME " + "x" * 1000
    out = compress(text, ["KEEPME"], max_chars=100)
    assert "KEEPME" in out and len(out) <= 110
    assert out.count("x") < 1000  # the filler got truncated, not the protected span
