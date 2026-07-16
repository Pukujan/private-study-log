"""Robust extraction of JSON out of an LLM response — resilient to REASONING models.

Frontier models (Claude/GPT) emit clean JSON; reasoning models (qwen35b/yolo-qwen, GLM,
DeepSeek, ...) wrap their answer in chain-of-thought, ``<think>...</think>`` tags, and/or
markdown fences. A naive ``json.loads`` or a greedy ``{.*}`` silently drops their answer:
a judge verdict collapses to UNVERIFIABLE, research framing collapses to one sub-question.
That is a silent, cross-cutting failure that only shows up when you test with a non-frontier
model — so it lives in ONE place, used everywhere a model's JSON is read.

Key move for objects: scan for *balanced* ``{...}`` blocks and prefer the LAST parseable one
— a reasoning model puts its final answer after the reasoning, so "last object wins."
"""

from __future__ import annotations

import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip(text: str | None) -> str:
    return _THINK.sub("", text or "").strip()


def extract_json_object(text: str | None) -> dict | None:
    """The last parseable JSON object in the text, robust to reasoning wrappers. None if none."""
    t = _strip(text)
    try:
        v = json.loads(t)
        if isinstance(v, dict):
            return v
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if fence:
        try:
            v = json.loads(fence.group(1))
            if isinstance(v, dict):
                return v
        except (json.JSONDecodeError, ValueError):
            pass
    # Balanced-brace scan; keep the LAST object that parses (the answer after reasoning).
    found: dict | None = None
    for start in (i for i, c in enumerate(t) if c == "{"):
        depth = 0
        for end in range(start, len(t)):
            c = t[end]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(t[start:end + 1])
                        if isinstance(v, dict):
                            found = v
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
    return found


def extract_tool_call(text: str | None, legal_tools: list[str] | None = None) -> dict:
    """Extract a tool-call object ``{"tool": ..., "payload": ...}`` from a model reply.

    Distinct from ``extract_json_object`` (which keeps the LAST object): a reasoning model
    nests ``{"tool": "x", "payload": {...}}``, and "last object wins" grabs the INNER payload,
    dropping the tool. This scans only TOP-LEVEL balanced objects and prefers the one whose
    ``tool`` is in ``legal_tools`` (else any object carrying a ``tool`` key). Returns ``{}`` if
    none is found. Validated overnight against qwen-4b / qwen35b / mimo -- the fix that took
    qwen35b from 2 dropped tool-calls per run to 0.
    """
    t = _strip(text)
    legal = set(legal_tools or [])
    try:
        whole = json.loads(t)
        if isinstance(whole, dict) and "tool" in whole and (not legal or whole.get("tool") in legal):
            return whole
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    candidates: list[dict] = []
    depth, start = 0, None
    for i, c in enumerate(t):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    o = json.loads(t[start:i + 1])
                    if isinstance(o, dict) and "tool" in o:
                        candidates.append(o)
                except (json.JSONDecodeError, ValueError):
                    pass
                start = None
    if legal:
        for o in candidates:
            if o.get("tool") in legal:
                return o
    return candidates[0] if candidates else {}


def extract_json_list(text: str | None) -> list | None:
    """The first parseable JSON array of the text, robust to reasoning wrappers. None if none."""
    t = _strip(text)
    try:
        v = json.loads(t)
        if isinstance(v, list):
            return v
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", t, re.DOTALL)
    if fence:
        try:
            v = json.loads(fence.group(1))
            if isinstance(v, list):
                return v
        except (json.JSONDecodeError, ValueError):
            pass
    bare = re.search(r"\[\s*(?:\"(?:[^\"\\]|\\.)*\"\s*,?\s*)+\]", t, re.DOTALL)
    if bare:
        try:
            v = json.loads(bare.group(0))
            if isinstance(v, list):
                return v
        except (json.JSONDecodeError, ValueError):
            pass
    return None
