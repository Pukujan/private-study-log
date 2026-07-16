"""GAP-0022 foundation: the subprocess-isolated task+checker format, and the control-based
validation that admits a task without trusting its author."""

from __future__ import annotations

from cortex_core.bakeoff_tasks import author_hash, build_checker, validate_task

_ADD_TASK = {
    "id": "t_add",
    "title": "add two ints",
    "prompt": "Define add(a, b) that returns a + b.",
    "entry": "add",
    "cases": [{"args": [2, 3], "expected": 5}, {"args": [-1, 1], "expected": 0}],
    "hidden": [{"args": [100, 200], "expected": 300}],
    "reference": "def add(a, b):\n    return a + b\n",
    "wrong": ["def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a * b\n"],
}


def test_checker_passes_correct_and_fails_wrong():
    check = build_checker(_ADD_TASK)
    assert check("def add(a, b):\n    return a + b\n")[0] is True
    assert check("def add(a, b):\n    return a - b\n")[0] is False


def test_checker_fails_noncompiling_and_missing_entry():
    check = build_checker(_ADD_TASK)
    assert check("def add(a, b) return a+b")[0] is False       # syntax error
    assert check("def other(a, b):\n    return a + b\n")[0] is False  # no add()


def test_checker_survives_infinite_loop_via_timeout():
    check = build_checker(_ADD_TASK, timeout=2.0)
    passed, detail = check("def add(a, b):\n    while True:\n        pass\n")
    assert passed is False and detail == "timeout"


def test_validate_task_admits_good_controls():
    ok, detail = validate_task(_ADD_TASK)
    assert ok is True, detail


def test_validate_task_rejects_a_too_weak_checker():
    # A task whose only case is add(0,0)=0 -- a subtracting 'wrong' solution also passes it,
    # so the control catches that the checker is too weak and REJECTS the task.
    weak = dict(_ADD_TASK, cases=[{"args": [0, 0], "expected": 0}], hidden=[])
    ok, detail = validate_task(weak)
    assert ok is False and "unexpectedly passes" in detail


def test_author_hash_is_stable_and_blind():
    h = author_hash("glm-5.2")
    assert h == author_hash("glm-5.2") and h != author_hash("gpt-5.5x")
    assert "glm" not in h  # doesn't leak the identity
