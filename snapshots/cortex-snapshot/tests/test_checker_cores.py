"""Frozen tests for the five shared checker-design CORES (evals/checker_cores/).

Stage-2 contract, applied to library code instead of a single lane: for each core, a correct/
honest artifact must PASS, a theater/fake artifact must FAIL, and (where the core seeds errors
itself) every seeded mutant must be caught. These are the tests that make the cores load-bearing
-- see `evals/checker_cores/MAPPING.md` for which per-domain predicate each test line stands in
for, and `calibration/anchors/CALIBRATION-ANCHORS-README.md` for the design provenance.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.checker_cores.differential_execution import differential_execution  # noqa: E402
from evals.checker_cores.mutation_seeded_error import mutate_source, seeded_mutation_test  # noqa: E402
from evals.checker_cores.resolver_joins import (  # noqa: E402
    numeral_fidelity_join,
    recompute_probe,
    constant_symmetry,
)
from evals.checker_cores.schema_presence import (  # noqa: E402
    validate_schema,
    SM_SCHEMA,
    DA9_FINDING_SCHEMA,
)
from evals.checker_cores.lexicon_grammar import (  # noqa: E402
    classify_catcher,
    catcher_wiring_check,
    consensus_corroboration_check,
    classify_command,
    command_evidence_check,
    detect_empty_selection,
)


# =========================================================================== CORE 1: differential_execution

def test_diffexec_real_fix_passes():
    pre = "def f(x):\n    return x + 2\n"
    post = "def f(x):\n    return x + 1\n"
    tests = "def test_fix():\n    assert f(1) == 2\n"
    r = differential_execution(pre, post, tests, test_name="test_fix", task_type="bugfix")
    assert r.passed
    assert r.checks["fails_pre_by_assertion"] is True and r.checks["passes_post"] is True


def test_diffexec_theater_new_test_already_passed_pre_diff():
    # The "regression test" passes on unfixed code too -- verifies nothing.
    pre = "def f(x):\n    return x + 1\n"
    post = "def f(x):\n    return x + 1\n"
    tests = "def test_fix():\n    assert f(1) == 2\n"
    r = differential_execution(pre, post, tests, test_name="test_fix", task_type="bugfix")
    assert not r.passed and not r.checks["fails_pre_by_assertion"]


def test_diffexec_broken_fix_fails_post():
    pre = "def f(x):\n    return x + 2\n"
    post = "def f(x):\n    return x + 3\n"  # "fix" is still wrong
    tests = "def test_fix():\n    assert f(1) == 2\n"
    r = differential_execution(pre, post, tests, test_name="test_fix", task_type="bugfix")
    assert not r.passed and not r.checks["passes_post"]


def test_diffexec_feature_task_does_not_require_pre_failure():
    # feature already existed pre-diff under a different rendering (a comment change is enough
    # to give `differential_execution`'s changed-code-coverage gate a real line to observe).
    pre = "def f(x):\n    return x + 1  # v1\n"
    post = "def f(x):\n    return x + 1  # v2\n"
    tests = "def test_fix():\n    assert f(1) == 2\n"
    r = differential_execution(pre, post, tests, test_name="test_fix", task_type="feature")
    assert r.passed, r.diagnostics  # feature-type only requires passes_post + coverage


def test_diffexec_rejects_unrecognized_task_type():
    pre = "def f(x):\n    return x + 2\n"
    post = "def f(x):\n    return x + 1\n"
    tests = "def test_fix():\n    assert f(1) == 2\n"
    r = differential_execution(pre, post, tests, test_name="test_fix", task_type="typo")
    assert not r.passed and r.quarantine_reason == "invalid_task_type"


def test_diffexec_collection_error_pre_does_not_count_as_required_failure():
    # finding #5: a pre-diff run that fails via a SyntaxError/import error is NOT proof the
    # regression existed -- only an assertion failure counts.
    broken_pre = "def f(x)\n    return x + 2\n"
    post = "def f(x):\n    return x + 1\n"
    tests = "def test_fix():\n    assert f(1) == 2\n"
    r = differential_execution(broken_pre, post, tests, test_name="test_fix", task_type="bugfix")
    assert not r.passed
    assert r.checks["pre_outcome"] == "collection_error"
    assert not r.checks["fails_pre_by_assertion"]


def test_diffexec_requires_changed_code_coverage():
    # finding #5: a passing post-diff test that never executes the changed line proves nothing.
    pre = "def f(x):\n    return x + 2\n\ndef stable(y):\n    return y * 2\n"
    post = "def f(x):\n    return x + 1\n\ndef stable(y):\n    return y * 2\n"
    tests = "def test_stable():\n    assert stable(3) == 6\n"
    r = differential_execution(pre, post, tests, test_name="test_stable", task_type="feature")
    assert not r.passed
    assert not r.checks["covers_changed_code"]


# =========================================================================== CORE 2: mutation_seeded_error

def test_mutate_source_produces_at_least_one_mutant():
    mutants = mutate_source("def f(x):\n    return x + 1 if x > 0 else 0\n")
    assert len(mutants) >= 2
    for m in mutants:
        compile(m["code"], "<mutant>", "exec")  # every mutant must still be syntactically valid


def _behavioral_checker(code: str) -> bool:
    # Exercises the boundary (0, 1) as well as interior points -- required to catch a
    # comparator-flip mutant (x>0 -> x>=0) that only diverges from the original at x=0.
    ns: dict = {}
    try:
        exec(code, ns)  # noqa: S102 -- trusted local fixture source only
        f = ns["f"]
        return f(-1) == 0 and f(0) == 0 and f(1) == 2 and f(3) == 4
    except Exception:
        return False


def _syntax_only_checker(code: str) -> bool:
    try:
        compile(code, "<t>", "exec")
        return True
    except SyntaxError:
        return False


def test_seeded_mutation_kills_all_mutants_for_a_real_checker():
    good = "def f(x):\n    return x + 1 if x > 0 else 0\n"
    mutants = mutate_source(good)
    r = seeded_mutation_test(_behavioral_checker, good, mutants)
    assert r.passed and r.score == 1.0, r.diagnostics


def test_seeded_mutation_exposes_a_toothless_checker():
    # A checker that only checks the code compiles has no teeth against logic mutants.
    good = "def f(x):\n    return x + 1 if x > 0 else 0\n"
    mutants = mutate_source(good)
    r = seeded_mutation_test(_syntax_only_checker, good, mutants)
    assert not r.passed and r.score < 1.0


def test_seeded_mutation_good_artifact_must_itself_pass():
    def always_fail_checker(_code: str) -> bool:
        return False
    r = seeded_mutation_test(always_fail_checker, "def f(x):\n    return x\n", [{"label": "m0", "code": "x=1"}])
    assert not r.passed and not r.checks["good_artifact_passes"]


def test_seeded_mutation_empty_mutant_set_does_not_silently_pass():
    """finding #7 (HIGH) regression: the exact audit example -- with zero mutants, kill_rate
    used to default to 1.0, so ANY checker (however toothless) that merely accepted the good
    artifact read as "passed=True, kill_rate=1.0". Must now be an honest quarantine."""
    def anything_goes_checker(_artifact) -> bool:
        return True
    r = seeded_mutation_test(anything_goes_checker, "def f(x):\n    return x\n", [])
    assert not r.passed
    assert r.quarantine_reason == "insufficient_mutant_diversity"
    assert r.checks["kill_rate"] != 1.0


def test_seeded_mutation_below_diversity_floor_quarantines():
    good = "def f(x):\n    return x + 1 if x > 0 else 0\n"
    mutants = mutate_source(good)[:1]  # fewer than MIN_MUTANTS
    r = seeded_mutation_test(_behavioral_checker, good, mutants)
    assert not r.passed
    assert r.quarantine_reason == "insufficient_mutant_diversity"


def test_seeded_mutation_degenerate_noop_mutants_are_filtered_not_counted():
    """finding #7 regression: a "mutant" byte-identical to the good artifact mutated nothing and
    must not inflate the kill-rate denominator either way."""
    good = "def f(x):\n    return x + 1 if x > 0 else 0\n"
    noop_mutants = [{"label": f"noop{i}", "code": good} for i in range(5)]
    r = seeded_mutation_test(_behavioral_checker, good, noop_mutants)
    assert not r.passed
    assert r.quarantine_reason == "insufficient_mutant_diversity"
    assert len(r.diagnostics["degenerate"]) == 5


def test_seeded_mutation_checker_exception_quarantines_not_crashes():
    """finding #7 regression: a checker that raises on a mutant must not crash the harness AND
    must not be silently counted as "caught" (which would let an always-raising, genuinely
    toothless checker fake a perfect kill_rate)."""
    good = "def f(x):\n    return x + 1 if x > 0 else 0\n"
    mutants = mutate_source(good)

    def flaky_checker(code: str) -> bool:
        if "x >= 0" in code:
            raise RuntimeError("boom")
        ns: dict = {}
        exec(code, ns)  # noqa: S102
        return ns["f"](1) == 2

    r = seeded_mutation_test(flaky_checker, good, mutants)  # must not raise out of this call
    assert not r.passed
    assert r.quarantine_reason == "checker_exception_on_mutant"
    assert r.diagnostics["errored"]


def test_seeded_mutation_checker_exception_on_good_artifact_quarantines():
    good = "def f(x):\n    return x + 1 if x > 0 else 0\n"
    mutants = mutate_source(good)

    def always_raises(_artifact) -> bool:
        raise RuntimeError("always broken")

    r = seeded_mutation_test(always_raises, good, mutants)
    assert not r.passed
    assert r.quarantine_reason == "checker_exception_on_good_artifact"


# =========================================================================== CORE 3: resolver_joins

def test_numeral_fidelity_honest_rounding_passes():
    src = "Token count fell by 16.7% on the golden set."
    claim = "Roughly a sixth of tokens were saved."
    assert numeral_fidelity_join(claim, src).passed


def test_numeral_fidelity_fabricated_number_fails():
    src = "Token count fell by 16.7% on the golden set."
    claim = "The change cut tokens by 34%."
    r = numeral_fidelity_join(claim, src)
    assert not r.passed and 34.0 in r.diagnostics["unsupported_values"]


def test_numeral_fidelity_wrong_direction_word_fails():
    src = "Token count fell by 16.7% on the golden set."
    claim = "Roughly a quarter of tokens were saved."  # 25 vs 16.7, outside tolerance
    assert not numeral_fidelity_join(claim, src).passed


def test_numeral_fidelity_unit_mismatch_is_not_support():
    """finding #9 regression: the exact audit example -- "50% latency reduction" must NOT be
    "supported" by an unrelated "50 samples" just because the bare digits match."""
    claim = "The change delivered a 50% latency reduction."
    unrelated_source = "We tested with 50 samples drawn from the golden set."
    r = numeral_fidelity_join(claim, unrelated_source)
    assert not r.passed, r.diagnostics
    assert 50.0 in r.diagnostics["unsupported_values"]


def test_numeral_fidelity_same_unit_and_context_still_passes():
    claim = "Latency fell by roughly 50%."
    source = "Median latency dropped 50% after the change."
    assert numeral_fidelity_join(claim, source).passed


def test_recompute_probe_correct_derivation_passes():
    import hashlib
    expected = hashlib.sha256(b"secret").hexdigest()
    r = recompute_probe("sha256('secret')", expected)
    assert r.passed and r.checks["recomputable"]


def test_recompute_probe_wrong_constant_fails():
    r = recompute_probe("sha256('secret')", "not-the-real-hash")
    assert not r.passed


def test_recompute_probe_rejects_unsafe_expression():
    r = recompute_probe("__import__('os').system('echo pwned')", "anything")
    assert not r.passed and not r.checks["recomputable"]


def test_recompute_probe_rejects_keyword_arguments_instead_of_discarding_them():
    """finding #6 regression: `sum((1,2), start=999)` used to silently DISCARD `start=999` and
    evaluate as `sum((1,2)) == 3`, changing the expression's actual semantics vs. what it reads
    as. Both the "old buggy" answer and the "if kwargs worked" answer must now be rejected --
    the probe refuses to guess, it rejects the call outright."""
    r_old_answer = recompute_probe("sum((1,2), start=999)", 3)
    assert not r_old_answer.passed and not r_old_answer.checks["recomputable"]
    r_real_answer = recompute_probe("sum((1,2), start=999)", 1002)
    assert not r_real_answer.passed and not r_real_answer.checks["recomputable"]


def test_recompute_probe_rejects_starred_arguments():
    r = recompute_probe("sum(*[1, 2, 3])", 6)
    assert not r.passed and not r.checks["recomputable"]


def test_recompute_probe_bounds_unbounded_pow():
    """finding #6 regression: unbounded `Pow` is a CPU/memory DoS vector."""
    r = recompute_probe("10 ** (10 ** 10)", 1)
    assert not r.passed and not r.checks["recomputable"]


