"""Blinded multi-vendor task authoring for the real v4 bake-off (GAP-CORTEX-0022).

Each candidate model authors self-contained coding tasks (task + visible/hidden cases +
reference + wrong controls); we validate each against its own controls (`validate_task` --
admit only if reference passes and wrongs fail) and store admitted tasks under a BLIND author
hash. Author identity is deliberately multi-vendor and non-Anthropic (qwen/mimo/glm/deepseek/
gpt) so the task set doesn't carry a single lineage's bias -- the anti-circularity core.

The completer is injectable (`author_batch(complete, ...)`) so the pipeline is unit-testable
without real model calls; `tier_author(tier, ...)` is the real one.
"""

from __future__ import annotations

from typing import Any, Callable

from .bakeoff_tasks import author_hash, validate_task
from .llm_parse import extract_json_object

AUTHOR_PROMPT = (
    "You are authoring ONE self-contained Python coding task for a benchmark. A weak model will "
    "later try to solve it from your prompt alone. Output ONLY a JSON object, no prose:\n"
    "{\n"
    '  "title": "short title",\n'
    '  "prompt": "Define <name>(...) that ... -- FULL spec: signature, behavior, and every edge '
    'case a solver needs. Self-contained; NOT about any specific codebase.",\n'
    '  "entry": "<function name>",\n'
    '  "cases":  [{"args": [...], "expected": ...}, ...],   // 3-5 VISIBLE test cases\n'
    '  "hidden": [{"args": [...], "expected": ...}, ...],   // 2-3 HIDDEN edge cases\n'
    '  "reference": "def <name>(...):\\n    <a CORRECT solution>",\n'
    '  "wrong": ["def <name>(...):\\n    <a SUBTLY WRONG solution>", "<another wrong>"]\n'
    "}\n"
    "Rules: pure Python, stdlib only, no file/network/OS. args and expected must be JSON values. "
    "The reference MUST pass all cases; each wrong MUST fail at least one. Make it moderately "
    "tricky (an edge case a weak model would miss), not trivial. Output ONLY the JSON object."
)


def author_one(complete: Callable[[str], str], author_id: str, *,
               seq: int = 0, timeout: float = 6.0) -> tuple[dict[str, Any] | None, str]:
    """Author one task via `complete(prompt) -> text`. Returns (task, "admitted") or
    (None, reason). Admitted tasks carry a blind `author_hash` and an id, and drop the author's
    controls from the subject-facing copy is left to the runner (controls stay for scoring)."""
    task = extract_json_object(complete(AUTHOR_PROMPT) or "")
    if not isinstance(task, dict):
        return None, "no JSON task object"
    ok, detail = validate_task(task, timeout=timeout)
    if not ok:
        return None, detail
    h = author_hash(author_id)
    task = {**task, "id": f"{h}_{seq}", "author_hash": h}
    return task, "admitted"


def author_batch(complete: Callable[[str], str], author_id: str, n: int, *,
                 timeout: float = 6.0) -> tuple[list[dict[str, Any]], list[str]]:
    """Author up to `n` tasks; returns (admitted_tasks, rejection_reasons). Attempts 2*n times
    (authoring is lossy -- weak models write broken checkers) but stops at n admits."""
    admitted: list[dict[str, Any]] = []
    rejects: list[str] = []
    for _seq in range(2 * n):
        if len(admitted) >= n:
            break
        task, why = author_one(complete, author_id, seq=len(admitted), timeout=timeout)
        if task is not None:
            admitted.append(task)
        else:
            rejects.append(why)
    return admitted, rejects


def tier_author(tier: str, n: int, *, max_tokens: int = 2000, timeout: float = 6.0):
    """Real authoring by a judge-tier model. Returns (admitted, rejects)."""
    from .research import _llm_complete
    return author_batch(lambda p: _llm_complete(p, tier, max_tokens) or "", tier, n, timeout=timeout)
