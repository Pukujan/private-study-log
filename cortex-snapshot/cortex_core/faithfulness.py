"""Faithfulness interface — is a digest/summary GROUNDED in its cited sources?

Cortex-owned thin wrapper (GAP-CORTEX-0009 "Next Gate": build this first, then the RAPTOR
digest tree on top). Shape adopted from DeepEval's `FaithfulnessMetric` (referenceless:
actual_output + retrieval_context, no question needed); definition adopted from RAGAS
(score = supported_claims / total_claims) — the *definition*, not the churning `ragas` package.

Pipeline: decompose digest into atomic claims -> per-claim grounding verdict against the cited
sources -> score = supported / total. Pluggable grounding backend:

  "lexical"  (default, offline, deterministic, no model load): a claim is grounded iff every
             quoted span and number in it is traceable to the cited sources AND enough of its
             salient content words appear there. Fast, replayable, Windows-friendly.
  "hardened" (opt-in, offline, deterministic): the lexical backend with three failure modes it
             misses closed (Fable, 2026-07-05). (1) SENTENCE-LEVEL overlap — a claim's content
             words must concentrate in a SINGLE source sentence, not be scavenged from the whole
             blended bag-of-words (the lexical backend's inflation bug). (2) CONTRADICTION —
             negation-polarity flips ("is atomic" vs "is not atomic") and antonym mismatches
             ("latency decreased" vs "latency increased") against the best-matching sentence fail
             the claim even when the vocabulary overlaps. (3) HALLUCINATION — salient entities in
             the claim (code identifiers, acronyms, versioned/typed names, mid-sentence proper
             nouns) absent from every source fail the claim, catching invented specifics that
             ordinary content-word overlap dilutes below the threshold.
  "minicheck"(optional): MiniCheck (Liyan06/MiniCheck, EMNLP 2024) sentence-level fact-checker
             if installed — "on par with GPT-4, 400x cheaper", local, no API. The intended
             default per the gap; wired but not required (not installed here yet).
  "strict"   (opt-in, offline, deterministic): the hardened v2 backend adapted from
             evals/fable_capture/citation_checker_v2. Per-citation boundary tracking
             ([S1]-style markers resolve to individual sources instead of blending all
             context), per-claim statuses (UNCITED / UNRESOLVED_CITATION / QUOTE_SUPPORTED /
             QUOTE_UNSUPPORTED / NUMBER_SUPPORTED / NUMBER_UNSUPPORTED / CONTRADICTED /
             UNVERIFIABLE), metric-value contradiction detection, and robust number parsing
             (thousands separators, dollar-prefix, attached units like "210ms", spelled-out
             units like "48 percent"). UNVERIFIABLE claims ABSTAIN: they carry grounded=None
             in per_claim and are excluded from the score denominator — the checker never
             guesses on anchor-free prose.
  callable   : any (claim: str, context: str) -> bool.

Two guards from the gap's Scope Decision, both mandatory:
  * empty-context guard — a digest citing ZERO sources auto-fails (score forced 0.0), defusing
    the documented "nothing to contradict -> falsely ~1.0" artifact.
  * threshold is a CALIBRATION SEED (default 0.8), never asserted as universal; callers get the
    full per-claim distribution so the gate can be recalibrated against a real digest baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_QUOTE = re.compile(r"[\"“”]([^\"“”]{4,})[\"“”]")
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)%?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_STOP = {
    "the", "and", "for", "that", "this", "with", "was", "were", "are", "has", "have", "had",
    "from", "into", "over", "under", "than", "then", "them", "they", "their", "there", "here",
    "which", "while", "when", "what", "who", "whom", "whose", "will", "would", "could", "should",
    "been", "being", "also", "such", "each", "some", "most", "more", "less", "very", "not", "but",
    "its", "his", "her", "our", "your", "all", "any", "can", "may", "one", "two", "per", "via",
}


def decompose_claims(text: str) -> list:
    """Split a digest into atomic claim sentences (drops trivial fragments)."""
    out = []
    for s in _SENT_SPLIT.split((text or "").strip()):
        s = s.strip()
        if len(s) >= 12 and _WORD.search(s):
            out.append(s)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def _keywords(s: str) -> set:
    return {w for w in _WORD.findall(s.lower()) if w not in _STOP}


def lexical_grounded(claim: str, context: str, overlap: float = 0.5) -> bool:
    """Deterministic grounding: quotes + numbers must be present; content words mostly overlap."""
    ctx = _norm(context)
    for q in _QUOTE.findall(claim):
        if _norm(q) not in ctx:
            return False
    ctx_nums = set(re.findall(r"\d+(?:\.\d+)?", ctx))
    for n in _NUMBER.findall(claim):
        if n not in ctx_nums:
            return False
    kw = _keywords(claim)
    if not kw:
        return True  # no salient content to contradict (quotes/numbers already checked)
    ctx_kw = set(_WORD.findall(ctx))
    hit = sum(1 for w in kw if w in ctx_kw)
    return (hit / len(kw)) >= overlap


# ============================================================================
# "hardened" backend — deterministic lexical hardening (Fable, 2026-07-05).
# Closes three failure modes of lexical_grounded: cross-sentence word
# scavenging, polarity/antonym contradiction, and hallucinated entities.
# No model load, no LLM, offline, replayable.
# ============================================================================

# pure grammatical negation cues (antonym pairs live in _ANTONYMS, not here, so
# "failed"/"passed" are handled as antonyms rather than negation)
_NEG_CUES = frozenset({
    "not", "no", "never", "none", "without", "cannot", "cant", "nor", "neither",
    "isnt", "arent", "wasnt", "werent", "didnt", "doesnt", "dont", "wont",
    "couldnt", "shouldnt", "wouldnt", "hasnt", "havent", "isint", "non",
    "unable", "absent", "missing", "lacks", "lacking", "fails", "failed", "fail",
}) - {"fails", "failed", "fail"}  # fail-family are antonyms of pass, not negation

_ANTONYM_PAIRS = [
    ("increase", "decrease"), ("increased", "decreased"), ("increases", "decreases"),
    ("increasing", "decreasing"), ("rise", "fall"), ("rose", "fell"), ("rising", "falling"),
    ("higher", "lower"), ("more", "less"), ("greater", "fewer"), ("above", "below"),
    ("pass", "fail"), ("passed", "failed"), ("passes", "fails"), ("passing", "failing"),
    ("success", "failure"), ("succeed", "fail"), ("succeeded", "failed"),
    ("enable", "disable"), ("enabled", "disabled"), ("enables", "disables"),
    ("add", "remove"), ("added", "removed"), ("adds", "removes"),
    ("allow", "deny"), ("allowed", "denied"), ("accept", "reject"), ("accepted", "rejected"),
    ("valid", "invalid"), ("correct", "incorrect"), ("true", "false"),
    ("present", "absent"), ("include", "exclude"), ("included", "excluded"),
    ("before", "after"), ("start", "stop"), ("started", "stopped"), ("open", "closed"),
    ("atomic", "nonatomic"), ("safe", "unsafe"), ("secure", "insecure"),
    ("supported", "unsupported"), ("grounded", "ungrounded"), ("stable", "unstable"),
]
_EMPTY: frozenset = frozenset()
_ANTONYMS: dict = {}
for _a, _b in _ANTONYM_PAIRS:
    _ANTONYMS.setdefault(_a, set()).add(_b)
    _ANTONYMS.setdefault(_b, set()).add(_a)

# an entity token: has letters, may carry digits/underscore/@//. Pure numbers are
# excluded (numbers are already gated separately against the context).
_ENT_TOKEN = re.compile(r"[A-Za-z0-9_@/][A-Za-z0-9_@/.\-]*")

# ---------------------------------------------------------------------------
# v2 hardening (2026-07-11): four deterministic CONTRADICTION signals closing
# leaks the entity/overlap/negation checks miss. Each fires only to REJECT and
# only on a concrete, source-checkable mismatch — no world knowledge, no LLM.
# Classes: numeric (spelled-out cardinal, quantifier-vs-bound), relational
# (name-order swap), temporal ordering (date arithmetic). Design + evidence:
# docs/research/faithfulness-hardening-spec-2026-07-11.md.
# ---------------------------------------------------------------------------

# spelled-out cardinals. "one" is deliberately excluded (polysemous: "one of the").
_CARDINALS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 1000000,
    "billion": 1000000000,
}
_DIGIT_RE = re.compile(r"\d")
_CAP_BIGRAM = re.compile(r"\b([A-Z][a-zA-Z]+)\s+([A-Z][a-zA-Z]+)\b")
_QUANT_NUM = re.compile(r"\b(?:exactly|only|just|precisely)\s+\$?([\d,]+)")
_BOUND_NUM = re.compile(r"\b(?:more than|over|at least|upwards of|greater than|no fewer than)\s+\$?([\d,]+)")
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_PROPER = re.compile(r"[A-Z][A-Za-z0-9.&'\-]*(?:\s+[A-Z][A-Za-z0-9.&'\-]*)*")
# genuine comparative/superlative ORDERING cues only. Narrative "later/after/second"
# on their own are excluded — they are not ordering comparisons.
_ORDER_EARLIER = re.compile(
    r"\b(?:started first|born first|released first|founded first|came first|was first|"
    r"older|oldest|earlier|earliest)\b", re.I)
_ORDER_LATER = re.compile(r"\b(?:younger|youngest|released second|born later|came later)\b", re.I)


def _cardinal_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w in _CARDINALS}


def _cardinal_contradiction(claim: str, ctx_lower: str) -> bool:
    """Numeric class: a spelled-out cardinal in the claim, absent from the source, in a
    context that is itself numeric (has some number). The spelled-out twin of the existing
    digit-number gate — the digit regex never sees words like 'seven'."""
    if not (_cardinal_words(ctx_lower) or _DIGIT_RE.search(ctx_lower)):
        return False  # non-numeric context: don't police stray number-words in prose
    return any(w not in ctx_lower for w in _cardinal_words(claim))


def _quantifier_bound_contradiction(claim: str, context: str) -> bool:
    """Numeric class: claim pins a value ('exactly/only N') that the source bounds
    from below ('more than N') — same number, contradicted bound."""
    claim_pins = {m.group(1).replace(",", "") for m in _QUANT_NUM.finditer(claim.lower())}
    if not claim_pins:
        return False
    src_bounds = {m.group(1).replace(",", "") for m in _BOUND_NUM.finditer(context.lower())}
    return bool(claim_pins & src_bounds)


def _name_order_swap(claim: str, ctx_lower: str) -> bool:
    """Relational class: a Capitalized bigram 'A B' in the claim whose forward form is
    absent from the source but whose reversal 'B A' is present — a swapped proper name."""
    for m in _CAP_BIGRAM.finditer(claim):
        a, b = m.group(1).lower(), m.group(2).lower()
        if a == b:
            continue
        if ("%s %s" % (a, b)) not in ctx_lower and ("%s %s" % (b, a)) in ctx_lower:
            return True
    return False


def _entity_years(context: str) -> dict:
    """Map each Capitalized name-run in the source to the earliest year sharing its
    sentence. Deterministic proximity association for the temporal-ordering check."""
    d: dict = {}
    for sent in re.split(r"(?<=[.!?])\s+|\.(?=[A-Z])", context):
        years = [int(y) for y in _YEAR_RE.findall(sent)]
        if not years:
            continue
        y = min(years)
        for m in _PROPER.finditer(sent):
            name = m.group(0).strip().lower()
            if len(name) >= 3:
                d[name] = min(d.get(name, 9999), y)
    return d


def _temporal_ordering_contradiction(claim: str, context: str) -> bool:
    """Temporal-ordering class: when the claim carries a genuine comparative ordering cue
    and the source dates BOTH the claim's subject entity and >=1 competitor, compute the
    ordering from the parsed years. Claims 'earliest' but not the min (or 'latest' but not
    the max) -> contradiction. Abstains (returns False) whenever a needed year is absent —
    it never guesses an order it cannot compute."""
    earlier = bool(_ORDER_EARLIER.search(claim))
    later = bool(_ORDER_LATER.search(claim))
    if earlier == later:  # need exactly one, unambiguous direction
        return False
    years = _entity_years(context)
    if len(years) < 2:
        return False
    subj_m = _PROPER.match(claim.strip())
    if not subj_m:
        return False
    subj = subj_m.group(0).strip().lower()
    subj_toks = set(subj.split())

    def matches(name: str) -> bool:
        return subj in name or name in subj or bool(subj_toks & set(name.split()))

    subj_year = None
    for name, y in years.items():
        if matches(name):
            subj_year = y if subj_year is None else min(subj_year, y)
    if subj_year is None:
        return False
    others = [y for name, y in years.items() if not matches(name)]
    if not others:
        return False
    if earlier and subj_year > min(others):
        return True
    if later and subj_year < max(others):
        return True
    return False


def _context_sentences(context: str) -> list:
    """Split blended context into individual sentences (newline- and punctuation-aware)."""
    out = []
    for block in re.split(r"\n+", context or ""):
        for s in _SENT_SPLIT.split(block.strip()):
            s = s.strip()
            if s:
                out.append(s)
    return out


def _neg_present(s: str) -> bool:
    """True if a grammatical negation cue is present (apostrophes folded: isn't -> isnt)."""
    toks = set(re.findall(r"[a-z]+", s.lower().replace("'", "").replace("’", "")))
    return bool(_NEG_CUES & toks)


def _entities(claim: str) -> set:
    """Salient entities whose presence a real source must witness: code identifiers
    (underscore/@//), versioned/typed names (internal digits), acronyms (ALLCAPS), and
    mid-sentence proper nouns (a leading capital not at the sentence start). Ordinary
    lowercase words are left to the overlap check; sentence-initial capitals are skipped
    (they carry no proper-noun signal)."""
    ents = set()
    toks = list(_ENT_TOKEN.finditer(claim))
    for i, m in enumerate(toks):
        tok = m.group(0).strip("-./")
        if len(tok) < 2 or not re.search(r"[A-Za-z]", tok):
            continue
        low = tok.lower()
        if low in _STOP:
            continue
        has_struct = ("_" in tok) or bool(re.search(r"\d", tok)) or ("@" in tok) or ("/" in tok)
        allcaps = tok.isupper() and len(tok) >= 2
        # a proper noun: a capital letter on a token that is NOT the sentence's first word
        midcap = i > 0 and any(c.isupper() for c in tok)
        if has_struct or allcaps or midcap:
            ents.add(low)
    return ents


def hardened_grounded(claim: str, context: str, overlap: float = 0.6) -> bool:
    """Deterministic hardened grounding: quotes + numbers traceable, no hallucinated
    entities, content words concentrated in ONE source sentence, and no polarity/antonym
    contradiction against that sentence. The default overlap (0.6) is stricter than the
    lexical backend's 0.5 because the bar is now a single sentence, not the blended corpus,
    so scattered half-matches no longer clear it."""
    ctx = _norm(context)

    # quoted spans must appear verbatim
    for q in _QUOTE.findall(claim):
        if _norm(q) not in ctx:
            return False

    # numbers must be witnessed by the context
    ctx_nums = set(re.findall(r"\d+(?:\.\d+)?", ctx))
    for n in _NUMBER.findall(claim):
        if n not in ctx_nums:
            return False

    # v2 numeric hardening: spelled-out cardinal mismatch + quantifier-vs-bound
    if _cardinal_contradiction(claim, ctx):
        return False
    if _quantifier_bound_contradiction(claim, context):
        return False

    # v2 relational hardening: swapped proper-name order
    if _name_order_swap(claim, ctx):
        return False

    # v2 temporal hardening: comparative ordering contradicted by parsed source dates
    if _temporal_ordering_contradiction(claim, context):
        return False

    # hallucination: every salient entity must be traceable to some source
    for ent in _entities(claim):
        if ent not in ctx:
            return False

    sentences = _context_sentences(context)
    kw = _keywords(claim)

    # sentence-level overlap: the claim's content must land in a SINGLE sentence
    best_sent, best_ratio = None, -1.0
    for s in sentences:
        s_kw = set(_WORD.findall(s.lower()))
        ratio = (sum(1 for w in kw if w in s_kw) / len(kw)) if kw else 1.0
        if ratio > best_ratio:
            best_ratio, best_sent = ratio, s
    if kw and best_ratio < overlap:
        return False
    if best_sent is None:  # no context sentences at all
        return not kw

    # contradiction against the best-matching sentence
    return not _contradicts(claim, best_sent)


def _contradicts(claim: str, sentence: str) -> bool:
    """Polarity flip or antonym mismatch between a claim and the source sentence that
    otherwise supports it. Requires shared content so we only judge same-topic pairs."""
    c_kw = _keywords(claim)
    s_kw = _keywords(sentence)
    shared = c_kw & s_kw

    # negation polarity: exactly one side negated, with substantive shared topic
    if len(shared) >= 2 and (_neg_present(claim) != _neg_present(sentence)):
        return True

    # antonym mismatch: a claim word whose opposite appears in the sentence
    for w in c_kw:
        if _ANTONYMS.get(w, _EMPTY) & s_kw:
            return True
    return False


class _MiniCheckBackend:
    """Optional MiniCheck grounding backend (loaded lazily; only if the package is installed)."""

    _scorer = None

    @classmethod
    def available(cls) -> bool:
        try:
            import minicheck  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def grounded(cls, claim: str, context: str) -> bool:
        if cls._scorer is None:
            from minicheck.minicheck import MiniCheck  # type: ignore
            cls._scorer = MiniCheck(model_name="flan-t5-large")
        pred, _prob, _, _ = cls._scorer.score(docs=[context], claims=[claim])
        return bool(pred[0])


def _resolve_backend(backend):
    if callable(backend):
        return backend
    if backend == "minicheck":
        if not _MiniCheckBackend.available():
            raise RuntimeError("minicheck backend requested but package not installed")
        return _MiniCheckBackend.grounded
    if backend == "lexical":
        return lexical_grounded
    if backend == "hardened":
        return hardened_grounded
    raise ValueError(f"unknown faithfulness backend {backend!r}")


@dataclass
class FaithfulnessResult:
    score: float                         # supported / total  (0.0 if empty-context)
    supported: int
    total: int
    passed: bool                         # score >= threshold AND not empty-context
    empty_context: bool
    threshold: float
    backend: str
    per_claim: list = field(default_factory=list)  # [{claim, grounded}]

    def asdict(self):
        return {"score": round(self.score, 4), "supported": self.supported, "total": self.total,
                "passed": self.passed, "empty_context": self.empty_context,
                "threshold": self.threshold, "backend": self.backend,
                "per_claim": self.per_claim}


def faithfulness(actual_output: str, retrieval_context, *, threshold: float = 0.8,
                 backend="lexical") -> FaithfulnessResult:
    """Score how faithful `actual_output` (a digest) is to `retrieval_context` (cited sources).

    retrieval_context: a string, a list of source strings, or a dict {source_id: text}. An
    empty / whitespace-only context is the empty-context artifact -> auto-fail (score 0.0),
    never near-1.0.

    backend="strict" enables per-citation boundary tracking and per-claim statuses (see
    module docstring); all other backends blend the context as before.
    """
    if backend == "strict":
        return _strict_faithfulness(actual_output, retrieval_context, threshold)

    if isinstance(retrieval_context, dict):
        context = "\n\n".join(str(c) for c in retrieval_context.values() if c and str(c).strip())
    elif isinstance(retrieval_context, (list, tuple)):
        context = "\n\n".join(str(c) for c in retrieval_context if c and str(c).strip())
    else:
        context = str(retrieval_context or "")

    backend_name = backend if isinstance(backend, str) else getattr(backend, "__name__", "callable")
    claims = decompose_claims(actual_output)

    # empty-context guard: zero grounded sources -> auto-fail regardless of any score
    if not context.strip():
        return FaithfulnessResult(0.0, 0, len(claims), False, True, threshold, backend_name,
                                  [{"claim": c, "grounded": False} for c in claims])
    if not claims:
        # nothing asserted -> vacuously nothing to hallucinate, but not a passing digest either
        return FaithfulnessResult(0.0, 0, 0, False, False, threshold, backend_name, [])

    ground = _resolve_backend(backend)
    per_claim, supported = [], 0
    for c in claims:
        ok = bool(ground(c, context))
        supported += int(ok)
        per_claim.append({"claim": c, "grounded": ok})
    score = supported / len(claims)
    return FaithfulnessResult(score, supported, len(claims), score >= threshold, False,
                              threshold, backend_name, per_claim)


# ============================================================================
# "strict" backend — adapted from evals/fable_capture/citation_checker_v2.
#
# Deterministic claim-vs-source grounding with per-citation boundaries. No LLM,
# no model load. The v2 lexicon/number/quote/contradiction machinery is copied
# here (cortex_core must not import from evals/) with the same names so the two
# implementations stay diffable: _content_tokens, _extract_numbers, _Num,
# _qnorm, _quote_in, _contradicted.
# ============================================================================

_CITE_RE = re.compile(r"\[([A-Za-z]+\d+(?:\s*,\s*[A-Za-z]+\d+)*)\]")
_QUOTE_RE = re.compile(r"[\"“]([^\"“”]{4,}?)[\"”]")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-/]*")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")