def test_recompute_probe_bounded_pow_still_works():
    r = recompute_probe("2 ** 10", 1024)
    assert r.passed and r.checks["recomputable"]


def test_recompute_probe_rejects_custom_functions():
    """finding #6 regression: a caller must not be able to inject or override a callable in the
    function allowlist (`funcs`) -- e.g. shadowing `abs` with a dangerous/wrong implementation."""
    r = recompute_probe("abs(-1)", 1, funcs={"abs": lambda x: 999})
    assert not r.passed and not r.checks["recomputable"]


def test_recompute_probe_rejects_reserved_name_override():
    r = recompute_probe("pi", 3.14159, names={"pi": 3.14159})
    assert not r.passed and not r.checks["recomputable"]


def test_recompute_probe_allows_new_primitive_names():
    r = recompute_probe("x + 1", 3, names={"x": 2})
    assert r.passed and r.checks["recomputable"]


def test_constant_symmetry_flags_shared_undeclared_literal():
    r = constant_symmetry(impl_literals=[0.6180339887], test_literals=[0.6180339887])
    assert not r.passed


def test_constant_symmetry_allows_declared_derivation():
    r = constant_symmetry(
        impl_literals=[0.6180339887], test_literals=[0.6180339887],
        derivation_markers={0.6180339887},
    )
    assert r.passed


