import pytest

from cortex_core.assurance_result import finalize_assurance_result, overall_verdict, validate_assurance_result


def result():
    return {
        "schema_version": 1,
        "run_id": "run-1",
        "execution_contract_sha256": "a" * 64,
        "success_contract_sha256": "b" * 64,
        "artifact_hashes": {"app.py": "c" * 64},
        "evidence_refs": ["trace:1", "browser:1"],
        "axis_verdicts": {
            "procedure": "PASS",
            "behavior": "PASS",
            "evidence": "PASS",
            "independence": "PASS",
            "repeatability": "PASS",
            "human_acceptance": "PASS",
        },
        "unresolved": [],
    }


def test_all_axes_required_and_pass_means_pass():
    assert validate_assurance_result(result()) == (True, [])
    finalized = finalize_assurance_result(result())
    assert finalized["overall_verdict"] == "PASS"
    assert validate_assurance_result(finalized) == (True, [])


@pytest.mark.parametrize("verdict", ["FAIL", "ABSTAIN", "UNRESOLVED", "ENVIRONMENT_UNAVAILABLE"])
def test_nonpass_axis_cannot_be_averaged_away(verdict):
    row = result()
    row["axis_verdicts"]["behavior"] = verdict
    if verdict != "FAIL":
        row["unresolved"] = ["required evidence or authority is unavailable"]
    assert overall_verdict(row["axis_verdicts"]) == verdict


def test_missing_axis_and_fake_hash_are_invalid():
    row = result()
    del row["axis_verdicts"]["human_acceptance"]
    row["success_contract_sha256"] = "trust me"
    ok, problems = validate_assurance_result(row)
    assert not ok
    assert any("exactly" in p for p in problems)
    assert any("sha256" in p for p in problems)


def test_uncertainty_requires_explanation():
    row = result()
    row["axis_verdicts"]["evidence"] = "UNRESOLVED"
    ok, problems = validate_assurance_result(row)
    assert not ok
    assert any("unresolved explanation" in p for p in problems)


def test_finalize_refuses_invalid_result():
    row = result()
    row["axis_verdicts"]["behavior"] = "MAYBE"
    with pytest.raises(ValueError, match="invalid assurance result"):
        finalize_assurance_result(row)
