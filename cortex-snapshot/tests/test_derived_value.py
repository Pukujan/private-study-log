"""BUILD-02: the load-bearing integrity proof for the `derived_value` gate check.

The whole point of a metric card is that a FAKED or MISCOUNTED metric MUST fail the gate. We drive
the REAL gate (`cortex_core.app_gates.run_done_checks`) over:
  - the GOOD metric app (correct filtered COUNT) -> PASS;
  - three mutants that each break the metric in one way -> FAIL with DERIVED_FAIL.
If any mutant PASSES, the check is too weak — fix the check, not the test.

Also: `validate_check_spec` accepts a well-formed derived_value spec and rejects malformed ones,
and the stdlib-only no-LLM import firewall over app_gates still holds after the addition.
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
def test_good_metric_app_passes_derived_value(tmp_path):
    app_dir = tmp_path / "good"
    fx.write_metric_app("good", app_dir)
    v = run_done_checks(app_dir, [fx.derived_value_check()], ctx=_fast_ctx())
    assert v.passed, [r.detail for r in v.results]
    assert v.failure_class is None
    dv = [r for r in v.results if r.kind == "derived_value"]
    assert dv and dv[0].passed


@pytest.mark.parametrize("mutant", list(fx.METRIC_MUTANT_KINDS))
def test_metric_mutant_fails_derived_value(mutant, tmp_path):
    app_dir = tmp_path / mutant
    fx.write_metric_app(mutant, app_dir)
    v = run_done_checks(app_dir, [fx.derived_value_check()], ctx=_fast_ctx())
    assert not v.passed, f"{mutant} slipped through derived_value (check too weak)"
    assert v.failure_class == "DERIVED_FAIL", (mutant, v.failure_class,
                                               [r.detail for r in v.results])


def test_all_three_designed_mutants_present():
    assert fx.METRIC_MUTANT_KINDS == (
        "metric_hardcoded", "metric_total_not_filtered", "metric_missing_attr",
    )
    # each derives from the good app in exactly one place (single-substring surgery)
    good = fx.GOOD_APP_SOURCE
    for anchor in (fx.ANCHOR_METRIC_QUERY, fx.ANCHOR_METRIC_WHERE, fx.ANCHOR_METRIC_ATTR_ASSIGN):
        assert good.count(anchor) == 1, f"anchor {anchor!r} not unique in GOOD_APP_SOURCE"
    for mutant in fx.METRIC_MUTANT_KINDS:
        assert fx.metric_app_source(mutant) != good


# --------------------------------------------------------------------------- #
# Static spec lint                                                            #
# --------------------------------------------------------------------------- #
def test_validate_check_spec_accepts_wellformed_derived_value():
    assert validate_check_spec(fx.derived_value_check()) == []


def test_validate_check_spec_rejects_malformed_derived_value():
    base = fx.derived_value_check()

    no_create = {k: v for k, v in base.items() if k != "create"}
    assert any("create" in e for e in validate_check_spec(no_create))

    bad_create = {**base, "create": {"method": "POST"}}  # missing path
    assert any("create" in e for e in validate_check_spec(bad_create))

    no_match = {k: v for k, v in base.items() if k != "match_form"}
    assert any("match_form" in e for e in validate_check_spec(no_match))

    no_nomatch = {k: v for k, v in base.items() if k != "nomatch_form"}
    assert any("nomatch_form" in e for e in validate_check_spec(no_nomatch))

    no_marker = {k: v for k, v in base.items() if k != "marker_attr"}
    assert any("marker_attr" in e for e in validate_check_spec(no_marker))

    empty_marker = {**base, "marker_attr": ""}
    assert any("marker_attr" in e for e in validate_check_spec(empty_marker))


# --------------------------------------------------------------------------- #
# The no-LLM import firewall still holds                                      #
# --------------------------------------------------------------------------- #
def test_gate_module_imports_are_stdlib_only_after_derived_value():
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
