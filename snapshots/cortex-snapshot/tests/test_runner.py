"""RED tests (GLM-5.2, panel 2026-07-09) for P2 unified runner. Imports adapted to
cortex_core (the plan's brain invariant); logic verbatim from GLM's TDD."""
import httpx
from cortex_core.runner import run_eval
from fakes import FakeModel


def test_runner_aggregates_only_third_party_excludes_fable():
    sb = run_eval(cases=[("fable", "q", "a"), ("third_party", "q", "a")],
                  lanes=["third_party"], model=FakeModel({"q": "a"}))
    assert all(r.lane == "third_party" for r in sb.rows) and len(sb.rows) == 1


def test_runner_scoreboard_row_has_nonnull_ci():
    sb = run_eval(cases=[("third_party", "q", "a")] * 30,
                  lanes=["third_party"], model=FakeModel({"q": "a"}))
    assert all(r.ci_low is not None and r.ci_high is not None for r in sb.rows)


def test_runner_offline_with_fake_model_no_network(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network")))
    sb = run_eval(cases=[("third_party", "q", "a")] * 10,
                  lanes=["third_party"], model=FakeModel({"q": "a"}))
    assert len(sb.rows) == 1
