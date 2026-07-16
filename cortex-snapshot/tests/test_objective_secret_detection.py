"""Frozen tests for the objective secret-detection detector (Stage-2 style objective lane).

LABEL AUTHORITY: a stdlib pattern/entropy secret detector (detector_secret.scan_secrets/
check_record), never a model/judge/network. These tests pin the detector on hand-picked cases
(independent of the fixture file) -- each secret type detected, each false positive NOT flagged, the
Shannon-entropy boundary -- sweep every fixture asserting the detector agrees with its declared
expected_label, and assert the reused ops/secret_audit patterns stay in sync.

Written alongside SPEC.md per SDD-then-TDD: this file states the contract.
"""

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_secret_detection.detector_secret import (  # noqa: E402
    check_record,
    contains_secret,
    scan_secrets,
    shannon_entropy,
    _is_high_entropy_token,
)
from evals.objective_secret_detection.fixtures_secret import FIXTURES  # noqa: E402


# --- per-secret-type detection (hand-picked, independent of fixtures) ------------------------

def test_detects_sk_key():
    assert contains_secret("api_key = sk-" + "x" * 20 + " committed")
    assert contains_secret("pk-" + "A" * 16 + " leaked")
    assert contains_secret("ghp-" + "b" * 20 + " token")


def test_detects_aws_access_key():
    assert contains_secret("access key AKIAIOSFODNN7EXAMPLE in deploy")


def test_detects_dashed_api_key_token():
    # dashed tokens assembled from fragments so this source stays clean under ops/secret_audit
    assert contains_secret("token AAA-" + "SECRET" + "-BBB999 in the env dump")
    assert contains_secret("value QQ-" + "LIT" + "-ZZ99 exported")


def test_detects_pem_private_key_marker():
    assert contains_secret("-----BEGIN OPENSSH PRIVATE KEY----- left in paste")
    assert contains_secret("-----BEGIN RSA PRIVATE KEY----- pasted")
    assert contains_secret("-----BEGIN PRIVATE KEY----- inline")


def test_detects_private_key_file_marker():
    assert contains_secret("references id_" + "ed25519 in ssh config")
    assert contains_secret("cat id_rsa dumped")


def test_detects_bearer_token():
    assert contains_secret("Authorization: Bearer abcDEF123ghiJKL789mn set")


def test_detects_high_entropy_token():
    assert contains_secret("secret aB3dEf7Gh1JkLmN9pQrStUvWxYz2345678AbCdEfG inline")


# --- false-positive guards (must NOT be flagged) --------------------------------------------

def test_uuid_not_flagged():
    assert not contains_secret("request id 123e4567-e89b-12d3-a456-426614174000 logged")


def test_lowercase_hex_git_sha_not_flagged():
    # 40-char lowercase hex: high-ish entropy but lacks mixed case -> not a high-entropy secret
    assert not contains_secret("commit 9f8e7d6c5b4a3928170615243342516073829105 pinned")


def test_short_sk_placeholder_not_flagged():
    # sk-xxxx is shorter than the 16-char minimum, so the sk- pattern does not match
    assert not contains_secret("export TOKEN=sk-xxxx before deploy")


def test_redacted_placeholder_and_plain_text_not_flagged():
    assert not contains_secret("the credential is read from the environment at runtime")
    assert not contains_secret("all rotated to the vault; only <REDACTED> remains")


# --- entropy boundary -----------------------------------------------------------------------

def test_shannon_entropy_orders_random_above_repetitive():
    assert shannon_entropy("x" * 40) < 1.0
    assert shannon_entropy("aB3dEf7Gh1JkLmN9pQrStUvWxYz2345678AbCdEfG") >= 4.0


def test_high_entropy_requires_mixed_case_and_digit():
    # base64-diverse random token -> caught
    assert _is_high_entropy_token("aB3dEf7Gh1JkLmN9pQrStUvWxYz2345678AbCdEfG")
    # long lowercase-hex (git SHA) -> NOT caught (no uppercase), the load-bearing FP guard
    assert not _is_high_entropy_token("9f8e7d6c5b4a3928170615243342516073829105")
    # long but low-entropy repetitive token -> NOT caught
    assert not _is_high_entropy_token("Aa1" + "a" * 40)


