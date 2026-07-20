"""The Director cascade — cheap-to-expensive routing for the vague-build harness.

Agreed design (docs/research/director-cascade-actionable-plan-2026-07-10.md + DEEP-RESEARCH-mcp-
surface-and-hybrid-state-engine-2026-07-08.md): "routing-as-data, not a router component." A vague
utterance is routed by a CASCADE, cheapest tier first, escalating only when confidence is low:

    tier 1  deterministic rules         (<1ms)   keyword/shape rules + negation + MULTI-VERB guard
    tier 2  embedding router            (~5ms)   potion-base-8M over the routing log (route_vec index)
    tier 3  nearest-centroid classifier (~10ms)  trained FROM ops-local/routing-log.jsonl
    tier 4  LLM fallback                (~1s)    a FREE model, ONLY below the margin; EVERY use LOGGED

All four tiers now exist. The GLM-5.2 review's required fixes are baked in STRUCTURALLY:

  - FIX #1 (router safety): static embeddings can't tell "build a tracker for research" from
    "research how to build a tracker" (identical bag-of-words). Any utterance with MULTIPLE routing
    verbs or a negation is forced STRAIGHT to tier 4 — tiers 2/3 are never even consulted, so no
    embedding confidence can override the guard (see `direct()`: the guard is checked BEFORE the
    trained tiers, not inside them).
  - FIX #3 (label soundness): a routing decision becomes training data ONLY via an explicit
    human-acceptance record (`record_acceptance`, written by `reaction.confirm()` after a human
    binary). `load_trainable()` never looks at gate outcomes — gate-pass alone trains NOTHING
    (gate-pass selects for weak gates, not correct routes).
  - FIX #5 (stats): seeds are Fable-authored bootstrap examples KEPT until >= SEED_LIVE_FLOOR (50)
    live trainable records exist, then DECAY-BLENDED (distance penalty), never deleted-at-3.
    The exploration floor is a fixed deterministic count (every EXPLORATION_EVERY-th trained-tier
    decision re-routes via tier 4 and logs disagreement; minimum epsilon = 1/EXPLORATION_EVERY,
    and run 0 explores, so exploration exists even below 20 runs).
  - FIX #6 (WRONG_TRACK): this module offers `record_relabel()` as the ONLY way to mark a route
    wrong; it is called exclusively from `reaction.confirm()` (human binary) or a deterministic
    schema mismatch — never from token overlap (see cortex_core/reaction.py `infer_wrong_track`).
  - FIXES #2/#3 (anti-circularity): the router NEVER mutates the skill registry or the training
    set. It only PROPOSES a route + appends to the append-only routing log. Every mutation path
    (acceptance, relabel) lives behind cortex_core/reaction.py's human-confirm queue.

The Director decides the TRACK + which fresh-build skill; follow-on skills are still detected by the
driver. Today there is one track (app_build); the interface generalizes to multi-track routing.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# Routing verbs whose CO-OCCURRENCE flips intent (review fix #1): build vs research vs analyze ...
_ROUTING_VERBS = ("build", "make", "create", "track", "research", "analyze", "analyse", "review",
                  "audit", "summarize", "summarise", "explain", "find", "search", "design")
_NEGATION = re.compile(r"\b(don'?t|do not|no longer|never|without|instead of)\b", re.I)

# --- Tier-2/3 thresholds -------------------------------------------------------------------
# Small-n calibrated, documented, re-tunable — the MAX_VECTOR_DISTANCE discipline (vector.py).
# Distances are L2 over potion-base-8M (or an injected embedder in tests).
T2_MAX_DIST = 0.95        # a live neighbor farther than this cannot decide a route
T2_MARGIN = 0.15          # top-1 must beat the nearest DIFFERENT-label neighbor by this
T2_SEED_MAX_DIST = 0.75   # stricter ceiling when the deciding neighbor is a Fable-authored seed
T2_SEED_MARGIN = 0.25     # stricter margin for seed-decided routes (plan 5.1 cold-start guard)
SEED_LIVE_FLOOR = 50      # fix #5: seeds keep full strength until this many live trainable records
SEED_DECAY_PENALTY = 0.20 # past the floor, seed distances are penalized (decay-blend, never deleted)
T3_MIN_RECORDS = 6        # nearest-centroid needs at least this many live trainable records
T3_MIN_PER_LABEL = 2      # occurrence floor per label (patterns.py discipline)
T3_MARGIN = 0.10          # best centroid must beat second-best by this
EXPLORATION_EVERY = 10    # fix #5: fixed exploration floor — every Nth trained-tier decision goes
                          # to tier 4 anyway (deterministic modulo, min epsilon = 1/N, fires at n=0)

# Fable-authored bootstrap seeds for tier 2 (origin-marked; decay-blended past SEED_LIVE_FLOOR,
# per review fix #5 — NOT deleted at 3 live records). One fresh-build skill exists today, so seeds
# are single-label; multi-label discrimination activates as the registry grows.
SEED_ROUTES: tuple[tuple[str, str], ...] = (
    ("track my clients and who has paid", "scaffold-crud-sqlite"),
    ("a little app to track my expenses", "scaffold-crud-sqlite"),
    ("keep a list of my books and whether i have read them", "scaffold-crud-sqlite"),
    ("store my gym members and their status", "scaffold-crud-sqlite"),
    ("an inventory app for my shop", "scaffold-crud-sqlite"),
    ("save my job applications and their stage", "scaffold-crud-sqlite"),
    ("a database of my recipes", "scaffold-crud-sqlite"),
    ("manage my rental properties and tenants", "scaffold-crud-sqlite"),
    ("log my daily workouts", "scaffold-crud-sqlite"),
    ("keep tabs on invoices and which are overdue", "scaffold-crud-sqlite"),
)
SEED_ORIGIN = "seed_fable_authored"


@dataclass
class Route:
    track: str                       # the track/chart to run (today: "app_build")
    skill_id: str                    # the fresh-build skill selected
    tier_used: int                   # 1 (rules) | 2 (embed) | 3 (centroid) | 4 (llm)
    confidence: float                # 0..1
    features: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    route_id: str = ""               # joins acceptance/relabel records in the routing log


def _routing_log_path(workspace: str | Path | None) -> Path:
    # A passed valid directory is used directly (tests, explicit callers); otherwise resolve the
    # repo root. gitignored ops-local: the routing log is telemetry/training data, NOT committed corpus.
    if workspace is not None and Path(str(workspace)).is_dir():
        root = Path(str(workspace))
    else:
        from cortex_core.config import resolve_workspace
        root = Path(resolve_workspace(None))
    out = root / "ops-local" / "routing-log.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _append_log(record: dict, workspace: str | Path | None) -> None:
    with _routing_log_path(workspace).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_route(route: Route, utterance: str, workspace: str | Path | None = None) -> None:
    """Append one routing decision to the flywheel's data spine. FAIL-OPEN (never breaks a build).
    This is the source tiers 2/3 train FROM; it never mutates the skill registry (review fixes #2/#3)."""
    try:
        rec = {"kind": "route", "utterance": utterance, **asdict(route)}
        rec["ts"] = rec.get("ts") or time.time()
        _append_log(rec, workspace)
    except Exception:  # noqa: BLE001
        pass


#: The only admissible provenances for a negative relabel (terra HIGH #2: `source` used to be
#: a free string, so a caller could stamp "token_overlap" -- the exact heuristic fix #6 killed).
RELABEL_SOURCES = frozenset({"human_feedback", "schema_mismatch"})


def record_acceptance(route_id: str, accepted: bool, *, receipt: str,
                      reaction_class: str | None = None,
                      workspace: str | Path | None = None) -> None:
    """Append a human-acceptance record for a route. THE trainability bit (review fix #3):
    `load_trainable` returns only routes with an accepted=True record whose receipt verifies.

    terra HIGH #2 hardening -- this writer is now UN-FORGEABLE from a model verdict:
      - `accepted` must be a REAL bool. No coercion: `record_acceptance(id, "yes")` used to
        write accepted=true because bool("yes") is True; it now raises TypeError.
      - `receipt` (required, keyword-only) must be a live server-issued human-approval
        capability (`cortex_core.receipts.mint_approval`, the human-console primitive) whose
        decision matches `accepted`. A missing/unknown/mismatched receipt raises
        PermissionError and writes NOTHING.
    The only legitimate caller remains `reaction.confirm()` (the human binary), which passes
    its verified receipt through. `load_trainable` re-verifies the receipt on read, so even a
    hand-appended log line without a real receipt trains nothing."""
    if not isinstance(accepted, bool):
        raise TypeError("record_acceptance: accepted must be a real bool -- an LLM string "
                        "verdict must never reach the training ledger (terra finding #2)")
    from cortex_core import receipts as _receipts
    # terra RE-REVIEW #2: the receipt must be bound to THIS route (subject_id == route_id),
    # not merely to a matching decision. A receipt minted for route X (or a proposal about X)
    # can no longer be replayed to accept route Y.
    # terra RE-REVIEW-2 #2: the receipt must also be UNCONSUMED, and this writer consumes it
    # ATOMICALLY itself -- the receipt is single-use across the WHOLE write path, so calling
    # record_acceptance directly with a spent receipt (or racing two calls on one receipt)
    # writes at most one acceptance record. The confirm()/_apply flow no longer pre-consumes;
    # consumption happens HERE, at the ledger write.
    if _receipts.check_approval(receipt, subject_id=route_id, decision=accepted,
                                require_unconsumed=True, workspace=workspace) is None:
        raise PermissionError(
            "record_acceptance: a live UNCONSUMED server-issued human-approval receipt BOUND "
            "TO THIS ROUTE and matching this decision is required; a model cannot mint one, a "
            "receipt for another route/proposal cannot be replayed here, and a spent receipt "
            "cannot be reused (terra finding #2 / RE-REVIEW-2 #2)")
    if not _receipts.consume_approval(receipt, workspace):
        raise PermissionError("record_acceptance: approval receipt was already consumed (lost "
                              "a concurrent race) -- nothing written (terra RE-REVIEW-2 #2)")
    _append_log({"kind": "acceptance", "route_id": route_id, "accepted": accepted,
                 "reaction_class": reaction_class, "receipt": receipt,
                 "ts": time.time()}, workspace)


def record_relabel(route_id: str, label: str = "wrong_track", *, source: str,
                   receipt: str | None = None,
                   workspace: str | Path | None = None) -> None:
    """Negatively relabel a route (fix #6: ONLY from explicit human feedback via reaction.confirm,
    or a deterministic schema mismatch — `source` names which; NEVER token overlap). A relabeled
    route is excluded from training even if it was previously accepted.

    terra HIGH #2 hardening: `source` must be one of RELABEL_SOURCES (an arbitrary string like
    "token_overlap" is refused at write time), and `source="human_feedback"` requires a
    server-issued approval receipt. `schema_mismatch` is a deterministic gate-side fact and
    needs no receipt; note a forged relabel can only REMOVE training data (fail-safe
    direction), never add it."""
    if source not in RELABEL_SOURCES:
        raise ValueError(f"record_relabel: source {source!r} is not an admissible provenance "
                         f"{sorted(RELABEL_SOURCES)} (terra finding #2 / fix #6)")
    if source == "human_feedback":
        from cortex_core import receipts as _receipts
        # terra RE-REVIEW #2: bound to THIS route (subject_id == route_id).
        # terra RE-REVIEW-2 #2: UNCONSUMED required + atomic consume at the write, exactly as
        # record_acceptance -- the receipt is single-use across the whole write path.
        if _receipts.check_approval(receipt, subject_id=route_id, decision=True,
                                    require_unconsumed=True, workspace=workspace) is None:
            raise PermissionError("record_relabel: human_feedback relabels require a live "
                                  "UNCONSUMED server-issued human-approval receipt BOUND TO "
                                  "THIS ROUTE (terra finding #2 / RE-REVIEW-2 #2)")
        if not _receipts.consume_approval(receipt, workspace):
            raise PermissionError("record_relabel: approval receipt was already consumed "
                                  "(lost a concurrent race) -- nothing written "
                                  "(terra RE-REVIEW-2 #2)")
    _append_log({"kind": "relabel", "route_id": route_id, "label": label,
                 "source": source, "receipt": receipt, "ts": time.time()}, workspace)


def _read_log(workspace: str | Path | None) -> list[dict]:
    path = _routing_log_path(workspace)
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_trainable(workspace: str | Path | None = None) -> list[dict]:
    """Routes that may train tiers 2/3 (review fix #3): a route record is trainable iff a HUMAN
    acceptance record (accepted=True) exists for its route_id AND no negative relabel exists.
    Deliberately NOT keyed on gate outcomes: gate-pass alone selects for lenient gates, not correct
    routes, so it trains nothing here.

    terra HIGH #2: an acceptance record counts ONLY when its `receipt` verifies against the
    server-side approval store (`cortex_core.receipts`) with decision=True. A hand-appended
    JSONL line with `accepted: true` but no real receipt trains NOTHING. Relabels remain
    receipt-free on READ because a forged relabel can only remove training data (fail-safe);
    the write path already restricts their provenance."""
    from cortex_core import receipts as _receipts
    records = _read_log(workspace)
    accepted: set[str] = set()
    relabeled: set[str] = set()
    routes: dict[str, dict] = {}
    verified_receipts: dict[tuple[str, str], bool] = {}

    def _receipt_ok(receipt: Any, route_id: str) -> bool:
        # terra RE-REVIEW #2: the receipt must be bound to THIS route (subject_id == route_id),
        # so a real receipt for a different route can't make an arbitrary route trainable.
        if not isinstance(receipt, str) or not receipt:
            return False
        key = (receipt, route_id)
        if key not in verified_receipts:
            verified_receipts[key] = _receipts.check_approval(
                receipt, subject_id=route_id, decision=True, require_unconsumed=False,
                workspace=workspace) is not None
        return verified_receipts[key]

    for rec in records:
        kind = rec.get("kind", "route")
        rid = rec.get("route_id") or ""
        if kind == "route" and rid:
            routes[rid] = rec
        elif kind == "acceptance" and rid:
            if rec.get("accepted") is True:
                if _receipt_ok(rec.get("receipt"), rid):
                    accepted.add(rid)
                # unverified accepted=true: ignored outright -- it neither trains nor
                # cancels a genuine receipt-backed acceptance (a forged line is inert)
            else:
                accepted.discard(rid)
        elif kind == "relabel" and rid:
            relabeled.add(rid)
    return [routes[rid] for rid in routes if rid in accepted and rid not in relabeled]


def _route_count(workspace: str | Path | None) -> int:
    """Number of routing decisions logged so far — the deterministic exploration counter."""
    return sum(1 for r in _read_log(workspace) if r.get("kind", "route") == "route")


def _is_exploration_run(workspace: str | Path | None) -> bool:
    """Fix #5: fixed-count epsilon floor. Deterministic modulo on the decision counter — fires at
    n=0, 10, 20, ... so at least one exploration happens even below 20 runs (the GLM review's
    'a fraction that is 0 below 20 runs' bug, avoided by construction)."""
    return _route_count(workspace) % EXPLORATION_EVERY == 0


# --- the route_vec index (tier 2's store) ----------------------------------------------------

def _route_index_path(workspace: str | Path | None) -> Path:
    return _routing_log_path(workspace).parent / "route-index.db"


def _default_embed() -> Callable[[list[str]], Any] | None:
    """potion-base-8M via cortex_core.vector when the [vector] extra is installed; else None
    (tier 2 degrades to fall-through — the vector.py graceful-degradation pattern)."""
    try:
        from cortex_core import vector
        if not vector.vector_available():
            return None
        return vector.embed_texts
    except Exception:  # noqa: BLE001
        return None


def _as_lists(vecs: Any) -> list[list[float]]:
    return [[float(x) for x in v] for v in vecs]


def _valid_vecs(vecs: Any, expect_n: int | None = None,
                expect_dim: int | None = None) -> list[list[float]] | None:
    """terra MED #6: validate an embedder's output BEFORE any distance math. Checks vector
    COUNT (an embedder returning 0 or 2 vectors for 1 text used to raise at unpacking),
    a single consistent DIMENSION (unequal dims were silently truncated by zip() in _l2),
    and per-component finiteness (a NaN distance defeats every `>` threshold comparison
    and can clamp confidence to 1.0). Returns the coerced vectors, or None -- callers fall
    through to tier 4 (fail-closed routing, never a confident misroute)."""
    try:
        out = _as_lists(vecs)
    except Exception:  # noqa: BLE001 -- junk output shapes coerce loudly here, not downstream
        return None
    if not out or (expect_n is not None and len(out) != expect_n):
        return None
    dim = expect_dim if expect_dim is not None else len(out[0])
    if dim <= 0:
        return None
    for v in out:
        if len(v) != dim or not all(math.isfinite(x) for x in v):
            return None
    return out


def _safe_embed(embed: Callable[[list[str]], Any], texts: list[str]) -> Any:
    """terra RE-REVIEW #6: calling the embedder can itself RAISE (provider timeout, network
    error, model load failure). Any exception -> None, so the tier falls through to tier 4
    instead of crashing direct(). Pairs with _valid_vecs (which validates the returned shape)."""
    try:
        return embed(texts)
    except Exception:  # noqa: BLE001 -- embedder is arbitrary/remote; never crash routing
        return None


def fresh_build_ids(available: dict[str, Any]) -> list[str]:
    """terra HIGH #4: the ONLY skills any Director tier may return as the PRIMARY are those
    declaring role == "fresh_build". Follow-on skills share track == "app_build" but edit an
    existing scaffold -- executing one on a blank dir is a RenderError. The role defaults to
    "follow_on" when absent (fail-safe: an unlabeled skill can never become a primary)."""
    return [sid for sid, sk in available.items()
            if getattr(sk, "track", "") == "app_build"
            and getattr(sk, "role", "follow_on") == "fresh_build"]


def rebuild_route_index(workspace: str | Path | None = None,
                        embed: Callable[[list[str]], Any] | None = None) -> int:
    """(Re)build the route_vec index from SEED_ROUTES + the HUMAN-ACCEPTED routing log
    (`load_trainable`). Stored in gitignored ops-local (training data, not corpus). Uses a
    sqlite-vec vec0 table when the extension is importable, with a plain-JSON fallback so the
    tier degrades rather than crashes (vector.py discipline). Returns rows indexed, 0 if no
    embedder is available."""
    embed = embed or _default_embed()
    if embed is None:
        return 0
    rows: list[tuple[str, str, str, str]] = [
        (utt, skill, SEED_ORIGIN, "") for utt, skill in SEED_ROUTES]
    rows += [(r.get("utterance", ""), r.get("skill_id", ""), "live", r.get("route_id", ""))
             for r in load_trainable(workspace)]
    vecs = _valid_vecs(_safe_embed(embed, [r[0] for r in rows]), expect_n=len(rows))
    if vecs is None:
        # terra MED #6: a NaN / dim-inconsistent / miscounted / RAISED embedding batch must
        # not become a poisoned index that misroutes later -- refuse to build, leave tier 2
        # falling through to tier 4.
        return 0
    dim = len(vecs[0])
    conn = sqlite3.connect(str(_route_index_path(workspace)))
    try:
        conn.execute("DROP TABLE IF EXISTS route_meta")
        conn.execute("CREATE TABLE route_meta(id INTEGER PRIMARY KEY, route_id TEXT,"
                     " skill_id TEXT, origin TEXT, utterance TEXT, emb TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS route_idx_meta(key TEXT PRIMARY KEY, value TEXT)")
        backend = "json"
        try:  # optional sqlite-vec KNN table, rowid-aligned with route_meta
            import sqlite_vec
            conn.enable_load_extension(True)
            try:
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
            conn.execute("DROP TABLE IF EXISTS route_vec")
            conn.execute(f"CREATE VIRTUAL TABLE route_vec USING vec0(embedding float[{dim}])")
            backend = "sqlite-vec"
        except Exception:  # noqa: BLE001 -- extension unavailable: JSON fallback only
            backend = "json"
        for i, ((utt, skill, origin, rid), vec) in enumerate(zip(rows, vecs, strict=True), start=1):
            conn.execute("INSERT INTO route_meta(id, route_id, skill_id, origin, utterance, emb)"
                         " VALUES(?,?,?,?,?,?)",
                         (i, rid, skill, origin, utt, json.dumps(vec)))
            if backend == "sqlite-vec":
                import sqlite_vec
                conn.execute("INSERT INTO route_vec(rowid, embedding) VALUES(?,?)",
                             (i, sqlite_vec.serialize_float32(vec)))
        conn.execute("INSERT OR REPLACE INTO route_idx_meta(key, value) VALUES('backend', ?)",
                     (backend,))
        conn.execute("INSERT OR REPLACE INTO route_idx_meta(key, value) VALUES('dim', ?)",
                     (str(dim),))
        live_n = sum(1 for r in rows if r[2] == "live")
        conn.execute("INSERT OR REPLACE INTO route_idx_meta(key, value) VALUES('live_count', ?)",
                     (str(live_n),))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def _l2(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def nearest_routes(workspace: str | Path | None, utterance: str, k: int = 5,
                   embed: Callable[[list[str]], Any] | None = None) -> list[dict]:
    """K nearest indexed routes for an utterance: [{skill_id, dist, origin, utterance,
    route_id}, ...] nearest-first. [] when the index or an embedder is missing (graceful
    fall-through — the cascade escalates instead of crashing)."""
    path = _route_index_path(workspace)
    if not path.is_file():
        return []
    embed = embed or _default_embed()
    if embed is None:
        return []
    conn = sqlite3.connect(str(path))
    try:
        backend_row = conn.execute(
            "SELECT value FROM route_idx_meta WHERE key='backend'").fetchone()
        backend = backend_row[0] if backend_row else "json"
        # terra MED #6: the index persists the embedder dimension at build time; the query
        # vector must be exactly ONE vector of exactly THAT dimension with finite
        # components, or tier 2 falls through instead of computing garbage distances.
        dim_row = conn.execute(
            "SELECT value FROM route_idx_meta WHERE key='dim'").fetchone()
        try:
            stored_dim = int(dim_row[0]) if dim_row else None
        except (TypeError, ValueError):
            stored_dim = None
        qvecs = _valid_vecs(_safe_embed(embed, [utterance]), expect_n=1, expect_dim=stored_dim)
        if qvecs is None:  # embedder raised, or returned a bad-shape/NaN vector -> fall through
            return []
        qvec = qvecs[0]
        hits: list[tuple[int, float]] = []
        if backend == "sqlite-vec":
            try:
                import sqlite_vec
                conn.enable_load_extension(True)
                try:
                    sqlite_vec.load(conn)
                finally:
                    conn.enable_load_extension(False)
                rows = conn.execute(
                    "SELECT rowid, distance FROM route_vec WHERE embedding MATCH ?"
                    " ORDER BY distance LIMIT ?",
                    (sqlite_vec.serialize_float32(qvec), int(k))).fetchall()
                hits = [(int(r[0]), float(r[1])) for r in rows]
            except Exception:  # noqa: BLE001 -- extension broke at query time: scan instead
                backend = "json"
        if backend == "json":
            scored = []
            for r in conn.execute("SELECT id, emb FROM route_meta"):
                try:
                    emb = json.loads(r[1])
                except Exception:  # noqa: BLE001
                    continue
                # terra MED #6: skip rows whose stored vector is dim-mismatched or
                # non-finite -- zip() truncation in _l2 must never fake a distance.
                if (not isinstance(emb, list) or len(emb) != len(qvec)
                        or not all(isinstance(x, (int, float)) and math.isfinite(x)
                                   for x in emb)):
                    continue
                scored.append((int(r[0]), _l2(qvec, emb)))
            scored.sort(key=lambda t: t[1])
            hits = scored[:int(k)]
        out: list[dict] = []
        for rowid, dist in hits:
            m = conn.execute("SELECT route_id, skill_id, origin, utterance FROM route_meta"
                             " WHERE id=?", (rowid,)).fetchone()
            if m is None:
                continue
            out.append({"route_id": m[0], "skill_id": m[1], "origin": m[2],
                        "utterance": m[3], "dist": dist})
        return out
    finally:
        conn.close()


def _index_live_count(workspace: str | Path | None) -> int:
    path = _route_index_path(workspace)
    if not path.is_file():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT value FROM route_idx_meta WHERE key='live_count'").fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0
    finally:
        conn.close()


def _seed_penalty(live_count: int) -> float:
    """Fix #5 decay-blend: below SEED_LIVE_FLOOR live records, seeds compete at full strength
    (0 penalty). At/after the floor, seed distances are inflated so live evidence dominates —
    but seeds are never deleted (calibration continuity)."""
    return SEED_DECAY_PENALTY if live_count >= SEED_LIVE_FLOOR else 0.0


# --- cascade tiers ---------------------------------------------------------------------------

def _routing_verbs_present(utterance: str) -> list[str]:
    u = (utterance or "").lower()
    return [v for v in _ROUTING_VERBS if re.search(r"\b" + re.escape(v) + r"\b", u)]


def _guard_forces_tier4(utterance: str) -> bool:
    """Review fix #1: multi-routing-verb or negated utterances go STRAIGHT to tier 4. Checked
    BEFORE tiers 2/3 in direct(), so no embedding confidence can ever override it."""
    return len(set(_routing_verbs_present(utterance))) >= 2 or bool(_NEGATION.search(utterance or ""))


def _tier1_rules(utterance: str, available: dict[str, Any]) -> tuple[str | None, float]:
    """Cheap keyword/shape rules -> (skill_id, confidence). Returns (None, 0) when uncertain so the
    cascade escalates. Multi-verb / negated utterances deliberately return low confidence (fix #1)."""
    from cortex_core import vague_build as vb
    if _guard_forces_tier4(utterance):
        return None, 0.0  # role-reversal / negation ambiguity -> escalate to tier 4 (review fix #1)
    skill_id = vb.route(utterance, available)   # the existing deterministic keyword router
    # confident only if a real build keyword actually matched (not just the default fallback)
    u = (utterance or "").lower()
    matched = any(k in u for kws, _ in vb._ROUTES for k in kws)
    return (skill_id, 0.9 if matched else 0.3)


def _tier2_embed(utterance: str, available: dict[str, Any],
                 workspace: str | Path | None,
                 embed: Callable[[list[str]], Any] | None,
                 margin: float = 0.0) -> tuple[str, float, dict] | None:
    """Tier 2: margin-gated nearest-neighbor over the route_vec index. None = fall through.
    Seeds face stricter thresholds (cold-start guard) and a decay penalty past SEED_LIVE_FLOOR.
    terra MED #5: the CALLER's confidence `margin` is enforced too -- a neighbor inside
    T2_MAX_DIST whose derived confidence still sits below the requested margin escalates
    instead of deciding. terra HIGH #4: only fresh_build skills may decide."""
    if not math.isfinite(margin):
        return None  # terra RE-REVIEW #5: never decide under a non-finite margin
    nbrs = nearest_routes(workspace, utterance, k=5, embed=embed)
    if not nbrs:
        return None
    penalty = _seed_penalty(_index_live_count(workspace))
    if penalty:
        for n in nbrs:
            if n["origin"] == SEED_ORIGIN:
                n["dist"] += penalty
        nbrs.sort(key=lambda n: n["dist"])
    top = nbrs[0]
    if top["skill_id"] not in fresh_build_ids(available):
        return None
    is_seed = top["origin"] == SEED_ORIGIN
    if top["dist"] > (T2_SEED_MAX_DIST if is_seed else T2_MAX_DIST):
        return None
    diff = next((n for n in nbrs if n["skill_id"] != top["skill_id"]), None)
    need = T2_SEED_MARGIN if is_seed else T2_MARGIN
    if diff is not None and (diff["dist"] - top["dist"]) < need:
        return None  # ambiguous between labels -> escalate
    conf = max(0.0, min(1.0, 1.0 - top["dist"]))
    if conf < margin:
        return None  # terra MED #5: below the caller's confidence policy -> escalate
    feats = {"neighbors": [[n["skill_id"], round(n["dist"], 4), n["origin"]] for n in nbrs[:3]],
             "seed_decided": is_seed}
    return top["skill_id"], conf, feats


def _tier3_centroid(utterance: str, available: dict[str, Any],
                    workspace: str | Path | None,
                    embed: Callable[[list[str]], Any] | None,
                    margin: float = 0.0) -> tuple[str, float, dict] | None:
    """Tier 3: nearest-centroid classifier computed FROM the human-accepted routing log (pure
    python, zero new deps). Labels need >= T3_MIN_PER_LABEL records (occurrence floor); the whole
    tier needs >= T3_MIN_RECORDS. None = fall through (graceful when the log is small).
    terra MED #5: enforces the caller's confidence `margin`. terra MED #6: the embedding
    batch is validated (count/dimension/finiteness) before any centroid math. terra HIGH #4:
    only fresh_build labels can decide."""
    if not math.isfinite(margin):
        return None  # terra RE-REVIEW #5: never decide under a non-finite margin
    embed = embed or _default_embed()
    if embed is None:
        return None
    recs = load_trainable(workspace)
    if len(recs) < T3_MIN_RECORDS:
        return None
    fresh = set(fresh_build_ids(available))
    by_label: dict[str, list[str]] = {}
    for r in recs:
        sid, utt = r.get("skill_id", ""), r.get("utterance", "")
        if sid in fresh and utt:
            by_label.setdefault(sid, []).append(utt)
    by_label = {sid: utts for sid, utts in by_label.items() if len(utts) >= T3_MIN_PER_LABEL}
    if not by_label:
        return None
    all_texts = [utterance] + [u for utts in by_label.values() for u in utts]
    vecs = _valid_vecs(_safe_embed(embed, all_texts), expect_n=len(all_texts))
    if vecs is None:
        return None  # terra MED #6: NaN/miscounted/dim-mismatched/RAISED batch -> escalate
    qvec, rest = vecs[0], vecs[1:]
    centroids: list[tuple[str, list[float]]] = []
    i = 0
    for sid, utts in by_label.items():
        group = rest[i:i + len(utts)]
        i += len(utts)
        dim = len(group[0])
        centroids.append((sid, [sum(v[d] for v in group) / len(group) for d in range(dim)]))
    dists = sorted(((_l2(qvec, c), sid) for sid, c in centroids))
    d1, best = dists[0]
    if d1 > T2_MAX_DIST:
        return None
    if len(dists) > 1 and (dists[1][0] - d1) < T3_MARGIN:
        return None  # two centroids too close -> escalate
    conf = max(0.0, min(1.0, 1.0 - d1))
    if conf < margin:
        return None  # terra MED #5: below the caller's confidence policy -> escalate
    return best, conf, {"centroid_dists": [[sid, round(d, 4)] for d, sid in dists[:3]]}


def direct(utterance: str, available: dict[str, Any], *,
           margin: float = 0.5, llm: Callable[[str], str] | None = None,
           workspace: str | Path | None = None,
           embed: Callable[[list[str]], Any] | None = None) -> Route:
    """Route a vague utterance through the cascade and LOG the decision.

    Order (cheapest first): fix-#1 guard -> tier 1 rules -> tier 2 embed -> tier 3 centroid ->
    tier 4 LLM. The guard runs FIRST: a multi-verb/negated utterance never reaches tiers 2/3, so
    embedding confidence cannot override it. On every EXPLORATION_EVERY-th decision a trained-tier
    answer is demoted to a logged "shadow" and tier 4 routes instead (fix #5's epsilon floor);
    tier-4/shadow disagreement is logged as router_disagreement. `llm`/`embed` are injectable for
    tests. Never mutates any registry — the log is append-only proposals for the trainable tiers."""
    fresh = fresh_build_ids(available)
    if not fresh:
        # terra HIGH #4: with zero declared fresh-build skills there is NO valid primary --
        # refuse honestly instead of routing a follow-on onto a blank dir.
        raise LookupError("director.direct: no fresh_build skill is declared; a follow-on "
                          "skill can never be the primary (terra finding #4)")
    features: dict[str, Any] = {"verbs": _routing_verbs_present(utterance)}
    skill_id: str | None = None
    conf = 0.0
    tier = 0
    # terra RE-REVIEW #5: validate the caller's confidence margin BEFORE any comparison.
    # A non-finite margin (NaN makes every `conf < margin` false -> would accept a
    # low-confidence route; None crashes `conf >= margin`) fails SAFE: skip the trained
    # tiers entirely and escalate to tier 4.
    bad_margin = not isinstance(margin, (int, float)) or isinstance(margin, bool) \
        or not math.isfinite(margin)
    if bad_margin:
        features["bad_margin"] = repr(margin)
    if _guard_forces_tier4(utterance) or bad_margin:
        features.setdefault("forced_tier4",
                            "bad_margin" if bad_margin else "multi_verb_or_negation")
    else:
        skill_id, conf = _tier1_rules(utterance, available)
        if skill_id is not None and skill_id not in fresh:
            skill_id, conf = None, 0.0  # terra #4: a non-fresh tier-1 pick cannot decide
        if skill_id is not None and conf >= margin:
            tier = 1
        else:
            # terra MED #5: tiers 2/3 now receive -- and must beat -- the caller's margin.
            shadow = _tier2_embed(utterance, available, workspace, embed, margin=margin)
            shadow_tier = 2
            if shadow is None:
                shadow = _tier3_centroid(utterance, available, workspace, embed, margin=margin)
                shadow_tier = 3
            if shadow is not None:
                s_skill, s_conf, s_feats = shadow
                if _is_exploration_run(workspace):
                    features["exploration"] = True
                    features["shadow"] = {"tier": shadow_tier, "skill_id": s_skill,
                                          "confidence": s_conf}
                else:
                    skill_id, conf, tier = s_skill, s_conf, shadow_tier
                    features.update(s_feats)
    if tier == 0:
        # tier 4: LLM fallback (a FREE model). Ask it to pick among the DECLARED FRESH-BUILD
        # skills only (bounded decision space -- the model selects, it does not invent a
        # route, and it can never return a follow-on as the primary: terra HIGH #4).
        from cortex_core import vague_build as vb
        tier = 4
        default = vb._DEFAULT_SKILL if vb._DEFAULT_SKILL in fresh else fresh[0]
        picked = _tier4_llm(utterance, available, llm)
        if picked is not None and "shadow" in features:
            features["router_disagreement"] = (features["shadow"]["skill_id"] != picked)
        skill_id = picked or skill_id or default
        conf = 0.5
    if skill_id not in fresh:  # belt-and-braces: NO tier may emit a non-fresh primary
        features["non_fresh_rejected"] = skill_id
        skill_id = fresh[0]
    route = Route(track="app_build", skill_id=skill_id, tier_used=tier, confidence=conf,
                  features=features, ts=time.time(), route_id="r_" + uuid.uuid4().hex)
    log_route(route, utterance, workspace)
    return route


def _default_skill(available: dict[str, Any]) -> Callable[[], str] | None:
    from cortex_core import vague_build as vb
    if vb._DEFAULT_SKILL in available:
        return lambda: vb._DEFAULT_SKILL
    return None


def _tier4_llm(utterance: str, available: dict[str, Any],
               llm: Callable[[str], str] | None) -> str | None:
    """Tier-4: let a FREE model pick among the DECLARED fresh-build skills. Bounded: it must return
    one of the offered ids. None on any failure (cascade then keeps the tier-1 pick / default).
    terra HIGH #4: the offered id set is fresh_build ONLY -- a follow-on (add-dashboard etc.)
    shares track == "app_build" but can never be offered, so the model cannot select one as
    the primary even by answering 'correctly'."""
    ids = fresh_build_ids(available)
    if not ids:
        return None
    if llm is None:
        from cortex_core.judge import apply_min_max_tokens
        from cortex_core.research import _llm_complete
        base, override = _resolve_free_tier()
        def llm(p: str) -> str:  # noqa: E306
            return _llm_complete(p, base, max_tokens=apply_min_max_tokens(base, 200),
                                 model_override=override) or ""
    prompt = ("Pick the ONE best fresh-build skill for this request. Reply with ONLY the skill id.\n"
              f"Request: {utterance!r}\nSkills: {', '.join(ids)}\n")
    try:
        out = (llm(prompt) or "").strip()
    except Exception:  # noqa: BLE001
        return None
    for sid in ids:
        if sid in out:
            return sid
    return None


def _resolve_free_tier() -> tuple[str, str | None]:
    """The tier-4 fallback model (FREE). Uses the harness's pinned always-on default."""
    from cortex_core import vague_build as vb
    return vb._resolve_tier("big-pickle")
