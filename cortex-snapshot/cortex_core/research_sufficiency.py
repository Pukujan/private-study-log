"""Decision-specific, non-self-certifying research sufficiency receipts.

The builder may propose a frozen evidence bundle. Mechanical policy assessment is deterministic.
Substantive-review and qualified-human attestations are stored through a separate trusted surface;
the builder-facing MCP API can reference their opaque IDs but cannot mint them. Only a server-stored,
digest-bound receipt may authorize dependent work.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import resolve_workspace_override

SCHEMA_VERSION = 1
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
OUTCOMES = {"SUFFICIENT_FOR_DECISION", "UNRESOLVED", "ABSTAIN"}

POLICY_FIELDS = {
    "schema_version", "policy_id", "task_id", "risk_tier", "valid_from", "expires_at",
    "lanes", "claim_rules", "conflict_policy", "review_policy", "human_requirements",
    "invalidation_triggers",
}
PROPOSAL_FIELDS = {
    "schema_version", "proposal_id", "run_id", "task_id", "research_task_id", "decision",
    "policy_id", "policy_sha256", "proposer", "report", "questions", "claims", "evidence",
    "conflicts", "remaining_uncertainties", "rejected_evidence", "invalidation_triggers",
}
ATTESTATION_FIELDS = {
    "schema_version", "attestation_id", "kind", "proposal_sha256", "policy_sha256",
    "decision_id", "authority", "issuer_id", "scope_ids", "verdict", "reason",
    "evidence_refs", "issued_at", "expires_at",
}
SOURCE_AUTHORITY_FIELDS = {
    "schema_version", "source_id", "url_origin", "authority_class", "independence_group",
    "issuer_id", "valid_from", "expires_at",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _exact(obj: Any, fields: set[str], name: str, problems: list[str]) -> bool:
    if not isinstance(obj, dict):
        problems.append(f"{name} must be an object")
        return False
    missing = fields - set(obj)
    unknown = set(obj) - fields
    problems.extend(f"{name} missing {field}" for field in sorted(missing))
    problems.extend(f"{name} unknown field {field}" for field in sorted(unknown))
    return not missing and not unknown


def _strings(value: Any, name: str, problems: list[str], *, empty: bool = False) -> None:
    if not isinstance(value, list) or any(not _nonempty(item) for item in value):
        problems.append(f"{name} must be a list of non-empty strings")
    elif not empty and not value:
        problems.append(f"{name} must not be empty")


def _objects(value: Any, name: str, problems: list[str], *, empty: bool = False) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        problems.append(f"{name} must be a list of objects")
        return []
    if not empty and not value:
        problems.append(f"{name} must not be empty")
    return value


def _parse_time(value: Any, name: str, problems: list[str]) -> datetime | None:
    if not _nonempty(value):
        problems.append(f"{name} must be an ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        problems.append(f"{name} must be a timezone-aware ISO-8601 timestamp")
        return None


def _risk_at_least(risk: str, floor: str) -> bool:
    return RISK_ORDER.get(risk, -1) >= RISK_ORDER.get(floor, 99)


def _sha256(value: Any, name: str, problems: list[str]) -> None:
    if not (_nonempty(value) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)):
        problems.append(f"{name} must be a lowercase SHA-256 hex digest")


def validate_sufficiency_policy(policy: Any) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if not _exact(policy, POLICY_FIELDS, "policy", problems):
        return False, problems
    if policy.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("policy_id", "task_id"):
        if not _nonempty(policy.get(field)):
            problems.append(f"{field} must be non-empty")
    if policy.get("risk_tier") not in RISK_ORDER:
        problems.append("risk_tier must be low, medium, or high")
    start = _parse_time(policy.get("valid_from"), "valid_from", problems)
    end = _parse_time(policy.get("expires_at"), "expires_at", problems)
    if start and end and start >= end:
        problems.append("valid_from must precede expires_at")

    lane_fields = {
        "lane_id", "required_question_ids", "required_authority_classes",
        "min_independence_groups", "max_age_days", "snapshot_required",
    }
    lanes = _objects(policy.get("lanes"), "lanes", problems)
    lane_ids: list[str] = []
    for i, lane in enumerate(lanes):
        if not _exact(lane, lane_fields, f"lanes[{i}]", problems):
            continue
        lane_ids.append(lane.get("lane_id", ""))
        if not _nonempty(lane.get("lane_id")):
            problems.append(f"lanes[{i}].lane_id must be non-empty")
        _strings(lane.get("required_question_ids"), f"lanes[{i}].required_question_ids", problems)
        _strings(lane.get("required_authority_classes"), f"lanes[{i}].required_authority_classes", problems)
        for field in ("min_independence_groups", "max_age_days"):
            value = lane.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"lanes[{i}].{field} must be an integer >= 1")
        if not isinstance(lane.get("snapshot_required"), bool):
            problems.append(f"lanes[{i}].snapshot_required must be boolean")
    if len(lane_ids) != len(set(lane_ids)):
        problems.append("lane_id values must be unique")

    rule_fields = {
        "claim_kind", "required_authority_classes", "min_independence_groups", "max_age_days"
    }
    rules = _objects(policy.get("claim_rules"), "claim_rules", problems, empty=True)
    kinds: list[str] = []
    for i, rule in enumerate(rules):
        if not _exact(rule, rule_fields, f"claim_rules[{i}]", problems):
            continue
        kinds.append(rule.get("claim_kind", ""))
        if not _nonempty(rule.get("claim_kind")):
            problems.append(f"claim_rules[{i}].claim_kind must be non-empty")
        _strings(rule.get("required_authority_classes"), f"claim_rules[{i}].required_authority_classes", problems)
        for field in ("min_independence_groups", "max_age_days"):
            value = rule.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"claim_rules[{i}].{field} must be an integer >= 1")
    if len(kinds) != len(set(kinds)):
        problems.append("claim_kind rules must be unique")

    conflict = policy.get("conflict_policy")
    if _exact(conflict, {"blocking_severities"}, "conflict_policy", problems):
        _strings(conflict.get("blocking_severities"), "conflict_policy.blocking_severities", problems)
    review = policy.get("review_policy")
    review_fields = {"required_from_risk", "trusted_issuer_ids", "required_scopes"}
    if _exact(review, review_fields, "review_policy", problems):
        if review.get("required_from_risk") not in RISK_ORDER:
            problems.append("review_policy.required_from_risk has invalid risk tier")
        _strings(review.get("trusted_issuer_ids"), "review_policy.trusted_issuer_ids", problems, empty=True)
        _strings(review.get("required_scopes"), "review_policy.required_scopes", problems, empty=True)

    human_fields = {"scope_id", "required_from_risk", "accepted_roles", "trusted_issuer_ids"}
    humans = _objects(policy.get("human_requirements"), "human_requirements", problems, empty=True)
    scopes: list[str] = []
    for i, requirement in enumerate(humans):
        if not _exact(requirement, human_fields, f"human_requirements[{i}]", problems):
            continue
        scopes.append(requirement.get("scope_id", ""))
        if not _nonempty(requirement.get("scope_id")):
            problems.append(f"human_requirements[{i}].scope_id must be non-empty")
        if requirement.get("required_from_risk") not in RISK_ORDER:
            problems.append(f"human_requirements[{i}].required_from_risk has invalid risk tier")
        _strings(requirement.get("accepted_roles"), f"human_requirements[{i}].accepted_roles", problems)
        _strings(requirement.get("trusted_issuer_ids"), f"human_requirements[{i}].trusted_issuer_ids", problems)
    if len(scopes) != len(set(scopes)):
        problems.append("human scope_id values must be unique")
    _strings(policy.get("invalidation_triggers"), "invalidation_triggers", problems)
    return not problems, problems


def validate_sufficiency_proposal(proposal: Any) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if not _exact(proposal, PROPOSAL_FIELDS, "proposal", problems):
        return False, problems
    if proposal.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("proposal_id", "run_id", "task_id", "research_task_id", "policy_id", "policy_sha256"):
        if not _nonempty(proposal.get(field)):
            problems.append(f"{field} must be non-empty")
    decision_fields = {"decision_id", "statement", "scope", "downstream_work", "risk_tier", "as_of", "expires_at"}
    decision = proposal.get("decision")
    if _exact(decision, decision_fields, "decision", problems):
        for field in ("decision_id", "statement", "scope"):
            if not _nonempty(decision.get(field)):
                problems.append(f"decision.{field} must be non-empty")
        _strings(decision.get("downstream_work"), "decision.downstream_work", problems)
        if decision.get("risk_tier") not in RISK_ORDER:
            problems.append("decision.risk_tier has invalid risk tier")
        as_of = _parse_time(decision.get("as_of"), "decision.as_of", problems)
        expires = _parse_time(decision.get("expires_at"), "decision.expires_at", problems)
        if as_of and expires and as_of >= expires:
            problems.append("decision.as_of must precede decision.expires_at")
    proposer = proposal.get("proposer")
    if _exact(proposer, {"id", "family"}, "proposer", problems):
        if not _nonempty(proposer.get("id")) or not _nonempty(proposer.get("family")):
            problems.append("proposer id and family must be non-empty")
    report = proposal.get("report")
    if _exact(report, {"path", "sha256"}, "report", problems):
        if not _nonempty(report.get("path")):
            problems.append("report path must be non-empty")
        _sha256(report.get("sha256"), "report.sha256", problems)
    _sha256(proposal.get("policy_sha256"), "policy_sha256", problems)

    question_fields = {"question_id", "lane_id", "status", "claim_ids"}
    claim_fields = {"claim_id", "lane_id", "claim_kind", "statement", "evidence_ids"}
    evidence_fields = {
        "evidence_id", "source_url", "local_path", "content_sha256", "captured_at",
        "source_version", "authority_class", "independence_group",
    }
    conflict_fields = {
        "conflict_id", "severity", "status", "evidence_ids", "resolution", "resolution_evidence_ids"
    }
    uncertainty_fields = {"uncertainty_id", "severity", "disposition", "required_approval_scope"}
    id_sets: dict[str, list[str]] = {"question": [], "claim": [], "evidence": [], "conflict": [], "uncertainty": []}
    for i, question in enumerate(_objects(proposal.get("questions"), "questions", problems)):
        if _exact(question, question_fields, f"questions[{i}]", problems):
            id_sets["question"].append(question.get("question_id", ""))
            if question.get("status") not in {"ANSWERED", "UNRESOLVED"}:
                problems.append(f"questions[{i}].status is invalid")
            for field in ("question_id", "lane_id"):
                if not _nonempty(question.get(field)):
                    problems.append(f"questions[{i}].{field} must be non-empty")
            _strings(question.get("claim_ids"), f"questions[{i}].claim_ids", problems, empty=True)
    for i, claim in enumerate(_objects(proposal.get("claims"), "claims", problems)):
        if _exact(claim, claim_fields, f"claims[{i}]", problems):
            id_sets["claim"].append(claim.get("claim_id", ""))
            for field in ("claim_id", "lane_id", "claim_kind", "statement"):
                if not _nonempty(claim.get(field)):
                    problems.append(f"claims[{i}].{field} must be non-empty")
            _strings(claim.get("evidence_ids"), f"claims[{i}].evidence_ids", problems)
    for i, evidence in enumerate(_objects(proposal.get("evidence"), "evidence", problems)):
        if _exact(evidence, evidence_fields, f"evidence[{i}]", problems):
            id_sets["evidence"].append(evidence.get("evidence_id", ""))
            for field in evidence_fields:
                if not _nonempty(evidence.get(field)):
                    problems.append(f"evidence[{i}].{field} must be non-empty")
            _sha256(evidence.get("content_sha256"), f"evidence[{i}].content_sha256", problems)
            _parse_time(evidence.get("captured_at"), f"evidence[{i}].captured_at", problems)
    for i, conflict in enumerate(_objects(proposal.get("conflicts"), "conflicts", problems, empty=True)):
        if _exact(conflict, conflict_fields, f"conflicts[{i}]", problems):
            id_sets["conflict"].append(conflict.get("conflict_id", ""))
            if conflict.get("status") not in {"RESOLVED", "UNRESOLVED"}:
                problems.append(f"conflicts[{i}].status is invalid")
            for field in ("conflict_id", "severity", "resolution"):
                if not _nonempty(conflict.get(field)):
                    problems.append(f"conflicts[{i}].{field} must be non-empty")
            _strings(conflict.get("evidence_ids"), f"conflicts[{i}].evidence_ids", problems)
            _strings(conflict.get("resolution_evidence_ids"), f"conflicts[{i}].resolution_evidence_ids", problems, empty=True)
            if conflict.get("status") == "RESOLVED" and not conflict.get("resolution_evidence_ids"):
                problems.append(f"conflicts[{i}] resolved without resolution evidence")
    for i, uncertainty in enumerate(_objects(proposal.get("remaining_uncertainties"), "remaining_uncertainties", problems, empty=True)):
        if _exact(uncertainty, uncertainty_fields, f"remaining_uncertainties[{i}]", problems):
            id_sets["uncertainty"].append(uncertainty.get("uncertainty_id", ""))
            if uncertainty.get("disposition") not in {"ACCEPT_RISK", "BLOCK"}:
                problems.append(f"remaining_uncertainties[{i}].disposition is invalid")
            for field in ("uncertainty_id", "severity", "required_approval_scope"):
                if not _nonempty(uncertainty.get(field)):
                    problems.append(f"remaining_uncertainties[{i}].{field} must be non-empty")
    for kind, ids in id_sets.items():
        if len(ids) != len(set(ids)):
            problems.append(f"{kind} IDs must be unique")
    _strings(proposal.get("rejected_evidence"), "rejected_evidence", problems, empty=True)
    _strings(proposal.get("invalidation_triggers"), "invalidation_triggers", problems)

    question_ids = set(id_sets["question"])
    claim_ids = set(id_sets["claim"])
    evidence_ids = set(id_sets["evidence"])
    for i, question in enumerate(proposal.get("questions") or []):
        for claim_id in question.get("claim_ids", []):
            if claim_id not in claim_ids:
                problems.append(f"questions[{i}] references unknown claim {claim_id}")
    for i, claim in enumerate(proposal.get("claims") or []):
        for evidence_id in claim.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                problems.append(f"claims[{i}] references unknown evidence {evidence_id}")
    for i, conflict in enumerate(proposal.get("conflicts") or []):
        for evidence_id in conflict.get("evidence_ids", []) + conflict.get("resolution_evidence_ids", []):
            if evidence_id not in evidence_ids:
                problems.append(f"conflicts[{i}] references unknown evidence {evidence_id}")
    if not question_ids:
        problems.append("at least one question is required")
    return not problems, problems


def validate_attestation(attestation: Any) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if not _exact(attestation, ATTESTATION_FIELDS, "attestation", problems):
        return False, problems
    if attestation.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("attestation_id", "proposal_sha256", "policy_sha256", "decision_id", "issuer_id", "reason"):
        if not _nonempty(attestation.get(field)):
            problems.append(f"{field} must be non-empty")
    kind = attestation.get("kind")
    if kind not in {"SUBSTANTIVE_REVIEW", "HUMAN_APPROVAL"}:
        problems.append("kind is invalid")
    allowed = {"ADEQUATE", "INADEQUATE", "ABSTAIN"} if kind == "SUBSTANTIVE_REVIEW" else {"ACCEPT", "REJECT", "ABSTAIN"}
    if attestation.get("verdict") not in allowed:
        problems.append("verdict is invalid for attestation kind")
    authority = attestation.get("authority")
    authority_fields = {"authority_id", "authority_family", "authority_type", "role", "credential_ref"}
    if _exact(authority, authority_fields, "authority", problems):
        for field in authority_fields:
            if not _nonempty(authority.get(field)):
                problems.append(f"authority.{field} must be non-empty")
        expected = "external_evaluator" if kind == "SUBSTANTIVE_REVIEW" else "human"
        if authority.get("authority_type") != expected:
            problems.append(f"authority.authority_type must be {expected}")
    _strings(attestation.get("scope_ids"), "scope_ids", problems)
    _strings(attestation.get("evidence_refs"), "evidence_refs", problems, empty=True)
    issued = _parse_time(attestation.get("issued_at"), "issued_at", problems)
    expires = _parse_time(attestation.get("expires_at"), "expires_at", problems)
    if issued and expires and issued >= expires:
        problems.append("issued_at must precede expires_at")
    return not problems, problems


def _url_origin(value: Any) -> str | None:
    if not _nonempty(value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}"


def validate_source_authority(source: Any) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if not _exact(source, SOURCE_AUTHORITY_FIELDS, "source_authority", problems):
        return False, problems
    if source.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("source_id", "authority_class", "independence_group", "issuer_id"):
        if not _nonempty(source.get(field)):
            problems.append(f"source_authority.{field} must be non-empty")
    origin = _url_origin(source.get("url_origin"))
    if not origin or origin != source.get("url_origin"):
        problems.append("source_authority.url_origin must be a normalized http(s) origin")
    start = _parse_time(source.get("valid_from"), "source_authority.valid_from", problems)
    end = _parse_time(source.get("expires_at"), "source_authority.expires_at", problems)
    if start and end and start >= end:
        problems.append("source_authority.valid_from must precede expires_at")
    return not problems, problems


def _db_path(workspace: str | Path) -> Path:
    ws = resolve_workspace_override(workspace)
    path = ws / "ops-local" / "research-sufficiency.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(workspace: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(workspace)))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS policy(
      policy_id TEXT PRIMARY KEY, policy_sha256 TEXT NOT NULL,
      canonical_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_authority(
      source_id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
      canonical_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS proposal(
      proposal_sha256 TEXT PRIMARY KEY, canonical_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS attestation(
      attestation_id TEXT PRIMARY KEY, attestation_sha256 TEXT NOT NULL,
      canonical_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS receipt(
      receipt_id TEXT PRIMARY KEY, receipt_sha256 TEXT NOT NULL,
      canonical_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS trust_proof(
      subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
      canonical_json TEXT NOT NULL, PRIMARY KEY(subject_kind,subject_id));
    """)
    return conn


