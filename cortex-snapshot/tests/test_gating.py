"""RED tests (GLM-5.2, panel 2026-07-09) for P3 third-party-only gating."""
import json

from cortex_core.gating import decide, GateDecision  # noqa: F401


def test_gating_fable_pass_third_party_fail_returns_pass_false():
    sb = {"third_party": {"accuracy": 0.4, "abstain": 0.0, "parse_failure": 0.0},
          "fable": {"accuracy": 1.0}}
    assert decide(sb, bar=0.8, caps={"abstain": 0.1, "parse_failure": 0.1}).pass_ is False


def test_gating_bar_read_from_bars_json(tmp_path):
    bars = tmp_path / "bars.json"
    bars.write_text(json.dumps({"default": 0.85}))
    sb = {"third_party": {"accuracy": 0.84, "abstain": 0.0, "parse_failure": 0.0}}
    assert decide(sb, bars_path=str(bars), caps={"abstain": 0.1, "parse_failure": 0.1}).pass_ is False


def test_gating_abstain_over_cap_fails():
    sb = {"third_party": {"accuracy": 0.95, "abstain": 0.5, "parse_failure": 0.0}}
    assert decide(sb, bar=0.8, caps={"abstain": 0.1, "parse_failure": 0.1}).pass_ is False
