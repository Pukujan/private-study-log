"""RED-first TDD suite for M3 of BUILD-01: the template-injection skill registry
(`cortex_core/build_skills.py` + the two seed skills under `skills/`).

Enforces the non-negotiables from docs/research/BUILD-01-sdd-tdd-design-2026-07-10.md §4:
  - template injection, NOT code generation (model emits ONE JSON slot; harness renders code);
  - validate_skill REFUSES an app_build skill whose done_checks lack a behavioral state check;
  - rendering is DETERMINISTIC (same slot -> byte-identical files) and App-Contract §1.1 compliant;
  - slot-schema validation rejects malformed / injection payloads BEFORE any render;
  - the renderer is firewalled from any LLM module.
"""

from __future__ import annotations

import ast
import json
import py_compile
import shutil
import sys
from pathlib import Path

import pytest

from cortex_core import build_skills as bs
from cortex_core.app_contract import BEHAVIORAL_STATE_PRIMARY

REPO = Path(__file__).resolve().parents[1]
SEED_IDS = ("scaffold-crud-sqlite", "add-conditional-class")


@pytest.fixture(autouse=True)
def _no_ambient_workspace(monkeypatch):
    # Tests pin the workspace explicitly; the ambient CORTEX_WORKSPACE must not win.
    monkeypatch.delenv("CORTEX_WORKSPACE", raising=False)


def _seed_ws(tmp_path: Path) -> Path:
    """A throwaway workspace holding copies of both seed skills (so tests can mutate
    a skill.json without touching the real repo)."""
    (tmp_path / "docs").mkdir(exist_ok=True)  # makes resolve_workspace recognize tmp_path
    src = REPO / "skills"
    for sid in SEED_IDS:
        shutil.copytree(src / sid, tmp_path / "skills" / sid)
    return tmp_path


def _load_real(skill_id: str) -> "bs.BuildSkill":
    return bs.load_skills(REPO)[skill_id]


# --- 1. layout / load -------------------------------------------------------

def test_skills_dir_layout_and_seed_files_exist():
    for sid in SEED_IDS:
        sj = REPO / "skills" / sid / "skill.json"
        assert sj.is_file(), f"missing {sj}"
        json.loads(sj.read_text(encoding="utf-8"))  # parses as JSON
    assert (REPO / "skills" / "scaffold-crud-sqlite" / "templates" / "app.py.tmpl").is_file()


def test_load_skills_returns_both_seeds_validated():
    skills = bs.load_skills(REPO)
    assert set(SEED_IDS) <= set(skills)
    for sid in SEED_IDS:
        sk = skills[sid]
        assert sk.origin == "seed_fable_authored"
        assert sk.verified is False
        assert sk.track == "app_build"


# --- 2. validate_skill: the gate-theater blocker ----------------------------

def test_validate_skill_refuses_missing_state_check(tmp_path):
    ws = _seed_ws(tmp_path)
    sj = ws / "skills" / "scaffold-crud-sqlite" / "skill.json"
    d = json.loads(sj.read_text(encoding="utf-8"))
    # strip the behavioral state check (data_persists) and its fallback pair
    d["done_checks"] = [c for c in d["done_checks"]
                        if c.get("kind") not in ("data_persists", "schema_real", "buttons_work")]
    sj.write_text(json.dumps(d), encoding="utf-8")

    sk = bs.BuildSkill.from_dict(d)
    ok, errors = bs.validate_skill(sk, ws)
    assert ok is False
    assert any(BEHAVIORAL_STATE_PRIMARY in e for e in errors)
    with pytest.raises(bs.SkillValidationError):
        bs.load_skills(ws)


def test_validate_skill_accepts_schema_real_plus_write_button_fallback(tmp_path):
    ws = _seed_ws(tmp_path)
    sk = _load_real("scaffold-crud-sqlite")
    # keep schema_real + a write (POST) buttons_work action, drop data_persists -> fallback holds
    kept = [c for c in sk.done_checks if c.get("kind") in ("app_starts", "schema_real", "buttons_work")]
    sk2 = bs.BuildSkill.from_dict({**sk.to_dict(), "done_checks": kept})
    ok, errors = bs.validate_skill(sk2, ws)
    assert ok is True, errors


