"""Phase 5 gate 5.2: the scope pack builder.

Assertions target the gate's contract, not incidental output:
  - the pack never exceeds its TOKEN budget,
  - items are deduped by (path, chunk) and ranked by score,
  - the token estimate is a chars/4 estimate, not a character cap,
  - measure_context_cut reports a real cut vs. serving whole documents.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cortex_core import packs
from cortex_core.search import CortexSearchIndex


def _make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library" / "search").mkdir(parents=True)
    (ws / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    # A handful of docs so the retrieval has real, differently-sized candidates.
    docs = ws / "docs" / "cortex-1"
    docs.mkdir(parents=True)
    (docs / "ssrf.md").write_text(
        "# SSRF guard\n\n"
        + ("The fetch path pins the connection to the validated global IP to "
           "defeat DNS rebinding. " * 40),
        encoding="utf-8",
    )
    (docs / "chunking.md").write_text(
        "# Chunking\n\n"
        + ("Documents are chunked and indexed into FTS5 for BM25 retrieval. " * 40),
        encoding="utf-8",
    )
    (docs / "vector.md").write_text(
        "# Vector leg\n\n"
        + ("Dense retrieval fuses with BM25 via reciprocal rank fusion. " * 40),
        encoding="utf-8",
    )
    (ws / "audit" / "audit-log-1" / "agent").mkdir(parents=True)
    (ws / "audit" / "audit-log-1" / "agent" / "c1.md").write_text(
        "# Closeout\n\nFixed the SSRF pinning regression; added a rebinding test.",
        encoding="utf-8",
    )
    return ws


def test_estimate_tokens_is_an_estimate_not_a_char_cap():
    """The gate's pitfall: the budget is tokens, ~chars/4 -- not raw characters."""
    assert packs.estimate_tokens("") == 1  # floored, never zero
    assert packs.estimate_tokens("a" * 400) == 100
    assert packs.estimate_tokens("word " * 100) == 125


def test_pack_never_exceeds_token_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    for budget in (200, 500, 1000):
        pack = packs.build_scope_pack("SSRF DNS rebinding IP pinning", workspace=ws, token_budget=budget)
        assert pack["tokens_used"] <= budget
        assert sum(it["tokens"] for it in pack["items"]) == pack["tokens_used"]
        assert pack["n_items"] == len(pack["items"])


def test_pack_dedupes_and_ranks_by_score(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    pack = packs.build_scope_pack("SSRF rebinding", workspace=ws, token_budget=4000)
    # The dedupe key is (path, chunk_index): the same chunk is never served
    # twice, but distinct chunks of one doc legitimately can be (and are carried
    # with their chunk_index so a consumer can tell them apart -- not dupes).
    keys = [(it["ref"], it["chunk_index"]) for it in pack["items"]]
    assert len(keys) == len(set(keys))
    scores = [it["retrieval_score"] for it in pack["items"]]
    assert scores == sorted(scores, reverse=True)  # output ordered by its own score


def test_higher_relevance_gets_higher_score(tmp_path, monkeypatch):
    """Non-tautological ranking check (review MED-2): the score must reflect
    retrieval quality, not merely be internally sorted. Two docs sharing a
    distinctive term, one matching it far more densely -- the denser match must
    carry the higher retrieval_score. This fails if the BM25/RRF sign
    normalization were inverted (which the old `sorted()`-only assertion missed).
    A nonsense term keeps it deterministic and free of vector-threshold quirks."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    shard = ws / "docs" / "cortex-1"
    (shard / "dense.md").write_text("# Dense\n\n" + ("zephyrquux " * 30), encoding="utf-8")
    (shard / "sparse.md").write_text("# Sparse\n\nzephyrquux appears once here.\n", encoding="utf-8")
    pack = packs.build_scope_pack("zephyrquux", workspace=ws, token_budget=8000)
    by_ref = {it["ref"]: it["retrieval_score"] for it in pack["items"]}
    dense = next(s for r, s in by_ref.items() if r.endswith("dense.md"))
    sparse = next(s for r, s in by_ref.items() if r.endswith("sparse.md"))
    assert dense > sparse  # denser lexical match ranks higher -> sign is correct


def test_greedy_pack_skips_over_budget_item_and_keeps_scanning(monkeypatch):
    """Review MED-3: the skip-and-continue behaviour, tested deterministically on
    the pure helper -- a high-scoring BIG item is skipped while a lower-scoring
    SMALL item that fits the leftover budget is still admitted."""
    def _item(ref, score, tokens):
        return packs.PackItem(
            kind="doc", ref=ref, chunk_index=0, title=ref, snippet="",
            content="x", retrieval_score=score, tokens=tokens,
        )
    big = _item("big.md", 10.0, 100)   # highest score, over budget
    small = _item("small.md", 5.0, 20)  # lower score, fits
    packed, used = packs._greedy_pack([big, small], token_budget=50)
    refs = [it.ref for it in packed]
    assert "big.md" not in refs  # skipped for size despite ranking first
    assert "small.md" in refs    # admitted after the skip -- scanning continued
    assert used == 20


def test_measure_context_cut_clears_the_50pct_gate(tmp_path, monkeypatch):
    """Review MED-4: the denominator is ALL candidate docs (the dump-everything
    baseline), not just packed ones. With big candidate docs and a tight budget
    the cut must clear the gate's >=50% bar."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    m = packs.measure_context_cut("SSRF DNS rebinding IP pinning", workspace=ws, token_budget=300)
    assert m["full_docs_tokens"] >= m["pack_tokens"]
    assert 0.0 <= m["context_cut_fraction"] <= 1.0
    assert m["candidates_considered"] >= 2  # denominator spans multiple docs
    assert m["context_cut_fraction"] >= 0.5  # the gate 5.2 bar


def test_pack_items_carry_provenance(tmp_path, monkeypatch):
    """Every packed item says what it is and why it's there (kind + score) --
    the whole point vs. dumping raw text."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    pack = packs.build_scope_pack("SSRF rebinding", workspace=ws, token_budget=4000)
    assert pack["items"], "expected at least one hit"
    for it in pack["items"]:
        assert it["kind"] in {"pattern", "doc", "closeout"}
        assert it["ref"]
        assert isinstance(it["retrieval_score"], float)
        assert it["tokens"] >= 1
    assert "escalation" in pack  # the always-granted larger-budget escape hatch


# --- Phase 5.4: the escalation loop -------------------------------------------


def _write_events(ws: Path, events: list[dict]) -> None:
    log_path = ws / "logs" / "mcp-events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


def test_escalation_sli_computes_rate_and_surfaces_reasons(tmp_path, monkeypatch):
    """Gate 5.4: the SLI counts escalations / scope-pack requests and RETURNS the
    reasons (the curriculum), never just a bare count."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_events(
        ws,
        [
            {"tool": "cortex_scope_pack", "task": "a", "escalated": False},
            {"tool": "cortex_scope_pack", "task": "b", "escalated": False},
            {"tool": "cortex_scope_pack", "task": "c", "escalated": True,
             "escalation_reason": "top item alone blew the 2k budget", "ts": "t1", "tokens_used": 8000},
            {"tool": "cortex_search", "query": "noise"},  # not a scope-pack event
        ],
    )
    sli = packs.escalation_sli(ws)
    assert sli["scope_pack_requests"] == 3  # the search event is excluded
    assert sli["escalations"] == 1
    assert sli["escalation_rate"] == round(1 / 3, 3)
    assert sli["within_target"] is False  # 1/3 = 33% is over the 20% target
    assert len(sli["reasons"]) == 1
    assert "blew the 2k budget" in sli["reasons"][0]["reason"]