def test_constant_symmetry_ignores_trivial_literals():
    r = constant_symmetry(impl_literals=[0, 1, -1], test_literals=[0, 1, -1])
    assert r.passed  # 0/1/-1 are not "non-trivial"


# =========================================================================== CORE 4: schema_presence

_GOOD_METRIC = {
    "goal": "reduce mean time to a merged fix",
    "measured_object": "hours from bug report to merged PR",
    "unit": "hours",
    "direction": "down",
    "counter_metrics": [{"name": "defect_reopen_rate", "where_reported": "weekly dashboard",
                          "cadence": "weekly", "gated_jointly": True}],
    "gaming_vectors": [{"mode": "rush_low_quality_fix", "cheapest_move": "merge unreviewed",
                         "catcher": "defect_reopen_rate"}],
    "review": {"trigger": "quarterly", "owner": "eng-lead"},
    "level": "per-system",
}


def test_schema_presence_complete_metric_passes():
    r = validate_schema(_GOOD_METRIC, SM_SCHEMA)
    assert r.passed, r.diagnostics


def test_schema_presence_missing_counter_metrics_fails():
    bad = {k: v for k, v in _GOOD_METRIC.items() if k != "counter_metrics"}
    r = validate_schema(bad, SM_SCHEMA)
    assert not r.passed and any("counter_metrics" in m for m in r.diagnostics["missing"])


