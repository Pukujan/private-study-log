"""Deep-research pipeline v0 (cortex_core/research.py).

Network is mocked throughout: a fake fetcher writes small markdown docs into the
workspace, so tests exercise select -> fetch -> gather -> cite-check -> report
without touching the wire. Assertions target the design's gates: no silent
truncation, corpus-first evidence, coverage honesty, deterministic selection.
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex_core import research as R


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library" / "search").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    (ws / "research").mkdir(parents=True)
    return ws


_REGISTRY = """sources:
  - url: https://arxiv.org/abs/1111.11111
    title: Dense retrieval paper
    source_type: primary-paper
    trust_tier: T1
    topics: ["dense retrieval embeddings"]
    status: candidate
  - url: https://arxiv.org/abs/2222.22222
    title: Agent memory paper
    source_type: primary-paper
    trust_tier: T1
    topics: ["agent memory temporal knowledge graph"]
    status: candidate
  - url: https://github.com/foo/bar
    title: Some retrieval tool
    source_type: code-impl
    trust_tier: T2
    topics: ["dense retrieval tool"]
    status: candidate
"""


def _write_registry(ws: Path, body: str = _REGISTRY) -> None:
    (ws / "research" / "sources.yaml").write_text(body, encoding="utf-8")


def _fake_fetcher(fetched_urls: list[str]):
    """Returns a fetcher that records the URL and writes a tiny corpus doc."""
    def fetcher(url: str, name: str, workspace) -> Path:
        ws = Path(workspace)
        shard = ws / "docs" / "cortex-1"
        shard.mkdir(parents=True, exist_ok=True)
        path = shard / f"{name}.md"
        body = "dense retrieval embeddings vector" if "1111" in url else "agent memory graph"
        path.write_text(f"# {name}\n\nSource: {url}\n\n{body} content here.\n", encoding="utf-8")
        fetched_urls.append(url)
        return path
    return fetcher


def test_load_registry_parses_candidates(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    reg = R.load_registry(ws)
    assert len(reg) == 3
    assert all(s.status == "candidate" for s in reg)
    assert reg[0].trust_tier in {"T1", "T2"}


def test_load_registry_missing_is_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    assert R.load_registry(ws) == []


def test_select_sources_matches_topic_and_ranks_trust(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    reg = R.load_registry(ws)
    chosen = R.select_sources(reg, ["dense retrieval"], max_sources=10)
    urls = [s.url for s in chosen]
    # both dense-retrieval sources selected; the memory paper is not
    assert "https://arxiv.org/abs/1111.11111" in urls
    assert "https://github.com/foo/bar" in urls
    assert "https://arxiv.org/abs/2222.22222" not in urls
    # T1 ranks before T2 on equal overlap
    assert urls.index("https://arxiv.org/abs/1111.11111") < urls.index("https://github.com/foo/bar")


def test_bounded_fetch_reports_skipped_no_silent_truncation(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    reg = R.load_registry(ws)
    got = []
    res = R.bounded_fetch(reg, ws, max_sources=1, fetcher=_fake_fetcher(got))
    assert len(res["fetched"]) == 1
    assert len(res["skipped"]) == 2  # the two over-budget sources are surfaced
    assert res["failed"] == []
    assert len(got) == 1  # only the one within budget was actually fetched


def test_bounded_fetch_one_bad_source_does_not_sink_run(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    reg = R.load_registry(ws)

    def flaky(url, name, workspace):
        if "2222" in url:
            raise RuntimeError("boom")
        return _fake_fetcher([])(url, name, workspace)

    res = R.bounded_fetch(reg, ws, max_sources=3, fetcher=flaky)
    assert len(res["fetched"]) == 2
    assert len(res["failed"]) == 1
    assert "boom" in res["failed"][0]


def test_run_research_end_to_end_writes_report_and_coverage(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    got = []
    out = R.run_research(
        "How does dense retrieval work?",
        sub_questions=["dense retrieval embeddings", "agent memory graph"],
        topics=["dense retrieval"],
        workspace=ws,
        max_sources=5,
        fetcher=_fake_fetcher(got),
        now="2026-07-04T00:00:00Z",
    )
    report = ws / out["report_path"]
    assert report.is_file()
    body = report.read_text(encoding="utf-8")
    assert "## Coverage" in body
    assert "## Findings by sub-question" in body
    # both sub-questions found supporting chunks from the fetched docs
    assert out["coverage"] == 1.0
    assert out["unanswered"] == []


def test_unanswered_subquestion_is_flagged_not_hidden(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    out = R.run_research(
        "Q",
        sub_questions=["dense retrieval embeddings", "zzzznomatchtopiczzzz88 sentinel"],
        topics=["dense retrieval"],
        workspace=ws,
        max_sources=5,
        fetcher=_fake_fetcher([]),
        now="2026-07-04T00:00:00Z",
    )
    assert "zzzznomatchtopiczzzz88 sentinel" in out["unanswered"]
    assert out["coverage"] < 1.0
    body = (ws / out["report_path"]).read_text(encoding="utf-8")
    assert "UNANSWERED" in body


def test_malformed_registry_is_empty_not_crash(tmp_path, monkeypatch):
    """Review H2: a top-level list/scalar registry must return [], not raise an
    AttributeError that sinks the whole run."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    (ws / "research" / "sources.yaml").write_text(
        "- url: https://a.com\n- url: https://b.com\n", encoding="utf-8"
    )
    assert R.load_registry(ws) == []
    (ws / "research" / "sources.yaml").write_text("just a bare string", encoding="utf-8")
    assert R.load_registry(ws) == []