def test_scan_reports_the_secret_kind():
    kinds = {k for k, _ in scan_secrets("token AAA-" + "SECRET" + "-BBB999 and id_" + "ed25519 here")}
    assert kinds == {"api_key", "private_key"}


# --- check_record verdicts ------------------------------------------------------------------

def test_check_record_correct_and_incorrect():
    assert check_record("api_key = sk-" + "x" * 20, "SECRET_PRESENT").objective_label == "CORRECT"
    assert check_record("api_key = sk-" + "x" * 20, "CLEAN").objective_label == "INCORRECT"
    assert check_record("read from env at runtime", "CLEAN").objective_label == "CORRECT"
    assert check_record("read from env at runtime", "SECRET_PRESENT").objective_label == "INCORRECT"


def test_check_record_rejects_bad_candidate_answer():
    import pytest
    with pytest.raises(ValueError):
        check_record("text", "MAYBE")


# --- reused ops/secret_audit patterns stay in sync ------------------------------------------

def test_reused_secret_audit_patterns_are_in_sync():
    """The detector REPLICATES (does not import) two load-bearing regexes from ops/secret_audit.py.
    Assert the exact source strings still appear in that file, so a drift there is caught here."""
    audit_src = (ROOT / "ops" / "secret_audit.py").read_text(encoding="utf-8")
    # api-key-ish dashed ALL-CAPS token
    assert r"[A-Z0-9]{2,}-(?:LIT|SECRET|PRIV|TOKEN)-[A-Z0-9]{2,}" in audit_src
    # sk-/pk-/ghp-/gho-/xox* provider key
    assert r"(?:sk|pk|ghp|gho|xox[baprs])-[A-Za-z0-9]{16,}" in audit_src
    # private-key-file marker (needle assembled so this line stays clean under ops/secret_audit)
    assert ("id_" + "ed25519") in audit_src


# --- full fixture sweep ---------------------------------------------------------------------

def test_all_fixtures_detector_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["text"], fx["candidate_answer"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"detector/fixture disagreement: {mismatches}"


def test_failure_class_semantics_match_detector():
    """secret-bearing classes must actually contain a detectable secret; false_positive/none must be clean."""
    for fx in FIXTURES:
        has = contains_secret(fx["text"])
        if fx["failure_class"] in ("false_positive", "none"):
            assert not has, f"{fx['id']} is {fx['failure_class']} but detector found a secret"
        else:
            assert has, f"{fx['id']} is {fx['failure_class']} but detector found no secret"


# --- structural tests -----------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 18 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "api_key_missed", "private_key_marker", "bearer_token", "high_entropy_token",
        "false_positive", "none",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    import json

    by_text = {}
    for fx in FIXTURES:
        by_text.setdefault(json.dumps(fx["text"], sort_keys=True), []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_text[json.dumps(fx["text"], sort_keys=True)]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


# --- end-to-end lane run --------------------------------------------------------------------

def test_run_promotes_all_and_quarantine_empty():
    import evals.objective_secret_detection.run_secret as run_mod
    manifest = run_mod.run()
    assert manifest["hard_gold"] == len(FIXTURES)
    assert manifest["quarantine"] == 0
    assert manifest["label_dist"].get("CORRECT", 0) >= 8
    assert manifest["label_dist"].get("INCORRECT", 0) >= 8


def test_hard_gold_carries_objective_label():
    import json

    import evals.objective_secret_detection.run_secret as run_mod
    run_mod.run()
    lane_dir = Path(run_mod.__file__).parent
    rows = [json.loads(line) for line in
            (lane_dir / "hard_gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    for r in rows:
        assert r["objective_label"] in ("CORRECT", "INCORRECT")
        assert r["label_authority"] == "regex_entropy_secret_detector"
