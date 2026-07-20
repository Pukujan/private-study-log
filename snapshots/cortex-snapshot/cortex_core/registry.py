"""Unified prompt / artifact registry (product consolidation).

Cortex's durable, model-independent assets — prompts, rubrics, exemplars, checkers, schemas —
are currently scattered across `cortex_core/research_prompts.py`, `calibration/rubrics/*.yaml`,
judge system prompts, and inline strings. This unifies them into one **versioned, provenance-
stamped, trust-tiered** registry so an asset can be looked up by name, its version history
inspected, and a superseded version retired without deletion (the corpus "supersede-not-delete"
rule). It is where a strong model's durable output (e.g. a Fable-authored rubric) is CAPTURED so
it stays valuable even if that model's access ends.

Storage follows the repo's corpus convention: **`registry/artifacts.jsonl` is the committed
source of truth** (append-only, human-diffable, version-controllable); **`registry/registry.sqlite`
is a derived query index** (gitignored, rebuilt from the JSONL). register() writes both; a stale
or missing index is rebuilt from the JSONL on read.

Trust tiers mirror the lab policy: hard_gold / cross_vendor_synthetic_gold (trainable) >
weak_candidate_exemplar (single-model/Fable-authored, e.g. a captured rubric) > quarantine.
Provenance (author model + source) is mandatory — a registry entry always says who made it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from cortex_core.config import resolve_workspace

KINDS = ("prompt", "rubric", "exemplar", "checker", "schema", "template")
TRUST_TIERS = ("hard_gold", "cross_vendor_synthetic_gold", "weak_candidate_exemplar",
               "gold_candidate", "fable_semi_ground_truth", "non_human_verified",
               "quarantine", "unverified")
# Only these reach a trainable sink; registering at one is the mandatory attestation chokepoint.
TRAINABLE_TIERS = ("hard_gold", "cross_vendor_synthetic_gold")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  name TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
  author_model TEXT NOT NULL, source TEXT, trust_tier TEXT NOT NULL, sha TEXT NOT NULL,
  created_at TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0, metadata TEXT,
  PRIMARY KEY (name, version)
);
CREATE INDEX IF NOT EXISTS idx_name ON artifacts(name);
CREATE INDEX IF NOT EXISTS idx_kind ON artifacts(kind);
"""


@dataclass
class Artifact:
    name: str
    version: int
    kind: str
    content: str
    author_model: str
    source: str
    trust_tier: str
    sha: str
    created_at: str
    superseded: int = 0
    metadata: dict = None

    def asdict(self):
        d = asdict(self)
        return d


def _ws(workspace: Path | str | None) -> Path:
    """Use an explicitly-passed workspace dir as-is; only auto-resolve when none is given."""
    if workspace is not None:
        return Path(workspace)
    return resolve_workspace(None)


def _reg_dir(ws: Path) -> Path:
    d = ws / "registry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jsonl_path(ws: Path) -> Path:
    return _reg_dir(ws) / "artifacts.jsonl"


def _db_path(ws: Path) -> Path:
    return _reg_dir(ws) / "registry.sqlite"


def _connect(ws: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(ws))
    conn.execute("PRAGMA busy_timeout=5000")   # Phase-0 rule: busy_timeout on every connection
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _rows_from_jsonl(ws: Path):
    p = _jsonl_path(ws)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def rebuild_index(ws: Path | None = None) -> int:
    """Rebuild the SQLite query index from the committed JSONL source of truth."""
    ws = _ws(ws)
    conn = _connect(ws)
    conn.execute("DELETE FROM artifacts")
    rows = _rows_from_jsonl(ws)
    conn.executemany(
        "INSERT OR REPLACE INTO artifacts (name,version,kind,content,author_model,source,"
        "trust_tier,sha,created_at,superseded,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(r["name"], r["version"], r["kind"], r["content"], r["author_model"], r.get("source", ""),
          r["trust_tier"], r["sha"], r["created_at"], r.get("superseded", 0),
          json.dumps(r.get("metadata") or {})) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


def _ensure_index(ws: Path):
    if not _db_path(ws).exists():
        rebuild_index(ws)