def test_schema_presence_empty_gaming_vectors_fails_nonempty_check():
    bad = dict(_GOOD_METRIC, gaming_vectors=[])
    r = validate_schema(bad, SM_SCHEMA)
    assert not r.passed


def test_schema_presence_wrong_type_fails():
    bad = dict(_GOOD_METRIC, counter_metrics="not a list")
    r = validate_schema(bad, SM_SCHEMA)
    assert not r.passed and r.diagnostics["type_errors"]


def test_schema_presence_whitespace_only_required_field_fails():
    """finding #9 regression: a whitespace-only string is not `""`, so it used to satisfy a
    required field's presence check."""
    bad = dict(_GOOD_METRIC, goal="   ")
    r = validate_schema(bad, SM_SCHEMA)
    assert not r.passed and "goal" in r.diagnostics["missing"]


def test_schema_presence_empty_dict_required_field_fails():
    """finding #9 regression: an empty dict is not `None`/`""`, so a required `type: dict` field
    (e.g. `review`) used to be satisfiable by a completely empty object."""
    bad = dict(_GOOD_METRIC, review={})
    r = validate_schema(bad, SM_SCHEMA)
    assert not r.passed and "review" in r.diagnostics["missing"]


def test_schema_presence_consistency_check_catches_ceremony_catcher():
    ceremony = dict(_GOOD_METRIC)
    ceremony["gaming_vectors"] = [{"mode": "x", "cheapest_move": "y", "catcher": "monitor closely"}]
    r = validate_schema(
        ceremony, SM_SCHEMA,
        consistency_checks=[
            ("catcher_is_mechanism", lambda rec: all(
                classify_catcher(gv["catcher"]) == "mechanism" for gv in rec["gaming_vectors"]
            )),
        ],
    )
    assert not r.passed and "catcher_is_mechanism" in r.diagnostics["consistency_failed"]


