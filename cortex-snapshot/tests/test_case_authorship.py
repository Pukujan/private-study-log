"""RED tests (authored by GLM-5.2, panel 2026-07-09) for P0 case-authorship stamping.

Guards the vendor-neutrality invariant: ONLY third_party-authored cases may gate model
selection, so misclassifying a Fable-authored case as third_party — or laundering
authorship by renaming a file — is the exact failure these tests prevent. See
docs/EVAL-FLYWHEEL-PLAN.md P0.
"""
from cortex_core.case_authorship import stamp


def test_third_party_path_classified_as_third_party():
    """Guards against mislabeling HF dataset rows as fable (would let vendors gate themselves)."""
    rec = {"path": "evals/hf_datasets/gsm8k/row_42.json", "content": {"q": "2+2", "a": "4"}}
    out = stamp(rec)
    assert out["case_authorship"] == "third_party"
    assert out["source"].endswith("row_42.json")


def test_fable_path_classified_as_single_vendor():
    """Guards against fable rows counting toward third-party gating."""
    rec = {"path": "fables/acme/row_7.md", "content": {"q": "2+2", "a": "4"}}
    assert stamp(rec)["case_authorship"] == "single_vendor_fable"


def test_provenance_hash_ignores_filename():
    """Guards against hash gaming via filename rename."""
    a = stamp({"path": "evals/hf_datasets/x/row_1.json", "content": {"q": "2+2", "a": "4"}})
    b = stamp({"path": "evals/hf_datasets/y/renamed.json", "content": {"q": "2+2", "a": "4"}})
    assert a["provenance_hash"] == b["provenance_hash"]


def test_provenance_hash_changes_with_content():
    """Guards against content-mutation going undetected."""
    a = stamp({"path": "evals/hf_datasets/x/r.json", "content": {"q": "2+2", "a": "4"}})
    b = stamp({"path": "evals/hf_datasets/x/r.json", "content": {"q": "2+2", "a": "5"}})
    assert a["provenance_hash"] != b["provenance_hash"]
