"""Frozen tests for the Stage-2 crypto-misuse lane (evals/objective_crypto_misuse/).

Prior to 2026-07-14 this lane had NO lane-specific frozen test -- only the shared
`test_objective_lane_integrity.py` floor covered it. Codex terra's red-team review
(`reviewed/cyber-lanes-redteam-terra-2026-07-14.md`) found the same CRITICAL exit-0 bypass here
as the CWE lane (deliberately mirrored checker), plus HIGH exact-answer-not-class gaps: a
99,999-round PBKDF2 (below the stated >=100_000 minimum) passed because the exploit only spot-
checked 10 discrete round counts, and `sha256(key + b":" + msg)` passed because the exploit
rejected only 3 exact ad-hoc digests. This file locks the fixes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_crypto_misuse import checker as CK  # noqa: E402
from evals.objective_crypto_misuse.fixtures import CRYPTO_FIXTURES  # noqa: E402


def _find(id_):
    return next(fx for fx in CRYPTO_FIXTURES if fx["id"] == id_)


# ---- 1. every fixture's secure/vulnerable reference is correctly graded ------------------------

def test_every_secure_reference_passes():
    for fx in CRYPTO_FIXTURES:
        r = CK.check_patch(fx["secure_code"], fx["functional_tests"], fx["exploit_test"])
        assert r.verdict == "pass", (fx["id"], r.asdict())


def test_every_vulnerable_reference_fails_at_exploit_regression():
    for fx in CRYPTO_FIXTURES:
        r = CK.check_patch(fx["vulnerable_code"], fx["functional_tests"], fx["exploit_test"])
        assert r.verdict == "fail", (fx["id"], r.asdict())
        assert r.failed_stage == "exploit_regression", (fx["id"], r.asdict())


def test_all_seeded_mutations_are_killed_at_exploit_regression():
    for fx in CRYPTO_FIXTURES:
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
    r = CK.check_patch("import os\nos._exit(0)\n", _BROKEN_TEST, _BROKEN_TEST)
    assert r.verdict == "fail", r.asdict()


def test_raise_system_exit0_bypass_is_blocked():
    r = CK.check_patch("raise SystemExit(0)\n", _BROKEN_TEST, _BROKEN_TEST)
    assert r.verdict == "fail", r.asdict()


# ---- 3. FROZEN REGRESSION: class-vs-exact-answer gaps (red-team 2026-07-14, finding 4) ---------

def test_99999_round_pbkdf2_near_miss_is_blocked():
    # below the fixture's stated >=100_000-iteration minimum by exactly one -- the original
    # exploit_test only spot-checked 10 discrete round counts (1..10_000) and missed this.
    fx = _find("weak_password_kdf")
    near_miss = (
        "import hashlib\n"
        "def hash_password(password, salt):\n"
        "    return hashlib.pbkdf2_hmac('sha256', password, salt, 99_999).hex()\n"
        "def verify_password(password, salt, digest):\n"
        "    return hash_password(password, salt) == digest\n"
    )
    r = CK.check_patch(near_miss, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "fail", r.asdict()
    assert r.failed_stage == "exploit_regression"


def test_100000_round_pbkdf2_at_the_floor_passes():
    # sanity check on the other side of the threshold: exactly the stated minimum must PASS,
    # proving the new exhaustive sweep is a real threshold, not an off-by-one over-reject.
    fx = _find("weak_password_kdf")
    at_floor = (
        "import hashlib\n"
        "def hash_password(password, salt):\n"
        "    return hashlib.pbkdf2_hmac('sha256', password, salt, 100_000).hex()\n"
        "def verify_password(password, salt, digest):\n"
        "    return hash_password(password, salt) == digest\n"
    )
    r = CK.check_patch(at_floor, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "pass", r.asdict()


def test_adhoc_prefix_mac_with_different_separator_is_blocked():
    # the original exploit_test rejected only 3 EXACT digests (sha1/md5/sha256 of
    # secret_key+message, no separator); a different hash+separator combo, still an ad-hoc
    # prefix-MAC, evaded it.
    fx = _find("weak_hash_signature_hmac")
    near_miss = (
        "import hashlib\n"
        "def sign_message(secret_key, message):\n"
        "    return hashlib.sha256(secret_key + b':' + message).hexdigest()\n"
    )
    r = CK.check_patch(near_miss, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "fail", r.asdict()
    assert r.failed_stage == "exploit_regression"


def test_real_hmac_with_a_different_hash_still_passes():
    # sanity check: a REAL HMAC (not ad-hoc concatenation) using a different-but-still-strong
    # hash must still pass -- the fix targets the CONSTRUCTION, not "any hash other than sha256".
    fx = _find("weak_hash_signature_hmac")
    real_hmac_sha512 = (
        "import hmac, hashlib\n"
        "def sign_message(secret_key, message):\n"
        "    return hmac.new(secret_key, message, hashlib.sha512).hexdigest()\n"
    )
    r = CK.check_patch(real_hmac_sha512, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "pass", r.asdict()


def test_different_literal_fallback_key_is_blocked_not_just_the_exact_original():
    fx = _find("hardcoded_encryption_key")
    diff_literal = (
        "import os\n"
        "def get_encryption_key():\n"
        "    return bytes.fromhex(os.environ.get("
        "'ENCRYPTION_KEY_HEX', 'deadbeefdeadbeefdeadbeefdeadbeef'))\n"
    )
    r = CK.check_patch(diff_literal, fx["functional_tests"], fx["exploit_test"])
    assert r.verdict == "fail", r.asdict()
    assert r.failed_stage == "exploit_regression"


# ---- 4. FALSE-POSITIVE fix: expensive-but-secure KDF must not fail on timeout alone -------------

def test_check_patch_accepts_a_caller_supplied_longer_timeout_for_slow_kdfs():
    # red-team false-positive #2: a deliberately expensive (still correct) secure KDF can be
    # slower than the lane's default TIMEOUT_S. check_patch's timeout is caller-overridable
    # (KDF_TIMEOUT_S is exported precisely so run_crypto_misuse.py can raise it per-fixture).
    fx = _find("weak_password_kdf")
    slow_but_secure = (
        "import hashlib\n"
        "def hash_password(password, salt):\n"
        "    return hashlib.pbkdf2_hmac('sha256', password, salt, 600_000).hex()\n"
        "def verify_password(password, salt, digest):\n"
        "    return hash_password(password, salt) == digest\n"
    )
    r = CK.check_patch(slow_but_secure, fx["functional_tests"], fx["exploit_test"],
                       timeout=CK.KDF_TIMEOUT_S)
    assert r.verdict == "pass", r.asdict()


# ---- 5. structural / no-judge invariants --------------------------------------------------------

def test_no_judge_or_network_client_import_in_checker_module():
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from lanes import module_forbidden_imports  # noqa: E402
    bad = module_forbidden_imports(ROOT / "evals" / "objective_crypto_misuse" / "checker.py")
    assert not bad, f"checker.py imports forbidden {bad}"