def test_validate_skill_lints_every_done_check(tmp_path):
    ws = _seed_ws(tmp_path)
    sk = _load_real("scaffold-crud-sqlite")
    bad = bs.BuildSkill.from_dict({**sk.to_dict(),
                                   "done_checks": sk.done_checks + [{"kind": "vibes"}]})
    ok, errors = bs.validate_skill(bad, ws)
    assert ok is False
    assert any("vibes" in e for e in errors)


def test_validate_skill_requires_valid_example(tmp_path):
    ws = _seed_ws(tmp_path)
    sk = _load_real("scaffold-crud-sqlite")
    broken = {**sk.to_dict()}
    broken["slot"] = {**broken["slot"], "example": {"entity": "DROP", "fields": []}}
    sk2 = bs.BuildSkill.from_dict(broken)
    ok, errors = bs.validate_skill(sk2, ws)
    assert ok is False
    assert any("example" in e.lower() for e in errors)


# --- 3. the restricted slot-schema dialect ----------------------------------

_DIALECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entity", "fields"],
    "properties": {
        "entity": {"type": "string", "format": "identifier"},
        "fields": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string", "format": "identifier"},
                    "type": {"type": "string", "enum": ["text", "int", "bool"]},
                },
            },
        },
    },
}


def test_validate_schema_value_dialect():
    good = {"entity": "client", "fields": [{"name": "amount", "type": "int"}]}
    assert bs.validate_schema_value(_DIALECT_SCHEMA, good) == []

    cases = [
        ({"entity": "client"}, "$.fields"),                                 # missing required
        ({"entity": 3, "fields": [{"name": "a", "type": "int"}]}, "$.entity"),  # wrong type
        ({"entity": "client", "fields": [{"name": "a", "type": "float"}]}, "$.fields[0].type"),  # enum
        ({"entity": "client", "fields": [{"name": "1x", "type": "int"}]}, "$.fields[0].name"),   # pattern
        ({"entity": "client", "fields": [{"name": "a", "type": "int"}] * 4}, "$.fields"),        # maxItems
        ({"entity": "client", "fields": [{"name": "a", "type": "int", "x": 1}]}, "$.fields[0].x"),  # unknown prop
    ]
    for payload, path_frag in cases:
        errors = bs.validate_schema_value(_DIALECT_SCHEMA, payload)
        assert errors, f"expected error for {payload!r}"
        assert any(path_frag in e for e in errors), f"{payload!r}: {errors} lacks {path_frag}"


def test_slot_identifier_and_reserved_blocklist():
    sk = _load_real("scaffold-crud-sqlite")
    # reserved word as entity
    ok, _ = bs.validate_slot(sk, {"entity": "drop",
                                  "fields": [{"name": "name", "type": "text", "required": True}]})
    assert ok is False
    # reserved word as field name
    ok, _ = bs.validate_slot(sk, {"entity": "client",
                                  "fields": [{"name": "select", "type": "text", "required": True}]})
    assert ok is False
    # injection attempt fails the identifier pattern
    ok, _ = bs.validate_slot(sk, {"entity": "Robert'); DROP TABLE clients;--",
                                  "fields": [{"name": "name", "type": "text", "required": True}]})
    assert ok is False
    # a clean slot is accepted
    ok, errors = bs.validate_slot(sk, {"entity": "client",
                                       "fields": [{"name": "name", "type": "text", "required": True}]})
    assert ok is True, errors


def test_slot_field_count_bounds():
    sk = _load_real("scaffold-crud-sqlite")
    one = [{"name": "f0", "type": "text", "required": True}]
    eight = [{"name": f"f{i}", "type": "text", "required": True} for i in range(8)]
    nine = [{"name": f"f{i}", "type": "text", "required": True} for i in range(9)]
    assert bs.validate_slot(sk, {"entity": "client", "fields": []})[0] is False
    assert bs.validate_slot(sk, {"entity": "client", "fields": nine})[0] is False
    assert bs.validate_slot(sk, {"entity": "client", "fields": one})[0] is True
    assert bs.validate_slot(sk, {"entity": "client", "fields": eight})[0] is True


