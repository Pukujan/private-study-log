"""Vendor-agnostic output extraction / normalization (Eval Flywheel P1).

The panel's load-bearing fix: a deterministic checker over un-normalized output measures
FORMAT COMPLIANCE (how Claude-shaped the text is), not capability — a non-Claude model that
wraps code in prose or emits CoT fails at parsing before the checker runs. So EVERY model
output passes through here first, projected onto a `contract`, and the checker only ever sees
the normalized projection.

Contracts:
  raw         — pass the output through unchanged (no normalization; audit/debug lane).
  code_only   — extract the fenced code block, drop surrounding prose.
  json_only   — extract the first balanced JSON object.
  tool_calls  — extract the first balanced JSON array (list of tool calls).
  answer_only — strip fences/CoT, keep the prose answer.

`filler_stripped` records whether normalization actually changed the text, so a caller can
distinguish "clean output" from "we had to dig it out" — and P4 can separate a genuine
`parse_failure` (nothing extractable) from a semantic abstain. Nothing here is ever silently
rewritten under the `raw` contract.
"""
from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from typing import Any

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
CONTRACTS = ("raw", "code_only", "json_only", "answer_only", "tool_calls")


@dataclass
class NormalizedOutput:
    raw: str
    contract: str
    code: str | None = None
    json: Any = None
    answer: str | None = None
    tool_calls: list | None = None
    filler_stripped: bool = False


def has_code_fence(raw: str) -> bool:
    """True if the output contains a ```...``` fenced block — used to separate a genuine
    parse_failure (model ignored the code contract) from a wrong-but-formatted answer."""
    return _FENCE_RE.search(raw) is not None


def _strip_think(text: str) -> str:
    """Drop <think>...</think> reasoning blocks (reasoning models leak them)."""
    return _THINK_RE.sub("", text)


def _first_fence(text: str) -> str | None:
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else None


def _first_balanced(text: str, opener: str, closer: str) -> Any:
    """Return the first balanced {..}/[..] JSON value in `text`, else None. Tries a
    fenced block first, then the raw text."""
    for candidate in ([_first_fence(text)] if _first_fence(text) else []) + [text]:
        start = candidate.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(candidate)):
            if candidate[i] == opener:
                depth += 1
            elif candidate[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return _json.loads(candidate[start : i + 1])
                    except ValueError:
                        break
    return None


def normalize_output(raw: str, contract: str = "raw") -> NormalizedOutput:
    """Project a model's raw output onto `contract` before any checker sees it."""
    if contract not in CONTRACTS:
        raise ValueError(f"unknown contract {contract!r}; known: {list(CONTRACTS)}")
    out = NormalizedOutput(raw=raw, contract=contract)

    if contract == "raw":
        out.code = raw  # everything preserved, nothing normalized
        out.filler_stripped = False
        return out

    text = _strip_think(raw)

    if contract == "code_only":
        fenced = _first_fence(text)
        out.code = fenced if fenced is not None else text.strip()
        out.filler_stripped = (out.code or "").strip() != raw.strip()
    elif contract == "json_only":
        out.json = _first_balanced(text, "{", "}")
        out.filler_stripped = True
    elif contract == "tool_calls":
        out.tool_calls = _first_balanced(text, "[", "]")
        out.filler_stripped = True
    elif contract == "answer_only":
        ans = _FENCE_RE.sub("", text).strip()
        out.answer = ans
        out.filler_stripped = ans != raw.strip()

    return out
