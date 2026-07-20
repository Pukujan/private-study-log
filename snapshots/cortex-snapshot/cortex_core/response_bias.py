"""Heuristic bias-signal scanner for Claude/agent response transcripts.

This is intentionally not an LLM judge. It scans observed assistant responses for explicit,
auditable language patterns that often indicate model-blame, Claude-centric framing, circular
validation, or imported governance frames. Prometheus gets counts; the report path can show snippets
for human review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CLAUDE_ROOT = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class BiasRule:
    rule_id: str
    category: str
    severity: str
    description: str
    patterns: tuple[str, ...]


RULES: tuple[BiasRule, ...] = (
    BiasRule(
        "model_blame_weak",
        "model_blame",
        "high",
        "Labels a model/agent as weak instead of naming the observed interface failure.",
        (
            r"\bweak (?:model|models|agent|agents)\b",
            r"\bweaker (?:model|models|agent|agents)\b",
            r"\bcheap (?:model|models)\b",
        ),
    ),
    BiasRule(
        "claude_shaped_frame",
        "claude_centric",
        "medium",
        "Frames success/failure around Claude-specific behavior or non-Claude deviation.",
        (
            r"\bClaude[- ]shaped\b",
            r"\bnon[- ]Claude\b",
            r"\bdoesn'?t behave like Claude\b",
            r"\bimitat(?:e|ing) Claude\b",
        ),
    ),
    BiasRule(
        "unsupported_intent",
        "unsupported_intent",
        "medium",
        "Infers design intent or motive from behavior without an explicit evidence qualifier.",
        (
            r"\bdesigned (?:it|this|the harness|the workflow) (?:specifically )?for\b",
            r"\bintentionally (?:designed|made|built)\b",
            r"\bthe intent was to make\b",
        ),
    ),
    BiasRule(
        "circular_validation",
        "circular_validation",
        "high",
        "Treats self-produced or same-loop evidence as validation.",
        (
            r"\bcircular validation\b",
            r"\bself[- ]?validat(?:e|ed|ion)\b",
            r"\bvalidated (?:itself|against itself)\b",
            r"\bthe model judged itself\b",
        ),
    ),
    BiasRule(
        "over_certainty",
        "over_certainty",
        "low",
        "Uses strong certainty language that should be backed by measured evidence.",
        (
            r"\bobviously\b",
            r"\bclearly\b",
            r"\bdefinitively\b",
            r"\bproves?\b",
            r"\bno doubt\b",
        ),
    ),
    BiasRule(
        "governance_drift",
        "governance_drift",
        "medium",
        "Imports governance/provenance framing that can drift from the user's immediate task.",
        (
            r"\balways[- ]pass theater\b",
            r"\bevidence theater\b",
            r"\bSLSA\b",
            r"\banti[- ]fabrication\b",
            r"\bprovenance\b",
        ),
    ),
)

_COMPILED_RULES = tuple(
    (rule, tuple(re.compile(p, re.IGNORECASE) for p in rule.patterns)) for rule in RULES
)


@dataclass
class BiasHit:
    source: str
    session_id: str
    message_uuid: str
    timestamp: str
    model: str
    slug: str
    category: str
    severity: str
    rule_id: str
    match: str
    snippet: str


@dataclass
class BiasScan:
    root: str
    files_scanned: int = 0
    assistant_messages_scanned: int = 0
    flagged_messages: int = 0
    hits: list[BiasHit] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)
    by_rule: dict[str, int] = field(default_factory=dict)


def discover_transcripts(roots: Iterable[str | Path]) -> list[Path]:
    out: set[Path] = set()
    for rootish in roots:
        root = Path(rootish).expanduser()
        if not root.exists():
            continue
        if root.is_file() and root.suffix.lower() in (".jsonl", ".json"):
            out.add(root)
            continue
        for path in root.rglob("*.jsonl"):
            out.add(path)
    return sorted(out)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return ""


def _assistant_text_from_record(record: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    # Claude Code project transcript shape.
    if record.get("type") == "assistant":
        msg = record.get("message") if isinstance(record.get("message"), dict) else {}
        if msg.get("role") != "assistant":
            return None
        text = _message_text(msg.get("content"))
        meta = {
            "session_id": str(record.get("sessionId") or ""),
            "message_uuid": str(record.get("uuid") or msg.get("id") or ""),
            "timestamp": str(record.get("timestamp") or ""),
            "model": str(msg.get("model") or ""),
            "slug": str(record.get("slug") or ""),
        }
        return text, meta

    # Hermes agent_runner transcript shape; raw is the assistant response from the model.
    if "raw" in record:
        text = str(record.get("raw") or "")
        meta = {
            "session_id": str(record.get("session_id") or ""),
            "message_uuid": str(record.get("turn") if record.get("turn") is not None else ""),
            "timestamp": str(record.get("timestamp") or ""),
            "model": str(record.get("model") or ""),
            "slug": "",
        }
        return text, meta
    return None


def _snippet(text: str, start: int, end: int, width: int = 180) -> str:
    lo = max(0, start - width // 2)
    hi = min(len(text), end + width // 2)
    s = text[lo:hi].replace("\r", " ").replace("\n", " ")
    if lo > 0:
        s = "..." + s
    if hi < len(text):
        s += "..."
    return s


def scan_text(text: str, source: str, meta: dict[str, str]) -> list[BiasHit]:
    hits: list[BiasHit] = []
    if not text.strip():
        return hits
    for rule, patterns in _COMPILED_RULES:
        for pat in patterns:
            for m in pat.finditer(text):
                hits.append(BiasHit(
                    source=source,
                    session_id=meta.get("session_id", ""),
                    message_uuid=meta.get("message_uuid", ""),
                    timestamp=meta.get("timestamp", ""),
                    model=meta.get("model", ""),
                    slug=meta.get("slug", ""),
                    category=rule.category,
                    severity=rule.severity,
                    rule_id=rule.rule_id,
                    match=m.group(0),
                    snippet=_snippet(text, m.start(), m.end()),
                ))
    return hits


def scan_transcript(path: Path) -> tuple[int, list[BiasHit]]:
    messages = 0
    hits: list[BiasHit] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return messages, hits
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        parsed = _assistant_text_from_record(record)
        if parsed is None:
            continue
        text, meta = parsed
        if not text.strip():
            continue
        messages += 1
        hits.extend(scan_text(text, path.as_posix(), meta))
    return messages, hits


def scan_roots(roots: Iterable[str | Path]) -> BiasScan:
    root_label = ",".join(str(Path(r).expanduser()) for r in roots)
    result = BiasScan(root=root_label)
    flagged_keys: set[tuple[str, str]] = set()
    for path in discover_transcripts(roots):
        result.files_scanned += 1
        n_messages, hits = scan_transcript(path)
        result.assistant_messages_scanned += n_messages
        result.hits.extend(hits)
        for hit in hits:
            result.by_category[hit.category] = result.by_category.get(hit.category, 0) + 1
            result.by_rule[hit.rule_id] = result.by_rule.get(hit.rule_id, 0) + 1
            flagged_keys.add((hit.source, hit.message_uuid))
    result.flagged_messages = len(flagged_keys)
    return result


def hit_to_dict(hit: BiasHit) -> dict[str, str]:
    return {
        "source": hit.source,
        "session_id": hit.session_id,
        "message_uuid": hit.message_uuid,
        "timestamp": hit.timestamp,
        "model": hit.model,
        "slug": hit.slug,
        "category": hit.category,
        "severity": hit.severity,
        "rule_id": hit.rule_id,
        "match": hit.match,
        "snippet": hit.snippet,
    }


def scan_to_dict(scan: BiasScan, limit: int = 50) -> dict[str, Any]:
    return {
        "root": scan.root,
        "files_scanned": scan.files_scanned,
        "assistant_messages_scanned": scan.assistant_messages_scanned,
        "flagged_messages": scan.flagged_messages,
        "hits_total": len(scan.hits),
        "by_category": dict(sorted(scan.by_category.items())),
        "by_rule": dict(sorted(scan.by_rule.items())),
        "hits": [hit_to_dict(h) for h in scan.hits[:limit]],
    }


def _escape_metric_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _metric_labels(labels: dict[str, str]) -> str:
    return "{" + ",".join(f'{k}="{_escape_metric_label(v)}"' for k, v in sorted(labels.items())) + "}"


def _metric_line(name: str, labels: dict[str, str], value: int | float) -> str:
    return f"{name}{_metric_labels(labels)} {value}"


def render_prometheus(scan: BiasScan) -> str:
    """Render a scan as Prometheus text exposition.

    Raw snippets are deliberately omitted from metrics; use `scan_to_dict` for human review.
    """
    lines = [
        "# HELP claude_bias_files_scanned Transcript files scanned for assistant responses.",
        "# TYPE claude_bias_files_scanned gauge",
        "# HELP claude_bias_assistant_messages_scanned Assistant text messages scanned.",
        "# TYPE claude_bias_assistant_messages_scanned gauge",
        "# HELP claude_bias_flagged_messages Assistant messages with at least one bias signal.",
        "# TYPE claude_bias_flagged_messages gauge",
        "# HELP claude_bias_signal_hits Bias-signal hits by category/rule/severity/model/session.",
        "# TYPE claude_bias_signal_hits gauge",
    ]
    root_label = {"root": scan.root}
    lines.append(_metric_line("claude_bias_files_scanned", root_label, scan.files_scanned))
    lines.append(_metric_line("claude_bias_assistant_messages_scanned", root_label,
                              scan.assistant_messages_scanned))
    lines.append(_metric_line("claude_bias_flagged_messages", root_label, scan.flagged_messages))
    lines.append(_metric_line("claude_bias_signal_hits_total", root_label, len(scan.hits)))
    for category, count in sorted(scan.by_category.items()):
        lines.append(_metric_line("claude_bias_signal_hits_by_category",
                                  {**root_label, "category": category}, count))
    for rule_id, count in sorted(scan.by_rule.items()):
        lines.append(_metric_line("claude_bias_signal_hits_by_rule",
                                  {**root_label, "rule_id": rule_id}, count))

    grouped: dict[tuple[str, str, str, str, str, str], int] = {}
    for hit in scan.hits:
        key = (
            hit.category,
            hit.severity,
            hit.rule_id,
            hit.model or "unknown",
            hit.session_id or "unknown",
            hit.slug or "",
        )
        grouped[key] = grouped.get(key, 0) + 1
    for (category, severity, rule_id, model, session_id, slug), count in sorted(grouped.items()):
        lines.append(_metric_line("claude_bias_signal_hits", {
            **root_label,
            "category": category,
            "severity": severity,
            "rule_id": rule_id,
            "model": model,
            "session_id": session_id,
            "slug": slug,
        }, count))
    lines.append("")
    return "\n".join(lines)
