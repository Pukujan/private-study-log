"""Frozen checker tests for the SSRF + path-traversal BEHAVIORAL lane (Stage-2 P0).

Proves, per the Stage-2 contract:
  1. Every fixture's secure reference PASSES and vulnerable reference FAILS, specifically at
     the behavioral stage (`ssrf_canary_hit` / `sentinel_leaked`) -- proving the canary
     listener / sentinel file is a real, non-tautological probe.
  2. Every seeded mutation (incl. the two NAMED examples from the durable-artifact plan --
     blocklist-only SSRF filter, normalize-without-containment path traversal) is KILLED.
  3. A legitimate request still succeeds for every candidate that is graded `pass`.
  4. No judge/LLM anywhere in the verdict path.
  5. hard_gold.jsonl / PROMOTION.jsonl / run_manifest.json are internally consistent and
     carry the label field + honest counts the runner produced.

Real network (loopback only) and real filesystem I/O -- these tests are slower than a pure
unit test but still fast (each candidate run is a few hundred ms subprocess call) and fully
local: no Docker, no gravebuster, no external network of any kind.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_ssrf_path_traversal_behavioral import checker as CK  # noqa: E402
from evals.objective_ssrf_path_traversal_behavioral import detector as DT  # noqa: E402
from evals.objective_ssrf_path_traversal_behavioral.fixtures import (  # noqa: E402
    PATH_TRAVERSAL_FIXTURES, SSRF_FIXTURES)

HERE = ROOT / "evals" / "objective_ssrf_path_traversal_behavioral"


# ---- 1. every fixture's secure/vulnerable reference is correctly graded -----------------------

def test_every_ssrf_secure_reference_passes():
    for fx in SSRF_FIXTURES:
        r = CK.check_ssrf(fx, fx["secure_code"])
        assert r.verdict == "pass", (fx["id"], r.asdict())


def test_every_ssrf_vulnerable_reference_fails_at_canary_hit():
    for fx in SSRF_FIXTURES:
        r = CK.check_ssrf(fx, fx["vulnerable_code"])
        assert r.verdict == "fail", (fx["id"], r.asdict())
        assert r.failed_stage == CK.V_SSRF_CANARY_HIT, (fx["id"], r.asdict())


def test_every_path_traversal_secure_reference_passes():
    for fx in PATH_TRAVERSAL_FIXTURES:
        r = CK.check_path_traversal(fx, fx["secure_code"])
        assert r.verdict == "pass", (fx["id"], r.asdict())


def test_every_path_traversal_vulnerable_reference_fails_at_sentinel_leak():
    for fx in PATH_TRAVERSAL_FIXTURES:
        r = CK.check_path_traversal(fx, fx["vulnerable_code"])
        assert r.verdict == "fail", (fx["id"], r.asdict())
        assert r.failed_stage == CK.V_PT_SENTINEL_LEAKED, (fx["id"], r.asdict())


# ---- 2. seeded mutations are killed, including the two NAMED examples -------------------------

def test_all_seeded_mutations_are_killed():
    killed = []
    for fx in SSRF_FIXTURES:
        for m in fx.get("mutations", []):
            r = CK.check_ssrf(fx, m["code"], attack_url=m.get("attack_url"))
            killed.append((fx["id"], m["label"], r.verdict))
            assert r.verdict == "fail", (fx["id"], m["label"], r.asdict())
    for fx in PATH_TRAVERSAL_FIXTURES:
        for m in fx.get("mutations", []):
            r = CK.check_path_traversal(fx, m["code"], attack_name=m.get("attack_name"))
            killed.append((fx["id"], m["label"], r.verdict))
            assert r.verdict == "fail", (fx["id"], m["label"], r.asdict())
    assert killed, "no mutations were exercised at all -- coverage gap"


def test_blocklist_only_ssrf_filter_named_mutation_is_bypassed_via_localhost_alias():
    fx = next(f for f in SSRF_FIXTURES if f["id"] == "ssrf_fetch_avatar_no_allowlist")
    mut = next(m for m in fx["mutations"] if m["label"] == "blocklist_known_bad_combo_only")
    # the base attack (literal "127.0.0.1") IS blocked by this naive filter -- proves the
    # mutation looks like a real fix, not a no-op
    blocked = CK.check_ssrf(fx, mut["code"])
    assert blocked.verdict == "pass", blocked.asdict()
    # the SAME real canary destination via the "localhost" alias evades it
    bypassed = CK.check_ssrf(fx, mut["code"], attack_url=mut["attack_url"])
    assert bypassed.verdict == "fail" and bypassed.failed_stage == CK.V_SSRF_CANARY_HIT, bypassed.asdict()


def test_normalize_without_containment_named_mutation_is_bypassed_via_dotdot():
    fx = next(f for f in PATH_TRAVERSAL_FIXTURES if f["id"] == "path_traversal_read_doc_no_check")
    mut = next(m for m in fx["mutations"] if m["label"] == "normalize_without_containment")
    r = CK.check_path_traversal(fx, mut["code"])
    assert r.verdict == "fail" and r.failed_stage == CK.V_PT_SENTINEL_LEAKED, r.asdict()


# ---- 3. a legitimate request still works wherever the verdict is pass --------------------------

def test_secure_ssrf_candidates_still_serve_the_legit_target():
    for fx in SSRF_FIXTURES:
        r = CK.check_ssrf(fx, fx["secure_code"])
        assert r.checks.get("legit_fetch_ok") is True, (fx["id"], r.asdict())


def test_secure_path_traversal_candidates_still_serve_in_root_files():
    for fx in PATH_TRAVERSAL_FIXTURES:
        r = CK.check_path_traversal(fx, fx["secure_code"])
        assert r.checks.get("legit_read_ok") is True, (fx["id"], r.asdict())


# ---- 4. no judge/LLM anywhere in the verdict path -----------------------------------------------

def test_no_judge_or_network_client_import_in_checker_module():
    # AST-based (mirrors scripts/ci/lanes.py::module_forbidden_imports), not a substring
    # scan -- checker.py's docstrings and self_test() legitimately CONTAIN the string
    # "urllib.request" inside candidate-code string literals (documenting what a candidate
    # under test does), which is not an import of the checker module itself.
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from lanes import module_forbidden_imports  # noqa: E402
    bad = module_forbidden_imports(HERE / "checker.py")
    assert not bad, f"checker.py imports forbidden {bad}"


def test_no_judge_import_in_detector_module():
    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from lanes import module_forbidden_imports  # noqa: E402
    bad = module_forbidden_imports(HERE / "detector.py")
    assert not bad, f"detector.py imports forbidden {bad}"


# ---- 5. independent AST detector cross-check is structurally distinct --------------------------

def test_ast_detector_flags_every_vulnerable_reference():
    # the crude heuristic is expected to catch the straightforward "no guard at all" shape
    # on every reference that literally has zero guard logic (a stronger claim than merely
    # "not always identical" -- this pins down the specific cases it must never miss).
    misses = []
    for fx in SSRF_FIXTURES + PATH_TRAVERSAL_FIXTURES:
        code = fx["vulnerable_code"]
        if "if" in code:
            continue  # this fixture's vulnerable_code has SOME guard-shaped code; skip
        flagged = (DT.flags_ssrf(code) if fx in SSRF_FIXTURES else DT.flags_path_traversal(code))
        if not flagged:
            misses.append(fx["id"])
    assert not misses, misses


def test_ast_detector_is_not_always_identical_to_the_behavioral_checker():
    # if it always agreed, it would be suspicious (same logic twice); the lane's honest
    # finding is exactly 2 disagreements (see run_manifest.json ast_detector_agreement).
    disagreements = 0
    for fx in SSRF_FIXTURES:
        for code in (fx["vulnerable_code"], fx["secure_code"]):
            r = CK.check_ssrf(fx, code)
            flagged = DT.flags_ssrf(code)
            if flagged != (r.verdict == "fail"):
                disagreements += 1
    assert disagreements > 0
    assert disagreements <= 4  # bounded -- most candidates should still agree


# ---- 6. produced-artifact consistency -----------------------------------------------------------

def test_hard_gold_matches_run_manifest_counts():
    manifest = json.loads((HERE / "run_manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (HERE / "hard_gold.jsonl").read_text(encoding="utf-8")
            .splitlines() if l.strip()]
    assert rows, "hard_gold.jsonl is empty -- run run_ssrf_path_traversal.py first"
    assert len(rows) == manifest["hard_gold"]
    assert all("objective_verdict" in r for r in rows)
    assert manifest["judge_in_verdict_path"] is False
    assert manifest["gravebuster_required"] is False


def test_promotion_record_is_judge_free_and_matches_hard_gold_count():
    rows = [json.loads(l) for l in (HERE / "PROMOTION.jsonl").read_text(encoding="utf-8")
            .splitlines() if l.strip()]
    # Record[0] is the fixture hard_gold promotion (the frozen invariant). Later records are
    # append-only live-gen events (e.g. the 2026-07-15 provisional re-grade of live_hard_gold
    # against the hardened checker) -- every record must remain judge-free, but only the
    # fixture record is count-matched to hard_gold.jsonl.
    assert len(rows) >= 1
    promo = rows[0]
    assert promo["judge_in_verdict_path"] is False
    assert promo["trainable"] is False
    assert promo["label_field"] == "objective_verdict"
    hard_gold_rows = [json.loads(l) for l in (HERE / "hard_gold.jsonl").read_text(encoding="utf-8")
                      .splitlines() if l.strip()]
    assert promo["count"] == len(hard_gold_rows)
    assert all(r.get("judge_in_verdict_path") is False for r in rows)


# ---- 7. FROZEN REGRESSION TESTS (red-team 2026-07-14) ------------------------------------------

def test_leak_during_legit_run_ssrf_candidate_is_caught():
    # HIGH regression: the canary snapshot used to be read right after the ATTACK run only, so
    # a candidate that correctly rejects the attack payload but connects to the canary during
    # the LATER legit request slipped through as "pass".
    fx = next(f for f in SSRF_FIXTURES if f["id"] == "ssrf_fetch_avatar_no_allowlist")
    leak_during_legit = (
        "import urllib.request\n"
        "from urllib.parse import urlparse\n"
        "def fetch_resource(url):\n"
        "    p = urlparse(url)\n"
        "    if p.port == __CANARY_PORT__:\n"
        "        raise ValueError('blocked')\n"
        "    try:\n"
        "        urllib.request.urlopen("
        "'http://__CANARY_HOST__:__CANARY_PORT__/sneaky', timeout=1).read()\n"
        "    except Exception:\n"
        "        pass\n"
        "    return urllib.request.urlopen(url, timeout=3).read()\n"
    )
    r = CK.check_ssrf(fx, leak_during_legit)
    assert r.verdict == "fail", (
        "leak-during-legit-run SSRF candidate NOT caught: " + str(r.asdict()))
    assert r.failed_stage == CK.V_SSRF_CANARY_HIT


def test_read_but_redact_path_traversal_candidate_is_caught():
    # HIGH regression: the sentinel-leak signal used to come ONLY from the entrypoint's return
    # value, so a candidate that reads the real out-of-root sentinel file but discards/redacts
    # the content before returning was graded "pass" despite the real unauthorized file read.
    fx = next(f for f in PATH_TRAVERSAL_FIXTURES if f["id"] == "path_traversal_read_doc_no_check")
    read_but_redact = (
        "import os\n"
        "def read_doc(base, name):\n"
        "    full = os.path.join(base, name)\n"
        "    with open(full, encoding='utf-8') as f:\n"
        "        f.read()\n"
        "    return 'redacted'\n"
    )
    r = CK.check_path_traversal(fx, read_but_redact)
    assert r.verdict == "fail", (
        "read-but-redact path traversal candidate NOT caught: " + str(r.asdict()))
    assert r.failed_stage == CK.V_PT_SENTINEL_LEAKED


def test_quarantine_entries_are_never_silently_dropped_from_hard_gold():
    # detector_disagreement is informational -- the disagreeing candidates must still be
    # present in hard_gold.jsonl (never gated out), matching objective_cwe_patch_execution's
    # tool_disagreement convention.
    quarantine = [json.loads(l) for l in (HERE / "quarantine.jsonl").read_text(encoding="utf-8")
                  .splitlines() if l.strip()]
    hard_gold = [json.loads(l) for l in (HERE / "hard_gold.jsonl").read_text(encoding="utf-8")
                 .splitlines() if l.strip()]
    disagreements = [q for q in quarantine if q["reason"] == "detector_disagreement"]
    assert disagreements, "expected at least one detector_disagreement quarantine"
    flagged_in_hard_gold = sum(1 for r in hard_gold if r.get("detector_disagreement") is True)
    assert flagged_in_hard_gold == len(disagreements)