def _insert_immutable(conn: sqlite3.Connection, table: str, key_name: str, key: str,
                      canonical: str, created_at: str, digest_name: str | None = None) -> None:
    row = conn.execute(f"SELECT canonical_json FROM {table} WHERE {key_name}=?", (key,)).fetchone()
    if row:
        if row["canonical_json"] != canonical:
            raise ValueError(f"immutable {table} identity collision: {key}")
        return
    if digest_name:
        conn.execute(
            f"INSERT INTO {table}({key_name},{digest_name},canonical_json,created_at) VALUES(?,?,?,?)",
            (key, hashlib.sha256(canonical.encode()).hexdigest(), canonical, created_at),
        )
    else:
        conn.execute(
            f"INSERT INTO {table}({key_name},canonical_json,created_at) VALUES(?,?,?)",
            (key, canonical, created_at),
        )


def _insert_trust_proof(conn: sqlite3.Connection, kind: str, subject_id: str,
                        envelope: dict[str, Any]) -> None:
    canonical = canonical_json(envelope)
    row = conn.execute(
        "SELECT canonical_json FROM trust_proof WHERE subject_kind=? AND subject_id=?",
        (kind, subject_id),
    ).fetchone()
    if row:
        if row["canonical_json"] != canonical:
            raise ValueError(f"immutable trust proof collision: {kind}/{subject_id}")
        return
    conn.execute(
        "INSERT INTO trust_proof(subject_kind,subject_id,canonical_json) VALUES(?,?,?)",
        (kind, subject_id, canonical),
    )


