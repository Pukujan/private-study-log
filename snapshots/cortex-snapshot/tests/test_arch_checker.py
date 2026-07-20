"""Frozen tests for the Stage-2E objective architecture checker."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_architecture.arch_checker import (  # noqa: E402
    build_graph, check_layering, find_cycles, check_api_compat, check_project)
from evals.objective_architecture.fixtures import FIXTURES  # noqa: E402


def test_layering_violation_detected():
    g = build_graph({"util": "import service\n", "service": "x=1\n"})
    v = check_layering(g, {"util": 0, "service": 2})
    assert any(x["type"] == "forbidden_import" for x in v)


def test_clean_layering_ok():
    g = build_graph({"core": "import util\n", "util": "x=1\n"})
    assert check_layering(g, {"util": 0, "core": 1}) == []


def test_cycle_detected():
    g = build_graph({"a": "import b\n", "b": "import c\n", "c": "import a\n"})
    assert find_cycles(g)


def test_no_cycle():
    g = build_graph({"a": "import b\n", "b": "import c\n", "c": "x=1\n"})
    assert find_cycles(g) == []


def test_api_removed_is_break():
    v = check_api_compat("def f():\n ...\ndef g():\n ...\n", "def f():\n ...\n")
    assert any(x["type"] == "api_removed" for x in v)


def test_api_new_required_is_break():
    v = check_api_compat("def f(a):\n ...\n", "def f(a, b):\n ...\n")
    assert any(x["type"] == "api_new_required_param" for x in v)


def test_api_new_optional_is_compatible():
    v = check_api_compat("def f(a):\n ...\n", "def f(a, b=1):\n ...\n")
    assert v == []


def test_all_fixtures_match_expected():
    for fx in FIXTURES:
        assert check_project(fx).verdict == fx["expected"], fx["id"]
