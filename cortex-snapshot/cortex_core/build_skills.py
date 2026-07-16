"""M3 of BUILD-01: the template-injection **build-skill registry**.

A build-skill turns a vague app-build utterance into a deterministically-rendered,
App-Contract-compliant app WITHOUT letting a model write code. The model's ENTIRE
deliverable is ONE JSON object matching a restricted slot schema; the harness owns
every line of rendered code (template + anchored-block substitution, predicates from
an allowlisted enum -- never an executable expression from the model).

Mirrors `cortex_core/patterns.py` discipline: a frozen dataclass, `load_*`,
`validate_*`, JSON+md-authored sidecars, fail-at-load validation. The one rule that
cannot be waived (GLM review fix, load-enforced): an ``app_build`` skill whose
``done_checks`` lack a behavioral state check (``data_persists`` -- or the
``schema_real`` + write-``buttons_work`` fallback) REFUSES to load. No gate theater.

Firewall: this module imports stdlib + ``.app_contract`` + ``.config`` ONLY. The
renderer never consults an LLM (AST-enforced by the test suite). It does NOT import
``app_gates`` -- the gate observes what this renders; rendering must not depend on it.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .app_contract import (
    BEHAVIORAL_STATE_FALLBACK,
    BEHAVIORAL_STATE_PRIMARY,
    validate_check_spec,
)
from .config import resolve_workspace

SKILLS_DIRNAME = "skills"

# Identifier fields (entity / field name / column / css class): a-z start, then
# a-z0-9_, capped at 30 chars. No uppercase, no quotes, no punctuation -> the
# rendered SQL/HTML/CSS can never carry an injected token.
IDENT_RE = r"^[a-z][a-z0-9_]{0,29}$"
_IDENT = re.compile(IDENT_RE)

# SQL / reserved words that must never become a table, column, or entity name.
RESERVED_IDENTIFIERS: frozenset[str] = frozenset(
    {"id", "rowid", "sqlite_master", "table", "select", "insert", "update",
     "delete", "drop", "where", "from", "into", "values", "create", "alter"}
)

_MARKER = "{{"  # unresolved-template-marker sentinel (fail-closed on any leftover)


class SkillValidationError(ValueError):
    """A skill.json is malformed or violates the mandatory-behavioral-check rule."""


class SlotValidationError(ValueError):
    """A slot payload failed schema validation; render must NOT proceed."""


class RenderError(ValueError):
    """Anchored replacement failed or an unresolved marker survived rendering."""


# --------------------------------------------------------------------------- #
# dataclasses
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SlotSpec:
    """The ONE JSON object the model fills. ``schema`` is the restricted dialect
    (see ``validate_schema_value``); ``example`` is a known-valid worked example
    (validated against ``schema`` at skill load)."""

    name: str
    schema: dict[str, Any]
    example: dict[str, Any]


#: terra HIGH #4: every skill declares its ROLE. "fresh_build" skills scaffold a NEW app on a
#: blank dir and are the ONLY admissible Director primaries; "follow_on" skills edit an existing
#: scaffold and can never be the primary (executing one on a blank dir is a RenderError). The
#: role is immutable at runtime (frozen dataclass) and validated at load; a skill.json that
#: omits it defaults to "follow_on" -- fail-safe: an unlabeled skill cannot become a primary.
SKILL_ROLES = ("fresh_build", "follow_on")


@dataclass(frozen=True)
class BuildSkill:
    skill_id: str
    title: str
    track: str  # "app_build"
    trigger_features: list[str]
    preconditions: list[dict[str, Any]]  # deterministic (file_absent/exists/sqlite_column_exists)
    slot: SlotSpec
    step_prompt: str  # imperative guidance; harness embeds schema+example around it
    tools: list[str]  # reserved for the later Director increment; [] in BUILD-01
    done_checks: list[dict[str, Any]]  # app_contract check specs
    occurrence_count: int = 0
    pass_count: int = 0
    origin: str = "seed_fable_authored"
    verified: bool = False
    status: str = "active"
    schema_version: int = 1
    role: str = "follow_on"  # terra HIGH #4; fail-safe default (see SKILL_ROLES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "title": self.title,
            "track": self.track,
            "role": self.role,
            "trigger_features": list(self.trigger_features),
            "preconditions": copy.deepcopy(self.preconditions),
            "slot": {"name": self.slot.name,
                     "schema": copy.deepcopy(self.slot.schema),
                     "example": copy.deepcopy(self.slot.example)},
            "step_prompt": self.step_prompt,
            "tools": list(self.tools),
            "done_checks": copy.deepcopy(self.done_checks),
            "occurrence_count": self.occurrence_count,
            "pass_count": self.pass_count,
            "origin": self.origin,
            "verified": self.verified,
            "status": self.status,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BuildSkill":
        slot = d["slot"]
        return cls(
            skill_id=d["skill_id"],
            title=d["title"],
            track=d.get("track", "app_build"),
            trigger_features=list(d.get("trigger_features", [])),
            preconditions=list(d.get("preconditions", [])),
            slot=SlotSpec(name=slot["name"], schema=slot["schema"], example=slot["example"]),
            step_prompt=d["step_prompt"],
            tools=list(d.get("tools", [])),
            done_checks=list(d.get("done_checks", [])),
            occurrence_count=int(d.get("occurrence_count", 0)),
            pass_count=int(d.get("pass_count", 0)),
            origin=d.get("origin", "seed_fable_authored"),
            verified=bool(d.get("verified", False)),
            status=d.get("status", "active"),
            schema_version=int(d.get("schema_version", 1)),
            role=d.get("role", "follow_on"),
        )


# --------------------------------------------------------------------------- #
# the restricted slot-schema dialect (local, ~no jsonschema dep)
# --------------------------------------------------------------------------- #

_ALLOWED_TYPES = {"object", "array", "string", "integer", "boolean"}


def _typename(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def validate_schema_value(schema: dict[str, Any], value: Any, path: str = "$") -> list[str]:
    """The restricted-dialect validator (pure). Supports only:
    type/object/properties/required/enum/pattern/minLength/maxLength/minItems/
    maxItems/items, plus ``additionalProperties`` and a custom ``format:"identifier"``
    (enforces IDENT_RE + the reserved-word blocklist). Returns path-bearing error
    strings (empty == valid)."""
    errors: list[str] = []
    t = schema.get("type")

    if t == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {_typename(value)}"]
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: required property missing")
        allow_extra = schema.get("additionalProperties", False)
        for k, v in value.items():
            if k in props:
                errors += validate_schema_value(props[k], v, f"{path}.{k}")
            elif not allow_extra:
                errors.append(f"{path}.{k}: unknown property (additionalProperties not allowed)")

    elif t == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {_typename(value)}"]
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: has {len(value)} items, minItems is {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: has {len(value)} items, maxItems is {schema['maxItems']}")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                errors += validate_schema_value(items, item, f"{path}[{i}]")

    elif t == "string":
        if not isinstance(value, str):
            return [f"{path}: expected string, got {_typename(value)}"]
        if schema.get("format") == "identifier":
            if not _IDENT.match(value):
                errors.append(f"{path}: {value!r} is not a valid identifier ({IDENT_RE})")
            elif value.lower() in RESERVED_IDENTIFIERS:
                errors.append(f"{path}: {value!r} is a reserved word")
        if "pattern" in schema and not re.match(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")

    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected integer, got {_typename(value)}")

    elif t == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean, got {_typename(value)}")

    elif t is not None and t not in _ALLOWED_TYPES:
        errors.append(f"{path}: unsupported schema type {t!r}")

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")

    return errors


# --------------------------------------------------------------------------- #
# workspace / load
# --------------------------------------------------------------------------- #

def skills_dir(workspace: str | Path | None = None) -> Path:
    return resolve_workspace(workspace) / SKILLS_DIRNAME


def load_skills(workspace: str | Path | None = None) -> dict[str, BuildSkill]:
    """Load every ``skills/*/skill.json`` build-skill. Fail-at-load: ANY invalid
    skill raises ``SkillValidationError`` (never a silent skip -- a broken skill in
    the registry is a load error, mirroring patterns.py's refuse-don't-warn stance).
    Directories without a ``skill.json`` (e.g. the SKILL.md agent skills) are ignored.
    """
    d = skills_dir(workspace)
    out: dict[str, BuildSkill] = {}
    if not d.is_dir():
        return out
    for sj in sorted(d.glob("*/skill.json")):
        try:
            data = json.loads(sj.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise SkillValidationError(f"{sj}: not valid JSON: {exc}") from exc
        try:
            skill = BuildSkill.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            raise SkillValidationError(f"{sj}: malformed skill: {exc}") from exc
        ok, errors = validate_skill(skill, workspace)
        if not ok:
            raise SkillValidationError(f"{sj}: {'; '.join(errors)}")
        out[skill.skill_id] = skill
    return out


def _has_write_button(done_checks: list[dict[str, Any]]) -> bool:
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for c in done_checks:
        if c.get("kind") != "buttons_work":
            continue
        for action in c.get("actions", []):
            method = (action.get("request") or {}).get("method", "").upper()
            if method in write_methods:
                return True
    return False


#: Cache the (renderer-derived) fresh-build capability so load_skills doesn't re-render on every
#: call. Keyed by skill_id + a digest of the slot example (the only render-relevant inputs).
_FRESH_PROBE_CACHE: dict[str, bool] = {}


def _fresh_build_capable(skill: "BuildSkill", workspace: str | Path | None = None) -> bool:
    """terra RE-REVIEW #4: DERIVE primary-eligibility from the RENDERER, not the manifest.
    Render the skill's own (validated) slot example onto an EMPTY temp dir: a genuine
    fresh-build renderer scaffolds a whole app from scratch (succeeds); every follow-on
    renderer requires an existing scaffold app.py and raises RenderError. Any failure ->
    not fresh-capable (fail-safe). `workspace` is threaded through so a renderer that
    resolves its templates from the skills dir (scaffold does) finds them for THIS registry.
    Result cached (per skill_id + example + workspace); the temp dir is always cleaned up."""
    import hashlib
    import shutil
    import tempfile
    key = skill.skill_id + "|" + str(workspace) + "|" + hashlib.sha256(
        json.dumps(skill.slot.example, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    if key in _FRESH_PROBE_CACHE:
        return _FRESH_PROBE_CACHE[key]
    tmp = Path(tempfile.mkdtemp(prefix="cortex_role_probe_"))
    try:
        render_skill(skill, skill.slot.example, tmp, workspace)   # empty dir -> only fresh wins
        capable = True
    except Exception:  # noqa: BLE001 -- RenderError (needs scaffold) or anything else -> not fresh
        capable = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _FRESH_PROBE_CACHE[key] = capable
    return capable


def validate_skill(skill: BuildSkill, workspace: str | Path | None = None) -> tuple[bool, list[str]]:
    """Load-time gate. Refuses (returns False) when:
      - an ``app_build`` skill's done_checks lack a behavioral state check
        (``data_persists`` OR ``schema_real`` + a write ``buttons_work`` action);
      - any done_check fails ``validate_check_spec`` (unknown kind, empty security, ...);
      - the slot ``example`` does not validate against its own schema (self-consistency).
    """
    errors: list[str] = []

    if not skill.skill_id or not _IDENT.match(skill.skill_id.replace("-", "_")):
        errors.append(f"skill_id {skill.skill_id!r} is not a safe directory identifier")

    # terra HIGH #4: the role must be one of the declared roles -- an arbitrary string
    # could otherwise dodge both the "fresh only as primary" filter and the fail-safe default.
    if skill.role not in SKILL_ROLES:
        errors.append(f"role {skill.role!r} must be one of {list(SKILL_ROLES)}")
    elif skill.track == "app_build":
        # terra RE-REVIEW #4: primary-eligibility is DERIVED from the renderer, not trusted
        # from the manifest string. Probe the renderer on an EMPTY dir: a fresh_build skill
        # scaffolds from scratch (succeeds); a follow_on edits an existing app.py (RenderError).
        # A declared/renderer MISMATCH is a load error -- flipping add-dashboard's manifest role
        # to "fresh_build" no longer makes it primary-eligible; it fails here.
        capable = _fresh_build_capable(skill, workspace)
        declared_fresh = skill.role == "fresh_build"
        if declared_fresh != capable:
            errors.append(
                f"role {skill.role!r} contradicts the renderer capability probe "
                f"(builds_on_empty_dir={capable}): a skill's primary-eligibility is derived "
                f"from whether its renderer scaffolds on an empty dir, NOT from the manifest "
                f"string (terra finding #4)")

    # every done_check must be a well-formed check spec
    for i, c in enumerate(skill.done_checks):
        for e in validate_check_spec(c):
            errors.append(f"done_checks[{i}]: {e}")

    # THE mandatory rule (no gate theater)
    if skill.track == "app_build":
        kinds = {c.get("kind") for c in skill.done_checks}
        primary = BEHAVIORAL_STATE_PRIMARY in kinds
        fallback = all(k in kinds for k in BEHAVIORAL_STATE_FALLBACK) and _has_write_button(skill.done_checks)
        if not (primary or fallback):
            errors.append(
                f"app_build skill must include a behavioral state check: "
                f"{BEHAVIORAL_STATE_PRIMARY!r}, OR {list(BEHAVIORAL_STATE_FALLBACK)} "
                f"with a write (POST/PUT/PATCH/DELETE) buttons_work action "
                f"(the no-gate-theater rule)"
            )

    # the example must satisfy the skill's own slot schema
    example_errors = validate_schema_value(skill.slot.schema, skill.slot.example)
    for e in example_errors:
        errors.append(f"slot.example invalid: {e}")

    return (not errors, errors)


def validate_slot(skill: BuildSkill, payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a model-supplied slot payload against the skill's slot schema, then apply any
    cross-field semantic rules the JSON schema can't express. An invalid slot is a refusal --
    render_skill raises rather than proceed."""
    if not isinstance(payload, dict):
        return (False, [f"slot payload must be a JSON object, got {_typename(payload)}"])
    errors = validate_schema_value(skill.slot.schema, payload)
    if not errors:
        errors = _semantic_slot_errors(skill.skill_id, payload)
    return (not errors, errors)


def _semantic_slot_errors(skill_id: str, payload: dict[str, Any]) -> list[str]:
    """Cross-field slot rules beyond the JSON schema. For the CRUD scaffold: the gate tags and
    re-finds rows by planting a hidden canary in a TEXT field (persistence / regression / metric /
    search checks all rely on it), so a scaffold with NO text field is un-gateable -- a hex canary
    minted into an int/bool column just 400s. Require at least one text field (fail-closed here so
    the driver folds the reason into its retry, rather than the app silently failing its own gate)."""
    if skill_id in ("scaffold-crud-sqlite", "add-second-entity-relation"):
        fields = payload.get("fields")
        if isinstance(fields, list) and not any(
                isinstance(f, dict) and f.get("type") == "text" for f in fields):
            noun = "child entity" if skill_id == "add-second-entity-relation" else "scaffold"
            return [f"{noun} needs at least one text field (e.g. a name/title/label) so rows are "
                    "human-readable and uniquely identifiable; add a field with \"type\":\"text\""]
    return []


# --------------------------------------------------------------------------- #
# tolerant slot extraction (from noisy model output)
# --------------------------------------------------------------------------- #

def extract_slot_json(model_output: str) -> dict[str, Any] | None:
    """Pull the FIRST JSON object out of noisy model output (prose, ```json fences).
    Returns None on garbage -- NEVER raises (a failed extraction is an honest refusal
    upstream, not a crash)."""
    if not isinstance(model_output, str):
        return None
    n = len(model_output)
    i = 0
    while i < n:
        if model_output[i] != "{":
            i += 1
            continue
        # scan for the matching closing brace, string-aware
        depth = 0
        j = i
        in_str = False
        esc = False
        while j < n:
            ch = model_output[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = model_output[i:j + 1]
                        try:
                            obj = json.loads(candidate)
                        except Exception:  # noqa: BLE001
                            break  # not valid; advance past this '{'
                        if isinstance(obj, dict):
                            return obj
                        break
            j += 1
        i += 1
    return None


# --------------------------------------------------------------------------- #
# deterministic anchored rendering
# --------------------------------------------------------------------------- #

def _substitute(text: str, mapping: dict[str, str]) -> str:
    """Replace every ``{{key}}`` from ``mapping``; a surviving ``{{`` is fail-closed
    (``RenderError``) -- the model never gets to leave an unresolved marker."""
    for key, val in mapping.items():
        text = text.replace("{{" + key + "}}", val)
    if _MARKER in text:
        leftovers = re.findall(r"\{\{[^}]*\}\}", text)
        raise RenderError(f"unresolved template marker(s): {leftovers}")
    return text


def replace_anchored(text: str, anchor_name: str, new_block: str) -> str:
    """Replace the lines strictly BETWEEN a matched
    ``CORTEX-SLOT:<anchor_name> BEGIN`` / ``... END`` pair, preserving the marker
    lines. Missing OR duplicated anchor -> ``RenderError`` (fail-closed). Works for
    ``#``, ``/* */`` and ``<!-- -->`` comment styles (matches on the marker token)."""
    begin = f"CORTEX-SLOT:{anchor_name} BEGIN"
    end = f"CORTEX-SLOT:{anchor_name} END"
    lines = text.split("\n")
    b = [i for i, ln in enumerate(lines) if begin in ln]
    e = [i for i, ln in enumerate(lines) if end in ln]
    if len(b) != 1 or len(e) != 1:
        raise RenderError(
            f"anchor {anchor_name!r}: need exactly one BEGIN and one END "
            f"(found {len(b)} BEGIN / {len(e)} END)"
        )
    if b[0] >= e[0]:
        raise RenderError(f"anchor {anchor_name!r}: END precedes BEGIN")
    block_lines = new_block.split("\n") if new_block else []
    out = lines[: b[0] + 1] + block_lines + lines[e[0]:]
    return "\n".join(out)


def _render_crud_scaffold(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                          workspace: str | Path | None) -> list[Path]:
    tmpl = skills_dir(workspace) / skill.skill_id / "templates" / "app.py.tmpl"
    text = tmpl.read_text(encoding="utf-8", errors="replace")
    entity = slot["entity"]
    table = entity + "s"  # deterministic pluralization -> route /<table>
    fields_json = json.dumps(slot["fields"], sort_keys=True)
    text = _substitute(text, {"slot.entity": entity, "table": table, "fields_json": fields_json})
    app_dir.mkdir(parents=True, exist_ok=True)
    out = app_dir / "app.py"
    out.write_text(text, encoding="utf-8")
    return [out]


def _render_conditional_class(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                              workspace: str | Path | None) -> list[Path]:
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-conditional-class needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    # Predicate is DATA, not code: the model's op is an enum mapped at runtime by the
    # scaffold's _apply_op; the value is injected via json.loads so quotes can't inject.
    cond = {"column": slot["column"], "op": slot["op"],
            "value": slot["value"], "css_class": slot["css_class"]}
    cond_json = json.dumps(cond, sort_keys=True)
    row_block = (
        "    _cond = json.loads(r'''" + cond_json + "''')\n"
        "    row_class = _cond[\"css_class\"] if _apply_op("
        "str(row[_cond[\"column\"]]), _cond[\"op\"], _cond[\"value\"]) else \"\""
    )
    text = replace_anchored(text, "row_class", row_block)
    # css_class is an identifier, color is a validated 12-name enum -> no CSS injection.
    # Emitted as a Python string assignment (json.dumps quotes safely) so the anchor
    # block stays valid Python.
    css_rule = "." + slot["css_class"] + " { background-color: " + slot["color"] + "; }"
    css_block = "EXTRA_CSS = " + json.dumps(css_rule)
    text = replace_anchored(text, "extra_css", css_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


# op ENUM -> SQL comparison operator. The model picks one of these NAMES; the harness maps it to
# the operator string. The model never supplies an executable expression or raw SQL.
_METRIC_SQL_OPS = {"eq": "=", "ne": "!=", "gt": ">", "lt": "<"}


def _render_summary_metric(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                           workspace: str | Path | None) -> list[Path]:
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-summary-metric needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    # field is a validated identifier (IDENT_RE + reserved blocklist) -> safe to bake into the SQL
    # string. op is a validated enum -> mapped to an operator by the harness map above. value is a
    # BOUND PARAM (never string-formatted into SQL). label is html.escaped at runtime. The runtime
    # TABLE variable (already defined in the scaffold app) keeps this correct for ANY entity.
    field = slot["field"]
    op_sql = _METRIC_SQL_OPS[slot["op"]]
    value_literal = json.dumps(str(slot["value"]))   # safe Python string literal for the bound param
    label_literal = json.dumps(str(slot["label"]))   # safe Python string literal, escaped at runtime
    field_literal = json.dumps(field)
    block = (
        # Coerce the compared value THE SAME WAY the app stores the field, so a bool field stored as
        # INTEGER 0/1 matches a natural-language value like "true"/"yes" (a raw 'true' text compare
        # would never equal integer 1). Falls back to the raw value if coercion doesn't apply.
        "    _mfield = next((_f for _f in FIELDS if _f[\"name\"] == " + field_literal + "), None)\n"
        "    try:\n"
        "        _mval = _coerce(_mfield, " + value_literal + ") if _mfield else " + value_literal + "\n"
        "    except (ValueError, TypeError):\n"
        "        _mval = " + value_literal + "\n"
        "    with _connect() as _mconn:\n"
        "        _mcount = _mconn.execute(\n"
        "            \"SELECT COUNT(*) FROM \" + TABLE + \" WHERE " + field + " " + op_sql + " ?\",\n"
        "            (_mval,),\n"
        "        ).fetchone()[0]\n"
        "    metrics_html = (\n"
        "        '<div class=\"metric\" data-cortex-metric=\"' + html.escape(str(_mcount)) + '\">'\n"
        "        + html.escape(" + label_literal + ") + ': ' + html.escape(str(_mcount)) + '</div>'\n"
        "    )"
    )
    text = replace_anchored(text, "metrics", block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_search_filter(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                          workspace: str | Path | None) -> list[Path]:
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-search-filter needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    # field is a validated identifier (IDENT_RE + reserved blocklist) -> safe to bake into the SQL
    # column position. The search term rides a BOUND PARAM (`%q%`), never string-formatted into SQL,
    # and the filter only applies when q is non-empty (empty q -> full list, byte-identical to the
    # scaffold default). The runtime TABLE variable keeps this correct for ANY entity.
    field = slot["field"]
    filter_block = (
        "        if q:\n"
        "            rows = conn.execute(\n"
        "                \"SELECT * FROM \" + TABLE + \" WHERE " + field + " LIKE ? ORDER BY id\",\n"
        "                (\"%\" + q + \"%\",),\n"
        "            ).fetchall()\n"
        "        else:\n"
        "            rows = conn.execute(\"SELECT * FROM \" + TABLE + \" ORDER BY id\").fetchall()"
    )
    text = replace_anchored(text, "search_filter", filter_block)
    # A plain GET search form. This HTML is a fixed HARNESS-authored constant (no model input, no
    # single quotes) -> emitting it as a single-quoted Python literal is safe and injection-free.
    box_html = '<form method="get"><input name="q" placeholder="search"><button type="submit">search</button></form>'
    assert "'" not in box_html  # guard: keep the single-quoted-literal emission safe
    box_block = "    search_box = '" + box_html + "'"
    text = replace_anchored(text, "search_box", box_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_delete_confirm(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                           workspace: str | Path | None) -> list[Path]:
    """Inject a GUARDED per-row delete: a delete button per row (carrying the row id + a confirm
    flag) and a server-side /<table>/delete endpoint that REJECTS any request lacking confirm=yes,
    then DELETEs by id from the file-backed sqlite. Parameterless -- the slot is ignored; every line
    is harness-authored and keyed to the runtime TABLE, so it is correct for ANY entity and carries
    zero model input into the code path."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-delete-with-confirm needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")

    # Per-row delete control. Fixed harness HTML; the only dynamic parts are the runtime TABLE and
    # the row's id, and the id is html.escape'd. confirm=yes rides a hidden field so the button works;
    # the SERVER still enforces the confirmation (an unconfirmed direct POST is rejected below).
    delete_cell_block = (
        "    delete_cell = (\n"
        "        '<td><form method=\"post\" action=\"/' + TABLE + '/delete\">'\n"
        "        '<input type=\"hidden\" name=\"id\" value=\"' + html.escape(str(row[\"id\"])) + '\">'\n"
        "        '<input type=\"hidden\" name=\"confirm\" value=\"yes\">'\n"
        "        '<button type=\"submit\">delete</button></form></td>')"
    )
    text = replace_anchored(text, "delete_cell", delete_cell_block)

    # Server-side delete endpoint with the confirmation GUARD. `raw` (the request body) is already
    # read at the top of do_POST; parse_qs/_connect/_list_html/TABLE are all in scope.
    delete_route_block = (
        "        if path == \"/{}/delete\".format(TABLE):\n"
        "            try:\n"
        "                dform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>bad request</h1>\")\n"
        "                return\n"
        "            if (dform.get(\"confirm\") or [\"\"])[0] != \"yes\":\n"
        "                self._send(400, \"<h1>confirm required</h1><p>deletion must be confirmed</p>\")\n"
        "                return\n"
        "            try:\n"
        "                _del_id = int((dform.get(\"id\") or [\"\"])[0])\n"
        "            except (ValueError, TypeError):\n"
        "                self._send(400, \"<h1>invalid id</h1>\")\n"
        "                return\n"
        "            with _connect() as conn:\n"
        "                conn.execute(\"DELETE FROM {} WHERE id = ?\".format(TABLE), (_del_id,))\n"
        "                conn.commit()\n"
        "            self._send(200, _list_html())\n"
        "            return"
    )
    text = replace_anchored(text, "delete_route", delete_route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_edit_record(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                        workspace: str | Path | None) -> list[Path]:
    """Inject a per-row EDIT: a per-row form pre-filled with the row's current values (+ hidden id)
    and a server-side /<table>/edit endpoint that re-validates the whole row and UPDATEs it WHERE
    id = ? -- so an edit changes exactly the targeted row. Parameterless (full-row edit); every line
    is harness-authored, keyed to the runtime FIELDS/TABLE, correct for ANY entity, zero model input
    in the code path. Reuses the scaffold's own _validate/_coerce so edits obey the same rules as
    creates."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-edit-record needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")

    # Per-row edit form: one input per field, pre-filled with the row's escaped current value.
    edit_cell_block = (
        "    _edit_inputs = \"\".join(\n"
        "        '<input name=\"{}\" value=\"{}\">'.format(\n"
        "            html.escape(_f[\"name\"]), html.escape(str(row[_f[\"name\"]]))) for _f in FIELDS)\n"
        "    edit_cell = (\n"
        "        '<td><form method=\"post\" action=\"/' + TABLE + '/edit\">'\n"
        "        '<input type=\"hidden\" name=\"id\" value=\"' + html.escape(str(row[\"id\"])) + '\">'\n"
        "        + _edit_inputs +\n"
        "        '<button type=\"submit\">save</button></form></td>')"
    )
    text = replace_anchored(text, "edit_cell", edit_cell_block)

    # Server-side edit endpoint: validate the full row (same rules as create), then UPDATE by id.
    # `raw` is already read at the top of do_POST; _validate/_coerce/FIELDS/_connect/_list_html scope.
    edit_route_block = (
        "        if path == \"/{}/edit\".format(TABLE):\n"
        "            try:\n"
        "                eform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>bad request</h1>\")\n"
        "                return\n"
        "            try:\n"
        "                _edit_id = int((eform.get(\"id\") or [\"\"])[0])\n"
        "            except (ValueError, TypeError):\n"
        "                self._send(400, \"<h1>invalid id</h1>\")\n"
        "                return\n"
        "            _eerr = _validate(eform)\n"
        "            if _eerr:\n"
        "                self._send(400, \"<h1>invalid</h1><p>{}</p>\".format(html.escape(_eerr)))\n"
        "                return\n"
        "            _evalues = [_coerce(f, eform.get(f[\"name\"], [\"\"])[0]) for f in FIELDS]\n"
        "            _esets = \", \".join(f[\"name\"] + \" = ?\" for f in FIELDS)\n"
        "            try:\n"
        "                with _connect() as conn:\n"
        "                    conn.execute(\"UPDATE {} SET {} WHERE id = ?\".format(TABLE, _esets),\n"
        "                                 _evalues + [_edit_id])\n"
        "                    conn.commit()\n"
        "            except sqlite3.IntegrityError:\n"
        "                self._send(400, \"<h1>duplicate</h1>\")\n"
        "                return\n"
        "            self._send(200, _list_html())\n"
        "            return"
    )
    text = replace_anchored(text, "edit_route", edit_route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_role_gate(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                      workspace: str | Path | None) -> list[Path]:
    """Inject a token-protected admin route: GET /<...>/admin/export returns every row as JSON, but
    ONLY when the request carries the correct X-Admin-Token (the scaffold's visible ADMIN_TOKEN);
    otherwise 401. Purely additive -- it adds a NEW route and touches no existing one, so it composes
    with every other skill. Parameterless; every line is harness-authored, keyed to runtime
    TABLE/ADMIN_TOKEN, correct for ANY entity, zero model input in the code path."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-role-gate needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    admin_route_block = (
        "        if path == \"/admin/export\":\n"
        "            if self.headers.get(\"X-Admin-Token\") != ADMIN_TOKEN:\n"
        "                self._send(401, \"<h1>unauthorized</h1>\")\n"
        "                return\n"
        "            with _connect() as conn:\n"
        "                _rows = [dict(_r) for _r in\n"
        "                         conn.execute(\"SELECT * FROM \" + TABLE + \" ORDER BY id\").fetchall()]\n"
        "            self._send(200, json.dumps(_rows), ctype=\"application/json\")\n"
        "            return"
    )
    text = replace_anchored(text, "admin_route", admin_route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_audit_log(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                      workspace: str | Path | None) -> list[Path]:
    """Inject an APPEND-ONLY audit log: an `audit_log` table, an append on every create (recording
    the created row's primary text value), and a GET /audit view. Parameterless; harness-authored,
    keyed to runtime FIELDS/ENTITY, correct for ANY entity. The log is append-only — the app never
    updates or deletes past entries."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-audit-log needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")

    schema_block = (
        "        conn.execute(\n"
        "            \"CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, \"\n"
        "            \"action TEXT, entity TEXT, detail TEXT)\"\n"
        "        )"
    )
    text = replace_anchored(text, "audit_schema", schema_block)

    # Append one entry per create, recording the new row's primary text value. `form` is in scope.
    write_block = (
        "            _au_f = next((_f for _f in FIELDS if _f[\"type\"] == \"text\"), FIELDS[0])\n"
        "            with _connect() as _au:\n"
        "                _au.execute(\n"
        "                    \"INSERT INTO audit_log (action, entity, detail) VALUES (?, ?, ?)\",\n"
        "                    (\"create\", ENTITY, form.get(_au_f[\"name\"], [\"\"])[0]))\n"
        "                _au.commit()"
    )
    text = replace_anchored(text, "audit_write", write_block)

    route_block = (
        "        if path == \"/audit\":\n"
        "            with _connect() as conn:\n"
        "                _arows = conn.execute(\n"
        "                    \"SELECT id, action, entity, detail FROM audit_log ORDER BY id\").fetchall()\n"
        "            _abody = \"\".join(\n"
        "                \"<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>\".format(\n"
        "                    _r[\"id\"], html.escape(str(_r[\"action\"])), html.escape(str(_r[\"entity\"])),\n"
        "                    html.escape(str(_r[\"detail\"]))) for _r in _arows)\n"
        "            self._send(200, \"<h1>audit log</h1><table id=\\\"audit_log\\\">\" + _abody + \"</table>\")\n"
        "            return"
    )
    text = replace_anchored(text, "audit_route", route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_relation(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                     workspace: str | Path | None) -> list[Path]:
    """Inject a CHILD entity related to the scaffold PARENT by a foreign key: a `<child>s` table with
    a `<parent>_id` FK, a POST /<child>s endpoint that REQUIRES the parent to exist (bogus parent ->
    400), and a GET /<child>s view that LEFT JOINs the parent (showing the link). One child per apply;
    create-side referential integrity (cascade-delete deferred). Child field names are validated
    identifiers; values ride bound params. Reuses the scaffold's _coerce/_connect."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-second-entity+relation needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    child = slot["entity"]
    child_table = child + "s"
    child_fields_json = json.dumps(slot["fields"])

    consts = (
        "CHILD_FIELDS = json.loads(r'''" + child_fields_json + "''')\n"
        "CHILD_TABLE = " + json.dumps(child_table) + "\n"
        "FK_COL = ENTITY + \"_id\"  # references the parent (scaffold) entity's id"
    )
    text = replace_anchored(text, "relation_consts", consts)

    schema = (
        "        _ccols = \", \".join(\"{} {}\".format(_f[\"name\"], _sql_type(_f[\"type\"])) for _f in CHILD_FIELDS)\n"
        "        conn.execute(\n"
        "            \"CREATE TABLE IF NOT EXISTS \" + CHILD_TABLE + \" (id INTEGER PRIMARY KEY AUTOINCREMENT, \"\n"
        "            + _ccols + \", \" + FK_COL + \" INTEGER, FOREIGN KEY(\" + FK_COL + \") REFERENCES \" + TABLE + \"(id))\"\n"
        "        )"
    )
    text = replace_anchored(text, "child_schema", schema)

    route = (
        "        if path == \"/\" + CHILD_TABLE:\n"
        "            try:\n"
        "                cform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>bad request</h1>\")\n"
        "                return\n"
        "            for _cf in CHILD_FIELDS:\n"
        "                _cv = (cform.get(_cf[\"name\"], [\"\"]) or [\"\"])[0]\n"
        "                if _cf.get(\"required\") and str(_cv).strip() == \"\":\n"
        "                    self._send(400, \"<h1>invalid</h1>\")\n"
        "                    return\n"
        "                try:\n"
        "                    _coerce(_cf, _cv)\n"
        "                except (ValueError, TypeError):\n"
        "                    self._send(400, \"<h1>invalid</h1>\")\n"
        "                    return\n"
        "            try:\n"
        "                _fk = int((cform.get(FK_COL) or [\"\"])[0])\n"
        "            except (ValueError, TypeError):\n"
        "                self._send(400, \"<h1>invalid parent</h1>\")\n"
        "                return\n"
        "            with _connect() as conn:\n"
        "                if not conn.execute(\"SELECT 1 FROM \" + TABLE + \" WHERE id = ?\", (_fk,)).fetchone():\n"
        "                    self._send(400, \"<h1>invalid parent</h1>\")\n"
        "                    return\n"
        "                _cnames = \", \".join(_f[\"name\"] for _f in CHILD_FIELDS)\n"
        "                _cmarks = \", \".join(\"?\" for _ in CHILD_FIELDS)\n"
        "                _cvals = [_coerce(_f, (cform.get(_f[\"name\"], [\"\"]) or [\"\"])[0]) for _f in CHILD_FIELDS]\n"
        "                conn.execute(\"INSERT INTO \" + CHILD_TABLE + \" (\" + _cnames + \", \" + FK_COL\n"
        "                             + \") VALUES (\" + _cmarks + \", ?)\", _cvals + [_fk])\n"
        "                conn.commit()\n"
        "            self._send(200, \"ok\")\n"
        "            return"
    )
    text = replace_anchored(text, "child_route", route)

    view = (
        "        if path == \"/\" + CHILD_TABLE:\n"
        "            _ptf = next((_f[\"name\"] for _f in FIELDS if _f[\"type\"] == \"text\"), FIELDS[0][\"name\"])\n"
        "            with _connect() as conn:\n"
        "                _crows = conn.execute(\n"
        "                    \"SELECT c.*, p.\" + _ptf + \" AS _parent FROM \" + CHILD_TABLE + \" c \"\n"
        "                    + \"LEFT JOIN \" + TABLE + \" p ON c.\" + FK_COL + \" = p.id ORDER BY c.id\").fetchall()\n"
        "            _cbody = \"\".join(\n"
        "                \"<tr>\" + \"\".join(\"<td>\" + html.escape(str(_r[_f[\"name\"]])) + \"</td>\" for _f in CHILD_FIELDS)\n"
        "                + \"<td>\" + html.escape(str(_r[\"_parent\"])) + \"</td></tr>\" for _r in _crows)\n"
        "            self._send(200, \"<h1>\" + html.escape(CHILD_TABLE) + \"</h1><table id=\\\"\"\n"
        "                       + html.escape(CHILD_TABLE) + \"\\\">\" + _cbody + \"</table>\")\n"
        "            return"
    )
    text = replace_anchored(text, "child_view", view)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_detail_view(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                        workspace: str | Path | None) -> list[Path]:
    """Inject GET /<table>/<id>: a single-record page showing that row's fields, with a
    machine-readable `data-cortex-detail-id="<id>"`. A missing id 404s. Parameterless, fully RUNTIME
    (loops FIELDS); the id is int-parsed (never interpolated as text)."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-detail-view needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    block = (
        "        _dvpre = \"/\" + TABLE + \"/\"\n"
        "        if path.startswith(_dvpre) and path[len(_dvpre):].isdigit():\n"
        "            _dvid = int(path[len(_dvpre):])\n"
        "            with _connect() as _dvc:\n"
        "                _dvrow = _dvc.execute(\n"
        "                    \"SELECT * FROM \" + TABLE + \" WHERE id = ?\", (_dvid,)).fetchone()\n"
        "            if _dvrow is None:\n"
        "                self._send(404, \"<h1>not found</h1>\")\n"
        "                return\n"
        "            _dvcells = \"\".join(\n"
        "                '<div>{}: {}</div>'.format(html.escape(_f[\"name\"]),\n"
        "                                          html.escape(str(_dvrow[_f[\"name\"]]))) for _f in FIELDS)\n"
        "            self._send(200, '<h1>record</h1><div data-cortex-detail-id=\"'\n"
        "                       + html.escape(str(_dvid)) + '\">' + _dvcells + '</div>')\n"
        "            return"
    )
    text = replace_anchored(text, "detail_route", block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_dashboard(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                      workspace: str | Path | None) -> list[Path]:
    """Inject a GET /dashboard view: a TOTAL-count card plus a 'true' count card per BOOL field, each
    machine-readable (`data-cortex-dash-<field>="N"`). Parameterless and fully RUNTIME (loops FIELDS),
    so it is correct for ANY entity. Field names are validated identifiers -> safe in the SQL; counts
    ride no user input (fixed `= 1` predicate)."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-dashboard needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    block = (
        "        if path == \"/dashboard\":\n"
        "            with _connect() as _dconn:\n"
        "                _dtotal = _dconn.execute(\"SELECT COUNT(*) FROM \" + TABLE).fetchone()[0]\n"
        "                _dcards = ['<div class=\"metric\" data-cortex-dash-total=\"'\n"
        "                           + html.escape(str(_dtotal)) + '\">Total: '\n"
        "                           + html.escape(str(_dtotal)) + '</div>']\n"
        "                for _bf in FIELDS:\n"
        "                    if _bf[\"type\"] == \"bool\":\n"
        "                        _bn = _bf[\"name\"]\n"
        "                        _bc = _dconn.execute(\n"
        "                            \"SELECT COUNT(*) FROM \" + TABLE + \" WHERE \" + _bn + \" = 1\"\n"
        "                        ).fetchone()[0]\n"
        "                        _dcards.append('<div class=\"metric\" data-cortex-dash-' + _bn + '=\"'\n"
        "                                       + html.escape(str(_bc)) + '\">' + html.escape(_bn) + ': '\n"
        "                                       + html.escape(str(_bc)) + '</div>')\n"
        "            self._send(200, '<h1>dashboard</h1>' + ''.join(_dcards))\n"
        "            return"
    )
    text = replace_anchored(text, "dashboard_route", block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_status_lifecycle(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                             workspace: str | Path | None) -> list[Path]:
    """Inject a fixed status STATE MACHINE: a `status` column (default 'new') and a
    POST /<table>/status endpoint that ONLY permits the declared transitions (new->active->done);
    any other transition is 400 and mutates nothing. Parameterless -- the lifecycle is a harness-fixed
    allow-map (never model code), keyed to the runtime TABLE, correct for ANY entity."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-status-lifecycle needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    schema_block = (
        "        try:\n"
        "            conn.execute(\"ALTER TABLE \" + TABLE + \" ADD COLUMN status TEXT NOT NULL DEFAULT 'new'\")\n"
        "        except sqlite3.OperationalError:\n"
        "            pass"
    )
    text = replace_anchored(text, "status_schema", schema_block)
    route_block = (
        "        if path == \"/{}/status\".format(TABLE):\n"
        "            try:\n"
        "                sform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "                _sid = int((sform.get(\"id\") or [\"\"])[0])\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>invalid</h1>\")\n"
        "                return\n"
        "            _to = (sform.get(\"to\") or [\"\"])[0]\n"
        "            _ALLOWED = {\"new\": [\"active\"], \"active\": [\"done\"], \"done\": []}\n"
        "            with _connect() as conn:\n"
        "                _cur = conn.execute(\"SELECT status FROM \" + TABLE + \" WHERE id = ?\", (_sid,)).fetchone()\n"
        "                if _cur is None:\n"
        "                    self._send(404, \"<h1>not found</h1>\")\n"
        "                    return\n"
        "                if _to not in _ALLOWED.get(_cur[\"status\"], []):\n"
        "                    self._send(400, \"<h1>illegal transition</h1>\")\n"
        "                    return\n"
        "                conn.execute(\"UPDATE \" + TABLE + \" SET status = ? WHERE id = ?\", (_to, _sid))\n"
        "                conn.commit()\n"
        "            self._send(200, \"<h1>ok</h1>\")\n"
        "            return"
    )
    text = replace_anchored(text, "status_route", route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_soft_delete(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                        workspace: str | Path | None) -> list[Path]:
    """Inject a SOFT delete: an `archived` column (default 0), GET /<table>/active (archived=0) and
    /<table>/archived (archived=1) partition views, and POST /<table>/archive + /<table>/restore that
    flip the flag WITHOUT deleting the sqlite row. Parameterless; harness-authored, keyed to runtime
    TABLE, correct for ANY entity. (Composes on new routes; it does not touch the default list.)"""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-soft-delete needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    schema_block = (
        "        try:\n"
        "            conn.execute(\"ALTER TABLE \" + TABLE + \" ADD COLUMN archived INTEGER NOT NULL DEFAULT 0\")\n"
        "        except sqlite3.OperationalError:\n"
        "            pass"
    )
    text = replace_anchored(text, "archive_schema", schema_block)
    views_block = (
        "        if path == \"/{}/active\".format(TABLE):\n"
        "            with _connect() as conn:\n"
        "                _rows = conn.execute(\"SELECT * FROM \" + TABLE + \" WHERE archived = 0 ORDER BY id\").fetchall()\n"
        "            self._send(200, '<h1>active</h1><table id=\"active\">'\n"
        "                       + \"\".join(_row_html(_r) for _r in _rows) + '</table>')\n"
        "            return\n"
        "        if path == \"/{}/archived\".format(TABLE):\n"
        "            with _connect() as conn:\n"
        "                _rows = conn.execute(\"SELECT * FROM \" + TABLE + \" WHERE archived = 1 ORDER BY id\").fetchall()\n"
        "            self._send(200, '<h1>archived</h1><table id=\"archived\">'\n"
        "                       + \"\".join(_row_html(_r) for _r in _rows) + '</table>')\n"
        "            return"
    )
    text = replace_anchored(text, "soft_delete_views", views_block)
    route_block = (
        "        if path in (\"/{}/archive\".format(TABLE), \"/{}/restore\".format(TABLE)):\n"
        "            try:\n"
        "                adform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "                _adid = int((adform.get(\"id\") or [\"\"])[0])\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>invalid</h1>\")\n"
        "                return\n"
        "            _arch = 1 if path.endswith(\"/archive\") else 0\n"
        "            with _connect() as conn:\n"
        "                conn.execute(\"UPDATE \" + TABLE + \" SET archived = ? WHERE id = ?\", (_arch, _adid))\n"
        "                conn.commit()\n"
        "            self._send(200, \"<h1>ok</h1>\")\n"
        "            return"
    )
    text = replace_anchored(text, "archive_route", route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_ownership_assignment(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                                 workspace: str | Path | None) -> list[Path]:
    """Inject OWNERSHIP: an `assignee` column (default ''), a POST /<table>/assign endpoint that sets
    a row's assignee, and a GET /<table>/assigned?assignee=<who> scoped 'my items' view returning ONLY
    that owner's rows. Parameterless; harness-authored, keyed to runtime TABLE, correct for ANY
    entity. The assignee rides a BOUND PARAM (never string-formatted into SQL)."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-ownership-assignment needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    schema_block = (
        "        try:\n"
        "            conn.execute(\"ALTER TABLE \" + TABLE + \" ADD COLUMN assignee TEXT NOT NULL DEFAULT ''\")\n"
        "        except sqlite3.OperationalError:\n"
        "            pass"
    )
    text = replace_anchored(text, "assign_schema", schema_block)
    view_block = (
        "        if path == \"/{}/assigned\".format(TABLE):\n"
        "            _who = (parse_qs(parsed.query).get(\"assignee\") or [\"\"])[0]\n"
        "            with _connect() as conn:\n"
        "                _rows = conn.execute(\"SELECT * FROM \" + TABLE + \" WHERE assignee = ? ORDER BY id\",\n"
        "                                     (_who,)).fetchall()\n"
        "            self._send(200, '<h1>assigned</h1><table id=\"assigned\">'\n"
        "                       + \"\".join(_row_html(_r) for _r in _rows) + '</table>')\n"
        "            return"
    )
    text = replace_anchored(text, "assign_view", view_block)
    route_block = (
        "        if path == \"/{}/assign\".format(TABLE):\n"
        "            try:\n"
        "                asform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "                _asid = int((asform.get(\"id\") or [\"\"])[0])\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>invalid</h1>\")\n"
        "                return\n"
        "            _who = (asform.get(\"assignee\") or [\"\"])[0]\n"
        "            with _connect() as conn:\n"
        "                if not conn.execute(\"SELECT 1 FROM \" + TABLE + \" WHERE id = ?\", (_asid,)).fetchone():\n"
        "                    self._send(404, \"<h1>not found</h1>\")\n"
        "                    return\n"
        "                conn.execute(\"UPDATE \" + TABLE + \" SET assignee = ? WHERE id = ?\", (_who, _asid))\n"
        "                conn.commit()\n"
        "            self._send(200, \"<h1>assigned</h1>\")\n"
        "            return"
    )
    text = replace_anchored(text, "assign_route", route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


def _render_review_approval(skill: BuildSkill, slot: dict[str, Any], app_dir: Path,
                            workspace: str | Path | None) -> list[Path]:
    """Inject a REVIEW/APPROVAL workflow: `review_status` (default 'pending') + `approver` (default '')
    columns and a POST /<table>/review endpoint that, ONLY from 'pending', sets approved/rejected and
    records the approver -- a second decision on a decided row is 400 (terminal). Parameterless;
    harness-authored, keyed to runtime TABLE, correct for ANY entity."""
    app_py = app_dir / "app.py"
    if not app_py.is_file():
        raise RenderError("add-review-approval needs an existing scaffold app.py to edit")
    text = app_py.read_text(encoding="utf-8", errors="replace")
    schema_block = (
        "        for _rvc, _rvd in ((\"review_status\", \"'pending'\"), (\"approver\", \"''\")):\n"
        "            try:\n"
        "                conn.execute(\"ALTER TABLE \" + TABLE + \" ADD COLUMN \" + _rvc\n"
        "                             + \" TEXT NOT NULL DEFAULT \" + _rvd)\n"
        "            except sqlite3.OperationalError:\n"
        "                pass"
    )
    text = replace_anchored(text, "review_schema", schema_block)
    route_block = (
        "        if path == \"/{}/review\".format(TABLE):\n"
        "            try:\n"
        "                rvform = parse_qs(raw.decode(\"utf-8\", errors=\"replace\"), keep_blank_values=True)\n"
        "                _rvid = int((rvform.get(\"id\") or [\"\"])[0])\n"
        "            except Exception:\n"
        "                self._send(400, \"<h1>invalid</h1>\")\n"
        "                return\n"
        "            _decision = (rvform.get(\"decision\") or [\"\"])[0]\n"
        "            _approver = (rvform.get(\"approver\") or [\"\"])[0]\n"
        "            _NEXT = {\"approve\": \"approved\", \"reject\": \"rejected\"}\n"
        "            if _decision not in _NEXT:\n"
        "                self._send(400, \"<h1>invalid decision</h1>\")\n"
        "                return\n"
        "            with _connect() as conn:\n"
        "                _row = conn.execute(\"SELECT review_status FROM \" + TABLE + \" WHERE id = ?\",\n"
        "                                    (_rvid,)).fetchone()\n"
        "                if _row is None:\n"
        "                    self._send(404, \"<h1>not found</h1>\")\n"
        "                    return\n"
        "                if _row[\"review_status\"] != \"pending\":\n"
        "                    self._send(400, \"<h1>already decided</h1>\")\n"
        "                    return\n"
        "                conn.execute(\"UPDATE \" + TABLE + \" SET review_status = ?, approver = ? WHERE id = ?\",\n"
        "                             (_NEXT[_decision], _approver, _rvid))\n"
        "                conn.commit()\n"
        "            self._send(200, \"<h1>ok</h1>\")\n"
        "            return"
    )
    text = replace_anchored(text, "review_route", route_block)
    app_py.write_text(text, encoding="utf-8")
    return [app_py]


# skill_id -> harness-owned renderer. The model supplies ONLY the slot; every line of
# code emitted here is authored by the harness, keyed by the (trusted) skill id.
RENDERERS: dict[str, Callable[[BuildSkill, dict, Path, Any], list[Path]]] = {
    "scaffold-crud-sqlite": _render_crud_scaffold,
    "add-conditional-class": _render_conditional_class,
    "add-summary-metric": _render_summary_metric,
    "add-search-filter": _render_search_filter,
    "add-delete-with-confirm": _render_delete_confirm,
    "add-edit-record": _render_edit_record,
    "add-role-gate": _render_role_gate,
    "add-audit-log": _render_audit_log,
    "add-dashboard": _render_dashboard,
    "add-detail-view": _render_detail_view,
    "add-second-entity-relation": _render_relation,
    "add-status-lifecycle": _render_status_lifecycle,
    "add-soft-delete": _render_soft_delete,
    "add-ownership-assignment": _render_ownership_assignment,
    "add-review-approval": _render_review_approval,
}


# --- Done-check generation ------------------------------------------------------------------
# The harness authors the app, so it must author the app's CHECKS from the slot too. A skill's
# declared done_checks (in skill.json) are a static EXAMPLE instance (the skill's own example
# entity); running them verbatim against a different slot points the gate at the wrong
# table/route/fields. This regenerates the App-Contract check suite from the actual slot --
# table = entity+"s", route = /<table>, forms carrying every required field, expected columns
# from the field list -- so ANY entity/field set is gated correctly. Kinds/order match the
# validated static suite (data_persists precedes the other file-coupled checks; see app_gates).
def _scaffold_table(slot: dict[str, Any]) -> str:
    return str(slot["entity"]) + "s"


def _primary_text_field(fields: list[dict[str, Any]]) -> str:
    return next((f["name"] for f in fields if f.get("type") == "text"), fields[0]["name"])


def _valid_form(fields: list[dict[str, Any]], token_field: str, token: str) -> dict[str, str]:
    """A form that satisfies the scaffold's _validate: the token field carries the @hidden token;
    every OTHER required field gets a type-appropriate valid default so the insert succeeds."""
    form: dict[str, str] = {}
    for f in fields:
        name = f["name"]
        if name == token_field:
            form[name] = token
        elif f.get("required"):
            form[name] = "0" if f["type"] == "bool" else ("1" if f["type"] == "int" else "cxval")
    form.setdefault(token_field, token)
    return form


def scaffold_done_checks(slot: dict[str, Any]) -> list[dict[str, Any]]:
    """The App-Contract check suite for a scaffold-crud-sqlite build, generated FROM ITS SLOT
    so the gate checks the real table/route/fields the built app actually has."""
    fields = slot["fields"]
    table = _scaffold_table(slot)
    path = "/" + table
    tf = _primary_text_field(fields)
    cols = ["id"] + [f["name"] for f in fields]
    return [
        {"kind": "app_starts"},
        {"kind": "buttons_work", "actions": [
            {"name": "add_row",
             "request": {"method": "POST", "path": path,
                         "form": _valid_form(fields, tf, "@hidden:btn_token")},
             "expect": {"status_lt": 400},
             "state_change": {"get_path": path, "must_contain": "@hidden:btn_token",
                              "before_must_not_contain": "@hidden:btn_token"}}]},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:persist_token")},
                      "read_path": path, "table": table, "column": tf}},
        {"kind": "schema_real", "db_file": "app.db", "table": table, "required_columns": cols,
         "row_delta": {"via": {"method": "POST", "path": path,
                               "form": _valid_form(fields, tf, "@hidden:delta_token")},
                       "expect_delta": 1}},
        {"kind": "input_handling",
         "target": {"method": "POST", "path": path,
                    "fields": {f["name"]: f["type"] for f in fields}},
         "variants": ["empty_required", "type_invalid", "oversized", "duplicate", "malformed_body"],
         "policy": {"max_field_bytes": 4096},
         "safe": {"status_lt": 500, "process_alive": True, "canary_row_survives": True}},
        {"kind": "security_controls",
         "write": {"method": "POST", "path": path, "field": tf},
         "read_paths": ["/", path], "table": table,
         "tests": ["reflected_escape", "canary_secret", "path_traversal"],
         "protected": [{"method": "POST", "path": "/admin/reset",
                        "expect_without_auth": {"status_in": [401, 403, 404],
                                                "state_unchanged": True}}]},
        {"kind": "regression", "ledger_file": "gate_ledger.jsonl"},
    ]


def _metric_predicate_values(op: str, value: str, ftype: str) -> tuple[str, str]:
    """Return (match_value, nomatch_value) for a `<field> <op> value` predicate: the first
    SATISFIES the predicate, the second does NOT. Numeric for gt/lt; type-aware 'other' for eq/ne."""
    if op in ("gt", "lt"):
        v = int(value)
        return (str(v + 1), str(v)) if op == "gt" else (str(v - 1), str(v))
    if ftype == "int":
        other = str(int(value) + 1)
    elif ftype == "bool":
        other = "0" if str(value).strip().lower() in ("1", "true", "yes", "on") else "1"
    else:
        other = str(value) + "_x"
    if op == "eq":
        return str(value), other
    return other, str(value)  # ne: match differs from value, nomatch equals it


def _metric_form(fields: list[dict[str, Any]], token_field: str, token: str,
                 pred_field: str, pred_value: str) -> dict[str, str]:
    """A valid create form (every required field filled, token on the text field) with the
    predicate field pinned to `pred_value`."""
    form = _valid_form(fields, token_field, token)
    form[pred_field] = str(pred_value)
    return form


def summary_metric_done_checks(scaffold_slot: dict[str, Any],
                               metric_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-summary-metric build on a KNOWN scaffold
    entity + metric predicate. Emits a `derived_value` check (seeds predicate-matching and
    predicate-NOT-matching rows, asserts the metric moved by exactly the matching count) plus a
    `data_persists` check (the mandatory behavioral-state check). The metric predicate field is
    kept distinct from the unique text field so the gate's per-row uniquing cannot break it."""
    fields = scaffold_slot["fields"]
    entity = scaffold_slot["entity"]
    table = entity + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    field = metric_slot["field"]
    op = metric_slot["op"]
    value = metric_slot["value"]
    ftype = next((f["type"] for f in fields if f["name"] == field), "text")
    match_val, nomatch_val = _metric_predicate_values(op, value, ftype)
    return [
        {"kind": "derived_value", "get_path": "/", "marker_attr": "data-cortex-metric",
         "create": {"method": "POST", "path": path},
         # The predicate field must be preserved when the seeder uniquifies rows -- critical when it
         # IS the only text field (tf == field), so the seeder can't clobber it or mint a text token
         # into a numeric spare field. See _seed_derived_row.
         "predicate_field": field,
         "match_form": _metric_form(fields, tf, "@hidden:metric_match", field, match_val),
         "nomatch_form": _metric_form(fields, tf, "@hidden:metric_nomatch", field, nomatch_val)},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:metric_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def search_filter_done_checks(scaffold_slot: dict[str, Any],
                              search_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-search-filter build on a KNOWN scaffold
    entity + searched field. Emits a `filtered_results` check (the gate mints a hidden term, seeds
    rows whose searched field contains / omits it, and asserts the search returns exactly the
    matching rows) plus a `data_persists` check (the mandatory behavioral-state check). The searched
    field carries the per-row `@hidden` token so the gate can inject the term-bearing / non-term
    value; the create path/table/read_path come from the real scaffold entity."""
    fields = scaffold_slot["fields"]
    entity = scaffold_slot["entity"]
    table = entity + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    field = search_slot["field"]
    return [
        {"kind": "filtered_results",
         "create": {"method": "POST", "path": path},
         "search": {"get_path": "/", "query_param": "q"},
         "match_form": _valid_form(fields, field, "@hidden:search_match"),
         "nomatch_form": _valid_form(fields, field, "@hidden:search_nomatch")},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:search_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def delete_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-delete-with-confirm build on a KNOWN scaffold
    entity. Parameterless skill: the `deletes_row` check seeds a canary row (token on the text field),
    asserts an UNconfirmed delete is rejected + leaves the row, a CONFIRMED delete removes it, and the
    removal PERSISTS across restart -- plus the mandatory `data_persists`. Table/route/text column
    come from the real scaffold entity, so the check targets the app the model actually built."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "deletes_row",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:del_seed")},
         "delete": {"path": path + "/delete", "id_param": "id",
                    "confirm_param": "confirm", "confirm_value": "yes"},
         "read_path": path, "table": table, "column": tf},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:del_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def edit_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-edit-record build on a KNOWN scaffold entity.
    Parameterless skill: the `edits_row` check seeds an edited row + a bystander (distinct tokens on
    the text column), edits the first's text value to a NEW token, and asserts the new value applies,
    the old is gone, the bystander is untouched, and the change PERSISTS across restart -- plus the
    mandatory `data_persists`. Table/route/text column come from the real scaffold entity."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "edits_row",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:edit_old")},
         "create_b": {"method": "POST", "path": path,
                      "form": _valid_form(fields, tf, "@hidden:edit_bystander")},
         "edit": {"method": "POST", "path": path + "/edit", "id_param": "id",
                  "form": _valid_form(fields, tf, "@hidden:edit_new")},
         "read_path": path, "table": table, "column": tf},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:edit_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def role_gate_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-role-gate build on a KNOWN scaffold entity.
    Parameterless skill: the `auth_required` check seeds a canary row via the open create endpoint,
    then proves the protected /admin/export route serves that row ONLY under the correct
    X-Admin-Token (no-auth AND wrong-auth are rejected with no leak) -- plus the mandatory
    `data_persists`. The token is the scaffold's visible ADMIN_TOKEN; the check verifies the guard
    LOGIC, not secrecy."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "auth_required",
         "protected_path": "/admin/export",
         "auth_header": "X-Admin-Token",
         "auth_value": "cortex-admin-token",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:auth_seed")}},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:auth_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def audit_log_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-audit-log build on a KNOWN scaffold entity.
    Parameterless: the `audit_trail` check seeds N canary creates and asserts each was recorded in the
    append-only audit_log (detail column) and that the log appended (>= N entries) -- plus the
    mandatory `data_persists`."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "audit_trail",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:audit_seed")},
         "audit_table": "audit_log", "detail_column": "detail", "column": tf},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:audit_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def dashboard_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-dashboard build on a KNOWN scaffold entity.
    Parameterless: the dashboard auto-shows a TOTAL card plus a 'true' count per BOOL field. The
    `dashboard_metrics` check verifies the total moves by 1 per create and each bool card moves by the
    number of matching (field=1) rows seeded -- plus the mandatory `data_persists`."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    cards = []
    for f in fields:
        if f.get("type") == "bool":
            nm = f["name"]
            cards.append({
                "marker_attr": "data-cortex-dash-" + nm,
                "predicate_field": nm,
                "match_form": _metric_form(fields, tf, "@hidden:dash_" + nm + "_m", nm, "1"),
                "nomatch_form": _metric_form(fields, tf, "@hidden:dash_" + nm + "_n", nm, "0"),
            })
    return [
        {"kind": "dashboard_metrics", "get_path": "/dashboard",
         "create": {"method": "POST", "path": path},
         "total_attr": "data-cortex-dash-total",
         "total_form": _valid_form(fields, tf, "@hidden:dash_total"),
         "cards": cards},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:dash_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def detail_view_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-detail-view build on a KNOWN scaffold entity.
    Parameterless: the `detail_view` check seeds two canary rows and asserts GET /<table>/<id> shows
    only the requested one (id marker matches, the other row absent) and a bogus id 404s -- plus the
    mandatory `data_persists`."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "detail_view",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:detail_seed")},
         "table": table, "column": tf,
         "detail_path_prefix": path, "id_marker_attr": "data-cortex-detail-id"},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:detail_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def relation_done_checks(parent_slot: dict[str, Any],
                         child_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate the App-Contract done-checks for an add-second-entity+relation build on a KNOWN parent
    scaffold + child slot. The `relation_integrity` check seeds a parent canary, creates a child with
    that valid parent FK (accepted + shown joined), and asserts a child with a bogus parent id is
    rejected -- plus the mandatory `data_persists` on the parent."""
    pf = parent_slot["fields"]
    parent = parent_slot["entity"]
    ptable = parent + "s"
    ppath = "/" + ptable
    ptf = _primary_text_field(pf)
    cf = child_slot["fields"]
    ctable = child_slot["entity"] + "s"
    cpath = "/" + ctable
    ctf = _primary_text_field(cf)
    return [
        {"kind": "relation_integrity",
         "parent_create": {"method": "POST", "path": ppath,
                           "form": _valid_form(pf, ptf, "@hidden:rel_parent")},
         "child_create": {"method": "POST", "path": cpath},
         "parent_table": ptable, "parent_column": ptf,
         "child_fk_param": parent + "_id",
         "child_valid_form": _valid_form(cf, ctf, "@hidden:rel_child"),
         "child_view_path": cpath, "child_column": ctf},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": ppath,
                                 "form": _valid_form(pf, ptf, "@hidden:rel_persist")},
                      "read_path": ppath, "table": ptable, "column": ptf}},
    ]


def status_lifecycle_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Done-checks for an add-status-lifecycle build on a KNOWN scaffold entity. Parameterless: the
    `status_lifecycle` check seeds a row (status 'new'), asserts an illegal 'new'->'done' transition is
    rejected + leaves the status, a legal 'new'->'active' transition applies + persists across restart
    -- plus the mandatory `data_persists`. Table/route/text column come from the real scaffold."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "status_lifecycle",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:status_seed")},
         "transition": {"path": path + "/status", "id_param": "id", "to_param": "to"},
         "table": table, "column": tf, "status_column": "status",
         "initial": "new", "legal_to": "active", "illegal_to": "done"},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:status_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def soft_delete_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Done-checks for an add-soft-delete build on a KNOWN scaffold entity. Parameterless: the
    `soft_delete` check seeds a canary, asserts archive hides it from /active + keeps the sqlite row
    (archived=1) + shows it in /archived, that the archived state persists across restart, and that
    restore returns it to /active -- plus the mandatory `data_persists`."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "soft_delete",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:soft_seed")},
         "archive": {"path": path + "/archive", "id_param": "id"},
         "restore": {"path": path + "/restore", "id_param": "id"},
         "table": table, "column": tf, "archived_column": "archived",
         "active_view_path": path + "/active", "archived_view_path": path + "/archived"},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:soft_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def assignment_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Done-checks for an add-ownership-assignment build on a KNOWN scaffold entity. Parameterless: the
    `assignment` check seeds two rows, assigns each to a distinct owner, asserts the scoped
    /assigned?assignee=<who> view returns only that owner's rows, that reassignment moves a row and
    persists -- plus the mandatory `data_persists`."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "assignment",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:assign_seed")},
         "assign": {"path": path + "/assign", "id_param": "id", "assignee_param": "assignee"},
         "scoped_view": {"get_path": path + "/assigned", "query_param": "assignee"},
         "table": table, "column": tf, "assignee_column": "assignee"},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:assign_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def review_approval_done_checks(scaffold_slot: dict[str, Any]) -> list[dict[str, Any]]:
    """Done-checks for an add-review-approval build on a KNOWN scaffold entity. Parameterless: the
    `review_approval` check seeds pending rows, asserts approve records the approver + is terminal (a
    second decision is rejected), reject records its approver, and the decision persists -- plus the
    mandatory `data_persists`."""
    fields = scaffold_slot["fields"]
    table = scaffold_slot["entity"] + "s"
    path = "/" + table
    tf = _primary_text_field(fields)
    return [
        {"kind": "review_approval",
         "create": {"method": "POST", "path": path,
                    "form": _valid_form(fields, tf, "@hidden:review_seed")},
         "review": {"path": path + "/review", "id_param": "id",
                    "decision_param": "decision", "approver_param": "approver"},
         "table": table, "column": tf,
         "status_column": "review_status", "approver_column": "approver"},
        {"kind": "data_persists",
         "resource": {"create": {"method": "POST", "path": path,
                                 "form": _valid_form(fields, tf, "@hidden:review_persist")},
                      "read_path": path, "table": table, "column": tf}},
    ]


def resolve_done_checks(skill: BuildSkill, slot: dict[str, Any]) -> list[dict[str, Any]]:
    """The slot-specific done-checks the gate should run for a build. Skills whose checks depend
    on the slot (the CRUD scaffold: table/route/fields) regenerate from the slot; others resolve
    ``{{slot.<field>}}`` markers in their declared checks."""
    if skill.skill_id == "scaffold-crud-sqlite":
        return scaffold_done_checks(slot)
    return [_resolve_slot_markers(c, slot) for c in skill.done_checks]


def render_skill(skill: BuildSkill, slot_values: dict[str, Any], app_dir: str | Path,
                 workspace: str | Path | None = None) -> list[Path]:
    """Validate the slot, THEN deterministically render. An invalid slot raises
    ``SlotValidationError`` BEFORE anything is written (no silent proceed, no partial
    write)."""
    ok, errors = validate_slot(skill, slot_values)
    if not ok:
        raise SlotValidationError(f"slot rejected for {skill.skill_id}: {'; '.join(errors)}")
    renderer = RENDERERS.get(skill.skill_id)
    if renderer is None:
        raise RenderError(f"no renderer registered for skill {skill.skill_id!r}")
    return renderer(skill, slot_values, Path(app_dir), workspace)


# --------------------------------------------------------------------------- #
# deterministic preconditions
# --------------------------------------------------------------------------- #

def _resolve_slot_markers(spec: dict[str, Any], slot_values: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve ``{{slot.<field>}}`` placeholders inside a precondition spec from the
    slot payload (so a generic edit skill can point its precondition at the very
    column the slot names)."""
    if not slot_values:
        return spec
    out: dict[str, Any] = {}
    for k, v in spec.items():
        if isinstance(v, str):
            for field, val in slot_values.items():
                v = v.replace("{{slot." + field + "}}", str(val))
        out[k] = v
    return out


def _sqlite_column_exists(db: Path, column: str, table: str | None) -> bool:
    if not db.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        if table:
            tables = [table]
        else:
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            try:
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
            except sqlite3.Error:
                continue
            if column in cols:
                return True
        return False
    finally:
        con.close()


def check_preconditions(skill: BuildSkill, app_dir: str | Path,
                        slot_values: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    """Evaluate the skill's deterministic preconditions against ``app_dir``. A failed
    precondition is a refusal-with-reason, never a silent proceed. Kinds:
      - ``file_absent`` {path}:  app_dir/path must NOT exist;
      - ``file_exists`` {path}:  app_dir/path MUST exist;
      - ``sqlite_column_exists`` {db, column, table?}: column present (in table, or
        any table when table omitted). ``{{slot.<field>}}`` in a spec resolves from
        ``slot_values``.
    """
    ad = Path(app_dir)
    reasons: list[str] = []
    for raw in skill.preconditions:
        spec = _resolve_slot_markers(raw, slot_values)
        kind = spec.get("kind")
        if kind == "file_absent":
            p = ad / spec["path"]
            if p.exists():
                reasons.append(f"file {spec['path']!r} must be absent but exists")
        elif kind == "file_exists":
            p = ad / spec["path"]
            if not p.exists():
                reasons.append(f"file {spec['path']!r} must exist but is missing")
        elif kind == "sqlite_column_exists":
            db = ad / spec.get("db", "app.db")
            column = spec.get("column", "")
            table = spec.get("table")
            if not _sqlite_column_exists(db, column, table):
                where = f"table {table!r}" if table else "any table"
                reasons.append(f"column {column!r} not found in {where} of {spec.get('db', 'app.db')}")
        else:
            reasons.append(f"unknown precondition kind {kind!r}")
    return (not reasons, reasons)


# --------------------------------------------------------------------------- #
# the injected step-prompt (single-shot slot fill; NO gate internals)
# --------------------------------------------------------------------------- #

def build_step_prompt(skill: BuildSkill, utterance: str) -> str:
    """The lean instruction sent to the model: the utterance + the skill's imperative
    guidance + the serialized slot schema + a worked example + the hard rule that the
    entire reply is ONE JSON object. Deliberately carries NO gate internals (no
    ``@hidden:`` names, no done_checks, no expected values) -- the coach/student never
    see the gate."""
    schema_json = json.dumps(skill.slot.schema, indent=2, sort_keys=True)
    example_json = json.dumps(skill.slot.example, sort_keys=True)
    return (
        f"{utterance}\n\n"
        f"{skill.step_prompt}\n\n"
        f"Output EXACTLY ONE JSON object and nothing else -- no prose, no code, no fences.\n"
        f"It must match this schema:\n{schema_json}\n\n"
        f"Worked example of a valid object:\n{example_json}\n"
    )


# --------------------------------------------------------------------------- #
# occurrence floor (patterns.py discipline; never auto-verify)
# --------------------------------------------------------------------------- #

def record_outcome(skill_id: str, passed: bool, workspace: str | Path | None = None) -> None:
    """Increment ``occurrence_count`` (and ``pass_count`` on success) in the skill's
    ``skill.json``. ``verified`` is NEVER flipped here -- promotion to verified is a
    human action (>=2 live passes AND acceptance), exactly the patterns.py floor."""
    sj = skills_dir(workspace) / skill_id / "skill.json"
    if not sj.is_file():
        raise FileNotFoundError(f"no skill.json for {skill_id!r} at {sj}")
    data = json.loads(sj.read_text(encoding="utf-8", errors="replace"))
    data["occurrence_count"] = int(data.get("occurrence_count", 0)) + 1
    if passed:
        data["pass_count"] = int(data.get("pass_count", 0)) + 1
    # verified stays whatever it was; flipping it is a human-only action.
    sj.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import make_stdio_encoding_safe

    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Cortex build-skill registry (BUILD-01 M3)")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--list", action="store_true", help="list registered build-skills")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    skills = load_skills(args.workspace)
    if args.json:
        print(json.dumps({sid: sk.to_dict() for sid, sk in skills.items()}, indent=2))
    else:
        print(f"{len(skills)} build-skill(s):")
        for sid, sk in skills.items():
            print(f"  [{sk.track}] {sid}: {sk.title} "
                  f"(x{sk.occurrence_count}, pass {sk.pass_count}, verified={sk.verified})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
