#!/usr/bin/env python3
"""Post-hoc deterministic skip scorer — DETECTION, never coercion.

Runs AFTER the work. Reads git + a transcript (JSONL) + a receipt store and turns
any skipped discipline into a visible SLI. It refuses NOTHING and blocks NOTHING;
it emits a JSON scorecard. This is the load-bearing piece of "detection over
coercion" (START-HERE.md; reviewed/cortex-redesign-vs-past-learning-*.md §2.5).
There is no LLM anywhere in here — every verdict is a timestamp comparison, a set
intersection, an exit code, or a digest match.

What it computes per run:
  * research_first  -- first search/receipt ts vs first mutation ts.
  * docs_current    -- git-diff files ∩ docs.map.yaml doc-targets (task-typed).
  * verify_evidence -- test/verify events present and passing.
  * closeout        -- run-bound closeout present + digest matches the run.
  * coercion        -- refusal / loop / protocol-only-turn counts (should be ~0;
                       these are the Disease-B signature we watch for in OURSELVES).
  * context_tokens  -- resting + peak token counts.

Transcript event schema (one JSON object per line; unknown fields ignored):
  {"type":"task","ts":..,"run_id":"..","task":"..","task_type":"implementation",
                 "base_ref":"<git ref>","resting_tokens":250}
  {"type":"search","ts":..,"query":"..","receipt_id":".."}      # or tool_call cortex_search
  {"type":"tool_call","ts":..,"name":"cortex_search"}
  {"type":"mutation","ts":..,"path":"cortex_core/foo.py","action":"edit"}
  {"type":"test_run","ts":..,"cmd":"pytest","exit":0}
  {"type":"assistant","ts":..,"tokens":1234,"protocol_only":false}
  {"type":"refusal","ts":..,"reason":".."}
  {"type":"closeout","ts":..,"digest":".."}

Stdlib only. Offline. No install.

CLI:
    python scorer.py --transcript run.jsonl [--receipts receipts.jsonl] \
        [--docs-map ../protocol/docs.map.yaml] [--repo .] [--base <ref>] \
        [--out sli.json]
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_DEFAULT_DOCS_MAP = _HERE.parent / "protocol" / "docs.map.yaml"

# Search-like tool names that count as a research action if seen as a tool_call.
_SEARCH_TOOLS = {
    "cortex_search", "cortex_deep_research", "cortex_scope_pack",
    "cortex-search", "search", "grep", "deep_research",
}
_SEARCH_TYPES = {"search", "deep_research", "scope_pack"}


# --------------------------------------------------------------------------- #
# minimal stdlib YAML-subset parser (no PyYAML dependency)                     #
# --------------------------------------------------------------------------- #

def _scalar(text: str) -> Any:
    s = text.strip()
    if (len(s) >= 2) and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def _looks_like_map(inner: str) -> bool:
    """True if a list-item body is 'key: value' (a map start), not a plain scalar.
    A colon that is inside a leading quote does not count."""
    if inner.startswith(('"', "'")):
        return False
    return ":" in inner


def _strip_comment(line: str) -> str:
    # Remove a trailing ' #...' comment or a full-line '#...'. Quotes in this
    # schema never contain '#', so a naive cut is safe here.
    if line.lstrip().startswith("#"):
        return ""
    hashpos = line.find(" #")
    return line[:hashpos] if hashpos != -1 else line


def parse_simple_yaml(text: str) -> Any:
    items: list[tuple[int, str]] = []
    for raw in text.splitlines():
        raw = _strip_comment(raw).rstrip()
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        items.append((indent, raw.strip()))
    if not items:
        return {}
    value, _ = _parse_block(items, 0, items[0][0])
    return value


def _parse_block(items: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    if items[i][1].startswith("- "):
        seq: list[Any] = []
        while i < len(items) and items[i][0] == indent and items[i][1].startswith("- "):
            inner = items[i][1][2:]
            if _looks_like_map(inner):
                inner_indent = indent + 2
                items[i] = (inner_indent, inner)      # re-home the item's first key
                val, i = _parse_block(items, i, inner_indent)
                seq.append(val)
            else:
                seq.append(_scalar(inner))
                i += 1
        return seq, i
    mp: dict[str, Any] = {}
    while i < len(items) and items[i][0] == indent and not items[i][1].startswith("- "):
        key, _, rest = items[i][1].partition(":")
        key = key.strip()
        rest = rest.strip()
        i += 1
        if rest:
            mp[key] = _scalar(rest)
        elif i < len(items) and items[i][0] > indent:
            child, i = _parse_block(items, i, items[i][0])
            mp[key] = child
        else:
            mp[key] = None
    return mp, i


def load_docs_map(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        val = parse_simple_yaml(p.read_text(encoding="utf-8"))
        return val if isinstance(val, dict) else {}
    except Exception:      # degrade gracefully -- a malformed map is not a crash
        return {}


# --------------------------------------------------------------------------- #
# transcript / receipts loading                                               #
# --------------------------------------------------------------------------- #

def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def _event_ts(ev: dict[str, Any]) -> float | None:
    ts = ev.get("ts")
    return float(ts) if isinstance(ts, (int, float)) else None


# --------------------------------------------------------------------------- #
# individual checks                                                           #
# --------------------------------------------------------------------------- #

def _search_timestamps(events: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for ev in events:
        t = ev.get("type")
        if t in _SEARCH_TYPES:
            ts = _event_ts(ev)
            if ts is not None:
                out.append(ts)
        elif t == "tool_call" and ev.get("name") in _SEARCH_TOOLS:
            ts = _event_ts(ev)
            if ts is not None:
                out.append(ts)
    for r in receipts:
        ts = r.get("ts")
        if isinstance(ts, (int, float)):
            out.append(float(ts))
    return sorted(out)


def _mutation_timestamps(events: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for ev in events:
        if ev.get("type") == "mutation":
            ts = _event_ts(ev)
            if ts is not None:
                out.append(ts)
    return sorted(out)


def check_research_first(events, receipts) -> dict[str, Any]:
    searches = _search_timestamps(events, receipts)
    mutations = _mutation_timestamps(events)
    first_search = searches[0] if searches else None
    first_mutation = mutations[0] if mutations else None
    if first_mutation is None:
        return {"ok": True, "first_search_ts": first_search, "first_mutation_ts": None,
                "detail": "no mutations in this run — nothing could precede a search"}
    if first_search is None:
        return {"ok": False, "first_search_ts": None, "first_mutation_ts": first_mutation,
                "detail": "code was mutated but NO search/receipt was ever recorded"}
    ok = first_search <= first_mutation
    return {"ok": ok, "first_search_ts": first_search, "first_mutation_ts": first_mutation,
            "detail": ("search preceded first mutation" if ok
                       else "first mutation happened BEFORE any search")}


def _changed_files(events, repo: str | None, base: str | None) -> tuple[list[str], str]:
    """Prefer `git diff --name-only <base>`; fall back to transcript mutation
    paths (so the scorer runs with no git and no committed diff)."""
    if repo and base:
        try:
            res = subprocess.run(
                ["git", "-C", repo, "diff", "--name-only", base],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if res.returncode == 0:
                files = [ln.strip().replace("\\", "/")
                         for ln in res.stdout.splitlines() if ln.strip()]
                return files, "git"
        except (OSError, subprocess.SubprocessError):
            pass
    muts = sorted({str(ev.get("path", "")).replace("\\", "/")
                   for ev in events if ev.get("type") == "mutation" and ev.get("path")})
    return muts, "transcript"


def check_docs_current(events, docs_map, task_type, repo, base) -> dict[str, Any]:
    changed, source = _changed_files(events, repo, base)
    no_doc_types = set(docs_map.get("no_doc_task_types") or [])
    if task_type in no_doc_types:
        return {"ok": True, "source": source, "task_type": task_type,
                "detail": f"task_type '{task_type}' is doc-exempt", "missing": [],
                "code_touched": [], "expected_docs": [], "docs_touched": []}
    rules = docs_map.get("rules") or []
    changed_set = set(changed)
    all_targets = {t for r in rules for t in (r.get("doc_targets") or [])}
    code_touched: list[str] = []
    expected: set[str] = set()
    for rule in rules:
        area = rule.get("code_area")
        rtypes = rule.get("task_types")
        if rtypes and task_type not in rtypes:
            continue
        if not area:
            continue
        matched = [f for f in changed if f not in all_targets and fnmatch.fnmatch(f, area)]
        if matched:
            code_touched.extend(matched)
            expected.update(rule.get("doc_targets") or [])
    docs_touched = sorted(t for t in all_targets if t in changed_set)
    missing = sorted(expected - changed_set)
    return {"ok": not missing, "source": source, "task_type": task_type,
            "code_touched": sorted(set(code_touched)), "expected_docs": sorted(expected),
            "docs_touched": docs_touched, "missing": missing,
            "detail": ("all expected doc-targets were touched" if not missing
                       else f"{len(missing)} expected doc-target(s) not updated")}


def check_verify(events, task_type) -> dict[str, Any]:
    runs = [ev for ev in events if ev.get("type") == "test_run"]
    n = len(runs)
    passing = [ev for ev in runs if int(ev.get("exit", 1)) == 0]
    expected = task_type not in ("research-only", "maintenance")
    if not expected:
        return {"ok": True, "expected": False, "n_runs": n, "n_passing": len(passing),
                "detail": f"verify not expected for task_type '{task_type}'"}
    if n == 0:
        return {"ok": False, "expected": True, "n_runs": 0, "n_passing": 0,
                "detail": "no test/verify evidence recorded for an implementation task"}
    ok = len(passing) == n
    return {"ok": ok, "expected": True, "n_runs": n, "n_passing": len(passing),
            "detail": ("all recorded verify runs passed" if ok
                       else "at least one recorded verify run failed")}


def run_digest(events) -> str:
    """Deterministic digest binding a closeout to THIS run: sorted mutations +
    test outcomes. A closeout event may carry a `digest` to be matched against."""
    muts = sorted(f"{ev.get('path')}::{ev.get('action')}"
                  for ev in events if ev.get("type") == "mutation")
    tests = sorted(f"{ev.get('cmd')}::{ev.get('exit')}"
                   for ev in events if ev.get("type") == "test_run")
    payload = json.dumps({"mutations": muts, "tests": tests}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_closeout(events) -> dict[str, Any]:
    closeouts = [ev for ev in events if ev.get("type") == "closeout"]
    present = bool(closeouts)
    expected = run_digest(events)
    digest_matches = None
    if present:
        claimed = closeouts[-1].get("digest")
        digest_matches = (claimed == expected) if claimed is not None else None
    return {"present": present, "expected_digest": expected,
            "digest_matches": digest_matches,
            "detail": ("closeout present" if present
                       else "no closeout yet (scribe generates it post-hoc)")}


def check_coercion(events) -> dict[str, Any]:
    refusals = sum(1 for ev in events if ev.get("type") == "refusal")
    protocol_only = sum(1 for ev in events
                        if ev.get("type") == "assistant" and ev.get("protocol_only"))
    # loop = a run of >= 3 identical consecutive tool_call names.
    loops = 0
    prev = None
    run_len = 0
    for ev in events:
        if ev.get("type") == "tool_call":
            name = ev.get("name")
            if name == prev:
                run_len += 1
            else:
                if run_len >= 3:
                    loops += 1
                prev = name
                run_len = 1
    if run_len >= 3:
        loops += 1
    return {"refusals": refusals, "loops": loops, "protocol_only_turns": protocol_only,
            "detail": "Disease-B signature — should stay ~0 under detection-over-coercion"}


def check_context_tokens(events) -> dict[str, Any]:
    task_ev = next((ev for ev in events if ev.get("type") == "task"), {})
    resting = task_ev.get("resting_tokens")
    token_events = [int(ev["tokens"]) for ev in events
                    if ev.get("type") == "assistant" and isinstance(ev.get("tokens"), (int, float))]
    if resting is None and token_events:
        resting = token_events[0]
    peak = max(token_events) if token_events else None
    return {"resting": resting, "peak": peak}


# --------------------------------------------------------------------------- #
# top-level                                                                    #
# --------------------------------------------------------------------------- #

def resolve_project_paths(project: str | Path | None) -> dict[str, Path | None]:
    """Resolve the per-project record locations. A project is a self-contained
    `projects/<slug>/` folder (see projects/README.md). Records are project-scoped,
    never a global flat dump."""
    if project is None:
        return {"root": None, "receipts": None, "docs_map": None, "closeouts": None}
    root = Path(project)
    per_project_map = root / "docs.map.yaml"
    return {
        "root": root,
        "receipts": root / "audit" / "receipts.jsonl",
        "docs_map": per_project_map if per_project_map.exists() else None,
        "closeouts": root / "audit" / "closeouts",
    }


def score_run(
    transcript: str | Path,
    *,
    project: str | Path | None = None,
    receipts_store: str | Path | None = None,
    docs_map_path: str | Path | None = None,
    repo: str | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    events = load_jsonl(transcript)
    task_ev = next((ev for ev in events if ev.get("type") == "task"), {})
    run_id = task_ev.get("run_id")
    task_type = task_ev.get("task_type", "implementation")
    base = base or task_ev.get("base_ref")

    # Per-project scoping: unless overridden, records come from projects/<slug>/.
    proj = resolve_project_paths(project)
    if receipts_store is None:
        receipts_store = proj["receipts"]
    if docs_map_path is None and proj["docs_map"] is not None:
        docs_map_path = proj["docs_map"]

    # receipts: prefer explicit store; else any receipt lines embedded in transcript.
    receipts: list[dict[str, Any]] = []
    if receipts_store and Path(receipts_store).exists():
        # import lazily so scorer.py works even if receipts.py is absent.
        try:
            sys.path.insert(0, str(_HERE))
            import receipts as _r  # type: ignore
            receipts = _r.load_receipts(receipts_store)
        except Exception:
            receipts = load_jsonl(receipts_store)

    docs_map = load_docs_map(docs_map_path or _DEFAULT_DOCS_MAP)

    research = check_research_first(events, receipts)
    docs = check_docs_current(events, docs_map, task_type, repo, base)
    verify = check_verify(events, task_type)
    closeout = check_closeout(events)
    coercion = check_coercion(events)
    tokens = check_context_tokens(events)

    reasons: list[str] = []
    if not research["ok"]:
        reasons.append("research_first: " + research["detail"])
    if not docs["ok"]:
        reasons.append("docs_current: " + docs["detail"] + f" -> {docs['missing']}")
    if not verify["ok"]:
        reasons.append("verify_evidence: " + verify["detail"])
    skip_detected = bool(reasons)

    return {
        "run_id": run_id,
        "project": str(proj["root"]) if proj["root"] else None,
        "task": task_ev.get("task"),
        "task_type": task_type,
        "research_first": research,
        "docs_current": docs,
        "verify_evidence": verify,
        "closeout": closeout,
        "coercion": coercion,
        "context_tokens": tokens,
        "skip_detected": skip_detected,
        "reasons": reasons,
        "scorer_version": "1",
    }


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Post-hoc deterministic skip scorer.")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--project", help="projects/<slug>/ dir; scopes receipts + docs.map")
    ap.add_argument("--receipts")
    ap.add_argument("--docs-map")
    ap.add_argument("--repo")
    ap.add_argument("--base")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    sli = score_run(
        args.transcript, project=args.project, receipts_store=args.receipts,
        docs_map_path=args.docs_map, repo=args.repo, base=args.base,
    )
    text = json.dumps(sli, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    # Exit 0 ALWAYS. The scorer detects; it never refuses. skip_detected is data,
    # not an error code — nothing downstream is allowed to treat it as a block.
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