def test_report_frontmatter_is_valid_yaml(tmp_path, monkeypatch):
    """Review H3: the report header must round-trip through a YAML parser even
    when the question contains colons/quotes."""
    import yaml

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    question = 'Does "hybrid": BM25 + vector, beat pure dense?'
    out = R.run_research(
        question, sub_questions=["dense retrieval embeddings"], topics=["dense retrieval"],
        workspace=ws, fetcher=_fake_fetcher([]), now="2026-07-04T00:00:00Z",
    )
    body = (ws / out["report_path"]).read_text(encoding="utf-8")
    fm = body.split("---", 2)[1]
    parsed = yaml.safe_load(fm)  # must not raise
    assert parsed["question"] == question


def test_duplicate_subquestions_counted_once(tmp_path, monkeypatch):
    """Review M6: the coverage denominator must match the rendered sections."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    out = R.run_research(
        "Q",
        sub_questions=["dense retrieval embeddings", "dense retrieval embeddings"],
        topics=["dense retrieval"], workspace=ws, fetcher=_fake_fetcher([]),
        now="2026-07-04T00:00:00Z",
    )
    body = (ws / out["report_path"]).read_text(encoding="utf-8")
    assert body.count("### dense retrieval embeddings") == 1
    assert "sub-questions: 1" in body


def test_slug_colliding_sources_get_distinct_files(tmp_path, monkeypatch):
    """Review M7: two sources whose titles slugify identically must not overwrite
    each other's corpus doc."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    reg = [
        R.SourceCandidate(url="https://arxiv.org/abs/1111.11111", title="Same Title",
                          source_type="primary-paper", trust_tier="T1"),
        R.SourceCandidate(url="https://arxiv.org/abs/2222.22222", title="Same Title",
                          source_type="primary-paper", trust_tier="T1"),
    ]
    names: list[str] = []

    def recording_fetcher(url, name, workspace):
        names.append(name)
        p = Path(workspace) / "docs" / "cortex-1" / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return p

    res = R.bounded_fetch(reg, ws, max_sources=5, fetcher=recording_fetcher)
    assert len(res["fetched"]) == 2
    assert len(set(names)) == 2  # distinct filenames despite identical titles