def test_slot_requires_at_least_one_text_field():
    """Regression (live-battery find, 2026-07-10): the gate plants its per-row canary in a TEXT
    field, so an all-int/bool scaffold is un-gateable (a hex canary minted into an int column 400s,
    failing the app's OWN buttons/persistence/schema checks). Reject fail-closed with a clear message
    so the driver retries into a valid build instead of shipping a silently-broken app."""
    sk = _load_real("scaffold-crud-sqlite")
    no_text = [{"name": "value", "type": "int", "required": True},
               {"name": "high", "type": "bool", "required": True}]
    ok, errors = bs.validate_slot(sk, {"entity": "reading", "fields": no_text})
    assert ok is False
    assert any("text field" in e for e in errors)
    # one text field is enough
    assert bs.validate_slot(sk, {"entity": "reading",
                                 "fields": [{"name": "label", "type": "text", "required": True}] + no_text})[0] is True


# --- 4. tolerant slot extraction --------------------------------------------

def test_extract_slot_json_tolerates_noise():
    prose = 'Sure! Here is the slot:\n```json\n{"entity": "client", "fields": []}\n```\nHope that helps.'
    assert bs.extract_slot_json(prose) == {"entity": "client", "fields": []}
    two = 'first {"a": 1} then {"b": 2}'
    assert bs.extract_slot_json(two) == {"a": 1}
    assert bs.extract_slot_json("no json here at all") is None
    assert bs.extract_slot_json("{not valid json,,,}") is None


# --- 5. deterministic anchored rendering ------------------------------------

def _client_slot():
    return {"entity": "client",
            "fields": [{"name": "name", "type": "text", "required": True},
                       {"name": "paid", "type": "bool", "required": True},
                       {"name": "status", "type": "text", "required": True}]}


def test_render_rejects_unvalidated_slot(tmp_path):
    sk = _load_real("scaffold-crud-sqlite")
    app_dir = tmp_path / "app"
    with pytest.raises(bs.SlotValidationError):
        bs.render_skill(sk, {"entity": "drop", "fields": []}, app_dir, workspace=REPO)
    # nothing written on refusal
    assert not (app_dir / "app.py").exists()


def _structurally_contract_compliant(text: str):
    assert "--port" in text
    assert "--db" in text
    assert "CORTEX_APP_READY" in text
    assert "sqlite3" in text
    assert ":memory:" not in text
    assert "{{" not in text  # no unresolved template markers


def test_render_scaffold_writes_compilable_contract_app(tmp_path):
    sk = _load_real("scaffold-crud-sqlite")
    app_dir = tmp_path / "app"
    written = bs.render_skill(sk, _client_slot(), app_dir, workspace=REPO)
    app_py = app_dir / "app.py"
    assert app_py in written
    text = app_py.read_text(encoding="utf-8")
    _structurally_contract_compliant(text)
    py_compile.compile(str(app_py), doraise=True)


def test_render_is_deterministic(tmp_path):
    sk = _load_real("scaffold-crud-sqlite")
    a, b = tmp_path / "a", tmp_path / "b"
    bs.render_skill(sk, _client_slot(), a, workspace=REPO)
    bs.render_skill(sk, _client_slot(), b, workspace=REPO)
    assert (a / "app.py").read_bytes() == (b / "app.py").read_bytes()


def test_render_leftover_marker_is_error(tmp_path):
    sk = _load_real("scaffold-crud-sqlite")
    # a template referencing an undefined slot field must fail closed
    with pytest.raises(bs.RenderError):
        bs._substitute("head {{slot.nonexistent}} tail", {"slot.entity": "client"})


def test_replace_anchored_replaces_only_block():
    text = ("A\n# CORTEX-SLOT:blk BEGIN\nOLD\n# CORTEX-SLOT:blk END\nB\n")
    out = bs.replace_anchored(text, "blk", "NEW1\nNEW2")
    assert out.startswith("A\n# CORTEX-SLOT:blk BEGIN\n")
    assert out.endswith("# CORTEX-SLOT:blk END\nB\n")
    assert "NEW1\nNEW2" in out
    assert "OLD" not in out
    with pytest.raises(bs.RenderError):
        bs.replace_anchored("no anchors here", "blk", "x")
    dup = text + text
    with pytest.raises(bs.RenderError):
        bs.replace_anchored(dup, "blk", "x")


# --- 6. add-conditional-class -----------------------------------------------

def _scaffold_into(tmp_path: Path) -> Path:
    sk = _load_real("scaffold-crud-sqlite")
    app_dir = tmp_path / "app"
    bs.render_skill(sk, _client_slot(), app_dir, workspace=REPO)
    return app_dir


def _cond_slot():
    return {"column": "status", "op": "eq", "value": "late", "css_class": "late", "color": "red"}