_GOOD_FINDING = {
    "claim_ref": "audit/foo.md#claim-3",
    "claim_material": True,
    "verdict": "confirmed",
    "verified_depth": "full",
    "basis": [{"kind": "test", "transcript_ref": "runs/2026-07-14/pytest.log"}],
}


def test_da9_finding_schema_complete_passes():
    r = validate_schema(_GOOD_FINDING, DA9_FINDING_SCHEMA)
    assert r.passed, r.diagnostics


def test_da9_finding_schema_missing_basis_fails():
    bad = {k: v for k, v in _GOOD_FINDING.items() if k != "basis"}
    r = validate_schema(bad, DA9_FINDING_SCHEMA)
    assert not r.passed


# =========================================================================== CORE 5: lexicon_grammar

def test_classify_catcher_mechanism_vs_ceremony():
    assert classify_catcher("fails named test suite_regression_gate on threshold breach") == "mechanism"
    assert classify_catcher("we'll keep an eye on it and raise with the team") == "ceremony"


def test_catcher_wiring_check_passes_and_fails():
    assert catcher_wiring_check(["blocked by named test tool_calling_gate"]).passed
    assert not catcher_wiring_check(["spot check as capacity allows"]).passed
    assert not catcher_wiring_check([]).passed  # empty list is not a pass


def test_consensus_corroboration_requires_two_sources():
    strong = "Studies show this consistently improves recall."
    assert not consensus_corroboration_check(strong, 1).passed
    assert consensus_corroboration_check(strong, 2).passed


def test_consensus_single_source_register_always_passes():
    hedged = "One source reports a modest gain; not independently corroborated."
    assert consensus_corroboration_check(hedged, 1).passed


def test_consensus_single_source_register_does_not_rescue_coexisting_consensus_claim():
    """finding #8(c) regression: "one source reports ... studies show ... well established" in
    the SAME sentence must NOT bypass corroboration via the single-source safe harbor."""
    mixed = "One source reports that studies show this is well established practice."
    assert not consensus_corroboration_check(mixed, 1).passed
    assert consensus_corroboration_check(mixed, 2).passed