def test_frame_question_decomposes_into_subquestions(monkeypatch):
    """v1 framing: Haiku decomposes a broad question into specific sub-questions."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='["Why is dense retrieval important?", "How do embeddings work?"]')]

    with patch("cortex_core.research.Anthropic") as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        mock_instance.messages.create.return_value = mock_response

        subs = R.frame_question("How does retrieval work?")
        # Verify that the returned subs match what Haiku said (JSON parsed correctly)
        assert subs == ["Why is dense retrieval important?", "How do embeddings work?"]


def test_frame_question_gracefully_degrades_on_bad_json():
    """v1 framing: if Haiku returns bad JSON, fall back to original question."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not valid json")]

    with patch("cortex_core.research.Anthropic") as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        mock_instance.messages.create.return_value = mock_response

        subs = R.frame_question("How does retrieval work?")
        # Fallback: return original question if JSON parse fails
        assert subs == ["How does retrieval work?"]


def test_summarize_findings_writes_haiku_prose_with_citations(monkeypatch):
    """v1 summarization: Haiku synthesizes evidence into prose, with citations."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text="### Dense retrieval\nDense retrieval uses embeddings, as shown in `docs/cortex-1/dense.md` (chunk 0). Effective on large corpora."
        )
    ]

    with patch("cortex_core.research.Anthropic") as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        mock_instance.messages.create.return_value = mock_response

        evidence = {"Dense retrieval": [{"path": "docs/cortex-1/dense.md", "chunk_index": 0, "snippet": "Dense retrieval..."}]}
        check = {"answered": ["Dense retrieval"], "total_sub_questions": 1, "corroborated": ["Dense retrieval"]}

        findings = R.summarize_findings(evidence, check)
        assert "Dense retrieval" in findings
        assert "`docs/cortex-1/dense.md`" in findings  # citation preserved
        assert "(chunk 0)" in findings


def test_run_research_v1_frames_and_summarizes(tmp_path, monkeypatch):
    """End-to-end v1: frame question, gather evidence, summarize with Haiku prose."""
    from unittest.mock import MagicMock, patch

    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)

    frame_response = MagicMock()
    frame_response.content = [MagicMock(text='["dense retrieval", "retrieval tools"]')]

    summarize_response = MagicMock()
    summarize_response.content = [MagicMock(
        text="### dense retrieval\nDense retrieval uses embeddings per `docs/cortex-1/dense-retrieval-paper.md` (chunk 0).\n\n### retrieval tools\nTools implement search per `docs/cortex-1/some-retrieval-tool.md` (chunk 0)."
    )]

    with patch("cortex_core.research.Anthropic") as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        mock_instance.messages.create.side_effect = [frame_response, summarize_response]

        out = R.run_research(
            "How does retrieval work?",
            sub_questions=None,
            topics=["dense retrieval"],
            workspace=ws,
            fetcher=_fake_fetcher([]),
            do_frame=True,
            do_summarize=True,
            now="2026-07-04T00:00:00Z",
        )
        body = (ws / out["report_path"]).read_text(encoding="utf-8")
        # Verify v1 markers
        assert "v1" in body
        assert "Haiku" in body
        # Verify framing actually changed the sub-questions (from the framed list)
        assert "dense retrieval" in body
        assert "retrieval tools" in body
        # Verify summarization generated prose with citations (not template listing)
        assert "per `docs/cortex-1/" in body  # Haiku's inline citation format
        assert "(chunk 0)" in body


def test_prompt_versioning_selects_variant():
    """Deep-research prompt versioning: v2 differs from v1 and adds the honesty rules."""
    from cortex_core import research_prompts as RP

    f1 = RP.frame_prompt("How does X work?", "v1")
    f2 = RP.frame_prompt("How does X work?", "v2")
    assert f1 != f2
    assert "non-overlapping" in f2  # v2-specific tightening

    check = {"answered": 1, "total_sub_questions": 2, "corroborated": ["q1"]}
    s1 = RP.summarize_prompt("evidence", check, "v1")
    s2 = RP.summarize_prompt("evidence", check, "v2")
    assert s1 != s2
    # v2 enforces the deep_research.v1 rubric's honesty rules
    assert "UNANSWERED" in s2
    assert "single source" in s2
    assert "FAITHFULNESS" in s2


def test_frame_question_threads_prompt_version():
    """frame_question(prompt_version='v2') must actually send the v2 prompt."""
    from unittest.mock import MagicMock, patch

    captured = {}
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='["a", "b"]')]

    with patch("cortex_core.research.Anthropic") as mock_client:
        inst = MagicMock()
        mock_client.return_value = inst

        def _create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return mock_response

        inst.messages.create.side_effect = _create
        R.frame_question("How does X work?", prompt_version="v2")
    assert "non-overlapping" in captured["prompt"]  # proves v2 prompt was sent


def test_run_research_healthy_run_has_no_needs_sources_gap(tmp_path, monkeypatch):
    """A run whose topics have registry coverage must not report a gap."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    out = R.run_research(
        "How does dense retrieval work?",
        sub_questions=["dense retrieval embeddings"],
        topics=["dense retrieval"],
        workspace=ws,
        max_sources=5,
        fetcher=_fake_fetcher([]),
        now="2026-07-04T00:00:00Z",
    )
    assert out["needs_sources"] is None


