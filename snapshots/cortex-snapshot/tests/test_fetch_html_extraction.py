"""Gate 0.4 (partial): fetched HTML is converted to readable text, not stored
as raw markup that would poison the corpus (chunking would index script/style/
nav tags and drown the prose). stdlib-only heuristic extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

from cortex_core.fetch import _html_to_text, _looks_like_html, fetch_document


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "library" / "cortex-library" / "sources").mkdir(parents=True)
    (ws / "cortex.json").write_text(json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8")
    return ws


_SAMPLE_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<title>[2503.13657] Why Do Multi-Agent LLM Systems Fail?</title>
<meta property="og:description" content="We introduce MAST, a taxonomy of 14 failure modes in 3 categories." />
<style>.abstract { color: red; } body { margin: 0 }</style>
<script>window.tracker = function(){ steal(); };</script>
</head><body>
<nav><a href="/">home</a></nav>
<h1>Why Do Multi-Agent LLM Systems Fail?</h1>
<blockquote class="abstract"><p>Despite enthusiasm for MAS, gains are minimal.</p></blockquote>
<script>moreTracking(123)</script>
</body></html>"""


def test_looks_like_html_detects_pages_and_passes_text_through():
    assert _looks_like_html(_SAMPLE_HTML)
    assert not _looks_like_html("# A markdown doc\n\nplain prose")
    assert not _looks_like_html("just some plain text with a < in it")


def test_html_to_text_strips_tags_and_scripts_keeps_prose():
    out = _html_to_text(_SAMPLE_HTML)
    # visible prose survives
    assert "Despite enthusiasm for MAS, gains are minimal." in out
    # title becomes a heading; meta description (the abstract) is surfaced
    assert "Why Do Multi-Agent LLM Systems Fail?" in out
    assert "taxonomy of 14 failure modes" in out
    # HTML tags are gone (the leading '>' on the meta line is a markdown blockquote)
    assert not re.search(r"<[a-zA-Z/][^>]*>", out)
    assert "steal()" not in out and "window.tracker" not in out
    assert "moreTracking" not in out
    assert "margin: 0" not in out and ".abstract" not in out


def test_fetch_converts_html_body(tmp_path):
    ws = _make_workspace(tmp_path)

    def opener(_: str) -> _FakeResponse:
        return _FakeResponse(_SAMPLE_HTML)

    path = fetch_document("https://arxiv.org/abs/2503.13657", "mast", workspace=ws, opener=opener)
    saved = path.read_text(encoding="utf-8")
    assert "Despite enthusiasm for MAS" in saved
    assert "<html" not in saved and "<script" not in saved
    assert "steal()" not in saved
    # frontmatter provenance is still present
    assert "source_url:" in saved


def test_non_html_body_is_untouched(tmp_path):
    ws = _make_workspace(tmp_path)

    def opener(_: str) -> _FakeResponse:
        return _FakeResponse("# Real Markdown\n\nA paragraph with <b>not</b> a full page.")

    path = fetch_document("https://example.com/x.md", "md", workspace=ws, opener=opener)
    saved = path.read_text(encoding="utf-8")
    # a stray inline tag in markdown must not trigger extraction / mangling
    assert "A paragraph with <b>not</b> a full page." in saved


def test_malformed_html_falls_back_not_crash():
    broken = "<html><body><p>unclosed <div> <script>x(</p></body>"
    out = _html_to_text(broken)
    assert "unclosed" in out


def test_title_is_extracted_from_head(tmp_path):
    """Review H1: <title> lives inside <head>; the extractor must still capture
    it (regression -- head used to be skipped, killing the title). Title is
    chosen to NOT appear in the body, so the h1 can't mask the bug."""
    doc = (
        "<!doctype html><html><head>"
        "<title>UNIQUE_TITLE_NOT_IN_BODY_42</title></head>"
        "<body><h1>Different Body Heading</h1><p>Some prose.</p></body></html>"
    )
    out = _html_to_text(doc)
    assert "# UNIQUE_TITLE_NOT_IN_BODY_42" in out
    assert "Some prose." in out


def test_markdown_mentioning_html_is_not_mangled():
    """Review M4: a markdown doc that merely discusses <html>...</html> must NOT
    be detected as HTML (the sniff is anchored at the start of the document)."""
    md = "# Guide\n\nTo make a page use <html> and close it with </html>. Body follows."
    assert not _looks_like_html(md)


def test_unclosed_script_does_not_swallow_the_page_or_leak_js():
    """Review M5/L10: an unclosed <script> must not silently drop the rest of the
    page, and its JS body must not leak through as prose."""
    doc = (
        "<!doctype html><html><body><p>BEFORE_TEXT_KEEP</p>"
        "<script>var secret=steal(); // never closed"
    )
    out = _html_to_text(doc)
    assert "BEFORE_TEXT_KEEP" in out  # content before the bad script survives
    assert "steal()" not in out and "var secret" not in out  # JS not leaked
