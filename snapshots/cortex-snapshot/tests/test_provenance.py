"""RED tests (GLM-5.2, panel 2026-07-09) for P6 provenance/contamination."""
from cortex_core.provenance import contamination_score, exclude_contaminated  # noqa: F401


def test_provenance_contaminated_case_excluded_from_gate():
    cases = [{"id": "c1", "contamination": 0.9}, {"id": "c2", "contamination": 0.1}]
    clean = exclude_contaminated(cases, threshold=0.8)
    assert [c["id"] for c in clean] == ["c2"]