def assert_attested_for_training(content: str, trust_tier: str, metadata: dict | None,
                                 *, verifier=None, nonce_store=None, now=None,
                                 workspace: Path | None = None) -> None:
    """Mandatory training-export chokepoint — the single authoritative trainable sink. A trainable
    tier (hard_gold / cross_vendor_synthetic_gold) may only be minted when the metadata carries a
    server-signed attestation that verifies (valid signature, right issuer, unexpired, PASSING
    verdict, not replayed) AND binds to this exact content (subject_sha == sha256(content)).
    Non-trainable tiers pass through untouched. Raises PermissionError on a missing / forged /
    expired / replayed / wrong-subject / failing attestation — nothing unattested ever reaches a
    trainable sink. Never-wait: unattested data is still writable, just not at a trainable tier.

    Replay is defended BY DEFAULT here (sol@xhigh P1 #4): when no `nonce_store` is injected, a
    persistent per-workspace store is used so the same attestation can't register the same content
    under many names/tiers. (Fully-atomic DB-uniqueness single-consume is the phased hardening.)"""
    if trust_tier not in TRAINABLE_TIERS:
        return
    att = (metadata or {}).get("attestation")
    if att is None:
        raise PermissionError(
            f"trust_tier {trust_tier!r} is trainable and requires a server-signed attestation in "
            "metadata['attestation'] (never-wait: register at 'non_human_verified' to store it "
            "unattested and usable).")
    if verifier is None:
        from cortex_core.attestation import verify_attestation as verifier
    if nonce_store is None:
        from cortex_core.attestation import NonceStore
        nonce_store = NonceStore(_reg_dir(_ws(workspace)) / "attest_nonces.json")
    import hashlib
    subject_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    ok, reason = verifier(att, expected_subject_sha=subject_sha, nonce_store=nonce_store, now=now)
    if not ok:
        raise PermissionError(
            f"trainable registration refused: attestation not verified ({reason}). A forged / "
            "expired / replayed / wrong-subject / failing attestation can never earn a trainable "
            "tier.")


