"""Frozen tests for the prompt/artifact registry (cortex_core/registry.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortex_core import registry as R  # noqa: E402


def test_register_versions_autoincrement(tmp_path):
    a1 = R.register("p", "prompt", "content one", author_model="fable", workspace=tmp_path)
    a2 = R.register("p", "prompt", "content two", author_model="haiku", workspace=tmp_path)
    assert a1.version == 1 and a2.version == 2
    assert R.get("p", workspace=tmp_path).version == 2


def test_provenance_is_required(tmp_path):
    with pytest.raises(ValueError):
        R.register("p", "prompt", "x", author_model="", workspace=tmp_path)


def test_unknown_kind_and_tier_rejected(tmp_path):
    with pytest.raises(ValueError):
        R.register("p", "notakind", "x", author_model="m", workspace=tmp_path)
    with pytest.raises(ValueError):
        R.register("p", "prompt", "x", author_model="m", trust_tier="platinum", workspace=tmp_path)


def test_supersede_not_delete(tmp_path):
    R.register("p", "prompt", "v1", author_model="m", workspace=tmp_path)
    R.register("p", "prompt", "v2", author_model="m", workspace=tmp_path)
    assert R.get("p", workspace=tmp_path).version == 2
    R.supersede("p", 2, workspace=tmp_path)
    # v2 still exists in history, but latest non-superseded is v1
    assert R.get("p", workspace=tmp_path).version == 1
    assert len(R.versions("p", workspace=tmp_path)) == 2      # nothing deleted


def test_content_hash_and_provenance_recorded(tmp_path):
    a = R.register("p", "rubric", "some rubric body", author_model="fable",
                   source="calibration/rubrics/x.yaml", trust_tier="weak_candidate_exemplar",
                   workspace=tmp_path)
    assert a.sha and a.author_model == "fable" and a.trust_tier == "weak_candidate_exemplar"
    assert a.source.endswith("x.yaml")


def test_jsonl_is_source_of_truth_index_rebuildable(tmp_path):
    R.register("p", "prompt", "abc", author_model="m", workspace=tmp_path)
    R.register("q", "rubric", "def", author_model="fable", workspace=tmp_path)
    # blow away the derived index; it must rebuild from the committed JSONL
    (tmp_path / "registry" / "registry.sqlite").unlink()
    n = R.rebuild_index(tmp_path)
    assert n == 2
    assert {a.name for a in R.list_artifacts(workspace=tmp_path)} == {"p", "q"}


def test_list_filters_by_kind_and_tier(tmp_path):
    R.register("a", "prompt", "x", author_model="m", trust_tier="unverified", workspace=tmp_path)
    R.register("b", "rubric", "y", author_model="fable", trust_tier="weak_candidate_exemplar", workspace=tmp_path)
    assert {a.name for a in R.list_artifacts(kind="rubric", workspace=tmp_path)} == {"b"}
    assert {a.name for a in R.list_artifacts(trust_tier="unverified", workspace=tmp_path)} == {"a"}
