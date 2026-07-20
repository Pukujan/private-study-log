"""Bake-off coding tasks: the deterministic, subprocess-isolated task+checker format that the
blinded multi-vendor authoring produces and the engine gate consumes (GAP-CORTEX-0022).

A task is data:
    {"id", "author_hash", "title",
     "prompt":  full spec shown to the SUBJECT (signature + behavior + edge cases),
     "entry":   the function name the subject must define,
     "cases":   [{"args": [...], "expected": ...}, ...]  (the visible checker),
     "hidden":  [...]  same shape, NEVER shown to the subject (anti-teach-to-the-test),
     "reference": a solution that MUST pass all cases (author-provided control),
     "wrong":   [solutions that MUST fail] (author-provided controls)}

`build_checker(task)` returns a `check(patch) -> (passed, detail)` that runs the submitted code
in a SUBPROCESS with a timeout (so a model's infinite loop or bad code can't hang/harm the
runner) and compares outputs to the cases. Fail-closed. `validate_task` admits a task only if
its reference passes and every wrong solution fails -- so a broken checker or a mislabeled task
is rejected without trusting the author (the anti-circularity control from v4).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Callable

_RUNNER = """
import json, sys
_cases = json.loads({cases!r})
_fn = globals().get({entry!r})
if not callable(_fn):
    print("FAIL:no-entry"); sys.exit(0)
for _c in _cases:
    try:
        _got = _fn(*_c["args"])
    except Exception as _e:
        print("FAIL:raised:" + repr(_e)); sys.exit(0)
    if _got != _c["expected"]:
        print("FAIL:got " + repr(_got) + " want " + repr(_c["expected"])); sys.exit(0)
print("PASS")
"""


def _run(patch: str, entry: str, cases: list[dict], timeout: float) -> tuple[bool, str]:
    harness = f"{patch}\n\n" + _RUNNER.format(cases=json.dumps(cases), entry=entry)
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(harness)
        r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        return (out.endswith("PASS") if "PASS" in out else False,
                out.splitlines()[-1] if out else (r.stderr or "no output")[:200])
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"runner error: {exc!r}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def build_checker(task: dict[str, Any], *, include_hidden: bool = True,
                  timeout: float = 5.0) -> Callable[[str], "tuple[bool, str]"]:
    """A `check(patch) -> (passed, detail)` for this task. `include_hidden=True` runs the
    hidden holdout too (server-side verdict); the subject-facing gate would use the visible
    cases only."""
    cases = list(task.get("cases", []))
    if include_hidden:
        cases = cases + list(task.get("hidden", []))
    entry = task["entry"]

    def check(patch: str) -> "tuple[bool, str]":
        return _run(patch or "", entry, cases, timeout)

    return check


def validate_task(task: dict[str, Any], *, timeout: float = 5.0) -> tuple[bool, str]:
    """Admit a task only if its controls behave: reference passes ALL cases (visible+hidden),
    and every declared wrong solution FAILS at least one. Rejects broken checkers / mislabeled
    tasks without trusting the author."""
    for key in ("prompt", "entry", "cases", "reference"):
        if not task.get(key):
            return False, f"missing '{key}'"
    check = build_checker(task, include_hidden=True, timeout=timeout)
    ok, detail = check(task["reference"])
    if not ok:
        return False, f"reference solution fails its own checker: {detail}"
    for i, wrong in enumerate(task.get("wrong", [])):
        wok, _ = check(wrong)
        if wok:
            return False, f"wrong solution #{i} unexpectedly passes (checker too weak)"
    return True, "controls behave (reference passes, wrongs fail)"


def author_hash(author_id: str, salt: str = "bakeoff-v4") -> str:
    """Stable short hash of an author identity -- author-blind storage (revealed only after
    metrics lock)."""
    return hashlib.sha256(f"{salt}:{author_id}".encode()).hexdigest()[:12]
