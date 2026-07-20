"""Benchmark/measured-backed model dispatch-tier classifier.

This is the canonical replacement for the keyword-substring `classify()` that
lived in the wrapper's `.cortex/scripts/ninerouter_tiers.py` (`_RULES`), which
assigned a work tier by substring-matching the *model name* and silently
defaulted unrecognized models to ``medium`` -- i.e. a guess. Two concrete bugs
that produced:

  * every model whose id contained ``"flash"`` was routed to ``weak`` -- but our
    own BFCL tool-calling probe measured **gemini-3.5-flash = 1.000** and
    **gemini-3-flash = 1.000** (a strong/medium agentic model shoved into the
    fan-out lane); and
  * an un-probed model defaulted to ``medium`` (a name-guess) instead of
    ``UNKNOWN`` (probe-first), violating the research-first anti-guessing rule.

Per the flywheel principle "the driver imports cortex_core, never copies"
(docs/EVAL-FLYWHEEL-PLAN.md), the wrapper's ``classify()`` delegates here rather
than carrying its own copy. This module stays zero-dependency (stdlib only) so
the wrapper's zero-dep contract is preserved.

TIER SEMANTICS (the wrapper's own, unchanged):
    strong     driver / reason / spec / verify
    upper-mid  strong-adjacent executor (measured >=0.96 but a single lane, or
               owner/vendor-assessed pending a broader probe)
    medium     normal sub-tasks / second opinions
    weak       cheap/fast wide parallel fan-out
    utility    non-chat helpers (embeddings / rerank / vision / TTS / eval-only)
    UNKNOWN    un-probed -> probe-first, never auto-routed (the default)

DISPATCH tier (capability, what THIS module returns) is a DIFFERENT axis from
JUDGE tier (Cohen's kappa agreement, in judge.py JUDGE_LADDER). A model can be a
strong executor and a weak judge -- e.g. qwen35b measures 0.982 on the
tool-calling lane (fine executor) yet kappa 0.227 as a judge (worst measured).
Do not conflate them. This module is executor/dispatch only.

Provenance codes (kept per-row so a tier is never mistaken for a benchmark):
    pb    publicly-benchmarked (third-party or vendor number on a named public
          benchmark, cited in the tier-list doc)
    vr    vendor-reported (vendor's own table only)
    oa    owner-usage-assessed (repo owner's hands-on judgment)
    inf   inferred from a sibling model
    int   internally measured only (our own kappa/objective eval)
    measured   measured on OUR objective tool-calling lane (BFCL-style checker,
          zero judges) -- the strongest local signal for the dispatch axis

SOURCES (every number below is cited in these two in-repo docs):
    docs/research/model-tier-list-benchmarked-2026-07-14.md   (public + kappa)
    evals/reports/tier-probe-objective-lanes-2026-07-14.md    (MEASURED pass-rate)
"""

from __future__ import annotations

import re

# Tiers the wrapper's models.tiers.md renderer already understands, best-first.
# UNKNOWN is intentionally NOT in this order list -- it sorts last via the
# renderer's `index if in order else 9` fallback, exactly where a probe-first
# model belongs.
TIER_ORDER = ["strong", "upper-mid", "medium", "weak", "utility"]

# An un-probed model is UNBENCHMARKED, not medium. Flipping this default from
# "medium" -> "UNKNOWN" is the single most important correctness change here.
DEFAULT_TIER = "UNKNOWN"