def test_escalation_sli_flags_over_target(tmp_path, monkeypatch):
    """Above the 20% target -> budgets are systematically too small (retune)."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    _write_events(
        ws,
        [
            {"tool": "cortex_scope_pack", "task": "a", "escalated": True, "escalation_reason": "r1"},
            {"tool": "cortex_scope_pack", "task": "b", "escalated": False},
        ],
    )
    sli = packs.escalation_sli(ws)
    assert sli["escalation_rate"] == 0.5
    assert sli["within_target"] is False


def test_escalation_sli_empty_log_is_within_target(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    sli = packs.escalation_sli(ws)
    assert sli["scope_pack_requests"] == 0
    assert sli["escalation_rate"] == 0.0
    assert sli["within_target"] is True


def test_escalation_sli_reads_rotated_sibling(tmp_path, monkeypatch):
    """Review HIGH-1: escalations in the rotated `.1` backup must still count and
    surface. _log_event rotates the event log at 5 MB; reading only the live file
    would silently truncate the rate and lose the pre-roll curriculum."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    ws = _make_ws(tmp_path)
    logs = ws / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "mcp-events.jsonl.1").write_text(  # older window, rotated out
        json.dumps(
            {"tool": "cortex_scope_pack", "task": "old", "escalated": True,
             "escalation_reason": "pre-roll reason", "token_budget": 8000}
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "mcp-events.jsonl").write_text(  # current live file
        json.dumps({"tool": "cortex_scope_pack", "task": "new", "escalated": False}) + "\n",
        encoding="utf-8",
    )
    sli = packs.escalation_sli(ws)
    assert sli["scope_pack_requests"] == 2  # both files counted, not just live
    assert sli["escalations"] == 1
    assert any("pre-roll reason" in r["reason"] for r in sli["reasons"])
    assert sli["reasons"][0]["token_budget"] == 8000  # requested budget, not packed size


def test_mcp_escalation_is_granted_and_logged(tmp_path, monkeypatch):
    """Gate 5.4 end to end: an escalation (a scope-pack call with a reason) is
    always granted (one call, no gate) and always logged with its reason, so the
    SLI can then read it back."""
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)
    from cortex_core.mcp import cortex_scope_pack

    ws = _make_ws(tmp_path)
    out = asyncio.run(
        cortex_scope_pack(
            task="SSRF rebinding",
            workspace=str(ws),
            token_budget=6000,
            escalation_reason="the 2000-token pack cut off the fix section",
        )
    )
    assert out["escalation_granted"] is True  # always granted, no gatekeeper

    sli = packs.escalation_sli(ws)
    assert sli["escalations"] == 1
    assert sli["reasons"][0]["reason"] == "the 2000-token pack cut off the fix section"
