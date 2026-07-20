"""Frozen tests for the non-model-selectable hidden holdout (terra red-team Finding 6, Medium,
`reviewed/oracle-machinery-redteam-terra-2026-07-14.md`).

The bug: the old holdout split bucketed each RECORD by `sha256(content_sha256)[:8] % 1000`,
where `content_sha256` hashes `(case_id, tier, model, trace)` — and `trace` is mostly
model-controlled output, so a model could vary its own wording until the hash landed in
whichever bucket it preferred.

The fix (`evals/live_gen/holdout.py`): holdout membership is a pure function of `case_id`
alone, keyed by a persistent, gitignored, per-lane HMAC secret, computed BEFORE any
generation happens. These tests prove:
  1. assignment depends ONLY on case_id + secret — nothing derived from a model run can move
     a case in or out of holdout.
  2. it's deterministic/stable across repeated calls with the same secret.
  3. a different secret gives a different partition (the secret is load-bearing, not cosmetic).
  4. the roughly-requested fraction lands in holdout over a large case set.
  5. `commit_hash` is order-independent and changes iff the underlying SET changes — an
     auditable commitment without publishing the secret or the raw holdout records.
  6. `load_or_create_secret` persists (same secret on repeated calls against the same lane
     dir) and is written to a file `.gitignore` actually excludes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.live_gen import holdout as H  # noqa: E402

CASE_IDS = [f"case_{i:03d}" for i in range(500)]


# ---- 1 & 2. depends only on case_id + secret, deterministic -----------------------------------

def test_assignment_is_a_pure_function_of_case_id_and_secret():
    secret = "fixed-test-secret"
    a = H.assign_holdout(CASE_IDS, frac=0.1, secret=secret)
    b = H.assign_holdout(CASE_IDS, frac=0.1, secret=secret)
    assert a == b


def test_no_trace_or_model_output_is_part_of_the_assignment_signature():
    # Structural proof: assign_holdout's signature takes only case_ids/frac/secret -- there is
    # no parameter through which a trace, model id, or any live-run artifact could flow in.
    import inspect
    params = set(inspect.signature(H.assign_holdout).parameters)
    assert params == {"case_ids", "frac", "secret"}


def test_reordering_or_duplicating_case_ids_does_not_change_membership():
    secret = "fixed-test-secret"
    a = H.assign_holdout(CASE_IDS, frac=0.1, secret=secret)
    shuffled = list(reversed(CASE_IDS)) + CASE_IDS[:5]  # reordered + duplicated
    b = H.assign_holdout(shuffled, frac=0.1, secret=secret)
    assert a == b


# ---- 3. the secret is load-bearing -------------------------------------------------------------

def test_different_secret_gives_a_different_partition():
    a = H.assign_holdout(CASE_IDS, frac=0.1, secret="secret-one")
    b = H.assign_holdout(CASE_IDS, frac=0.1, secret="secret-two")
    assert a != b


# ---- 4. roughly the requested fraction ---------------------------------------------------------

def test_holdout_fraction_is_approximately_correct_at_scale():
    secret = "fixed-test-secret"
    holdout_set = H.assign_holdout(CASE_IDS, frac=0.10, secret=secret)
    frac_actual = len(holdout_set) / len(CASE_IDS)
    assert 0.05 <= frac_actual <= 0.15  # loose band -- HMAC bucketing, not exact quota


def test_zero_frac_holds_out_nothing_and_one_frac_holds_out_everything():
    secret = "fixed-test-secret"
    assert H.assign_holdout(CASE_IDS, frac=0.0, secret=secret) == set()
    assert H.assign_holdout(CASE_IDS, frac=1.0, secret=secret) == set(CASE_IDS)


# ---- 5. commit_hash is a real, order-independent commitment -------------------------------------

def test_commit_hash_is_order_independent():
    ids = ["c3", "c1", "c2"]
    assert H.commit_hash(ids) == H.commit_hash(list(reversed(ids)))
    assert H.commit_hash(ids) == H.commit_hash(["c1", "c2", "c3"])


def test_commit_hash_changes_when_the_set_changes():
    h1 = H.commit_hash(["c1", "c2", "c3"])
    h2 = H.commit_hash(["c1", "c2", "c3", "c4"])
    assert h1 != h2


def test_commit_hash_does_not_require_the_secret():
    # An auditor with the (public) case_id list and the manifest's commit_sha256 can verify
    # a holdout SET is unchanged run-to-run without ever seeing the secret.
    import inspect
    assert "secret" not in inspect.signature(H.commit_hash).parameters


# ---- 6. secret persistence + gitignore coverage --------------------------------------------------

def test_load_or_create_secret_persists_across_calls(tmp_path):
    s1 = H.load_or_create_secret(tmp_path)
    s2 = H.load_or_create_secret(tmp_path)
    assert s1 == s2
    assert (tmp_path / H.SECRET_FILENAME).exists()


def test_load_or_create_secret_is_unique_per_lane_dir(tmp_path):
    lane_a = tmp_path / "lane_a"
    lane_b = tmp_path / "lane_b"
    lane_a.mkdir()
    lane_b.mkdir()
    assert H.load_or_create_secret(lane_a) != H.load_or_create_secret(lane_b)


def test_gitignore_excludes_the_holdout_secret_file():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".holdout_secret" in gitignore


def test_secret_path_helper_matches_secret_filename(tmp_path):
    assert H.secret_path(tmp_path).name == H.SECRET_FILENAME


# ---- 7. end-to-end: generate.py's holdout split is keyed by the preassigned set, not by ----------
#         anything content/trace-derived (the actual bug this Finding closes)

def test_generate_module_imports_holdout_and_uses_case_id_split():
    src = (ROOT / "evals" / "live_gen" / "generate.py").read_text(encoding="utf-8")
    assert "from evals.live_gen import holdout" in src
    assert "holdout.assign_holdout" in src
    assert 'r["case_id"] in holdout_case_ids' in src
    # the OLD, gameable split must be gone
    assert 'int(r["content_sha256"][:8], 16)' not in src
