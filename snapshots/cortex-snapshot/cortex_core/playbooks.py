"""Self-learning per-site exploration **playbook** library (design:
`docs/research/browser-extension-and-key-issuance-design-2026-07-06.md` Part 3;
build: `docs/research/BROWSER-LEARNING-LOOP-2026-07-07.md`).

**Ownership boundary (read this first).** This module is a PASSIVE
KNOWLEDGE/AUDIT SERVICE. It does NOT connect to, launch, control, or hold a
session to any browser. There is no CDP client, no Playwright driver, no cookie
jar here. The customer's OWN already-authorized browser automation calls INTO
Cortex to (a) look up known navigation knowledge for a site before acting
(`lookup`), and (b) report back what happened after acting (`apply_report`), so
Cortex can log it and update the playbook. Cortex only ever *receives reports*
about a connection the customer already owns. A prior "Cortex-owns-the-CDP"
attempt was correctly hard-blocked as a data-exfiltration risk; nothing here may
recreate that shape.

A **playbook** is a KEDB/Voyager skill-library entry applied to web navigation:
versioned, self-healing per-site knowledge that stores *intent* (role + accessible
name + anchors), never raw CSS selectors that rot. It lives next to the pattern
library (`playbooks/`), indexed like the rest of the corpus. The learning loop:
verify-on-use (the `verification_check` IS the test) -> on a miss self-heal down
the locator ladder and write the new working locator as v+1 with a change_log ->
confidence decays with failure -> below threshold it is quarantined + flagged for
fresh exploration -> an AI-proposed locator edit stays uncorroborated (capped
confidence) until a SECOND successful run corroborates it (the corroboration gate).

Storage shape deliberately mirrors `cortex_core/patterns.py` (the repo's existing
learned-knowledge convention): a searchable markdown artifact + a JSON sidecar per
site, under a top-level dir in the workspace.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import resolve_workspace_override

PLAYBOOKS_DIRNAME = "playbooks"

# --- learning-loop constants (the confidence/quarantine/corroboration model) ---
INITIAL_CONFIDENCE = 0.4          # a freshly explored, AI-proposed playbook: low until verified
SUCCESS_INCREMENT = 0.15          # additive credit per verified success
FAILURE_DECAY = 0.5               # multiplicative decay per verified failure
DEGRADED_CONFIDENCE = 0.4         # below this -> status flips to `degraded` (canary/soft warning)
QUARANTINE_CONFIDENCE = 0.2       # below this -> `quarantined` + flagged for fresh exploration
QUARANTINE_FAILURE_STREAK = 3     # this many consecutive failures -> quarantined regardless of score
CORROBORATION_MIN = 2             # an AI-proposed edit needs this many successes before it's trusted
UNCORROBORATED_CONFIDENCE_CEILING = 0.6  # confidence can't exceed this while an edit is un-corroborated

STATUS_ACTIVE = "active"
STATUS_DEGRADED = "degraded"
STATUS_QUARANTINED = "quarantined"

# Robust-exploration locator ladder (design doc, ordered best->worst). Stored as the
# vocabulary a report's `locator_strategy_used` should name; CSS is deliberately LAST.
LOCATOR_LADDER = ["role", "label", "text", "testid", "css", "visual"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Redaction: customer-reported data whose shape we don't fully control. If a
# report payload happens to carry something credential-shaped, refuse to write
# it verbatim into the permanent audit trail -- redact it first. Defensive, not
# a security guarantee: the real contract is that Cortex never *asks for* or
# *stores* credentials at all (no auth tokens/cookies in the schema).
# --------------------------------------------------------------------------- #
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")
_KEYVAL_RE = re.compile(
    r"(?i)\b(set-cookie|cookie|session[_-]?id|sessionid|csrf[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|token|password|passwd|pwd|secret|"
    r"api[_-]?key|apikey|authorization|auth)\b\s*[:=]\s*[\"']?[^\s\"',;}]{6,}"
)
_HEXBLOB_RE = re.compile(r"\b[A-Fa-f0-9]{32,}\b")


def redact(text: str) -> tuple[str, bool]:
    """Redact credential-shaped substrings. Returns (clean_text, redacted?)."""
    if not isinstance(text, str) or not text:
        return text, False
    original = text
    text = _JWT_RE.sub("[REDACTED-JWT]", text)
    text = _BEARER_RE.sub("bearer [REDACTED]", text)
    text = _KEYVAL_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = _HEXBLOB_RE.sub("[REDACTED-HEX]", text)
    return text, (text != original)


def redact_obj(obj: Any) -> tuple[Any, bool]:
    """Deep-redact strings inside dicts/lists (report payloads are nested)."""
    hit = False
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            rv, h = redact_obj(v)
            out[k] = rv
            hit = hit or h
        return out, hit
    if isinstance(obj, list):
        out_l = []
        for v in obj:
            rv, h = redact_obj(v)
            out_l.append(rv)
            hit = hit or h
        return out_l, hit
    return obj, False


def normalize_site_id(site_id_or_url: str) -> str:
    """Reduce a URL or bare id to a stable site_id (registrable host, no scheme/
    port/www/path). `https://www.LinkedIn.com/feed` -> `linkedin.com`."""
    s = (site_id_or_url or "").strip()
    if not s:
        return s
    if "://" in s or s.startswith("//"):
        netloc = urlparse(s).netloc
    else:
        netloc = urlparse("//" + s).netloc
    netloc = (netloc or s).lower().split(":", 1)[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


@dataclass
class Playbook:
    site_id: str
    playbook_version: int = 1
    change_log: list[dict[str, Any]] = field(default_factory=list)
    last_verified: str | None = None
    confidence: float = INITIAL_CONFIDENCE
    status: str = STATUS_ACTIVE
    entry_points: list[str] = field(default_factory=list)
    # auth notes ONLY -- flags/prose, never tokens/cookies/session state.
    auth: dict[str, Any] = field(default_factory=dict)
    # each locator stores INTENT: {intent, role, name, anchors[], visual_fallback, corroborated}
    key_locators: list[dict[str, Any]] = field(default_factory=list)
    navigation: dict[str, Any] = field(default_factory=dict)
    rate_limit_antibot: dict[str, Any] = field(default_factory=dict)
    known_pitfalls: list[str] = field(default_factory=list)
    # the success oracle: assert a role+name landmark; negative_signal for a block/challenge.
    verification_check: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    corroboration_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    failure_streak: int = 0
    needs_exploration: bool = False
    pending_corroboration: bool = False  # an AI-proposed edit awaits a 2nd success
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Playbook":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]


def validate_playbook(pb: Playbook) -> tuple[bool, list[str]]:
    """Well-formed = a real site_id, a verification oracle, and locators that store
    INTENT (role+name), never raw CSS strings (the self-heal-requires-intent rule)."""
    errors: list[str] = []
    if not pb.site_id.strip():
        errors.append("site_id is empty")
    if pb.status not in (STATUS_ACTIVE, STATUS_DEGRADED, STATUS_QUARANTINED):
        errors.append(f"status {pb.status!r} not one of active|degraded|quarantined")
    for i, loc in enumerate(pb.key_locators):
        if "css" in loc and loc.get("css"):
            errors.append(
                f"key_locators[{i}] carries a raw `css` string -- store INTENT "
                "(role+name+anchors+visual_fallback), never CSS (it rots on re-render)"
            )
        if not str(loc.get("role", "")).strip() or not str(loc.get("name", "")).strip():
            errors.append(f"key_locators[{i}] missing role/name (user-facing locator, not CSS)")
    return (not errors, errors)


# --------------------------------------------------------------------------- #
# Storage -- mirrors patterns.py: a searchable markdown artifact + JSON sidecar.
# --------------------------------------------------------------------------- #
def playbooks_dir(workspace: str | Path | None = None) -> Path:
    # Arg-first (explicit workspace wins over CORTEX_WORKSPACE); omitted falls back env-first.
    return resolve_workspace_override(workspace) / PLAYBOOKS_DIRNAME


def _slug(site_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", site_id.lower()).strip("-")[:60] or "site"


def save_playbook(pb: Playbook, workspace: str | Path | None = None) -> Path:
    d = playbooks_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_slug(pb.site_id)}.md"
    fm = {
        "schema_version": pb.schema_version,
        "site_id": pb.site_id,
        "playbook_version": pb.playbook_version,
        "status": pb.status,
        "confidence": round(pb.confidence, 4),
        "last_verified": pb.last_verified,
        "corroboration_count": pb.corroboration_count,
        "needs_exploration": pb.needs_exploration,
    }
    body = ["---"]
    for k, v in fm.items():
        body.append(f"{k}: {json.dumps(v)}")
    body.append("---")
    body += [
        f"# Playbook: {pb.site_id}",
        "",
        f"- version **{pb.playbook_version}**, status **{pb.status}**, "
        f"confidence **{pb.confidence:.2f}** (successes {pb.success_count} / failures {pb.failure_count})",
        f"- last_verified: {pb.last_verified or 'never'}",
        "",
        "## Verification check (the success oracle)",
        json.dumps(pb.verification_check, indent=2) if pb.verification_check else "_none recorded_",
        "",
        "## Key locators (intent, not CSS)",
    ]
    if pb.key_locators:
        for loc in pb.key_locators:
            corr = "corroborated" if loc.get("corroborated") else "unverified"
            body.append(
                f"- **{loc.get('intent', '?')}**: role={loc.get('role')!r} "
                f"name={loc.get('name')!r} anchors={loc.get('anchors', [])} "
                f"visual_fallback={loc.get('visual_fallback')!r} ({corr})"
            )
    else:
        body.append("_none learned yet_")
    body += ["", "## Known pitfalls"]
    body += [f"- {p}" for p in pb.known_pitfalls] or ["_none recorded_"]
    body += ["", "## Change log"]
    for entry in pb.change_log:
        body.append(f"- v{entry.get('version')} ({entry.get('ts')}): {entry.get('change')}")
    body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(pb.to_dict(), indent=2), encoding="utf-8")
    return path


def load_playbook(site_id_or_url: str, workspace: str | Path | None = None) -> Playbook | None:
    sid = normalize_site_id(site_id_or_url)
    j = playbooks_dir(workspace) / f"{_slug(sid)}.json"
    if not j.is_file():
        return None
    try:
        return Playbook.from_dict(json.loads(j.read_text(encoding="utf-8")))
    except Exception:
        return None


def load_playbooks(workspace: str | Path | None = None) -> list[Playbook]:
    d = playbooks_dir(workspace)
    if not d.is_dir():
        return []
    out: list[Playbook] = []
    for j in sorted(d.glob("*.json")):
        try:
            out.append(Playbook.from_dict(json.loads(j.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def lookup(site_id_or_url: str, workspace: str | Path | None = None) -> dict[str, Any]:
    """Read-only: return the current playbook for a site, or a clear
    'no playbook yet -- explore and report back' response if none exists."""
    sid = normalize_site_id(site_id_or_url)
    pb = load_playbook(sid, workspace)
    if pb is None:
        return {
            "site_id": sid,
            "exists": False,
            "guidance": (
                "No playbook for this site yet. Explore it with robust primitives "
                "(perceive via the accessibility tree; ground with user-facing locators "
                "role+name -> label -> anchored text -> test-id -> CSS last; auto-wait; "
                "verify with a polling assertion on the expected next-state landmark; "
                "re-perceive after every action), then report what worked via "
                "cortex_playbook_report so the next agent inherits it."
            ),
            "exploration_primitives_order": LOCATOR_LADDER,
        }
    return {
        "site_id": sid,
        "exists": True,
        "playbook_version": pb.playbook_version,
        "status": pb.status,
        "confidence": round(pb.confidence, 4),
        "last_verified": pb.last_verified,
        "entry_points": pb.entry_points,
        "auth": pb.auth,
        "key_locators": pb.key_locators,
        "navigation": pb.navigation,
        "rate_limit_antibot": pb.rate_limit_antibot,
        "known_pitfalls": pb.known_pitfalls,
        "verification_check": pb.verification_check,
        "corroboration_count": pb.corroboration_count,
        "needs_exploration": pb.needs_exploration,
        "caveat": (
            "A logged-in/SPA site has no single true DOM (A/B/personalization); this is a "
            "decaying best-guess -- re-verify with the verification_check, don't blindly replay. "
            "If status is degraded/quarantined, treat as stale and re-explore."
        ),
    }


def _succeeded(outcome: str | None, verification_result: str | None) -> bool:
    """The verification_check IS the test (verify-on-use). If a verification_result
    is given, it is authoritative; else fall back to the agent's self-reported outcome."""
    def _norm(v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip().lower()
        if v in ("pass", "passed", "success", "succeeded", "ok", "true", "yes"):
            return "pass"
        if v in ("fail", "failed", "failure", "error", "blocked", "false", "no"):
            return "fail"
        return None
    return (_norm(verification_result) or _norm(outcome)) == "pass"


def apply_report(
    site_id_or_url: str,
    action_taken: str,
    locator_strategy_used: str,
    outcome: str,
    verification_result: str | None = None,
    new_locator: dict[str, Any] | None = None,
    pitfall: str | None = None,
    entry_point: str | None = None,
    verification_check: dict[str, Any] | None = None,
    auth_note: str | None = None,
    provenance_note: str | None = None,
    workspace: str | Path | None = None,
) -> tuple[Playbook, dict[str, Any]]:
    """Apply the learning loop from one customer-reported action and persist the
    playbook. Returns (playbook, summary of what changed). Does NOT write the audit
    closeout -- the MCP tool does that via write_closeout so the closeout path is
    identical to every other Cortex closeout."""
    sid = normalize_site_id(site_id_or_url)
    pb = load_playbook(sid, workspace)
    changes: list[str] = []
    created = False
    if pb is None:
        pb = Playbook(site_id=sid, provenance={"origin": "exploration", "note": provenance_note or ""})
        created = True
        changes.append("created new playbook from first exploration")

    success = _succeeded(outcome, verification_result)

    # Merge any freshly observed structural facts (all intent-shaped; no CSS, no auth secrets).
    if entry_point and entry_point not in pb.entry_points:
        pb.entry_points.append(entry_point)
        changes.append(f"entry_point learned: {entry_point}")
    if verification_check:
        pb.verification_check = verification_check
        changes.append("verification_check (success oracle) recorded/updated")
    if auth_note:
        # notes only -- redaction below still runs, but the schema never stores tokens.
        pb.auth = {**pb.auth, "notes": auth_note}
    if pitfall and pitfall not in pb.known_pitfalls:
        pb.known_pitfalls.append(pitfall)
        changes.append(f"pitfall recorded: {pitfall}")

    # A newly learned working locator (self-heal succeeded down the ladder) => v+1 with a
    # change_log entry (Voyager "verified skill enters library"), but it is AI-proposed and
    # stays UNcorroborated until a second successful run confirms it (the corroboration gate).
    if new_locator and success:
        loc = dict(new_locator)
        loc.pop("css", None)  # never persist a raw CSS string
        loc["corroborated"] = False
        pb.key_locators.append(loc)
        pb.playbook_version += 1
        pb.pending_corroboration = True
        pb.change_log.append({
            "version": pb.playbook_version, "ts": _now(),
            "change": f"learned locator via self-heal ({locator_strategy_used}): "
                      f"{loc.get('intent', loc.get('name', '?'))} -- awaiting corroboration",
        })
        changes.append(f"new locator learned -> v{pb.playbook_version} (uncorroborated)")

    if success:
        pb.success_count += 1
        pb.failure_streak = 0
        pb.last_verified = _now()
        if pb.pending_corroboration:
            pb.corroboration_count += 1
            if pb.corroboration_count >= CORROBORATION_MIN:
                pb.pending_corroboration = False
                for loc in pb.key_locators:
                    loc["corroborated"] = True
                changes.append("edit corroborated by a 2nd success -- locators now trusted")
        ceiling = UNCORROBORATED_CONFIDENCE_CEILING if pb.pending_corroboration else 1.0
        pb.confidence = min(ceiling, round(pb.confidence + SUCCESS_INCREMENT, 4))
        if pb.status == STATUS_DEGRADED and pb.confidence >= DEGRADED_CONFIDENCE:
            pb.status = STATUS_ACTIVE
            pb.needs_exploration = False
            changes.append("recovered: degraded -> active")
        changes.append(f"success: confidence -> {pb.confidence:.2f}, corroboration {pb.corroboration_count}")
    else:
        pb.failure_count += 1
        pb.failure_streak += 1
        pb.confidence = round(pb.confidence * FAILURE_DECAY, 4)
        changes.append(f"failure: confidence decayed -> {pb.confidence:.2f}, streak {pb.failure_streak}")
        if (pb.confidence < QUARANTINE_CONFIDENCE
                or pb.failure_streak >= QUARANTINE_FAILURE_STREAK):
            pb.status = STATUS_QUARANTINED
            pb.needs_exploration = True
            changes.append("QUARANTINED + flagged for fresh exploration")
        elif pb.confidence < DEGRADED_CONFIDENCE and pb.status == STATUS_ACTIVE:
            pb.status = STATUS_DEGRADED
            changes.append("degraded (canary/soft warning)")

    save_playbook(pb, workspace)
    summary = {
        "site_id": sid,
        "created": created,
        "playbook_version": pb.playbook_version,
        "status": pb.status,
        "confidence": round(pb.confidence, 4),
        "corroboration_count": pb.corroboration_count,
        "needs_exploration": pb.needs_exploration,
        "success_recorded": success,
        "changes": changes,
    }
    return pb, summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import make_stdio_encoding_safe

    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex per-site exploration playbooks (browser learning loop)")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--list", action="store_true", help="list existing playbooks")
    parser.add_argument("--lookup", metavar="SITE_OR_URL", help="look up one site's playbook")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.lookup:
        print(json.dumps(lookup(args.lookup, args.workspace), indent=2))
        return 0
    pbs = load_playbooks(args.workspace)
    if args.json:
        print(json.dumps([p.to_dict() for p in pbs], indent=2))
    else:
        print(f"{len(pbs)} playbook(s):")
        for pb in pbs:
            print(f"  [{pb.status}] {pb.site_id}  v{pb.playbook_version} "
                  f"conf={pb.confidence:.2f} (corroboration {pb.corroboration_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