def test_run_research_zero_registry_coverage_surfaces_needs_sources(tmp_path, monkeypatch):
    """The gap-surfacing contract: a topic with zero registry overlap must produce a
    structured `needs_sources` signal, not a silently thin report."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    out = R.run_research(
        "What is quantum error correction?",
        sub_questions=["zzz-novel-topic-no-registry-coverage-zzz"],
        topics=["zzz-novel-topic-no-registry-coverage-zzz"],
        workspace=ws,
        max_sources=5,
        fetcher=_fake_fetcher([]),
        now="2026-07-04T00:00:00Z",
    )
    gap = out["needs_sources"]
    assert gap is not None
    assert gap["state"] == "needs_sources"
    assert "zzz-novel-topic-no-registry-coverage-zzz" in gap["uncovered_topics"]
    assert "cortex_register_source" in gap["hint"]


def test_assess_source_gap_returns_none_when_covered():
    reg = [R.SourceCandidate(url="https://a.com", title="A", source_type="x",
                             trust_tier="T1", topics=["dense retrieval"])]
    fetch_result = {"fetched": ["https://a.com"], "failed": [], "skipped": []}
    check = {"unanswered": []}
    assert R.assess_source_gap(["dense retrieval"], reg, fetch_result, check) is None


def test_assess_source_gap_flags_uncovered_topic():
    reg = [R.SourceCandidate(url="https://a.com", title="A", source_type="x",
                             trust_tier="T1", topics=["dense retrieval"])]
    fetch_result = {"fetched": [], "failed": [], "skipped": []}
    check = {"unanswered": ["some sub-question"]}
    gap = R.assess_source_gap(["totally-unrelated-topic"], reg, fetch_result, check)
    assert gap is not None
    assert gap["uncovered_topics"] == ["totally-unrelated-topic"]
    assert gap["unanswered_sub_questions"] == ["some sub-question"]
    assert gap["registry_size"] == 1


# ---- cortex_register_source mechanism (research.register_source) ----------

def test_register_source_persists_and_reloadable(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    result = R.register_source(
        url="https://arxiv.org/abs/9999.99999",
        title="A newly discovered paper",
        topics=["novel discovered topic"],
        trust_tier="T2",
        discovered_via="WebSearch during cortex_deep_research gap",
        workspace=ws,
    )
    assert result["registered"] is True
    reg = R.load_registry(ws)
    urls = [s.url for s in reg]
    assert "https://arxiv.org/abs/9999.99999" in urls
    added = next(s for s in reg if s.url == "https://arxiv.org/abs/9999.99999")
    assert added.trust_tier == "T2"
    assert added.topics == ["novel discovered topic"]
    assert len(reg) == 4  # 3 seeded + 1 newly registered


def test_register_source_dedupes_by_url(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    dup_url = "https://arxiv.org/abs/1111.11111"  # already in _REGISTRY
    result = R.register_source(url=dup_url, title="dup", topics=["x"], workspace=ws)
    assert result["registered"] is False
    assert result["reason"] == "duplicate_url"
    reg = R.load_registry(ws)
    assert len(reg) == 3  # unchanged


def test_register_source_creates_registry_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    assert not (ws / "research" / "sources.yaml").is_file()
    result = R.register_source(
        url="https://example.com/paper", title="First ever source",
        topics=["bootstrap"], workspace=ws,
    )
    assert result["registered"] is True
    reg = R.load_registry(ws)
    assert len(reg) == 1
    assert reg[0].url == "https://example.com/paper"


def test_register_source_rejects_invalid_trust_tier(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    import pytest
    with pytest.raises(ValueError):
        R.register_source(url="https://example.com/x", title="x", topics=[],
                          trust_tier="T99", workspace=ws)


def test_register_source_rejects_ssrf_target(tmp_path, monkeypatch):
    """Reuses fetch.py's SSRF/scheme guard -- a private-network target must be refused,
    never silently persisted into the registry (a source-registration path that skipped
    this guard would itself be a corpus-poisoning vector)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    import pytest
    with pytest.raises(ValueError):
        R.register_source(url="http://127.0.0.1/admin", title="x", topics=[], workspace=ws)
    with pytest.raises(ValueError):
        R.register_source(url="ftp://example.com/x", title="x", topics=[], workspace=ws)
    reg = R.load_registry(ws)
    assert len(reg) == 3  # neither rejected URL made it into the registry