def test_classify_catcher_requires_structured_reference_not_bare_word():
    """finding #8(a) regression: a bare mechanism WORD with nothing concrete attached (no test
    id, no numeric threshold) must not be credited as a mechanism."""
    assert classify_catcher("we should probably set a threshold eventually") == "unclassified"
    assert classify_catcher("the code asserts things are fine") == "unclassified"
    assert classify_catcher("fails threshold of 0.8") == "mechanism"
    assert classify_catcher("blocked by named test suite_regression_gate") == "mechanism"


def test_classify_command_read_only_vs_behavior():
    assert classify_command("git log --oneline -5") == "read_only"
    assert classify_command("grep -n foo bar.py") == "read_only"
    assert classify_command('python -c "import cortex_core.judge"') == "read_only"
    assert classify_command("pytest tests/test_x.py -q") == "behavior"
    assert classify_command("curl http://localhost:8080/health") == "behavior"


def test_classify_command_collect_only_is_read_only_not_behavior():
    """finding #8(b) regression: the audit's own named example -- `pytest --collect-only` lists
    tests but executes none of their bodies, so it must NOT classify as `behavior` merely
    because the line starts with `pytest`."""
    assert classify_command("pytest --collect-only -q tests/") == "read_only"
    r = command_evidence_check(["pytest --collect-only -q"], requires_behavioral=True)
    assert not r.passed  # collection-only alone cannot satisfy the behavioral-evidence gate


def test_classify_command_python_dash_c_with_a_call_is_behavior():
    """finding #8(b) regression: the audit's other named example -- `python -c "import x;
    x.run()"` executes `x.run()`, so it must NOT classify as `read_only` merely because the
    payload starts with `import`."""
    assert classify_command('python -c "import x; x.run()"') == "behavior"
    assert classify_command('python -c "import cortex_core.judge"') == "read_only"


def test_classify_command_compound_line_is_quote_aware():
    """finding #8(b) regression: a compound line must be split on real chain operators, not on
    `;`/`&&` characters that merely appear INSIDE a quoted `-c` payload."""
    assert classify_command("cd /tmp && pytest tests/test_x.py") == "behavior"
    # the `;` here is inside the quoted python payload, not a shell chain operator -- must still
    # classify by the payload's actual content (a call after the import), not get shredded.
    assert classify_command('python -c "import x; x.run()"') == "behavior"


def test_command_evidence_check_blocks_all_read_only_evidence():
    r = command_evidence_check(["git log", "grep foo bar.py", "ls -la"], requires_behavioral=True)
    assert not r.passed


def test_command_evidence_check_passes_with_one_behavior_command():
    r = command_evidence_check(["git log", "pytest tests/"], requires_behavioral=True)
    assert r.passed


def test_detect_empty_selection_catches_vacuous_run():
    transcript = "collected 0 items\n\n=== no tests ran in 0.01s ===\n"
    r = detect_empty_selection(transcript)
    assert not r.passed and r.checks["empty_selection_detected"]


def test_detect_empty_selection_passes_real_run():
    transcript = "collected 12 items\n............\n12 passed in 1.20s\n"
    r = detect_empty_selection(transcript)
    assert r.passed


# =========================================================================== cross-core: no judge in verdict path

def test_checker_cores_are_judge_free():
    """Structural guarantee mirroring evals/oracle_adapter.py: none of the five core modules
    may import a judge/LLM/network module -- these are library primitives other lanes will
    treat as verdict-path code, so the AST scan applies to them directly."""
    from evals.oracle_adapter import verdict_path_is_judge_free

    core_dir = ROOT / "evals" / "checker_cores"
    modules = [
        core_dir / "differential_execution.py",
        core_dir / "mutation_seeded_error.py",
        core_dir / "resolver_joins.py",
        core_dir / "schema_presence.py",
        core_dir / "lexicon_grammar.py",
    ]
    clean, problems = verdict_path_is_judge_free(modules)
    assert clean, problems
