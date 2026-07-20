"""Frozen tests for the deterministic 'hardened' faithfulness backend.

Covers the three failure modes it closes over the lexical backend:
(1) sentence-level overlap, (2) contradiction (negation/antonym),
(3) hallucinated-entity detection. Plus the end-to-end faithfulness() wiring.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core.faithfulness import (  # noqa: E402
    faithfulness, hardened_grounded, lexical_grounded, _entities, _contradicts,
    _context_sentences,
)


# ---------------------------------------------------------------------------
# (1) sentence-level overlap: content words must land in ONE source sentence
# ---------------------------------------------------------------------------

def test_words_scavenged_across_sentences_are_not_grounded():
    ctx = "The cache is fast. The database is reliable."
    # 'cache' comes from sentence 1, 'reliable' from sentence 2 -> not grounded
    assert not hardened_grounded("The cache is reliable", ctx)


def test_single_sentence_support_is_grounded():
    ctx = "The cache is fast. The database is reliable."
    assert hardened_grounded("The cache is fast", ctx)


def test_lexical_is_fooled_where_hardened_is_not():
    # the exact inflation bug: lexical blends all sources so the scavenged claim
    # scores 1.0; hardened rejects it. This is the reason the backend exists.
    ctx = "The cache is fast. The database is reliable."
    assert lexical_grounded("The cache is reliable", ctx)
    assert not hardened_grounded("The cache is reliable", ctx)


def test_context_sentences_splits_newlines_and_punctuation():
    s = _context_sentences("One two three. Four five six.\n\nSeven eight nine.")
    assert len(s) == 3


# ---------------------------------------------------------------------------
# (2) contradiction: negation polarity + antonyms
# ---------------------------------------------------------------------------

def test_negation_flip_is_contradiction():
    assert _contradicts("The lock is not atomic", "The lock is atomic and safe.")
    assert not hardened_grounded("The lock is not atomic", "The lock is atomic and safe.")


def test_agreeing_negation_is_not_contradiction():
    # both negated -> same polarity -> supported
    assert not _contradicts("The lock is not atomic", "The lock is not atomic here.")
    assert hardened_grounded("The lock is not atomic", "The lock is not atomic here.")


def test_antonym_is_contradiction():
    src = "Latency decreased under load significantly."
    assert _contradicts("Latency increased under load", src)
    assert not hardened_grounded("Latency increased under load", src)


def test_matching_direction_passes():
    src = "Latency decreased under load significantly."
    assert not _contradicts("Latency decreased under load", src)
    assert hardened_grounded("Latency decreased under load", src)


def test_passed_failed_antonym():
    assert _contradicts("The build passed", "The build failed on CI.")
    assert not _contradicts("The build passed", "The build passed on CI.")


def test_unrelated_negation_is_not_contradiction():
    # different topics -> a stray negation must not fire a false contradiction
    assert not _contradicts("The queue is not empty", "The cache is warm and fast.")


# ---------------------------------------------------------------------------
# (3) hallucination: salient entities absent from every source
# ---------------------------------------------------------------------------

def test_entity_extraction_picks_salient_names():
    ents = _entities("It added a Redis queue using O_CREAT and HTTP/2 at v3")
    assert "redis" in ents        # mid-sentence proper noun
    assert "o_creat" in ents      # underscore identifier
    assert "http/2" in ents       # slash + digit
    assert "v3" in ents           # versioned token


def test_sentence_initial_capital_is_not_an_entity():
    # 'The' leads the sentence -> not treated as a proper noun
    assert "the" not in _entities("The cache is warm")


def test_hallucinated_entity_fails():
    assert not hardened_grounded("It added a Redis queue", "The system uses an in-memory queue.")


def test_present_entity_passes():
    src = "The system uses a Redis queue for buffering messages."
    assert hardened_grounded("It uses a Redis queue", src)


def test_pure_numbers_are_gated_not_treated_as_entities():
    # numbers go through the number check, not the entity check
    assert not hardened_grounded("latency was 12ms", "the latency was 30ms under load")
    assert hardened_grounded("latency was 30ms", "the latency was 30ms under load")


# ---------------------------------------------------------------------------
# end-to-end faithfulness(..., backend="hardened")
# ---------------------------------------------------------------------------

def test_grounded_digest_passes_hardened():
    src = ["The rebuild lock uses an atomic O_CREAT flag. Recall@5 was 0.82."]
    dig = "The rebuild lock uses an atomic O_CREAT flag. Recall@5 was 0.82."
    r = faithfulness(dig, src, backend="hardened")
    assert r.passed and r.score == 1.0 and r.backend == "hardened"


def test_hallucinated_digest_fails_hardened():
    src = ["Recall@5 was 0.82 on the benchmark."]
    r = faithfulness("Recall@5 reached 0.95. It also added a Redis queue.", src, backend="hardened")
    assert not r.passed and r.score == 0.0


def test_contradicting_digest_fails_hardened():
    src = ["The lock is atomic and the queue is durable."]
    r = faithfulness("The lock is not atomic and the queue is durable.", src, backend="hardened")
    # claim 1 contradicts, claim 2 agrees -> at most 0.5, below default 0.8 threshold
    assert not r.passed and r.score <= 0.5


def test_empty_context_auto_fails_hardened():
    r = faithfulness("Everything worked and nothing failed.", [], backend="hardened")
    assert r.empty_context and r.score == 0.0 and not r.passed