def test_add_conditional_class_precondition_refuses_missing_column(tmp_path):
    import sqlite3
    sk = _load_real("add-conditional-class")
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    db = app_dir / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")  # no status column
    con.commit(); con.close()
    (app_dir / "app.py").write_text("# scaffolded", encoding="utf-8")

    ok, reasons = bs.check_preconditions(sk, app_dir, _cond_slot())
    assert ok is False
    assert any("status" in r for r in reasons)

    con = sqlite3.connect(db)
    con.execute("ALTER TABLE clients ADD COLUMN status TEXT")
    con.commit(); con.close()
    ok, reasons = bs.check_preconditions(sk, app_dir, _cond_slot())
    assert ok is True, reasons


def test_add_conditional_class_renders_css_and_row_class(tmp_path):
    app_dir = _scaffold_into(tmp_path)
    before = (app_dir / "app.py").read_text(encoding="utf-8")
    sk = _load_real("add-conditional-class")
    bs.render_skill(sk, _cond_slot(), app_dir, workspace=REPO)
    after = (app_dir / "app.py").read_text(encoding="utf-8")

    assert after != before
    # the row_class block now references the condition column
    assert "status" in after
    # a CSS rule for the class was injected
    assert ".late" in after
    # still compiles and stays contract compliant
    _structurally_contract_compliant(after)
    py_compile.compile(str(app_dir / "app.py"), doraise=True)


def test_add_conditional_class_render_is_deterministic(tmp_path):
    a = _scaffold_into(tmp_path / "a")
    b = _scaffold_into(tmp_path / "b")
    sk = _load_real("add-conditional-class")
    bs.render_skill(sk, _cond_slot(), a, workspace=REPO)
    bs.render_skill(sk, _cond_slot(), b, workspace=REPO)
    assert (a / "app.py").read_bytes() == (b / "app.py").read_bytes()


# --- 7. the injected step-prompt --------------------------------------------

def test_step_prompt_embeds_schema_and_example_and_demands_json_only():
    for sid in SEED_IDS:
        sk = _load_real(sid)
        prompt = bs.build_step_prompt(sk, "make me a thing")
        assert "make me a thing" in prompt
        # the serialized schema keys appear
        for key in sk.slot.schema.get("properties", {}):
            assert key in prompt
        # the example is embedded
        assert json.dumps(sk.slot.example, sort_keys=True) in prompt or \
            all(str(v) in prompt for v in sk.slot.example.values())
        low = prompt.lower()
        assert "json" in low and "one" in low


def test_step_prompt_leaks_no_gate_internals():
    for sid in SEED_IDS:
        sk = _load_real(sid)
        prompt = bs.build_step_prompt(sk, "utterance")
        assert "@hidden:" not in prompt
        # no wholesale done_checks serialization leaks into the prompt
        assert json.dumps(sk.done_checks) not in prompt
        assert "ledger_file" not in prompt
        assert "security_controls" not in prompt


# --- 8. occurrence floor / never auto-verify --------------------------------

def test_record_outcome_floors_and_never_autoverifies(tmp_path):
    ws = _seed_ws(tmp_path)
    bs.record_outcome("scaffold-crud-sqlite", True, workspace=ws)
    bs.record_outcome("scaffold-crud-sqlite", True, workspace=ws)
    sk = bs.load_skills(ws)["scaffold-crud-sqlite"]
    assert sk.occurrence_count == 2
    assert sk.pass_count == 2
    assert sk.verified is False


# --- 9. the no-LLM firewall -------------------------------------------------

def test_build_skills_module_never_imports_llm_modules():
    src = (REPO / "cortex_core" / "build_skills.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed_relative = {"app_contract", "config"}
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in stdlib, f"non-stdlib absolute import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                assert node.module in allowed_relative, f"disallowed relative import: {node.module}"
            else:
                top = (node.module or "").split(".")[0]
                assert top in stdlib, f"non-stdlib import: {node.module}"


# --- 10. integration placeholder (needs M1 gate + M2 fixtures; skips until they land) ---

def test_scaffold_render_passes_gate_integration(tmp_path):
    # Forward-looking: once M1 (app_gates) + M2 (fixtures) land, render the client
    # slot and assert run_done_checks(app_dir, standard_checks()) passes. Skip until then.
    pytest.importorskip("cortex_core.app_gates")
    pytest.skip("gate integration is exercised once M1+M2 land")
