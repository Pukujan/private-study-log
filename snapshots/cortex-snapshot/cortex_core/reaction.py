"""The reaction loop — a human reacts to a built artifact; the harness learns, SAFELY.

Design (director-cascade plan §Phase 3 + the GLM-5.2 review's fixes #2/#3/#6, all BLOCKERS):

  - A reaction is classified by the SAME cascade shape as the Director: tier-1 deterministic
    keyword rules first, tier-4 LLM fallback (a FREE model, injectable) only when the rules are
    ambiguous — and the LLM is bounded to the DECLARED class list (it selects, never invents).
  - FIX #2 (anti-circularity): an LLM-classified reaction may only *PROPOSE*. Every mutation to
    the skill registry (pass_count), the routing training set (acceptance), or a route's label
    (wrong_track) goes through the PROPOSAL QUEUE and requires `confirm(proposal_id, True)` — an
    explicit human binary. `classify_reaction` and `proposals_from_reaction` write NOTHING except
    append-only queue/log records. There is no code path from a model verdict to a ledger.
  - FIX #3 (label soundness): trainability = human acceptance, NOT gate-pass. The `mark_trainable`
    proposal is only emitted for acceptance-shaped reactions (done / new_feature — i.e. reaction
    NOT in {bug, refine, wrong_track}); applying it (on human confirm) calls
    `director.record_acceptance`, which is the ONLY thing `director.load_trainable` reads.
  - FIX #6 (WRONG_TRACK): `infer_wrong_track` returns True ONLY for (a) explicit human feedback
    matched by the deterministic tier-1 rules, or (b) a deterministic schema mismatch flag from
    the gate. An LLM's wrong_track classification is a proposal like any other. Token overlap
    between utterance and artifact appears NOWHERE in this module (the corrupting heuristic the
    review killed).

Everything is append-only JSONL in gitignored ops-local (telemetry/training data, not corpus).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from cortex_core import director

# The declared reaction taxonomy (plan §Phase 3; Fable-authored — the >20% `unclear` SLI is the
# tripwire for revising it, a deterministic trigger per plan §7c).
REACTION_CLASSES = ("done", "refine", "new_feature", "bug", "undo", "wrong_track", "unclear")

# Classes that count as HUMAN ACCEPTANCE of the built chunk (fix #3: trainable iff accepted,
# i.e. reaction NOT in {bug, refine, wrong_track}). `undo`/`unclear` accept nothing.
ACCEPTANCE_CLASSES = frozenset({"done", "new_feature"})

# Tier-1 deterministic rules: ordered most-specific-first; >=2 distinct class hits -> ambiguous
# -> tier 4. Phrases are matched as substrings of the lowercased reaction.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wrong_track", ("wrong thing", "not what i asked", "not what i wanted", "that's not it",
                     "thats not it", "this is the wrong", "completely wrong", "wrong app",
                     "wrong feature")),
    ("bug", ("broken", "doesn't work", "does not work", "doesnt work", "crash", "error",
             "traceback", "a bug", "it bugs", "500", "fails when", "failing")),
    ("undo", ("undo", "go back", "revert", "put it back", "restore the old")),
    ("done", ("done", "perfect", "looks good", "looks great", "love it", "ship it",
              "that's it", "thats it", "all set", "we're good", "were good")),
    ("new_feature", ("also add", "now add", "can you add", "add a", "add an", "what about a",
                     "i also want", "next i want", "and also")),
    ("refine", ("instead", "should be", "make it", "make the", "make them", "change the",
                "change it", "rather than", "no, the", "not red", "not blue", "smaller",
                "bigger", "rename the")),
)


@dataclass
class Reaction:
    raw_text: str                    # ALWAYS verbatim, always logged (auditable, re-playable)
    classified_as: str               # one of REACTION_CLASSES
    tier_used: int                   # 1 (rules) | 4 (llm)
    confidence: float
    features: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0


def _ops_path(workspace: str | Path | None, name: str) -> Path:
    if workspace is not None and Path(str(workspace)).is_dir():
        root = Path(str(workspace))
    else:
        from cortex_core.config import resolve_workspace
        root = Path(resolve_workspace(None))
    out = root / "ops-local" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _reaction_log_path(workspace: str | Path | None) -> Path:
    return _ops_path(workspace, "reaction-log.jsonl")


def _queue_path(workspace: str | Path | None) -> Path:
    return _ops_path(workspace, "proposal-queue.jsonl")


def _rule_hits(text: str) -> list[str]:
    t = (text or "").lower()
    return [cls for cls, phrases in _RULES if any(p in t for p in phrases)]


def classify_reaction(raw_text: str, *, llm: Callable[[str], str] | None = None,
                      workspace: str | Path | None = None) -> Reaction:
    """Classify a verbatim human reaction via the cascade: tier-1 rules; tier-4 LLM only when the
    rules hit zero or >=2 classes. The LLM is bounded to REACTION_CLASSES (selects, never invents);
    on any LLM failure the class is honestly `unclear`. Classification NEVER mutates anything —
    it returns a Reaction plus (elsewhere) proposals for a human to confirm (review fix #2)."""
    hits = _rule_hits(raw_text)
    if len(hits) == 1:
        r = Reaction(raw_text=raw_text, classified_as=hits[0], tier_used=1, confidence=0.9,
                     features={"rule_hits": hits}, ts=time.time())
    else:
        cls = _tier4_classify(raw_text, llm)
        r = Reaction(raw_text=raw_text, classified_as=cls or "unclear", tier_used=4,
                     confidence=0.5 if cls else 0.0,
                     features={"rule_hits": hits}, ts=time.time())
    try:  # fail-open telemetry (the unclear-rate SLI reads this)
        with _reaction_log_path(workspace).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return r


def _tier4_classify(raw_text: str, llm: Callable[[str], str] | None) -> str | None:
    if llm is None:
        try:
            from cortex_core.judge import apply_min_max_tokens
            from cortex_core.research import _llm_complete
            base, override = director._resolve_free_tier()  # FREE tier only — never paid
            def llm(p: str) -> str:  # noqa: E306
                return _llm_complete(p, base, max_tokens=apply_min_max_tokens(base, 100),
                                     model_override=override) or ""
        except Exception:  # noqa: BLE001
            return None
    prompt = ("Classify this human reaction to a just-built app. Reply with ONLY one word from: "
              + ", ".join(REACTION_CLASSES) + f"\nReaction: {raw_text!r}\n")
    try:
        out = (llm(prompt) or "").lower()
    except Exception:  # noqa: BLE001
        return None
    # bounded selection: longest-name-first so `wrong_track` is not shadowed by a substring
    for cls in sorted(REACTION_CLASSES, key=len, reverse=True):
        if cls in out:
            return cls
    return None


def infer_wrong_track(reaction: Reaction | None, *, schema_mismatch: bool = False) -> bool:
    """Review fix #6: WRONG_TRACK is inferred ONLY from (a) a deterministic schema mismatch
    (a gate-side fact) or (b) explicit human feedback matched by the DETERMINISTIC tier-1 rules.
    An LLM (tier-4) wrong_track classification does NOT infer — it merely proposes (fix #2).
    Token overlap between utterance and artifact is deliberately absent (it corrupts training:
    'make it red' vs app.py share zero tokens yet the route was correct)."""
    if schema_mismatch:
        return True
    return (reaction is not None and reaction.classified_as == "wrong_track"
            and reaction.tier_used == 1)


# --- the proposal queue (fixes #2 + #3: LLM proposes, HUMAN disposes) -------------------------

#: kinds a proposal may carry and what confirm() applies for each:
#:   mark_trainable        -> director.record_acceptance(route_id)   [the fix-#3 trainability bit]
#:   skill_pass_decrement  -> pass_count -= 1 in skills/<id>/skill.json (floor 0)
#:   wrong_track_relabel   -> director.record_relabel(route_id)      [fix #6, human-confirmed arm]
#:   project_done          -> resolution record only (the driver reads it; plan 5.5: `done` is
#:                            never inferred, always a confirmed binary)
PROPOSAL_KINDS = ("mark_trainable", "skill_pass_decrement", "wrong_track_relabel", "project_done")


def propose(kind: str, payload: dict[str, Any], *, source: str = "llm_classifier",
            workspace: str | Path | None = None) -> str:
    """Queue a state-mutation PROPOSAL (append-only). Nothing is applied here — application
    requires `confirm(proposal_id, True)`, an explicit human binary (review fix #2)."""
    if kind not in PROPOSAL_KINDS:
        raise ValueError(f"unknown proposal kind {kind!r}; declared kinds: {PROPOSAL_KINDS}")
    pid = "p_" + uuid.uuid4().hex
    rec = {"kind": "proposal", "proposal_id": pid, "proposal_kind": kind,
           "payload": payload, "source": source, "ts": time.time()}
    with _queue_path(workspace).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return pid


def _read_queue(workspace: str | Path | None) -> list[dict]:
    path = _queue_path(workspace)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return out


def pending(workspace: str | Path | None = None) -> list[dict]:
    """Unresolved proposals, oldest-first — what the human is asked to confirm/deny."""
    records = _read_queue(workspace)
    resolved = {r["proposal_id"] for r in records if r.get("kind") == "resolution"}
    return [r for r in records if r.get("kind") == "proposal"
            and r["proposal_id"] not in resolved]


def _load_proposal(proposal_id: str, workspace: str | Path | None) -> dict | None:
    """Load a queued proposal FROM THE STORE by id (terra RE-REVIEW #2). `_apply` acts only
    on what this returns, so a fabricated `prop` dict can never be injected into a mutation."""
    for r in _read_queue(workspace):
        if r.get("kind") == "proposal" and r.get("proposal_id") == proposal_id:
            return r
    return None


def approval_subject(prop_or_id: Any, workspace: str | Path | None = None) -> str:
    """The identity an approval receipt for this proposal must be BOUND to (terra RE-REVIEW #2):
    a route-bearing proposal (mark_trainable / wrong_track_relabel / project_done) binds to its
    ROUTE id; a skill_pass_decrement binds to its SKILL id. So a receipt approving proposal P
    is tied to the concrete thing P mutates — it cannot be replayed onto a different route/skill.
    Accepts a proposal dict or a proposal_id (looked up in the store)."""
    prop = prop_or_id if isinstance(prop_or_id, dict) else _load_proposal(str(prop_or_id), workspace)
    if not prop:
        raise KeyError(f"approval_subject: no proposal {prop_or_id!r}")
    payload = prop.get("payload") or {}
    if prop.get("proposal_kind") == "skill_pass_decrement":
        return str(payload.get("skill_id") or prop.get("proposal_id"))
    return str(payload.get("route_id") or prop.get("proposal_id"))


def mint_confirmation(proposal_id: str, decision: bool, *,
                      channel: str = "human_cli",
                      workspace: str | Path | None = None) -> str:
    """The HUMAN-CONSOLE primitive: mint a single-use approval receipt correctly bound to a
    proposal's approval subject (route/skill). This is the ONE place that knows how to bind a
    receipt to a proposal, so a console operator answers `mint_confirmation(pid, yes/no)` and
    hands the result to `confirm`. HONEST LIMIT (terra RE-REVIEW #2): WHO may call this — i.e.
    human authentication — is an external trust boundary that is NOT implemented in-process;
    any in-process caller can mint. Until that boundary exists (a real out-of-band human
    console), the acceptance-WRITE path stays shadow. What IS structural here is the binding,
    single-use consume, and atomicity below — not the mint authority."""
    from cortex_core import receipts as rcp
    subject = approval_subject(proposal_id, workspace)
    return rcp.mint_approval(subject, decision, channel=channel, workspace=workspace)


def confirm(proposal_id: str, accepted: bool, *, receipt: str,
            workspace: str | Path | None = None) -> dict[str, Any]:
    """THE human binary (review fix #2). `accepted` must be a real bool — a yes/no answer, never
    a model's string. On True the proposal's mutation is applied; on False nothing mutates. Either
    way an append-only resolution record is written, so the audit trail shows who decided what.

    terra RE-REVIEW #2 hardening (round 2 restructure, terra RE-REVIEW-2 #2):
      - the approval `receipt` must be bound to the proposal's SUBJECT (route/skill), via
        `approval_subject` — a receipt minted for another proposal/route/skill is rejected;
      - application is ONE-TIME at the PROPOSAL level: `_apply` atomically claims the
        proposal's single resolution slot (`receipts.claim_proposal_resolution`, PRIMARY KEY
        proposal_id) before any mutation, so two concurrent confirms holding two DISTINCT
        valid receipts for the same proposal cannot both apply;
      - the receipt itself is consumed atomically inside the write path (`_apply` /
        `director.record_acceptance` / `record_relabel`), so it is single-use everywhere,
        including direct writer calls;
      - `_apply` reloads the proposal from the store by id (no caller-passed dict)."""
    if not isinstance(accepted, bool):
        raise TypeError("confirm() requires an explicit binary bool — an LLM verdict string "
                        "must never reach this call (review fix #2)")
    prop = _load_proposal(proposal_id, workspace)
    if prop is None:
        raise KeyError(f"unknown proposal_id {proposal_id!r}")
    if proposal_id not in {r["proposal_id"] for r in pending(workspace)}:
        raise ValueError(f"proposal {proposal_id!r} is already resolved")
    from cortex_core import receipts as rcp
    subject = approval_subject(prop, workspace)
    rec = rcp.check_approval(receipt, subject_id=subject, decision=accepted,
                             require_unconsumed=True, workspace=workspace)
    if rec is None:
        raise PermissionError(
            "confirm() requires a live server-issued human-approval receipt BOUND TO THIS "
            "proposal's subject and matching this decision — a `by` label, a bare bool, or a "
            "receipt for another proposal is not proof of a human (terra finding #2)")
    applied: dict[str, Any] = {"applied": False}
    if accepted:
        # _apply claims the proposal's one-time slot + consumes the receipt atomically; a
        # lost race (either axis) raises and applies nothing (terra RE-REVIEW-2 #2).
        applied = _apply(proposal_id, workspace, receipt=receipt)
    else:
        # A denial also resolves the proposal one-time: a later/concurrent accept with a
        # different receipt cannot re-open and apply it.
        if not rcp.claim_proposal_resolution(proposal_id, receipt, False, workspace=workspace):
            raise ValueError(f"proposal {proposal_id!r} was already resolved/applied — "
                             "resolution is one-time (terra RE-REVIEW-2 #2)")
        rcp.consume_approval(receipt, workspace)  # spend the 'no' receipt too (no replay)
    with _queue_path(workspace).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "resolution", "proposal_id": proposal_id,
                             "accepted": accepted, "receipt": receipt,
                             "channel": rec.get("channel"), "applied": applied,
                             "ts": time.time()}, ensure_ascii=False) + "\n")
    return {"proposal_id": proposal_id, "accepted": accepted, **applied}


