"""RED tests (GLM-5.2, panel 2026-07-09) for P7 oracle style-report (off the gate path)."""
import pathlib

from cortex_core.oracle_report import style_score


def test_oracle_report_parrot_candidate_style_score_low():
    assert style_score("parrot", text="parrot parrot parrot") <= 0.3


def test_oracle_report_import_lint_gating_has_no_oracle_report_import():
    src = pathlib.Path("cortex_core/gating.py").read_text(encoding="utf-8")
    assert "oracle_report" not in src


def test_oracle_report_nl_assertion_yields_abstain():
    assert style_score("assert", text="the answer is maybe") <= 0.3