def test_register_source_then_retry_pipeline_finds_it(tmp_path, monkeypatch):
    """The full discover -> register -> retry loop, end to end: a topic with zero
    registry coverage surfaces needs_sources; after cortex_register_source, re-issuing
    the same research call fetches and cites the newly registered source."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    topic = "novel-topic-not-yet-in-registry"

    # 1. First run: zero coverage -> needs_sources gap, nothing fetched for this topic.
    got = []
    first = R.run_research(
        "What about the novel topic?",
        sub_questions=[topic],
        topics=[topic],
        workspace=ws,
        fetcher=_fake_fetcher(got),
        now="2026-07-04T00:00:00Z",
    )
    assert first["needs_sources"] is not None
    assert got == []

    # 2. Agent discovers + registers a source for the gap topic.
    reg_result = R.register_source(
        url="https://arxiv.org/abs/8888.88888",
        title="Novel topic paper",
        topics=[topic],
        trust_tier="T1",
        discovered_via="WebSearch during cortex_deep_research gap",
        workspace=ws,
    )
    assert reg_result["registered"] is True

    # 3. Retry: the pipeline now finds and fetches the newly registered source.
    def fetcher_for_new_source(url, name, workspace):
        wsp = Path(workspace)
        shard = wsp / "docs" / "cortex-1"
        shard.mkdir(parents=True, exist_ok=True)
        path = shard / f"{name}.md"
        path.write_text(f"# {name}\n\n{topic} findings here.\n", encoding="utf-8")
        return path

    second = R.run_research(
        "What about the novel topic?",
        sub_questions=[topic],
        topics=[topic],
        workspace=ws,
        fetcher=fetcher_for_new_source,
        now="2026-07-04T00:01:00Z",
    )
    assert second["needs_sources"] is None
    assert "https://arxiv.org/abs/8888.88888" in second["fetch"]["fetched"]
    assert second["coverage"] == 1.0


def test_no_fetch_mode_is_corpus_first_no_network(tmp_path, monkeypatch):
    """do_fetch=False must never call the fetcher."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    # pre-seed the corpus directly
    shard = ws / "docs" / "cortex-1"
    shard.mkdir(parents=True)
    (shard / "seed.md").write_text("# Seed\n\ndense retrieval embeddings vector search.\n", encoding="utf-8")

    def exploding_fetcher(*a, **k):
        raise AssertionError("fetcher must not be called in --no-fetch mode")

    out = R.run_research(
        "Q", sub_questions=["dense retrieval embeddings"], workspace=ws,
        do_fetch=False, fetcher=exploding_fetcher, now="2026-07-04T00:00:00Z",
    )
    assert out["fetch"]["fetched"] == []
    assert out["coverage"] == 1.0


