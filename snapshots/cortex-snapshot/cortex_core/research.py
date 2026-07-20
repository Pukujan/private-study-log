"""Deep-research pipeline, v0 + v1.

Design: docs/DEEP-RESEARCH-DESIGN.md + docs/research/v1-haiku-framing-summarization-design.md.
Turns a research question into cited, chunked, retrievable corpus evidence --
bounded, corpus-first, every claim resolving to a fetched source.
- **v0** (no-LLM): caller-supplied sub-questions, retrieval-based consolidation,
  structured template report. Mechanism only; framing/summarization are v1.
- **v1** (Haiku): optional Haiku framing (decompose question), optional Haiku
  summarization (synthesize findings). Model is configurable (default Haiku 4.5).
  Gates and corpus-backing enforcement unchanged from v0.

Reuses existing primitives wholesale -- the SSRF-guarded ``fetch_document``,
the hybrid search index, and the catalog's URL dedupe -- and adds
orchestration + a seed registry + cite-checking on top.

Non-negotiables enforced here (the design's gates):
- **No silent truncation**: sources dropped for budget are returned in
  ``skipped``, never quietly discarded.
- **Corpus-first**: evidence is gathered by searching the corpus after fetch, so
  a claim is only "supported" if a real indexed chunk backs it.
- **Coverage honesty**: a sub-question with zero supporting chunks is reported as
  ``unanswered``, not hidden.
- **Candidate, not truth**: registry entries are candidates, never auto-trusted;
  a claim is only supported by chunks that were actually fetched and indexed.
  (``trust_tier`` is *recorded* on every candidate and used to rank selection;
  weighting it into the corroboration score is a v1 refinement -- v0 counts
  distinct supporting sources, tier-blind. Stated, not glossed.)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from anthropic import Anthropic

from .config import resolve_exact_workspace, resolve_workspace_override
from .fetch import fetch_document
from .knowledge import composite_search
from .research_prompts import frame_prompt, summarize_prompt
from .search import CortexSearchIndex

REGISTRY_REL = "research/sources.yaml"
DEFAULT_MAX_SOURCES = 12
DEFAULT_PER_QUESTION_LIMIT = 5

Fetcher = Callable[[str, str, Any], Path]


@dataclass
class SourceCandidate:
    url: str
    title: str
    source_type: str
    trust_tier: str
    topics: list[str] = field(default_factory=list)
    status: str = "candidate"


@dataclass
class ResearchRun:
    question: str
    sub_questions: list[str]
    topics: list[str]
    fetched: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    report_path: str | None = None


def _slug(text: str, cap: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:cap].strip("-") or "research"


def load_registry(workspace: str | Path | None = None) -> list[SourceCandidate]:
    """Load the seed-source registry (research/sources.yaml). Missing/empty ->
    []: a run can still proceed corpus-first with no new fetches."""
    ws = resolve_workspace_override(workspace)
    path = ws / REGISTRY_REL
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []  # a top-level list/scalar is malformed, not a run-sinking crash (review H2)
    out: list[SourceCandidate] = []
    for entry in data.get("sources", []) or []:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        out.append(
            SourceCandidate(
                url=str(entry["url"]),
                title=str(entry.get("title", entry["url"])),
                source_type=str(entry.get("source_type", "unknown")),
                trust_tier=str(entry.get("trust_tier", "T3")),
                topics=[str(t) for t in (entry.get("topics") or [])],
                status=str(entry.get("status", "candidate")),
            )
        )
    return out


_VALID_TRUST_TIERS = {"T1", "T2", "T3"}


def _dump_source_entry(entry: dict[str, Any]) -> str:
    """Render one registry entry as a YAML list-item block, matching the existing
    ``research/sources.yaml`` list style (surgical text append, not a full re-dump --
    a full ``yaml.safe_dump`` of the whole file would silently drop the file's leading
    comment header, which documents provenance/scope)."""
    text = yaml.safe_dump(entry, default_flow_style=False, sort_keys=False, allow_unicode=True)
    lines = text.rstrip("\n").split("\n")
    out = [("  - " if i == 0 else "    ") + line for i, line in enumerate(lines)]
    return "\n".join(out) + "\n"


def register_source(
    url: str,
    title: str,
    topics: list[str],
    trust_tier: str = "T3",
    discovered_via: str = "",
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Append an agent-discovered source candidate to the SAME seed registry
    (``research/sources.yaml``) that ``select_sources`` already reads from -- so a
    discovered source is a genuinely PERMANENT registry addition (tomorrow's
    ``cortex_deep_research`` run finds it without re-discovery), not a session-scoped
    throwaway list. This is the mechanism half of the agent-assisted source-discovery
    design (see ``docs/research/AGENT-ASSISTED-SOURCE-DISCOVERY-2026-07-07.md``): the
    zero-new-dependency alternative to a paid search-API integration -- a caller with
    WebSearch/WebFetch (e.g. Claude Code) discovers a URL, then hands it here to make
    it a durable registry candidate.

    Validates ``url`` with ``fetch.py``'s existing SSRF/scheme host guard BEFORE it is
    ever persisted -- reused, not reimplemented, so a registration path can never become
    a corpus-poisoning bypass of the guard ``fetch_document`` already enforces. Dedupes
    by URL against the current registry (a no-op re-registration is reported, not an
    error). Raises ``ValueError`` for an invalid ``trust_tier`` or a URL that fails the
    SSRF/scheme guard -- callers (the MCP tool) are expected to catch and surface that,
    not crash.

    Auth is NOT this function's job: it has none, matching every other write primitive
    in this module (``bounded_fetch`` also just writes). The MCP tool wrapping this is
    responsible for owner/admin-gating the call -- a registry anyone could write to is a
    real corpus-poisoning risk (the fable-sources.md pollution lesson, CLAUDE.md)."""
    if trust_tier not in _VALID_TRUST_TIERS:
        raise ValueError(f"trust_tier must be one of {sorted(_VALID_TRUST_TIERS)}, got {trust_tier!r}")

    from .fetch import _default_resolver, _validate_url

    _validate_url(url, _default_resolver)  # raises ValueError on non-http(s) / private-network targets

    ws = resolve_workspace_override(workspace)
    path = ws / REGISTRY_REL
    existing = load_registry(ws)
    if any(s.url == url for s in existing):
        return {"registered": False, "reason": "duplicate_url", "url": url}

    entry = {
        "url": url,
        "title": title or url,
        "source_type": "agent-discovered",
        "trust_tier": trust_tier,
        "topics": [str(t) for t in (topics or [])],
        "added_by": "agent",
        "discovered_via": discovered_via or "",
        "status": "candidate",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    block = _dump_source_entry(entry)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text += "\n"
        if "sources:" not in text:
            text = (text + "\n" if text else "") + "sources:\n"
        text += block
    else:
        text = "sources:\n" + block
    path.write_text(text, encoding="utf-8")
    return {
        "registered": True,
        "url": url,
        "title": entry["title"],
        "trust_tier": trust_tier,
        "topics": entry["topics"],
    }


def _topic_terms(topics: list[str]) -> set[str]:
    terms: set[str] = set()
    for t in topics:
        terms |= {w for w in re.findall(r"[a-z0-9]{3,}", t.lower())}
    return terms


def select_sources(
    registry: list[SourceCandidate], topics: list[str], max_sources: int
) -> list[SourceCandidate]:
    """Pick candidates whose own topics overlap the run's topics, ranked by
    trust_tier (T1 before T2 before T3), capped at max_sources. No topics given
    -> take the whole registry (still capped). Selection is deterministic."""
    want = _topic_terms(topics)
    if want:
        scored = []
        for s in registry:
            overlap = len(_topic_terms(s.topics) & want)
            if overlap:
                scored.append((overlap, s))
        scored.sort(key=lambda p: (-p[0], p[1].trust_tier, p[1].url))
        chosen = [s for _, s in scored]
    else:
        chosen = sorted(registry, key=lambda s: (s.trust_tier, s.url))
    return chosen[: max(0, max_sources)]


def assess_source_gap(
    topics: list[str],
    registry: list[SourceCandidate],
    fetch_result: dict[str, list[str]],
    check: dict[str, Any],
) -> dict[str, Any] | None:
    """Gap-surfacing (mirrors the ``died`` state pattern in ``deep_research.py``: a named,
    structured signal a caller can branch on instead of a silently thin/empty report).

    Triggers when either (a) one or more of the run's ``topics`` has ZERO overlap with any
    registry candidate's own topics -- ``select_sources`` could not have found anything for
    it no matter the cap -- or (b) nothing was fetched this run AND at least one sub-question
    came back unanswered from the corpus. Returns ``None`` when there's no gap (the normal,
    healthy case), never an empty-but-truthy dict, so callers can do a plain ``if gap:``.
    """
    uncovered_topics = [
        t for t in topics
        if _topic_terms([t]) and not any(_topic_terms(s.topics) & _topic_terms([t]) for s in registry)
    ]
    no_fetch = not fetch_result.get("fetched")
    unanswered = check.get("unanswered", [])
    # Any unanswered required question is a source gap. A successfully fetched but irrelevant
    # document must not suppress escalation merely because network I/O happened.
    if not uncovered_topics and not unanswered:
        return None
    return {
        "state": "needs_sources",
        "uncovered_topics": uncovered_topics,
        "unanswered_sub_questions": list(unanswered),
        "registry_size": len(registry),
        "hint": (
            "the seed registry (research/sources.yaml) has no candidates covering these "
            "topics/questions. An agent with WebSearch/WebFetch should discover source URLs "
            "and register them via cortex_register_source(url=..., title=..., topics=[...], "
            "trust_tier=...), then re-issue this research call so the pipeline can fetch and "
            "cite them."
        ),
    }


def bounded_fetch(
    sources: list[SourceCandidate],
    workspace: str | Path | None = None,
    max_sources: int = DEFAULT_MAX_SOURCES,
    fetcher: Fetcher = fetch_document,
) -> dict[str, list[str]]:
    """Fetch up to max_sources candidates into the corpus. Anything beyond the
    cap goes to ``skipped`` (surfaced, never silently dropped -- the design's
    no-silent-truncation gate). A fetch that raises goes to ``failed`` with its
    reason; one bad source never sinks the run."""
    ws = resolve_workspace_override(workspace)
    fetched: list[str] = []
    captured: list[dict[str, str]] = []
    failed: list[str] = []
    # De-dupe by URL first (review L9): a registry can repeat a URL, and there's
    # no point paying the same network round-trip twice in one run.
    unique: list[SourceCandidate] = []
    seen_urls: set[str] = set()
    for s in sources:
        if s.url not in seen_urls:
            seen_urls.add(s.url)
            unique.append(s)
    cap = max(0, max_sources)
    to_try = unique[:cap]
    skipped = [s.url for s in unique[cap:]]
    for s in to_try:
        try:
            # Disambiguate the corpus filename by a short URL hash (review M7):
            # two sources whose titles slugify identically must not overwrite one
            # another's doc (which would also leave the catalog mis-pointed).
            digest = hashlib.sha1(s.url.encode("utf-8")).hexdigest()[:8]
            name = f"{_slug(s.title or s.url)}-{digest}"
            path = Path(fetcher(s.url, name, ws)).resolve()
            path.relative_to(ws.resolve())
            fetched.append(s.url)
            raw = path.read_bytes()
            captured.append({
                "url": s.url,
                "corpus_path": path.relative_to(ws.resolve()).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
        except Exception as exc:  # noqa: BLE001 - a bad source must not sink the run
            failed.append(f"{s.url} :: {type(exc).__name__}: {exc}")
    return {"fetched": fetched, "captured": captured, "failed": failed, "skipped": skipped}


def gather_evidence(
    sub_questions: list[str],
    workspace: str | Path | None = None,
    brain_workspace: str | Path | None = None,
    per_question_limit: int = DEFAULT_PER_QUESTION_LIMIT,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """For each sub-question, retrieve supporting corpus chunks (hybrid search).
    Corpus-first: a sub-question is only 'supported' by chunks that actually
    exist in the index, each carrying its resolvable corpus path."""
    ws = resolve_workspace_override(workspace)
    brain = resolve_exact_workspace(brain_workspace) if brain_workspace is not None else ws
    evidence: dict[str, list[dict[str, Any]]] = {}
    coverage: list[dict[str, Any]] = []
    for q in sub_questions:
        result = composite_search(
            q,
            brain_workspace=brain,
            tenant_workspace=ws,
            limit=per_question_limit,
            include_structured=False,
        )
        coverage.append({"question": q, "coverage": result["coverage"], "gaps": result["gaps"]})
        evidence[q] = [
            {
                "path": (
                    h["relative_path"] if not result["composite"]
                    else f"{h['plane']}://{h['relative_path']}"
                ),
                "corpus_path": h["path"],
                "plane": h["plane"],
                "chunk_index": h["chunk_index"],
                "title": h["title"],
                "snippet": h["snippet"],
            }
            for h in result["results"]
        ]
    return evidence, coverage


def _rel(path: str, ws: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ws.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def cite_check(evidence: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The coverage gate. Reports, per sub-question, how many distinct sources
    back it; flags the unanswered ones. Corroboration = a sub-question backed by
    >= 2 distinct source docs (tier-blind in v0 -- trust_tier weighting is a v1
    refinement, see the module docstring)."""
    answered, unanswered, corroborated = [], [], []
    for q, hits in evidence.items():
        distinct = {h["path"] for h in hits}
        if not distinct:
            unanswered.append(q)
        else:
            answered.append(q)
            if len(distinct) >= 2:
                corroborated.append(q)
    total = len(evidence)
    return {
        "total_sub_questions": total,
        "answered": answered,
        "unanswered": unanswered,
        "corroborated": corroborated,
        "coverage": round(len(answered) / total, 3) if total else 0.0,
        "corroboration": round(len(corroborated) / total, 3) if total else 0.0,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# NOTE: the >=400 diagnostic logger (`_log_llm_error`) + the raw tier-dispatch completion it
# served moved to the PUBLIC-safe cortex_core.model_dispatch module in the 2026-07-14 extraction;
# `_llm_complete` below now delegates the tier branch there.


def _llm_complete(prompt: str, model: str, max_tokens: int, model_override: str | None = None) -> str | None:
    """Model-AGNOSTIC single-shot completion for the optional framing/summarize steps.

    ``model`` is either a judge-tier name resolved from .env (``glm5.2``, ``qwen35b``,
    ``ollama``, ``opencode``, ``ninerouter``, ...) OR a ``claude-*``/``anthropic`` model
    (uses the Anthropic SDK if a key is present). Returns None when no usable model is
    available -- callers then DEGRADE GRACEFULLY (frame -> use the question as-is;
    summarize -> fall back to the template) instead of crashing. This is what makes
    research runnable by ANY agent, not just one with an Anthropic key.

    ``model_override`` (2026-07-10): when set, the tier named by ``model`` supplies only the
    endpoint URL + API key, but the request's ``model`` field is this literal id instead of
    the tier's configured ``cfg.model``. This drives many models that share one endpoint/key
    (e.g. 9router antigravity ``ag/gemini-*``, OpenRouter ``:free`` models, opencode-go
    ``mimo-v2.5``) through the existing retry/backoff/concurrency path without minting a new
    env-var tier per model. No effect on the ``claude-*``/anthropic SDK branch."""
    if model.startswith("claude") or model.startswith("anthropic"):
        try:
            client = Anthropic()  # needs ANTHROPIC_API_KEY; missing -> graceful None below
            msg = client.messages.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception:  # noqa: BLE001 -- no key / transport error -> graceful skip
            return None
    # tier dispatch: GLM / qwen35b / ollama / opencode / ninerouter / prometheus / ...
    # The raw OpenAI-compatible completion (tier->endpoint/key resolution, the reasoning-token
    # FLOOR, cross-process concurrency slot, and the retry/backoff/Retry-After loop that once
    # lived inline here) now lives in the PUBLIC-safe cortex_core.model_dispatch module, so
    # fanout.py / model_probe.py can dispatch WITHOUT importing the private judge module.
    # Behavior is unchanged -- this just sources it from the shim. (2026-07-14 extraction.)
    from . import model_dispatch as _md
    return _md.llm_complete(prompt, model, max_tokens, model_override=model_override)


def _extract_json_list(text: str) -> list | None:
    """Reasoning-model-robust JSON-array extraction (shared parser, see cortex_core.llm_parse)."""
    from .llm_parse import extract_json_list
    return extract_json_list(text)


def frame_question(
    question: str, model: str = "claude-haiku-4-5-20251001", prompt_version: str = "v1"
) -> list[str]:
    """Framing: decompose the question into sub-questions using ANY configured model
    (see ``_llm_complete``). Robust to reasoning-model output (``_extract_json_list``).
    If no model is available OR nothing parses, the run continues with the original
    question -- never crashes, never silently loses a reasoning model's answer."""
    text = _llm_complete(frame_prompt(question, prompt_version), model, max_tokens=800)
    if not text:
        return [question]  # graceful: no framing model -> use the question as-is
    subs = _extract_json_list(text)
    if subs:
        cleaned = [str(s) for s in subs if s]
        if cleaned:
            return cleaned
    return [question]  # fallback: unparseable (even after reasoning-strip) -> question as-is


def summarize_findings(
    evidence: dict[str, list[dict[str, Any]]],
    check: dict[str, Any],
    model: str = "claude-haiku-4-5-20251001",
    prompt_version: str = "v1",
) -> str:
    """Summarization: write the findings section using an LLM, given evidence.
    ``prompt_version`` selects the prompt variant (see research_prompts.py; v2 adds
    citation-faithfulness + explicit UNANSWERED + single-source flagging). Returns
    markdown prose that synthesizes the evidence per sub-question with citations."""
    # Build evidence context for the model
    evidence_str = ""
    for q, hits in evidence.items():
        evidence_str += f"\n## {q}\n"
        if not hits:
            evidence_str += "No supporting evidence found.\n"
        else:
            for h in hits:
                evidence_str += (
                    f"- `{h['path']}` (chunk {h['chunk_index']}): {h['snippet']}\n"
                )
    prompt = summarize_prompt(evidence_str, check, prompt_version)
    text = _llm_complete(prompt, model, max_tokens=2000)
    # graceful: no summarize model available -> empty prose, caller keeps the template report
    return text or ""


def write_report(
    run: ResearchRun,
    evidence: dict[str, list[dict[str, Any]]],
    check: dict[str, Any],
    fetch_result: dict[str, list[str]],
    workspace: str | Path | None = None,
    now: str | None = None,
    findings_section: str | None = None,
) -> Path:
    """Write a structured research report into docs/research/ with citations to
    corpus paths (the fetched, re-verifiable copy -- not bare URLs). If
    findings_section is provided (v1 LLM-written), use that; otherwise render
    the v0 template (rule-based evidence layout)."""
    ws = resolve_workspace_override(workspace)
    out_dir = ws / "docs" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"auto-research__{_slug(run.question)}.md"
    ts = now or _now()
    version = "v1 (Haiku-framed + Haiku-summarized)" if findings_section else "v0"
    lines = [
        "---",
        f"generated_by: cortex-research {version}",
        f"question: {json.dumps(run.question, ensure_ascii=False)}",
        f"generated_at: {json.dumps(ts)}",
        "---",
        "",
        f"# Research: {run.question}",
        "",
        (
            "> Auto-generated by the v1 research pipeline (Haiku framing + summarization). "
            "Findings synthesized by Haiku from corpus evidence; every citation links to the "
            "source chunk. Sources are candidates, not ground truth."
            if findings_section
            else "> Auto-generated by the v0 research pipeline (no-LLM). Evidence is "
            "retrieval-linked to corpus paths; claims are NOT summarized by a model "
            "yet -- read the cited chunks. Sources are candidates, not ground truth."
        ),
        "",
        "## Coverage",
        f"- sub-questions: {check['total_sub_questions']}",
        f"- answered: {len(check['answered'])} ({check['coverage']:.0%})",
        f"- corroborated (>=2 sources): {len(check['corroborated'])} ({check['corroboration']:.0%})",
        f"- fetched this run: {len(fetch_result['fetched'])} | "
        f"failed: {len(fetch_result['failed'])} | "
        f"skipped-for-budget: {len(fetch_result['skipped'])}",
        "",
    ]
    if findings_section:
        lines += ["## Findings", "", findings_section, ""]
    else:
        lines += ["## Findings by sub-question", ""]
        for q in run.sub_questions:
            hits = evidence.get(q, [])
            lines.append(f"### {q}")
            if not hits:
                lines.append("")
                lines.append("**UNANSWERED** -- no supporting evidence in the corpus. "
                             "Widen the source frontier or mark out of scope.")
                lines.append("")
                continue
            for h in hits:
                snippet = h["snippet"].replace("\n", " ").strip()
                lines.append(f"- `{h['path']}` (chunk {h['chunk_index']}): {snippet}")
            lines.append("")
    if fetch_result["failed"]:
        lines += ["## Fetch failures (surfaced, not hidden)", ""]
        lines += [f"- {f}" for f in fetch_result["failed"]] + [""]
    if fetch_result["skipped"]:
        lines += ["## Skipped for budget (no silent truncation)", ""]
        lines += [f"- {u}" for u in fetch_result["skipped"]] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_research(
    question: str,
    sub_questions: list[str] | None = None,
    topics: list[str] | None = None,
    workspace: str | Path | None = None,
    brain_workspace: str | Path | None = None,
    max_sources: int = DEFAULT_MAX_SOURCES,
    per_question_limit: int = DEFAULT_PER_QUESTION_LIMIT,
    fetcher: Fetcher = fetch_document,
    do_fetch: bool = True,
    do_frame: bool = False,
    do_summarize: bool = False,
    frame_model: str = "claude-haiku-4-5-20251001",
    summarize_model: str = "claude-haiku-4-5-20251001",
    now: str | None = None,
) -> dict[str, Any]:
    """Orchestrate a research run: optionally frame question (v1) -> select seeds
    -> bounded fetch -> gather evidence -> cite-check -> optionally summarize
    (v1) -> write report. do_frame=True uses Haiku to decompose the question.
    do_summarize=True uses Haiku to write the findings. do_fetch=False runs
    corpus-first only (no network)."""
    ws = resolve_workspace_override(workspace)
    brain = resolve_exact_workspace(brain_workspace) if brain_workspace is not None else ws
    topics = topics or []

    # v1 framing: decompose question into sub-questions using configured model
    if do_frame and not sub_questions:
        sub_questions = frame_question(question, model=frame_model)
    sub_questions = sub_questions or [question]

    # De-dupe sub-questions, order-preserving (review M6)
    seen_q: set[str] = set()
    sub_questions = [q for q in sub_questions if not (q in seen_q or seen_q.add(q))]
    run = ResearchRun(question=question, sub_questions=sub_questions, topics=topics)

    registry = load_registry(ws)
    fetch_result: dict[str, Any] = {"fetched": [], "captured": [], "failed": [], "skipped": []}
    if do_fetch:
        selected = select_sources(registry, topics, max_sources)
        fetch_result = bounded_fetch(selected, ws, max_sources=max_sources, fetcher=fetcher)
    run.fetched = fetch_result["fetched"]
    run.failed = fetch_result["failed"]
    run.skipped = fetch_result["skipped"]

    evidence, knowledge_coverage = gather_evidence(
        sub_questions, ws, brain, per_question_limit
    )
    check = cite_check(evidence)
    needs_sources = assess_source_gap(topics, registry, fetch_result, check)

    # v1 summarization: write findings using configured model
    findings_section = None
    if do_summarize:
        findings_section = summarize_findings(evidence, check, model=summarize_model)

    report = write_report(
        run, evidence, check, fetch_result, ws, now=now, findings_section=findings_section
    )
    run.report_path = _rel(str(report), ws)
    # The report is a durable local artifact and must be discoverable on the next task without
    # waiting for an unrelated search to notice it.
    tenant_index = CortexSearchIndex(ws)
    if tenant_index.needs_rebuild():
        tenant_index.rebuild()
    return {
        "question": question,
        "report_path": run.report_path,
        "coverage": check["coverage"],
        "corroboration": check["corroboration"],
        "unanswered": check["unanswered"],
        "fetch": fetch_result,
        "knowledge_coverage": knowledge_coverage,
        "needs_sources": needs_sources,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    from .config import make_stdio_encoding_safe

    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex deep-research pipeline v0")
    parser.add_argument("question", help="the research question")
    parser.add_argument(
        "--sub", action="append", default=[], dest="subs",
        help="a sub-question to answer (repeatable); at least one recommended",
    )
    parser.add_argument("--topic", action="append", default=[], dest="topics",
                        help="topic tag(s) to select seed sources by (repeatable)")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--max-sources", type=int, default=DEFAULT_MAX_SOURCES)
    parser.add_argument("--no-fetch", action="store_true",
                        help="corpus-first only: don't fetch new sources")
    parser.add_argument("--frame", action="store_true",
                        help="v1: use a model to decompose the question into sub-questions")
    parser.add_argument("--frame-model", default="claude-haiku-4-5-20251001",
                        help="model for framing stage (default: Haiku 4.5)")
    parser.add_argument("--summarize", action="store_true",
                        help="v1: use a model to write the findings section from evidence")
    parser.add_argument("--summarize-model", default="claude-haiku-4-5-20251001",
                        help="model for summarization stage (default: Haiku 4.5)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    subs = args.subs or (None if args.frame else [args.question])
    result = run_research(
        args.question, subs, topics=args.topics, workspace=args.workspace,
        max_sources=args.max_sources, do_fetch=not args.no_fetch,
        do_frame=args.frame, do_summarize=args.summarize,
        frame_model=args.frame_model, summarize_model=args.summarize_model,
    )
    if args.json:
        print(_json.dumps(result, indent=2))
    else:
        print(f"report: {result['report_path']}")
        print(f"coverage {result['coverage']:.0%} | corroboration {result['corroboration']:.0%} "
              f"| fetched {len(result['fetch']['fetched'])} "
              f"failed {len(result['fetch']['failed'])} skipped {len(result['fetch']['skipped'])}")
        if result["unanswered"]:
            print(f"UNANSWERED ({len(result['unanswered'])}): " + "; ".join(result["unanswered"]))
        if result.get("needs_sources"):
            gap = result["needs_sources"]
            print(f"NEEDS_SOURCES: uncovered topics = {gap['uncovered_topics']}")
            print(f"  {gap['hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