# model-id fragment -> (tier, provenance). Matched by SEPARATOR-INSENSITIVE
# substring (see classify): "claude-sonnet-4-6" (served) matches "claude-sonnet-4.6"
# (table). The LONGEST matching key wins, so specific ids (…-preview) beat their
# own prefixes (…-flash). Keys are the stable stem of the served model id.
MODEL_TIER_TABLE: dict[str, tuple[str, str]] = {
    # ---- strong: driver / reason / spec / verify ----
    "claude-fable-5":            ("strong", "pb"),        # SWE-V 95.0 (vals.ai)
    "claude-opus-4.8":           ("strong", "pb"),        # SWE-V 88.6; Arena ~1510
    "claude-opus-4.6":           ("strong", "pb"),        # Arena ~1500-class (gateway)
    "claude-sonnet-4.6":         ("strong", "pb"),        # SWE-V 79.6; GPQA 89.9; kappa 0.924
    "gpt-5.5":                   ("strong", "pb"),        # SWE-V 82.6; GPQA 93.6
    "gemini-3.1-pro":            ("strong", "pb"),        # GPQA 94.3; SWE-V 80.6
    "gemini-pro-agent":          ("strong", "pb"),        # 9router served id for 3.1-pro
    "glm-5.2":                   ("strong", "pb"),        # AA 51.1 open leader; obj 0.928
    "glm5.2":                    ("strong", "pb"),
    "umans-glm-5.2":             ("strong", "pb"),
    "gemini-3.5-flash":          ("strong", "measured"),  # 1.000 our BFCL lane; kills flash->weak

    # ---- upper-mid: strong-adjacent executor (measured single-lane / assessed) ----
    "deepseek-v4-flash":         ("upper-mid", "measured"),  # 1.000 our lane (vr publicly); weak JUDGE (kappa 0.405)
    "big-pickle":                ("upper-mid", "measured"),  # 0.964 our lane; promoted from owner-assessed
    "aux":                       ("upper-mid", "measured"),  # resolves to big-pickle (judge.py, live 2026-07-08)
    "nemotron-3-ultra":          ("upper-mid", "pb"),        # AA 47.7 top US open-weight
    "claude-sonnet-4.5":         ("upper-mid", "pb"),        # prior-gen Sonnet

    # ---- medium: normal sub-tasks / second opinions ----
    "qwen3.6-35b-a3b":           ("medium", "measured"),  # qwen35b served id; 0.982 exec (kappa 0.227 JUDGE only)
    "qwen3.6-35b":               ("medium", "pb"),
    "qwen3.5-flash":             ("medium", "pb"),        # a "flash" that is medium, not weak
    "deepseek-3.2":              ("medium", "pb"),        # GPQA 79.9; SWE-V 67.8
    "mimo-v2.5":                 ("medium", "inf"),       # from Pro sibling (non-Pro weaker)
    "gpt-oss-120b":              ("medium", "pb"),        # AA 33.3
    "gemma-3-27b-it":            ("medium", "pb"),        # HumanEval 87.8; kappa 0.459
    "claude-haiku-4.5":          ("medium", "pb"),        # dispatch medium; JUDGE high-stakes @rubric_v2 (kappa 0.922)
    "gemini-3-flash":            ("medium", "measured"),  # 1.000 our lane; NOT weak (ceiling-effect caveat)

    # ---- weak: cheap/fast wide fan-out ----
    "qwen3:4b":                  ("weak", "measured"),    # 0.909 -- the one real downward separation
    "qwen3:4b-16k":              ("weak", "measured"),

    # ---- utility: non-chat / evaluator-only ----
    "prometheus-eval":           ("utility", "int"),      # native template only (kappa 0 off-template)
    "prometheus":                ("utility", "int"),

    # ---- explicit UNKNOWN (override a prefix match / document the abstention) ----
    "gemini-3-flash-preview":    ("UNKNOWN", "probe"),    # rate-limited 429s; unmeasured -> probe-first
    "gemini-preview":            ("UNKNOWN", "probe"),
    "north-mini-code":           ("UNKNOWN", "probe"),    # stealth; old rules GUESSED medium
}

# Non-chat helper needles -> utility (kept in the same longest-match table space
# so ordering is consistent with MODEL_TIER_TABLE).
_UTILITY_NEEDLES = ("embed", "rerank", "whisper", "-tts", "text-to-speech",
                    "moderation", "vision")

_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Lowercase and strip every non-alphanumeric char, so separator variants
    collapse: 'ag/claude-sonnet-4-6' and 'claude-sonnet-4.6' both -> 'claudesonnet46'.
    """
    return _ALNUM.sub("", s.lower())


# Precompute normalized keys once, longest-first so specific ids win.
_NORM_TABLE: list[tuple[str, str, str]] = sorted(
    ((_norm(k), tier, prov) for k, (tier, prov) in MODEL_TIER_TABLE.items()),
    key=lambda t: len(t[0]),
    reverse=True,
)
_NORM_UTILITY: list[str] = sorted((_norm(n) for n in _UTILITY_NEEDLES),
                                  key=len, reverse=True)


def tier_and_provenance(model_id: str) -> tuple[str, str]:
    """Return (tier, provenance) for a served model id.

    Benchmark/measured-backed exact-stem lookup (longest match wins), NOT a
    keyword guess. Unknown -> (DEFAULT_TIER, "none"): probe-first, never routed.
    """
    nid = _norm(model_id)
    # Longest table stem that is a substring of the normalized id wins.
    best: tuple[str, str] | None = None
    best_len = -1
    for nkey, tier, prov in _NORM_TABLE:
        if nkey and nkey in nid and len(nkey) > best_len:
            best, best_len = (tier, prov), len(nkey)
    # A utility needle only wins if it is more specific than any table stem hit.
    for nutil in _NORM_UTILITY:
        if nutil in nid and len(nutil) > best_len:
            best, best_len = ("utility", "role"), len(nutil)
    if best is not None:
        return best
    return DEFAULT_TIER, "none"


def classify(model_id: str) -> str:
    """Dispatch/executor tier for a served model id (one of TIER_ORDER or
    'UNKNOWN'). Drop-in signature-compatible with the wrapper's old classify()."""
    return tier_and_provenance(model_id)[0]
