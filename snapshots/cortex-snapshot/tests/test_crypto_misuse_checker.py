"""Frozen tests for the Stage-2 P1 crypto-misuse checker (the pass/fail authority for
`evals/objective_crypto_misuse/`).

The whole lane trusts this checker's verdicts, so: every secure reference must PASS (compile +
functional + exploit-blocked), every vulnerable reference must FAIL — and specifically at the
`exploit_regression` stage (proving the probe is a real known-answer/property regression test,
not a tautology). Every seeded mutation must ALSO fail (be "killed" by the exploit test) — a
mutation that slips through would mean the probe is too weak, and that must show up here, not
silently in the hard_gold set.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_crypto_misuse.checker import check_patch  # noqa: E402
from evals.objective_crypto_misuse.fixtures import CRYPTO_FIXTURES  # noqa: E402


def test_correct_patch_passes():
    func = "assert 1 + 1 == 2\n"
    exploit = "assert BLOCK is True, 'exploit succeeded'\n"
    r = check_patch("BLOCK = True\n", func, exploit)
    assert r.verdict == "pass", r.asdict()


def test_unpatched_code_fails_at_exploit_stage():
    func = "assert 1 + 1 == 2\n"
    exploit = "assert BLOCK is True, 'exploit succeeded'\n"
    r = check_patch("BLOCK = False\n", func, exploit)
    assert r.verdict == "fail" and r.failed_stage == "exploit_regression", r.asdict()


def test_functional_regression_detected_before_exploit_stage():
    r = check_patch("BLOCK = True\n", "assert 1 + 1 == 3\n", "assert BLOCK is True\n")
    assert r.verdict == "fail" and r.failed_stage == "functional_tests", r.asdict()


def test_syntax_error_detected_at_compile():
    r = check_patch("def f(x)\n return x\n", "assert 1==1\n", "assert 1==1\n")
    assert r.verdict == "fail" and r.failed_stage == "compile", r.asdict()


def test_timeout_is_a_failure_not_a_hang():
    slow = "while True: pass\n"
    r = check_patch(slow, "assert 1==1\n", "assert 1==1\n", timeout=3.0)
    assert r.verdict == "fail"


def test_all_secure_references_pass():
    for fx in CRYPTO_FIXTURES:
        r = check_patch(fx["secure_code"], fx["functional_tests"], fx["exploit_test"])
        assert r.verdict == "pass", f"{fx['id']} secure_code should pass: {r.asdict()}"


def test_all_vulnerable_references_fail_at_exploit_stage():
    for fx in CRYPTO_FIXTURES:
        r = check_patch(fx["vulnerable_code"], fx["functional_tests"], fx["exploit_test"])
        assert r.verdict == "fail", f"{fx['id']} vulnerable_code should fail: {r.asdict()}"
        assert r.failed_stage == "exploit_regression", (
            f"{fx['id']} vulnerable_code should fail at exploit_regression "
            f"(a non-tautological probe), got {r.failed_stage}: {r.asdict()}")


def test_all_seeded_mutations_are_killed():
    for fx in CRYPTO_FIXTURES:
        for m in fx.get("mutations", []):
            r = check_patch(m["code"], fx["functional_tests"], fx["exploit_test"])
            assert r.verdict == "fail", (
                f"{fx['id']}/{m['label']} mutation should be killed (fail) but passed: "
                f"{r.asdict()}")


def test_fixture_class_coverage_is_at_least_seven_classes():
    classes = {fx["vulnerability_class"] for fx in CRYPTO_FIXTURES}
    assert len(classes) >= 7, classes
    expected = {
        "weak_hash_for_security", "ecb_mode", "fixed_or_reused_iv_nonce",
        "non_crypto_rng_for_secrets", "tls_verify_disabled", "hardcoded_key_secret",
        "weak_or_absent_password_kdf",
    }
    assert expected <= classes, expected - classes


def test_fixture_count_is_small_but_complete():
    assert 8 <= len(CRYPTO_FIXTURES) <= 15, len(CRYPTO_FIXTURES)