def test_successful_but_irrelevant_fetch_still_needs_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    topic = "caseos-deadline-provenance"
    _write_registry(ws, f"""sources:
  - url: https://example.com/caseos
    title: CaseOS source
    source_type: vendor
    trust_tier: T2
    topics: ["{topic}"]
    status: candidate
""")

    def irrelevant_fetcher(url, name, workspace):
        path = Path(workspace) / "docs" / "cortex-1" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Bananas\n\nYellow fruit nutrition only.", encoding="utf-8")
        return path

    out = R.run_research(
        "How are CaseOS deadlines proven?",
        sub_questions=["zz-required-deadline-sentinel-zz"],
        topics=[topic], workspace=ws, fetcher=irrelevant_fetcher,
    )

    assert out["fetch"]["fetched"] == ["https://example.com/caseos"]
    assert out["unanswered"] == ["zz-required-deadline-sentinel-zz"]
    assert out["needs_sources"] is not None
    assert out["needs_sources"]["state"] == "needs_sources"


def test_fetch_records_local_path_and_content_hash(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_registry(ws)
    selected = R.select_sources(R.load_registry(ws), ["dense retrieval"], max_sources=1)
    result = R.bounded_fetch(selected, ws, max_sources=1, fetcher=_fake_fetcher([]))

    assert len(result["captured"]) == 1
    capture = result["captured"][0]
    target = ws / capture["corpus_path"]
    assert target.is_file()
    import hashlib
    assert capture["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_research_searches_brain_and_tenant_corpora(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    brain = _make_ws(tmp_path / "brain-root")
    tenant = _make_ws(tmp_path / "tenant-root")
    for ws, name, text in (
        (brain, "brain.md", "CASEOSCOMPOSITE canonical authority"),
        (tenant, "tenant.md", "CASEOSCOMPOSITE tenant matter history"),
    ):
        shard = ws / "docs" / "cortex-1"
        shard.mkdir(parents=True, exist_ok=True)
        (shard / name).write_text(f"# {name}\n\n{text}", encoding="utf-8")

    out = R.run_research(
        "CaseOS composite knowledge",
        sub_questions=["CASEOSCOMPOSITE"],
        workspace=tenant,
        brain_workspace=brain,
        do_fetch=False,
    )
    report = (tenant / out["report_path"]).read_text(encoding="utf-8")

    assert out["coverage"] == 1.0
    assert "brain://" in report
    assert "tenant://" in report
    sources = {c["source"] for c in out["knowledge_coverage"][0]["coverage"]}
    assert {"brain_corpus", "tenant_corpus"} <= sources


def test_explicit_research_workspace_wins_over_ambient_env(tmp_path, monkeypatch):
    explicit = _make_ws(tmp_path / "explicit-root")
    ambient = _make_ws(tmp_path / "ambient-root")
    monkeypatch.setenv("CORTEX_WORKSPACE", str(ambient))

    result = R.register_source(
        url="https://example.com/explicit-source",
        title="Explicit source",
        topics=["explicit routing"],
        workspace=explicit,
    )

    assert result["registered"] is True
    assert (explicit / "research" / "sources.yaml").is_file()
    assert not (ambient / "research" / "sources.yaml").is_file()