def _safe_snapshot(workspace: Path, relative: str) -> Path:
    if not _nonempty(relative):
        raise ValueError("snapshot path must be non-empty")
    target = (workspace / relative).resolve(strict=True)
    target.relative_to(workspace.resolve())
    if not target.is_file():
        raise ValueError(f"snapshot is not a file: {relative}")
    return target


def store_policy(policy: dict[str, Any], trust_envelope: dict[str, Any], *,
                 workspace: str | Path) -> dict[str, Any]:
    """Trusted policy-administration primitive. Intentionally not exposed by builder MCP."""
    ok, problems = validate_sufficiency_policy(policy)
    if not ok:
        raise ValueError(f"invalid sufficiency policy: {problems}")
    from .research_trust import verify_envelope
    trust = verify_envelope(policy, trust_envelope, kind="POLICY")
    canonical = canonical_json(policy)
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(workspace) as conn:
        _insert_immutable(
            conn, "policy", "policy_id", policy["policy_id"], canonical, created_at,
            "policy_sha256",
        )
        _insert_trust_proof(conn, "POLICY", policy["policy_id"], trust_envelope)
    return {"policy_id": policy["policy_id"], "policy_sha256": digest_json(policy),
            "trust": trust}


def lookup_policy(policy_id: str, workspace: str | Path) -> dict[str, Any] | None:
    return _load("policy", "policy_id", policy_id, workspace)