# number: optional $ prefix, thousands-grouped or plain, optional attached unit suffix
_NUM_RE = re.compile(
    r"(?<![\w.,/])(\$)?"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(%|x\b|ms\b|s\b|k\b|m\b|gb\b|mb\b|kb\b|tb\b)?",
    re.IGNORECASE,
)

_STOPS = {
    "the", "a", "an", "of", "is", "was", "were", "are", "be", "been", "being",
    "at", "to", "in", "on", "and", "or", "with", "for", "by", "as", "from",
    "that", "this", "these", "those", "it", "its", "their", "our", "his", "her",
    "they", "we", "he", "she", "you", "your", "after", "before", "under", "over",
    "above", "below", "about", "roughly", "approximately", "around", "near",
    "only", "just", "per", "up", "down", "out", "across", "between", "against",
    "while", "when", "where", "which", "who", "whom", "than", "then", "also",
    "not", "no", "but", "if", "because", "so", "such", "other", "own", "all",
    "each", "both", "more", "most", "some", "any", "very", "too", "now",
    "during", "through", "into", "onto", "via", "versus", "vs", "same",
}

_MULTIPLIERS = {"hundred", "thousand", "million", "billion", "trillion"}

# spelled-out unit words -> canonical unit token (also excluded from metric context)
_UNIT_WORDS = {
    "percent": "%", "percentage": "%", "pct": "%",
    "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "day": "d", "days": "d",
    "gb": "gb", "gigabyte": "gb", "gigabytes": "gb",
    "mb": "mb", "megabyte": "mb", "megabytes": "mb",
    "kb": "kb", "tb": "tb",
    "x": "x", "times": "x",
    "degree": "deg", "degrees": "deg", "celsius": "deg", "fahrenheit": "deg",
}