def _apply(proposal_id: str, workspace: str | Path | None, *,
           receipt: str) -> dict[str, Any]:
    """Apply ONE human-confirmed proposal, loaded FROM THE STORE by id (terra RE-REVIEW #2).

    `_apply` no longer accepts a caller-passed proposal dict — a fabricated
    `{"proposal_id": approved_pid, "proposal_kind": ..., "payload": {"route_id": "r_target"}}`
    can no longer be injected. It reloads the real queued proposal and re-verifies the receipt
    is bound to THAT proposal's subject before touching any writer.

    terra RE-REVIEW-2 #2 (this used to be the seam): `_apply` applied without consuming, so
    calling it twice with one live receipt produced two acceptance records. Now, in order:
      1. the receipt must be live AND UNCONSUMED (a spent receipt refuses outright);
      2. the proposal's ONE-TIME resolution slot is claimed atomically
         (`receipts.claim_proposal_resolution`, PK proposal_id) — application is one-time at
         the PROPOSAL level, so a second DISTINCT valid receipt cannot re-apply it;
      3. the receipt is consumed atomically inside the mutation path — by
         `director.record_acceptance`/`record_relabel` for ledger kinds, or here for the
         local kinds — so the same receipt cannot drive a second write anywhere."""
    prop = _load_proposal(proposal_id, workspace)
    if prop is None:
        raise KeyError(f"_apply: no queued proposal {proposal_id!r} in the store "
                       "(a fabricated proposal cannot be applied — terra finding #2)")
    from cortex_core import receipts as rcp
    subject = approval_subject(prop, workspace)
    if rcp.check_approval(receipt, subject_id=subject, decision=True,
                          require_unconsumed=True, workspace=workspace) is None:
        raise PermissionError("_apply requires a live UNCONSUMED human-approval receipt bound "
                              "to this proposal's subject — it is not a public mutation path, "
                              "and a spent receipt cannot re-apply "
                              "(terra finding #2 / RE-REVIEW-2 #2)")
    if not rcp.claim_proposal_resolution(proposal_id, receipt, True, workspace=workspace):
        raise ValueError(f"proposal {proposal_id!r} was already resolved/applied — "
                         "application is one-time at the proposal level "
                         "(terra RE-REVIEW-2 #2)")
    kind = prop["proposal_kind"]
    payload = prop.get("payload") or {}
    if kind == "mark_trainable":
        # record_acceptance consumes the receipt atomically at the ledger write.
        director.record_acceptance(payload["route_id"], True,
                                   reaction_class=payload.get("reaction_class"),
                                   receipt=receipt, workspace=workspace)
        return {"applied": True, "action": "acceptance_recorded"}
    if kind == "skill_pass_decrement":
        if not rcp.consume_approval(receipt, workspace):
            raise PermissionError("approval receipt was already consumed (lost a concurrent "
                                  "race) — nothing applied (terra RE-REVIEW-2 #2)")
        new = _decrement_pass_count(payload["skill_id"], workspace)
        return {"applied": True, "action": "pass_count_decremented", "pass_count": new}
    if kind == "wrong_track_relabel":
        # record_relabel consumes the receipt atomically at the ledger write.
        director.record_relabel(payload["route_id"], source="human_feedback",
                                receipt=receipt, workspace=workspace)
        return {"applied": True, "action": "route_relabeled"}
    if kind == "project_done":
        if not rcp.consume_approval(receipt, workspace):
            raise PermissionError("approval receipt was already consumed (lost a concurrent "
                                  "race) — nothing applied (terra RE-REVIEW-2 #2)")
        return {"applied": True, "action": "project_done_confirmed"}
    raise ValueError(f"unknown proposal kind {kind!r}")  # pragma: no cover — propose() gates kinds