def store_source_authority(source: dict[str, Any], trust_envelope: dict[str, Any], *,
                           workspace: str | Path) -> dict[str, Any]:
    """Trusted source-curation primitive; never exposed by builder MCP."""
    ok, problems = validate_source_authority(source)
    if not ok:
        raise ValueError(f"invalid source authority: {problems}")
    from .research_trust import verify_envelope
    trust = verify_envelope(source, trust_envelope, kind="SOURCE_AUTHORITY")
    canonical = canonical_json(source)
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(workspace) as conn:
        _insert_immutable(conn, "source_authority", "source_id", source["source_id"],
                          canonical, created_at, "source_sha256")
        _insert_trust_proof(conn, "SOURCE_AUTHORITY", source["source_id"], trust_envelope)
    return {"source_id": source["source_id"], "source_sha256": digest_json(source),
            "trust": trust}


def _require_source_authority(evidence: dict[str, Any], workspace: Path, *, now: datetime) -> None:
    origin = _url_origin(evidence.get("source_url"))
    if not origin:
        raise ValueError(f"evidence source_url is not a valid http(s) URL: {evidence['evidence_id']}")
    with _connect(workspace) as conn:
        ids = [row["source_id"] for row in conn.execute(
            "SELECT source_id FROM source_authority ORDER BY source_id"
        ).fetchall()]
    matches = []
    for source_id in ids:
        source = _load("source_authority", "source_id", source_id, workspace)
        if source and source["url_origin"] == origin:
            matches.append(source)
    for source in matches:
        problems: list[str] = []
        start = _parse_time(source["valid_from"], "valid_from", problems)
        end = _parse_time(source["expires_at"], "expires_at", problems)
        if (not problems and start and end and start <= now < end
                and source["authority_class"] == evidence["authority_class"]
                and source["independence_group"] == evidence["independence_group"]):
            return
    raise ValueError(
        f"evidence authority/independence is not backed by a current trusted source record: "
        f"{evidence['evidence_id']}"
    )


