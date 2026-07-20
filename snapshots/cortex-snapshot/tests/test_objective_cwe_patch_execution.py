"""Frozen tests for the Stage-2 CWE patch-execution lane (evals/objective_cwe_patch_execution/).

Prior to 2026-07-14 this lane had NO lane-specific frozen test -- only the shared
`test_objective_lane_integrity.py` floor covered it. Codex terra's red-team review
(`reviewed/cyber-lanes-redteam-terra-2026-07-14.md`) found a CRITICAL exit-0 bypass here (a
candidate consisting solely of `raise SystemExit(0)` / `os._exit(0)` passed even when the
appended test was `assert False`) plus HIGH exact-answer-not-class gaps (a 99,999-round PBKDF2 /
near-miss payloads slipping past narrow probes -- this lane's equivalent lives in the crypto
lane's fixtures, but the SQLi/hardcoded-secret analogues are HERE). This file locks the fixes so
they can never silently regress.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_cwe_patch_execution import checker as CK  # noqa: E402
from evals.objective_cwe_patch_execution.fixtures import CWE_FIXTURES  # noqa: E402

# hardcoded_secret's vulnerable_code legitimately fails the FUNCTIONAL test (it never reads the
# env var at all), not the exploit stage -- documented in run_cwe_patch.py's
# NON_EXPLOIT_VULN_CLASSES, mirrored here so this file's assertions match the runner's.
NON_EXPLOIT_VULN_CLASSES = {"hardcoded_secret"}


def _find(id_):
    return next(fx for fx in CWE_FIXTURES if fx["id"] == id_)


# ---- 1. every fixture's secure/vulnerable reference is correctly graded ------------------------

def test_every_secure_reference_passes():
    for fx in CWE_FIXTURES:
        r = CK.check_patch(fx["secure_code"], fx["functional_tests"], fx["exploit_test"])
        assert r.verdict == "pass", (fx["id"], r.asdict())


def test_every_vulnerable_reference_fails_at_the_right_stage():
    for fx in CWE_FIXTURES:
        r = CK.check_patch(fx["vulnerable_code"], fx["functional_tests"], fx["exploit_test"])
        assert r.verdict == "fail", (fx["id"], r.asdict())
        if fx["vulnerability_class"] in NON_EXPLOIT_VULN_CLASSES:
            assert r.failed_stage == "functional_tests", (fx["id"], r.asdict())
        else:
            assert r.failed_stage == "exploit_regression", (fx["id"], r.asdict())


def test_all_seeded_mutations_are_killed_at_exploit_regression():
    for fx in CWE_FIXTURES:
        for m in fx.get("mutations", []):
            r = CK.check_patch(m["code"], fx["functional_tests"], fx["exploit_test"])
            assert r.verdict == "fail", (fx["id"], m["label"], r.asdict())
            assert r.failed_stage == "exploit_regression", (fx["id"], m["label"], r.asdict())


# ---- 2. FROZEN REGRESSION: CRITICAL exit-0 bypass (red-team 2026-07-14) ------------------------

_BROKEN_TEST = "assert False\n"


def test_sys_exit0_bypass_is_blocked():
    r = CK.check_patch("import sys\nsys.exit(0)\n", _BROKEN_TEST, _BROKEN_TEST)
    assert r.verdict == "fail", r.asdict()


def test_os_exit0_bypass_is_blocked():
    # os._exit(0) cannot be caught by ANY Python exception handler -- it terminates the process
    # immediately. The fix relies on (exit==0 AND sentinel-printed), not exit code alone.
    r = CK.check_patch("import os\nos._exit(0)\n", _BROKEN_TEST, _BROKEN_TEST)
    assert r.verdict == "fail", r.asdict()


def test_raise_system_exit0_bypass_is_blocked():
    r = CK.check_patch("raise SystemExit(0)\n", _BROKEN_TEST, _BROKEN_TEST)
    assert r.verdict == "fail", r.asdict()


def test_exit0_bypass_blocked_even_when_functional_test_is_legitimate():
    # a candidate that passes the functional stage honestly, then exits early ONLY during the
    # exploit stage, must still fail there -- not slip through as an overall pass.
    func = "assert 1 + 1 == 2\n"
    exploit = "assert False, 'exploit succeeded'\n"
    r = CK.check_patch("import os\ndef _f():\n    os._exit(0)\n_f()\n", func, exploit)
    assert r.verdict == "fail", r.asdict()


def test_every_vulnerable_reference_still_fails_under_a_wrapped_exit0_smokescreen():
    # a more devious candidate: define the REAL vulnerable function correctly (so it "looks"
    # legitimate) but also exit early -- must still fail, proving the fix isn't just pattern-
    # matching "SystemExit" at the top of the file.
    fx = _find("sql_injection_get_user")
    smokescreen = fx["vulnerable_code"] + "\nimport os\nos._exit(0)\n"
    r = CK.check_patch(smokescreen, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "fail", r.asdict()


# ---- 3. FROZEN REGRESSION: class-vs-exact-answer gaps (red-team 2026-07-14, finding 4) ---------

def test_union_based_sqli_bypass_is_blocked_not_just_boolean_tautology():
    # the original exploit_test probed only `0 OR 1=1`; a candidate blocking that literal shape
    # (and its keyword) but still building SQL by string interpolation remains injectable via
    # UNION-based exfiltration.
    fx = _find("sql_injection_get_user")
    union_bypass = (
        "def get_user(cursor, uid):\n"
        "    uid = str(uid)\n"
        "    if '1=1' in uid or 'OR' in uid.upper():\n"
        "        raise ValueError('blocked')\n"
        "    q = f\"SELECT * FROM users WHERE id = {uid}\"\n"
        "    cursor.execute(q)\n"
        "    return cursor.fetchone()\n"
    )
    r = CK.check_patch(union_bypass, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "fail", r.asdict()
    assert r.failed_stage == "exploit_regression"


def test_different_literal_fallback_secret_is_blocked_not_just_the_exact_original():
    # the original exploit_test scanned for ONE exact secret literal; a candidate hardcoding a
    # DIFFERENT fallback value evaded it. The behavioral (env-unset) check closes this.
    fx = _find("hardcoded_secret_get_db_password")
    diff_literal = (
        "import os\n"
        "def get_db_password():\n"
        "    return os.environ.get('DB_PASSWORD', 'totally-different-fallback-999')\n"
    )
    r = CK.check_patch(diff_literal, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "fail", r.asdict()
    assert r.failed_stage == "exploit_regression"


# ---- 4. structural / no-judge invariants --------------------------------------------------------

def test_no_judge_or_network_client_import_in_checker_module():
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from lanes import module_forbidden_imports  # noqa: E402
    bad = module_forbidden_imports(ROOT / "evals" / "objective_cwe_patch_execution" / "checker.py")
    assert not bad, f"checker.py imports forbidden {bad}"