def _decrement_pass_count(skill_id: str, workspace: str | Path | None) -> int:
    """The human caught what the gate missed: decrement the skill's pass_count (floor 0).
    Mirrors build_skills.record_outcome's raw-JSON discipline; `verified` is untouched (flipping
    it stays a human-only promotion elsewhere)."""
    if workspace is not None and Path(str(workspace)).is_dir():
        root = Path(str(workspace))
    else:
        from cortex_core.config import resolve_workspace
        root = Path(resolve_workspace(None))
    sj = root / "skills" / skill_id / "skill.json"
    if not sj.is_file():
        raise FileNotFoundError(f"no skill.json for {skill_id!r} at {sj}")
    data = json.loads(sj.read_text(encoding="utf-8", errors="replace"))
    data["pass_count"] = max(0, int(data.get("pass_count", 0)) - 1)
    sj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data["pass_count"]


def proposals_from_reaction(reaction: Reaction, *, route_id: str,
                            skill_ids: list[str] | None = None,
                            workspace: str | Path | None = None) -> list[str]:
    """Turn a classified reaction into queued PROPOSALS (never mutations — fix #2):

      - acceptance-shaped (done/new_feature) -> ONE mark_trainable proposal for the route
        (fix #3: this is the only path to the training set, and it still needs the human yes)
      - bug -> a skill_pass_decrement proposal PER applied skill (the gate passed but the human
        caught a miss) and NO trainable proposal
      - wrong_track -> a wrong_track_relabel proposal (fix #6's human-confirmed arm)
      - refine / undo / unclear -> nothing (not acceptance, not evidence of a wrong route)

    Returns the queued proposal ids."""
    src = f"reaction_tier{reaction.tier_used}"
    out: list[str] = []
    cls = reaction.classified_as
    if cls in ACCEPTANCE_CLASSES and route_id:
        out.append(propose("mark_trainable",
                           {"route_id": route_id, "reaction_class": cls},
                           source=src, workspace=workspace))
        if cls == "done":
            out.append(propose("project_done", {"route_id": route_id},
                               source=src, workspace=workspace))
    elif cls == "bug":
        for sid in (skill_ids or []):
            out.append(propose("skill_pass_decrement", {"skill_id": sid, "route_id": route_id},
                               source=src, workspace=workspace))
    elif cls == "wrong_track" and route_id:
        out.append(propose("wrong_track_relabel", {"route_id": route_id},
                           source=src, workspace=workspace))
    return out
