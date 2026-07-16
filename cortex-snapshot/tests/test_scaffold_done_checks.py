"""Regression: the scaffold's done-checks must be generated FROM THE SLOT (real table/route/
fields), not frozen to the skill's example entity. Before this, only the example entity ('client')
passed the gate and every other entity failed BUTTON_FAIL/PERSISTENCE_FAIL because the checks
probed '/clients'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import build_skills as bs  # noqa: E402

_SLOT = {"entity": "gym_member",
         "fields": [{"name": "name", "type": "text", "required": True},
                    {"name": "email", "type": "text", "required": False},
                    {"name": "active", "type": "bool", "required": True}]}


def test_checks_target_the_slots_table_and_route_not_the_example():
    checks = bs.scaffold_done_checks(_SLOT)
    by = {c["kind"]: c for c in checks}
    # table = entity + "s"; route = /<table>
    assert by["data_persists"]["resource"]["table"] == "gym_members"
    assert by["data_persists"]["resource"]["read_path"] == "/gym_members"
    assert by["data_persists"]["resource"]["column"] == "name"          # primary text field
    assert by["schema_real"]["table"] == "gym_members"
    assert by["buttons_work"]["actions"][0]["request"]["path"] == "/gym_members"
    assert by["security_controls"]["write"]["path"] == "/gym_members"
    # NO frozen 'clients' anywhere
    import json
    assert "clients" not in json.dumps(checks)


def test_form_carries_every_required_field():
    form = bs.scaffold_done_checks(_SLOT)[1]["actions"][0]["request"]["form"]  # buttons_work
    assert "name" in form and "active" in form         # both required
    assert "email" not in form                          # optional -> omitted
    assert form["name"].startswith("@hidden:")          # token on the text field
    assert form["active"] == "0"                        # bool default


def test_schema_expects_all_columns():
    sc = bs.scaffold_done_checks(_SLOT)[3]  # schema_real
    assert set(sc["required_columns"]) >= {"id", "name", "email", "active"}


def test_resolve_done_checks_dispatches_scaffold_to_generator():
    skill = bs.load_skills()["scaffold-crud-sqlite"]
    assert bs.resolve_done_checks(skill, _SLOT) == bs.scaffold_done_checks(_SLOT)


def test_generated_checks_pass_the_static_spec_linter():
    from cortex_core.app_contract import validate_check_spec
    for c in bs.scaffold_done_checks(_SLOT):
        assert validate_check_spec(c) == [], c
