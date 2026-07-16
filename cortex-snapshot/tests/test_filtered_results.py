"""BUILD-03: the load-bearing integrity proof for the `filtered_results` gate check.

The whole point of a search/filter is that a BROKEN search MUST fail the gate. We drive the REAL
gate (`cortex_core.app_gates.run_done_checks`) over:
  - the GOOD search app (filters `name` by the query term) -> PASS;
  - three mutants that each break the search in one way -> FAIL with FILTER_FAIL:
      search_noop            -> ignores q, returns ALL rows (a filter that doesn't filter);
      search_wrong_field     -> filters the wrong column (matching rows never returned);
      search_returns_nothing -> never matches (matching rows never returned).
If any mutant PASSES, the check is too weak — fix the check, not the test.

Also: `validate_check_spec` accepts a well-formed filtered_results spec and rejects malformed
ones, and the stdlib-only no-LLM import firewall over app_gates still holds after the addition.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cortex_core import app_gates  # noqa: E402
from cortex_core.app_gates import GateContext, run_done_checks  # noqa: E402
from cortex_core.app_contract import validate_check_spec  # noqa: E402
from evals.app_gate_fixtures import fixtures as fx  # noqa: E402


def _fast_ctx(seed: int = 4242) -> GateContext:
    return GateContext(seed=seed, start_timeout_s=8.0)


# --------------------------------------------------------------------------- #
# The mutant-integrity sweep (run the REAL gate)                              #
# --------------------------------------------------------------------------- #
def test_good_search_app_passes_filtered_results(tmp_path):
    app_dir = tmp_path / "good"
    fx.write_search_app("good", app_dir)
    v = run_done_checks(app_dir, [fx.filtered_results_check()], ctx=_fast_ctx())
    assert v.passed, [r.detail for r in v.results]
    assert v.failure_class is None
    fr = [r for r in v.results if r.kind == "filtered_results"]
    assert fr and fr[0].passed


@pytest.mark.parametrize("mutant", list(fx.SEARCH_MUTANT_KINDS))
def test_search_mutant_fails_filtered_results(mutant, tmp_path):
    app_dir = tmp_path / mutant
    fx.write_search_app(mutant, app_dir)
    v = run_done_checks(app_dir, [fx.filtered_results_check()], ctx=_fast_ctx())
    assert not v.passed, f"{mutant} slipped through filtered_results (check too weak)"
    assert v.failure_class == "FILTER_FAIL", (mutant, v.failure_class,
                                              [r.detail for r in v.results])


def test_all_three_designed_search_mutants_present():
    assert fx.SEARCH_MUTANT_KINDS == (
        "search_noop", "search_wrong_field", "search_returns_nothing",
    )
    # each derives from the good app in exactly one place (single-substring surgery)
    good = fx.GOOD_APP_SOURCE
    for anchor in (fx.ANCHOR_SEARCH_GUARD, fx.ANCHOR_SEARCH_WHERE, fx.ANCHOR_SEARCH_PARAM):
        assert good.count(anchor) == 1, f"anchor {anchor!r} not unique in GOOD_APP_SOURCE"
    for mutant in fx.SEARCH_MUTANT_KINDS:
        assert fx.search_app_source(mutant) != good


def test_good_app_in_standard_checks_satisfies_filtered_results(tmp_path):
    # The full standard suite (end-to-end integrity) must include filtered_results AND the good
    # app must actually satisfy it -- otherwise --integrity is theater.
    kinds = [c["kind"] for c in fx.standard_checks()]
    assert "filtered_results" in kinds
    app_dir = tmp_path / "good_full"
    fx.write_good_app(app_dir)
    only_fr = [c for c in fx.standard_checks() if c["kind"] == "filtered_results"]
    v = run_done_checks(app_dir, only_fr, ctx=_fast_ctx())
    assert v.passed, [r.detail for r in v.results]


# --------------------------------------------------------------------------- #
# Static spec lint                                                            #
# --------------------------------------------------------------------------- #
def test_validate_check_spec_accepts_wellformed_filtered_results():
    assert validate_check_spec(fx.filtered_results_check()) == []


def test_validate_check_spec_rejects_malformed_filtered_results():
    base = fx.filtered_results_check()

    no_create = {k: v for k, v in base.items() if k != "create"}
    assert any("create" in e for e in validate_check_spec(no_create))

    bad_create = {**base, "create": {"method": "POST"}}  # missing path
    assert any("create" in e for e in validate_check_spec(bad_create))

    no_search = {k: v for k, v in base.items() if k != "search"}
    assert any("search" in e for e in validate_check_spec(no_search))

    bad_search = {**base, "search": {"get_path": "/"}}  # missing query_param
    assert any("search" in e for e in validate_check_spec(bad_search))

    no_match = {k: v for k, v in base.items() if k != "match_form"}
    assert any("match_form" in e for e in validate_check_spec(no_match))

    no_nomatch = {k: v for k, v in base.items() if k != "nomatch_form"}
    assert any("nomatch_form" in e for e in validate_check_spec(no_nomatch))


# --------------------------------------------------------------------------- #
# The no-LLM import firewall still holds                                      #
# --------------------------------------------------------------------------- #
def test_gate_module_imports_are_stdlib_only_after_filtered_results():
    tree = ast.parse(Path(app_gates.__file__).read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in stdlib:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                if node.module != "app_contract":
                    offenders.append(f".{node.module}")
            elif node.module not in {"app_contract", "cortex_core.app_contract"}:
                if (node.module or "").split(".")[0] not in stdlib:
                    offenders.append(node.module)
    assert offenders == [], f"non-stdlib / non-app_contract imports: {offenders}"