def freeze_proposal(proposal: dict[str, Any], policy: dict[str, Any], *, workspace: str | Path) -> dict[str, Any]:
    ok, problems = validate_sufficiency_policy(policy)
    if not ok:
        raise ValueError(f"invalid sufficiency policy: {problems}")
    ok, problems = validate_sufficiency_proposal(proposal)
    if not ok:
        raise ValueError(f"invalid sufficiency proposal: {problems}")
    policy_sha = digest_json(policy)
    if proposal["policy_id"] != policy["policy_id"] or proposal["policy_sha256"] != policy_sha:
        raise ValueError("proposal policy binding mismatch")
    if proposal["task_id"] != policy["task_id"]:
        raise ValueError("proposal task binding mismatch")
    if not _risk_at_least(proposal["decision"]["risk_tier"], policy["risk_tier"]):
        raise ValueError("proposal may not lower the policy risk tier")
    ws = resolve_workspace_override(workspace)
    registered = lookup_policy(policy["policy_id"], ws)
    if registered is None or canonical_json(registered) != canonical_json(policy):
        raise ValueError("policy is not registered on the trusted policy surface")
    report = _safe_snapshot(ws, proposal["report"]["path"])
    if hashlib.sha256(report.read_bytes()).hexdigest() != proposal["report"]["sha256"]:
        raise ValueError("report snapshot hash mismatch")
    for item in proposal["evidence"]:
        _require_source_authority(item, ws, now=datetime.now(timezone.utc))
        snapshot = _safe_snapshot(ws, item["local_path"])
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != item["content_sha256"]:
            raise ValueError(f"evidence snapshot hash mismatch: {item['evidence_id']}")
    proposal_sha = digest_json(proposal)
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(ws) as conn:
        _insert_immutable(conn, "proposal", "proposal_sha256", proposal_sha,
                          canonical_json(proposal), created_at)
    return {"proposal_sha256": proposal_sha, "policy_sha256": policy_sha, "proposal": proposal}