def register(name: str, kind: str, content: str, *, author_model: str,
             source: str = "", trust_tier: str = "unverified", metadata: dict | None = None,
             workspace: Path | None = None, now: str | None = None,
             attestation_verifier=None, nonce_store=None) -> Artifact:
    """Register a new VERSION of an artifact (auto-incremented per name). Appends to the JSONL
    source of truth and upserts the SQLite index. Provenance (author_model) is required.

    Trainable tiers pass through the mandatory attestation chokepoint
    (`assert_attested_for_training`): nothing unattested reaches a trainable sink."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; known: {KINDS}")
    if trust_tier not in TRUST_TIERS:
        raise ValueError(f"unknown trust_tier {trust_tier!r}; known: {TRUST_TIERS}")
    if not author_model:
        raise ValueError("author_model (provenance) is required — a registry entry must say who made it")
    assert_attested_for_training(content, trust_tier, metadata,
                                 verifier=attestation_verifier, nonce_store=nonce_store,
                                 workspace=workspace)
    ws = _ws(workspace)
    _ensure_index(ws)
    existing = versions(name, ws)
    version = (max((a.version for a in existing), default=0)) + 1
    art = Artifact(name=name, version=version, kind=kind, content=content,
                   author_model=author_model, source=source, trust_tier=trust_tier,
                   sha=_sha(content), created_at=now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   superseded=0, metadata=metadata or {})
    # append to source-of-truth JSONL
    with _jsonl_path(ws).open("a", encoding="utf-8") as f:
        f.write(json.dumps(art.asdict(), ensure_ascii=False) + "\n")
    # upsert index
    conn = _connect(ws)
    conn.execute(
        "INSERT OR REPLACE INTO artifacts (name,version,kind,content,author_model,source,"
        "trust_tier,sha,created_at,superseded,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (art.name, art.version, art.kind, art.content, art.author_model, art.source,
         art.trust_tier, art.sha, art.created_at, 0, json.dumps(art.metadata)))
    conn.commit()
    conn.close()
    return art


def _row_to_art(row) -> Artifact:
    return Artifact(name=row[0], version=row[1], kind=row[2], content=row[3], author_model=row[4],
                    source=row[5], trust_tier=row[6], sha=row[7], created_at=row[8],
                    superseded=row[9], metadata=json.loads(row[10] or "{}"))


_COLS = "name,version,kind,content,author_model,source,trust_tier,sha,created_at,superseded,metadata"


def get(name: str, version: int | None = None, workspace: Path | None = None) -> Artifact | None:
    """Fetch an artifact by name — latest non-superseded version by default, or a specific one."""
    ws = _ws(workspace)
    _ensure_index(ws)
    conn = _connect(ws)
    if version is not None:
        row = conn.execute(f"SELECT {_COLS} FROM artifacts WHERE name=? AND version=?",
                           (name, version)).fetchone()
    else:
        row = conn.execute(
            f"SELECT {_COLS} FROM artifacts WHERE name=? AND superseded=0 "
            "ORDER BY version DESC LIMIT 1", (name,)).fetchone()
    conn.close()
    return _row_to_art(row) if row else None


def versions(name: str, workspace: Path | None = None) -> list:
    ws = _ws(workspace)
    _ensure_index(ws)
    conn = _connect(ws)
    rows = conn.execute(f"SELECT {_COLS} FROM artifacts WHERE name=? ORDER BY version", (name,)).fetchall()
    conn.close()
    return [_row_to_art(r) for r in rows]


def list_artifacts(kind: str | None = None, trust_tier: str | None = None,
                   workspace: Path | None = None) -> list:
    """Latest version of each artifact, optionally filtered by kind / trust_tier."""
    ws = _ws(workspace)
    _ensure_index(ws)
    conn = _connect(ws)
    rows = conn.execute(f"SELECT {_COLS} FROM artifacts ORDER BY name, version").fetchall()
    conn.close()
    latest = {}
    for r in rows:
        a = _row_to_art(r)
        if kind and a.kind != kind:
            continue
        if trust_tier and a.trust_tier != trust_tier:
            continue
        latest[a.name] = a          # last wins == highest version (ordered)
    return sorted(latest.values(), key=lambda a: a.name)


def supersede(name: str, version: int, workspace: Path | None = None) -> bool:
    """Mark a version superseded (never delete). Records the change in the JSONL too."""
    ws = _ws(workspace)
    _ensure_index(ws)
    conn = _connect(ws)
    cur = conn.execute("UPDATE artifacts SET superseded=1 WHERE name=? AND version=?", (name, version))
    conn.commit()
    conn.close()
    if cur.rowcount:
        with _jsonl_path(ws).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"_op": "supersede", "name": name, "version": version,
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    return bool(cur.rowcount)


def _register_if_new(name, kind, content, **kw) -> Artifact | None:
    """Register only if content changed (idempotent seed — no duplicate versions on re-run)."""
    ws = kw.get("workspace")
    latest = get(name, workspace=ws)
    if latest and latest.sha == _sha(content):
        return latest
    return register(name, kind, content, **kw)


def seed_existing(workspace: Path | None = None) -> int:
    """Consolidate the currently-scattered durable assets into the registry (idempotent).

    Captures the Fable-authored rubric domains + the versioned research prompts + the judge
    system prompt — the exact assets worth preserving independent of any model's access.
    """
    ws = _ws(workspace)
    n = 0

    def _try(fn):
        nonlocal n
        try:
            if fn() is not None:
                n += 1
        except Exception:  # noqa: BLE001
            pass

    # Fable-authored rubric domains (authored, not yet calibrated -> weak_candidate_exemplar)
    rdir = ws / "calibration" / "rubrics"
    for f in sorted(rdir.glob("*.v1.yaml")) if rdir.exists() else []:
        domain = f.stem.replace(".v1", "")
        _try(lambda f=f, domain=domain: _register_if_new(
            f"rubric_{domain}", "rubric", f.read_text(encoding="utf-8"),
            author_model="fable", source=str(f.relative_to(ws)),
            trust_tier="weak_candidate_exemplar", workspace=ws, metadata={"domain": domain}))

    # versioned research prompts (v2 = Fable's citation-faithful rewrite)
    try:
        from cortex_core import research_prompts as RP
        for v in ("v1", "v2"):
            author = "fable" if v == "v2" else "cortex"
            _try(lambda v=v, author=author: _register_if_new(
                f"research_frame_{v}", "prompt", RP.frame_prompt("{QUESTION}", v),
                author_model=author, source="cortex_core/research_prompts.py",
                trust_tier="unverified", workspace=ws, metadata={"version": v}))
            _try(lambda v=v, author=author: _register_if_new(
                f"research_summarize_{v}", "prompt",
                RP.summarize_prompt("{EVIDENCE}", {"coverage": 0.0, "corroboration": 0.0,
                                                   "unanswered": []}, v),
                author_model=author, source="cortex_core/research_prompts.py",
                trust_tier="unverified", workspace=ws, metadata={"version": v}))
    except Exception:  # noqa: BLE001
        pass

    # judge system prompt (rubric v1)
    try:
        from cortex_core import judge as J
        if getattr(J, "_SYSTEM_PROMPT", None):
            _try(lambda: _register_if_new(
                "judge_system_rubric_v1", "prompt", J._SYSTEM_PROMPT, author_model="cortex",
                source="cortex_core/judge.py", trust_tier="unverified", workspace=ws))
    except Exception:  # noqa: BLE001
        pass
    return n


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Prompt/artifact registry")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list"); pl.add_argument("--kind"); pl.add_argument("--tier")
    pg = sub.add_parser("get"); pg.add_argument("name"); pg.add_argument("--version", type=int)
    pv = sub.add_parser("versions"); pv.add_argument("name")
    pr = sub.add_parser("register")
    pr.add_argument("name"); pr.add_argument("kind"); pr.add_argument("--content", required=True)
    pr.add_argument("--author", required=True); pr.add_argument("--tier", default="unverified")
    pr.add_argument("--source", default="")
    sub.add_parser("rebuild")
    sub.add_parser("seed")
    a = p.parse_args(argv)
    if a.cmd == "list":
        for art in list_artifacts(a.kind, a.tier):
            print(f"  {art.name:32s} v{art.version} [{art.kind}] tier={art.trust_tier} by={art.author_model}")
    elif a.cmd == "get":
        art = get(a.name, a.version)
        print(json.dumps(art.asdict(), indent=2) if art else f"not found: {a.name}")
    elif a.cmd == "versions":
        for art in versions(a.name):
            flag = " (superseded)" if art.superseded else ""
            print(f"  v{art.version} {art.created_at} sha={art.sha} tier={art.trust_tier}{flag}")
    elif a.cmd == "register":
        art = register(a.name, a.kind, a.content, author_model=a.author, source=a.source, trust_tier=a.tier)
        print(f"registered {art.name} v{art.version} ({art.kind}, tier={art.trust_tier})")
    elif a.cmd == "rebuild":
        print(f"rebuilt index from JSONL: {rebuild_index()} rows")
    elif a.cmd == "seed":
        print(f"seeded {seed_existing()} artifacts into the registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
