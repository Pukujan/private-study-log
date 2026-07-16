"""Versioned prompts for the deep-research LLM stages (framing + summarization).

The PROMPT-VERSIONING axis for deep research, the analogue of the judge-rubric
variants in calibration/. v1 is byte-identical to the original inline prompts (so
existing behavior/tests are unchanged); v2 is tuned to what the `deep_research.v1`
rubric rewards — claim-citation FAITHFULNESS (don't generalize past the cited chunk),
explicit UNANSWERED honesty (never fabricate a missing answer), and single-source
flagging (don't assert a 1-source claim as settled).

Eval loop: run deep research with each prompt version, then score the resulting
report against `calibration/rubrics/deep_research.v1.yaml` + its golden exemplars
with a calibrated judge. (Live A/B needs a research run — an ANTHROPIC_API_KEY for
the Haiku stages, or repointing these stages at the OpenAI-compatible judge
endpoints in .env. Documented in the calibration report.)
"""

from __future__ import annotations

from typing import Any

FRAME_VERSIONS = ("v1", "v2")
SUMMARIZE_VERSIONS = ("v1", "v2")


def frame_prompt(question: str, version: str = "v1") -> str:
    if version == "v2":
        return f"""You are a research strategist. Break this research question into 3-5 \
sub-questions that together fully address it.

Question: {question}

Each sub-question MUST be:
- specific and independently answerable from evidence (not vague, not a restatement),
- non-overlapping with the others,
- phrased so its answer can be cited to a source.

Return ONLY a JSON array of strings, each a real sub-question about the question above.
Example (for a DIFFERENT question, "How does caching improve web performance?") — imitate
the STYLE, do not copy these words:
["What caching strategies do modern web stacks use?", "How much latency does caching remove in measured benchmarks?", "What are the invalidation and staleness risks of caching?"]
Do not echo the example. Do not include any other text."""
    # v1 — original, unchanged
    return f"""You are a research strategist. Break down this research question into 3-5 specific sub-questions that, when answered together, fully address the main question.

Question: {question}

Return ONLY a JSON array of strings, each a real sub-question about the question above.
Example (for a DIFFERENT question, "How does caching improve web performance?") — imitate
the STYLE, do not copy these words:
["What caching strategies do modern web stacks use?", "How much latency does caching remove in measured benchmarks?", "What are the invalidation and staleness risks of caching?"]

Do not echo the example. Do not include any other text."""


def summarize_prompt(evidence_str: str, check: dict[str, Any], version: str = "v1") -> str:
    if version == "v2":
        return f"""You are a research synthesizer. Write a findings section that synthesizes ONLY the evidence below.

Rules (a claim you cannot cite is not allowed):
1. Structure by sub-question (### headings).
2. Cite every claim in backticks: `path/to/doc.md` (chunk N). A claim with no citation must be deleted.
3. FAITHFULNESS: state only what the cited chunk actually says. Do not generalize beyond it or combine chunks into a claim none of them makes.
4. HONESTY: if a sub-question has no evidence, write exactly "UNANSWERED" — never fabricate or guess an answer.
5. SINGLE-SOURCE: if a claim rests on only one source, append "(single source)"; do not present it as settled.
6. Return ONLY the markdown findings section (no frontmatter, title, or metadata).

Evidence:
{evidence_str}

Coverage: {check['answered']}/{check['total_sub_questions']} sub-questions answered
Corroboration: {len(check['corroborated'])}/{check['total_sub_questions']} have >=2 sources

Write the findings now:"""
    # v1 — original, unchanged
    return f"""You are a research synthesizer. Write a findings section that synthesizes the evidence below.

Rules:
1. Structure by sub-question (use ### headings).
2. For each claim, cite the source in backticks: `path/to/doc.md` (chunk N).
3. Be concise, direct, fact-based. No speculation beyond what the evidence supports.
4. If a sub-question has no evidence, write "No supporting evidence found."
5. Return ONLY the markdown findings section (no frontmatter, no title, no metadata).

Evidence:
{evidence_str}

Coverage: {check['answered']}/{check['total_sub_questions']} sub-questions answered
Corroboration: {len(check['corroborated'])}/{check['total_sub_questions']} have >=2 sources

Write the findings now:"""