def _load(table: str, key_name: str, key: str, workspace: str | Path) -> dict[str, Any] | None:
    with _connect(workspace) as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE {key_name}=?", (key,)).fetchone()
        proof_kind = {"policy": "POLICY", "attestation": None}.get(table)
        proof_row = None
        if table == "policy":
            proof_row = conn.execute(
                "SELECT canonical_json FROM trust_proof WHERE subject_kind='POLICY' AND subject_id=?",
                (key,),
            ).fetchone()
        elif table == "attestation" and row:
            att_data = json.loads(row["canonical_json"])
            proof_kind = att_data.get("kind")
            proof_row = conn.execute(
                "SELECT canonical_json FROM trust_proof WHERE subject_kind=? AND subject_id=?",
                (proof_kind, key),
            ).fetchone()
    if not row:
        return None
    canonical = row["canonical_json"]
    data = json.loads(canonical)
    actual = hashlib.sha256(canonical.encode()).hexdigest()
    digest_column = {
        "policy": "policy_sha256", "attestation": "attestation_sha256",
        "receipt": "receipt_sha256", "source_authority": "source_sha256",
    }.get(table)
    if digest_column and row[digest_column] != actual:
        raise ValueError(f"stored {table} digest mismatch: {key}")
    if table == "proposal" and (key != actual or digest_json(data) != key):
        raise ValueError(f"stored proposal digest mismatch: {key}")
    if table == "receipt":
        draft = {name: value for name, value in data.items() if name != "receipt_id"}
        expected_id = f"rs_{digest_json(draft)[:24]}"
        if data.get("receipt_id") != key or key != expected_id:
            raise ValueError(f"stored receipt identity mismatch: {key}")
    if table == "source_authority":
        proof_kind = "SOURCE_AUTHORITY"
        with _connect(workspace) as conn:
            proof_row = conn.execute(
                "SELECT canonical_json FROM trust_proof WHERE subject_kind=? AND subject_id=?",
                (proof_kind, key),
            ).fetchone()
    if table in {"policy", "attestation", "source_authority"}:
        if not proof_kind or not proof_row:
            raise ValueError(f"stored {table} lacks external trust proof: {key}")
        from .research_trust import verify_envelope
        verify_envelope(data, json.loads(proof_row["canonical_json"]), kind=proof_kind)
    return data


def store_attestation(attestation: dict[str, Any], trust_envelope: dict[str, Any], *,
                      workspace: str | Path) -> dict[str, Any]:
    """Trusted evaluator/human-console primitive. Intentionally not exposed by builder MCP."""
    ok, problems = validate_attestation(attestation)
    if not ok:
        raise ValueError(f"invalid attestation: {problems}")
    from .research_trust import verify_envelope
    trust = verify_envelope(attestation, trust_envelope, kind=attestation["kind"])
    canonical = canonical_json(attestation)
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(workspace) as conn:
        _insert_immutable(
            conn, "attestation", "attestation_id", attestation["attestation_id"], canonical,
            created_at, "attestation_sha256",
        )
        _insert_trust_proof(conn, attestation["kind"], attestation["attestation_id"],
                            trust_envelope)
    return {"attestation_id": attestation["attestation_id"], "sha256": digest_json(attestation),
            "trust": trust}


def assess_mechanical(frozen_proposal: dict[str, Any], policy: dict[str, Any], *, assessed_at: str) -> dict[str, Any]:
    proposal = frozen_proposal.get("proposal") if isinstance(frozen_proposal, dict) else None
    ok_policy, policy_problems = validate_sufficiency_policy(policy)
    ok_proposal, proposal_problems = validate_sufficiency_proposal(proposal)
    if not ok_policy or not ok_proposal:
        return {"outcome": "UNRESOLVED", "gaps": policy_problems + proposal_problems}
    now_problems: list[str] = []
    now = _parse_time(assessed_at, "assessed_at", now_problems)
    if not now:
        return {"outcome": "UNRESOLVED", "gaps": now_problems}
    gaps: list[str] = []
    if frozen_proposal.get("proposal_sha256") != digest_json(proposal):
        gaps.append("proposal digest mismatch")
    if frozen_proposal.get("policy_sha256") != digest_json(policy):
        gaps.append("policy digest mismatch")
    if proposal["policy_sha256"] != digest_json(policy):
        gaps.append("proposal policy binding mismatch")
    if proposal["task_id"] != policy["task_id"]:
        gaps.append("task binding mismatch")
    if not _risk_at_least(proposal["decision"]["risk_tier"], policy["risk_tier"]):
        gaps.append("proposal lowered policy risk tier")
    policy_start_problems: list[str] = []
    policy_start = _parse_time(policy["valid_from"], "valid_from", policy_start_problems)
    policy_end = _parse_time(policy["expires_at"], "expires_at", policy_start_problems)
    decision_as_of = _parse_time(proposal["decision"]["as_of"], "decision.as_of", policy_start_problems)
    decision_end = _parse_time(proposal["decision"]["expires_at"], "decision.expires_at", policy_start_problems)
    if policy_start_problems or not policy_start or not policy_end or not decision_as_of or not decision_end:
        gaps.extend(policy_start_problems)
    else:
        if now < policy_start or now >= policy_end:
            gaps.append("policy is not currently valid")
        if decision_as_of > now:
            gaps.append("decision as_of is in the future")
        if now >= decision_end:
            gaps.append("decision has expired")

    evidence = {item["evidence_id"]: item for item in proposal["evidence"]}
    claims = {item["claim_id"]: item for item in proposal["claims"]}
    questions = {item["question_id"]: item for item in proposal["questions"]}

    def qualifying(ids: list[str], authority: list[str], max_age: int) -> list[dict]:
        out = []
        for evidence_id in ids:
            item = evidence.get(evidence_id)
            if not item or item["authority_class"] not in authority:
                continue
            parsed: list[str] = []
            captured = _parse_time(item["captured_at"], "captured_at", parsed)
            if not captured or captured > now or (now - captured).total_seconds() > max_age * 86400:
                continue
            out.append(item)
        return out

    for lane in policy["lanes"]:
        lane_claims: list[dict] = []
        for question_id in lane["required_question_ids"]:
            question = questions.get(question_id)
            if not question or question.get("lane_id") != lane["lane_id"]:
                gaps.append(f"lane {lane['lane_id']} missing question {question_id}")
                continue
            if question["status"] != "ANSWERED":
                gaps.append(f"question {question_id} is unresolved")
            for claim_id in question["claim_ids"]:
                claim = claims.get(claim_id)
                if claim and claim.get("lane_id") == lane["lane_id"]:
                    lane_claims.append(claim)
                else:
                    gaps.append(f"question {question_id} references invalid claim {claim_id}")
        ids = [eid for claim in lane_claims for eid in claim["evidence_ids"]]
        qualified = qualifying(ids, lane["required_authority_classes"], lane["max_age_days"])
        groups = {item["independence_group"] for item in qualified}
        if len(groups) < lane["min_independence_groups"]:
            gaps.append(f"lane {lane['lane_id']} lacks authority/independence/freshness coverage")

    rules = {rule["claim_kind"]: rule for rule in policy["claim_rules"]}
    for claim in proposal["claims"]:
        rule = rules.get(claim["claim_kind"])
        if not rule:
            continue
        qualified = qualifying(claim["evidence_ids"], rule["required_authority_classes"], rule["max_age_days"])
        if len({item["independence_group"] for item in qualified}) < rule["min_independence_groups"]:
            gaps.append(f"claim {claim['claim_id']} fails its evidence rule")
    blocking = set(policy["conflict_policy"]["blocking_severities"])
    for conflict in proposal["conflicts"]:
        if conflict["severity"] in blocking and conflict["status"] != "RESOLVED":
            gaps.append(f"blocking conflict unresolved: {conflict['conflict_id']}")
    for uncertainty in proposal["remaining_uncertainties"]:
        if uncertainty["disposition"] == "BLOCK":
            gaps.append(f"blocking uncertainty: {uncertainty['uncertainty_id']}")
    return {"outcome": "MECHANICAL_PASS" if not gaps else "UNRESOLVED", "gaps": sorted(set(gaps))}