_ATTACHED = {"%": "%", "x": "x", "ms": "ms", "s": "s", "k": "k", "m": "m",
             "gb": "gb", "mb": "mb", "kb": "kb", "tb": "tb"}

# statuses the strict backend can emit per claim
STRICT_STATUSES = frozenset({
    "UNCITED", "UNRESOLVED_CITATION", "QUOTE_SUPPORTED", "QUOTE_UNSUPPORTED",
    "NUMBER_SUPPORTED", "NUMBER_UNSUPPORTED", "CONTRADICTED", "UNVERIFIABLE",
})
_SUPPORTED_STATUSES = frozenset({"QUOTE_SUPPORTED", "NUMBER_SUPPORTED"})
_ABSTAIN_STATUSES = frozenset({"UNVERIFIABLE"})


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _content_tokens(text: str) -> set:
    """Stemmed content words: no citation markers, stopwords, units, multipliers."""
    text = _CITE_RE.sub(" ", text)
    out = set()
    for w in _WORD_RE.findall(text.lower()):
        if w in _STOPS or w in _MULTIPLIERS or w in _UNIT_WORDS or len(w) < 2:
            continue
        out.add(_stem(w))
    return out


class _Num:
    __slots__ = ("value", "unit", "heads")

    def __init__(self, value, unit, heads):
        self.value, self.unit, self.heads = value, unit, heads