def finalize_sufficiency(proposal_sha256: str, policy: dict[str, Any], attestation_ids: list[str],
                         *, workspace: str | Path, assessed_at: str) -> dict[str, Any]:
    proposal = _load("proposal", "proposal_sha256", proposal_sha256, workspace)
    if proposal is None:
        raise ValueError("unknown proposal_sha256")
    registered = lookup_policy(policy.get("policy_id", ""), workspace)
    if registered is None or canonical_json(registered) != canonical_json(policy):
        raise ValueError("policy is not registered on the trusted policy surface")
    policy_sha = digest_json(policy)
    frozen = {"proposal_sha256": proposal_sha256, "policy_sha256": policy_sha, "proposal": proposal}
    mechanical = assess_mechanical(frozen, policy, assessed_at=assessed_at)
    if len(attestation_ids) != len(set(attestation_ids)):
        raise ValueError("attestation_ids must be unique")
    attestations = []
    for attestation_id in attestation_ids:
        item = _load("attestation", "attestation_id", attestation_id, workspace)
        if item is None:
            raise ValueError(f"unknown attestation_id: {attestation_id}")
        attestations.append(item)
    now_problems: list[str] = []
    now = _parse_time(assessed_at, "assessed_at", now_problems)
    assert now is not None
    decision_id = proposal["decision"]["decision_id"]
    valid: list[dict] = []
    binding_gaps: list[str] = []
    for attestation in attestations:
        if (attestation["proposal_sha256"] != proposal_sha256
                or attestation["policy_sha256"] != policy_sha
                or attestation["decision_id"] != decision_id):
            binding_gaps.append(f"attestation binding mismatch: {attestation['attestation_id']}")
            continue
        expires_problems: list[str] = []
        issued = _parse_time(attestation["issued_at"], "issued_at", expires_problems)
        expires = _parse_time(attestation["expires_at"], "expires_at", expires_problems)
        if not issued or issued > now:
            binding_gaps.append(f"attestation issued in the future: {attestation['attestation_id']}")
            continue
        if not expires or expires <= now:
            binding_gaps.append(f"attestation expired: {attestation['attestation_id']}")
            continue
        valid.append(attestation)

    outcome = "SUFFICIENT_FOR_DECISION"
    gaps = list(mechanical["gaps"]) + binding_gaps
    if mechanical["outcome"] != "MECHANICAL_PASS" or binding_gaps:
        outcome = "UNRESOLVED"
    risk = proposal["decision"]["risk_tier"]
    review_policy = policy["review_policy"]
    if outcome == "SUFFICIENT_FOR_DECISION" and _risk_at_least(risk, review_policy["required_from_risk"]):
        reviewers = [item for item in valid if item["kind"] == "SUBSTANTIVE_REVIEW"]
        reviewers = [item for item in reviewers
                     if item["issuer_id"] in review_policy["trusted_issuer_ids"]
                     and set(review_policy["required_scopes"]) <= set(item["scope_ids"])
                     and item["authority"]["authority_id"] != proposal["proposer"]["id"]
                     and item["authority"]["authority_family"] != proposal["proposer"]["family"]]
        if (not reviewers or any(item["verdict"] != "ADEQUATE" for item in reviewers)
                or not any(item["verdict"] == "ADEQUATE" for item in reviewers)):
            outcome = "UNRESOLVED"
            gaps.append("independent substantive review is missing or inadequate")
    if outcome == "SUFFICIENT_FOR_DECISION":
        human_findings: list[tuple[str, str]] = []
        for requirement in policy["human_requirements"]:
            if not _risk_at_least(risk, requirement["required_from_risk"]):
                continue
            humans = [item for item in valid if item["kind"] == "HUMAN_APPROVAL"
                      and item["issuer_id"] in requirement["trusted_issuer_ids"]
                      and requirement["scope_id"] in item["scope_ids"]
                      and item["authority"]["role"] in requirement["accepted_roles"]
                      and item["authority"]["authority_id"] != proposal["proposer"]["id"]]
            if any(item["verdict"] == "REJECT" for item in humans):
                human_findings.append(("REJECT", requirement["scope_id"]))
            elif not any(item["verdict"] == "ACCEPT" for item in humans):
                human_findings.append(("MISSING", requirement["scope_id"]))
        rejected_scopes = [scope for finding, scope in human_findings if finding == "REJECT"]
        missing_scopes = [scope for finding, scope in human_findings if finding == "MISSING"]
        if rejected_scopes:
            outcome = "UNRESOLVED"
            gaps.extend(f"human scope rejected: {scope}" for scope in rejected_scopes)
        elif missing_scopes:
            outcome = "ABSTAIN"
            gaps.extend(f"qualified human scope missing: {scope}" for scope in missing_scopes)

    expiry_candidates = [
        datetime.fromisoformat(policy["expires_at"].replace("Z", "+00:00")),
        datetime.fromisoformat(proposal["decision"]["expires_at"].replace("Z", "+00:00")),
    ]
    expiry_candidates += [datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")) for item in valid]
    expires_at = min(expiry_candidates).astimezone(timezone.utc).isoformat()
    draft = {
        "schema_version": SCHEMA_VERSION,
        "run_id": proposal["run_id"],
        "task_id": proposal["task_id"],
        "research_task_id": proposal["research_task_id"],
        "decision_id": decision_id,
        "proposal_sha256": proposal_sha256,
        "policy_sha256": policy_sha,
        "mechanical_assessment": mechanical,
        "attestation_sha256s": [digest_json(item) for item in valid],
        "outcome": outcome,
        "unlocked_work": proposal["decision"]["downstream_work"] if outcome == "SUFFICIENT_FOR_DECISION" else [],
        "remaining_uncertainty": sorted(set(gaps)),
        "issued_at": now.astimezone(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "invalidation_triggers": sorted(set(policy["invalidation_triggers"] + proposal["invalidation_triggers"])),
    }
    receipt_id = f"rs_{digest_json(draft)[:24]}"
    receipt = {"receipt_id": receipt_id, **draft}
    canonical = canonical_json(receipt)
    with _connect(workspace) as conn:
        _insert_immutable(conn, "receipt", "receipt_id", receipt_id, canonical,
                          draft["issued_at"], "receipt_sha256")
    return receipt


def lookup_sufficiency_receipt(receipt_id: str, workspace: str | Path) -> dict[str, Any] | None:
    return _load("receipt", "receipt_id", receipt_id, workspace)


def validate_sufficiency_receipt(receipt_id: Any, *, expected_task_id: str,
                                 expected_decision_id: str, expected_policy_sha256: str,
                                 workspace: str | Path, now: str,
                                 expected_research_task_id: str | None = None,
                                 expected_run_id: str | None = None) -> dict[str, Any]:
    if not _nonempty(receipt_id):
        return {"valid": False, "reason": "receipt_id must be non-empty"}
    receipt = lookup_sufficiency_receipt(receipt_id, workspace)
    if receipt is None:
        return {"valid": False, "reason": "unknown receipt"}
    if receipt.get("outcome") not in OUTCOMES:
        return {"valid": False, "reason": "invalid receipt outcome"}
    if receipt.get("task_id") != expected_task_id:
        return {"valid": False, "reason": "task mismatch"}
    if expected_run_id is not None and receipt.get("run_id") != expected_run_id:
        return {"valid": False, "reason": "run mismatch"}
    if (expected_research_task_id is not None
            and receipt.get("research_task_id") != expected_research_task_id):
        return {"valid": False, "reason": "research task mismatch"}
    if receipt.get("decision_id") != expected_decision_id:
        return {"valid": False, "reason": "decision mismatch"}
    if receipt.get("policy_sha256") != expected_policy_sha256:
        return {"valid": False, "reason": "policy mismatch"}
    problems: list[str] = []
    current = _parse_time(now, "now", problems)
    expires = _parse_time(receipt.get("expires_at"), "expires_at", problems)
    if problems or not current or not expires or current >= expires:
        return {"valid": False, "reason": "receipt expired or time invalid"}
    return {"valid": True, "outcome": receipt["outcome"], "receipt": receipt}