def _extract_numbers(text: str) -> list:
    """Numbers with normalized value, canonical unit ('' if none), and head tokens
    (content words right after the number, for empty-unit metric matching)."""
    text = _CITE_RE.sub(" ", text)
    nums = []
    for m in _NUM_RE.finditer(text):
        try:
            value = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        unit = "$" if m.group(1) else ""
        suffix = (m.group(3) or "").lower()
        if not unit and suffix:
            unit = _ATTACHED.get(suffix, suffix)
        tail = [w.lower() for w in _WORD_RE.findall(text[m.end():m.end() + 48])][:6]
        if not unit:
            i = 0
            while i < len(tail) and tail[i] in _MULTIPLIERS:
                i += 1
            if i < len(tail) and tail[i] in _UNIT_WORDS:
                unit = _UNIT_WORDS[tail[i]]
        heads = []
        for w in tail:
            if w in _STOPS or w in _MULTIPLIERS or w in _UNIT_WORDS:
                continue
            heads.append(_stem(w))
            if len(heads) == 2:
                break
        nums.append(_Num(value, unit, set(heads)))
    return nums


def _value_eq(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def _qnorm(s: str) -> str:
    """Normalize for verbatim-span matching: smart quotes/dashes, case, whitespace,
    digit separators removed, remaining punctuation -> space."""
    s = s.lower()
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-").replace("―", "-"))
    s = re.sub(r"(?<=\d)[.,](?=\d)", "", s)          # 48,000 -> 48000 ; 1.74 -> 174 (consistent)
    s = re.sub(r"[^a-z0-9%$\x01 ]+", " ", s)          # \x01 = ellipsis sentinel survives
    return re.sub(r"\s+", " ", s).strip()


def _quote_in(quote: str, source_norm: str) -> bool:
    """Whitespace/case/punctuation-tolerant substring; '...' splits into ordered segments."""
    q = quote.lower().replace("…", "...")
    q = re.sub(r"\.\.\.+", "\x01", q)
    segments = [_qnorm(part) for part in q.split("\x01")]
    segments = [seg for seg in segments if seg]
    if not segments:
        return False
    pos = 0
    for seg in segments:
        found = source_norm.find(seg, pos)
        if found < 0:
            return False
        pos = found + len(seg)
    return True


def _contradicted(claim_body: str, claim_nums: list, missing: list,
                  cited_texts: list) -> bool:
    """A missing claim number contradicts a source number when units are compatible
    and the metric context matches: >=2 shared content tokens between the claim and
    the source sentence (a shared exact co-anchor number counts double), and -- when
    neither side carries a unit -- the head nouns adjacent to the numbers intersect."""
    claim_tokens = _content_tokens(claim_body)
    claim_vals = [n.value for n in claim_nums]
    for text in cited_texts:
        for sent in _SENT_RE.split(text):
            src_nums = _extract_numbers(sent)
            if not src_nums:
                continue
            sent_tokens = _content_tokens(sent)
            sent_vals = [n.value for n in src_nums]
            overlap = len(claim_tokens & sent_tokens)
            for cn in missing:
                co_anchor = any(
                    not _value_eq(v, cn.value) and any(_value_eq(v, sv) for sv in sent_vals)
                    for v in claim_vals)
                score = overlap + (2 if co_anchor else 0)
                if score < 2:
                    continue
                for sn in src_nums:
                    if _value_eq(sn.value, cn.value):
                        continue
                    if cn.unit != sn.unit:
                        continue                       # unit-incompatible: not same metric
                    if not cn.unit and not (cn.heads & sn.heads):
                        continue                       # unitless: require adjacent-noun match
                    return True
    return False


def strict_status(claim_text: str, citations: list, sources: dict) -> str:
    """Return one status string for a single claim. Deterministic, no LLM.

    citations: list of source ids the claim cites (e.g. ["S1"]); sources: {id: text}.
    Precedence: unresolvable citations first, then metric-value contradiction (fires only
    on numbers absent from every cited source, so plain support is never shadowed), then
    quoted spans, then number traceability, else UNVERIFIABLE (abstain, no anchor)."""
    if not citations:
        return "UNCITED"
    if any(c not in sources for c in citations):
        return "UNRESOLVED_CITATION"

    cited_texts = [str(sources[c]) for c in citations]
    body = _CITE_RE.sub(" ", claim_text)
    quotes = [m.group(1).strip() for m in _QUOTE_RE.finditer(body)]
    claim_nums = _extract_numbers(body)

    src_values = [n.value for t in cited_texts for n in _extract_numbers(t)]
    missing = [n for n in claim_nums
               if not any(_value_eq(n.value, sv) for sv in src_values)]

    if missing and _contradicted(body, claim_nums, missing, cited_texts):
        return "CONTRADICTED"

    if quotes:
        source_norms = [_qnorm(t) for t in cited_texts]
        if all(any(_quote_in(q, sn) for sn in source_norms) for q in quotes):
            return "QUOTE_SUPPORTED"
        return "QUOTE_UNSUPPORTED"

    if claim_nums:
        return "NUMBER_UNSUPPORTED" if missing else "NUMBER_SUPPORTED"

    return "UNVERIFIABLE"


def decompose_claims_cited(text: str) -> list:
    """Like decompose_claims, but returns (claim_sentence, [citation_ids]) pairs so the
    strict backend can match each claim against only the sources it actually cites."""
    out = []
    for s in decompose_claims(text):
        cites = []
        for m in _CITE_RE.finditer(s):
            cites.extend(x.strip() for x in m.group(1).split(","))
        out.append((s, cites))
    return out


def _strict_sources(retrieval_context) -> dict:
    """Normalize retrieval_context into {source_id: text}. Dicts keep their ids; lists
    get positional ids S1..Sn (matching the common [S1] citation convention); a bare
    string becomes a single source S1."""
    if isinstance(retrieval_context, dict):
        return {str(k): str(v) for k, v in retrieval_context.items() if v and str(v).strip()}
    if isinstance(retrieval_context, (list, tuple)):
        return {"S%d" % (i + 1): str(c) for i, c in enumerate(retrieval_context)
                if c and str(c).strip()}
    text = str(retrieval_context or "")
    return {"S1": text} if text.strip() else {}


def _strict_faithfulness(actual_output: str, retrieval_context,
                         threshold: float) -> FaithfulnessResult:
    """The "strict" backend behind faithfulness(..., backend="strict").

    Scoring: score = supported / decided, where decided excludes UNVERIFIABLE
    abstentions (grounded=None in per_claim). CONTRADICTED / *_UNSUPPORTED /
    UNCITED / UNRESOLVED_CITATION all count as decided-and-ungrounded. A digest
    whose claims ALL abstain scores 0.0 and does not pass — abstention is honest,
    not free credit. If the digest carries no citation markers at all, per-claim
    boundaries degrade gracefully to "every claim cites every source"."""
    sources = _strict_sources(retrieval_context)
    claims = decompose_claims_cited(actual_output)

    if not sources:
        return FaithfulnessResult(0.0, 0, len(claims), False, True, threshold, "strict",
                                  [{"claim": c, "grounded": False, "status": "UNCITED",
                                    "citations": cites} for c, cites in claims])
    if not claims:
        return FaithfulnessResult(0.0, 0, 0, False, False, threshold, "strict", [])

    has_markers = any(cites for _, cites in claims)
    all_ids = list(sources.keys())
    per_claim, supported, decided = [], 0, 0
    for claim, cites in claims:
        effective = cites if has_markers else all_ids
        status = strict_status(claim, effective, sources)
        if status in _ABSTAIN_STATUSES:
            per_claim.append({"claim": claim, "grounded": None, "status": status,
                              "citations": cites})
            continue
        ok = status in _SUPPORTED_STATUSES
        decided += 1
        supported += int(ok)
        per_claim.append({"claim": claim, "grounded": ok, "status": status,
                          "citations": cites})

    score = supported / decided if decided else 0.0
    passed = decided > 0 and score >= threshold
    return FaithfulnessResult(score, supported, decided, passed, False, threshold,
                              "strict", per_claim)
