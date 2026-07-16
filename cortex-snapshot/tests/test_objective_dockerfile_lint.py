"""Frozen tests for the objective Dockerfile-lint checker (Stage-2 style lane).

LABEL AUTHORITY: a stdlib-only regex/string state machine over the instruction stream that computes
the set of violated encoded rule-ids (checker_docker.lint / check_record), never a model/judge. These
tests pin the checker on hand-picked cases (independent of the runner's fixture list), sweep every
fixture asserting the checker agrees with its declared expected_label, and assert the lane's
structural invariants (balance, unique ids, taxonomy coverage, mutation-integrity).

Written to state the contract per SDD-then-TDD.
"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.objective_dockerfile_lint.checker_docker import (  # noqa: E402
    check_record,
    lint,
)
from evals.objective_dockerfile_lint.run_docker import FIXTURES  # noqa: E402


# --- hand-picked cases, independent of the runner's fixture list ------------------------------

def test_clean_dockerfile_has_no_violations():
    df = "FROM alpine:3.18\nWORKDIR /app\nCOPY . /app\nCMD [\"echo\", \"hi\"]\n"
    assert lint(df) == []
    assert check_record(df, []).objective_label == "CORRECT"
    assert check_record(df, ["DL3006"]).objective_label == "INCORRECT"


def test_untagged_from_is_dl3006():
    assert lint("FROM ubuntu\nCMD [\"bash\"]\n") == ["DL3006"]
    assert check_record("FROM ubuntu\n", ["DL3006"]).objective_label == "CORRECT"
    assert check_record("FROM ubuntu\n", []).objective_label == "INCORRECT"


def test_latest_tag_is_dl3007_not_dl3006():
    assert lint("FROM alpine:latest\nCMD [\"sh\"]\n") == ["DL3007"]


def test_add_local_file_is_dl3020():
    assert lint("FROM alpine:3.18\nADD app.txt /app/app.txt\n") == ["DL3020"]
    # a remote URL or an archive is a legitimate ADD -> no DL3020
    assert lint("FROM alpine:3.18\nADD https://x.example/y.txt /y.txt\n") == []
    assert lint("FROM alpine:3.18\nADD bundle.tar.gz /opt/\n") == []


def test_shell_form_cmd_is_dl3025():
    assert lint("FROM alpine:3.18\nCMD nginx -g daemon\n") == ["DL3025"]
    # JSON-array (exec) form is compliant
    assert lint("FROM alpine:3.18\nCMD [\"nginx\", \"-g\", \"daemon\"]\n") == []


def test_shell_form_entrypoint_is_dl3025():
    assert lint("FROM alpine:3.18\nENTRYPOINT /bin/sh -c echo\n") == ["DL3025"]


def test_maintainer_is_dl4000():
    assert lint("FROM alpine:3.18\nMAINTAINER me@example.com\n") == ["DL4000"]


def test_apt_get_install_without_cleanup_is_dl3009():
    df = "FROM debian:12\nRUN apt-get update && apt-get install -y curl\n"
    assert lint(df) == ["DL3009"]


def test_apt_get_install_with_cleanup_is_clean():
    df = ("FROM debian:12\nRUN apt-get update && apt-get install -y curl "
          "&& rm -rf /var/lib/apt/lists/*\n")
    assert lint(df) == []


def test_relative_workdir_is_dl3000():
    assert lint("FROM alpine:3.18\nWORKDIR app/data\n") == ["DL3000"]
    # absolute / variable / drive-letter WORKDIR is compliant
    assert lint("FROM alpine:3.18\nWORKDIR /app/data\n") == []
    assert lint("FROM alpine:3.18\nWORKDIR $HOME/app\n") == []


def test_first_instruction_must_be_from_or_arg():
    assert lint("RUN echo hi\nFROM alpine:3.18\n") == ["missing-FROM-first"]
    assert lint("ARG V=3.18\nFROM alpine:${V}\n") == []


def test_consecutive_run_is_dl3059():
    assert lint("FROM alpine:3.18\nRUN echo a\nRUN echo b\n") == ["DL3059"]
    # a single RUN, or RUN separated by another instruction, does not trigger it
    assert lint("FROM alpine:3.18\nRUN echo a\nWORKDIR /x\nRUN echo b\n") == []


def test_digest_and_scratch_from_are_compliant():
    assert lint("FROM alpine@sha256:deadbeef\nCMD [\"sh\"]\n") == []
    assert lint("FROM scratch\nCMD [\"/app\"]\n") == []


def test_multi_rule_union_is_sorted():
    df = "FROM ubuntu\nMAINTAINER ops\nADD x.txt /x.txt\nWORKDIR rel\nCMD nginx\n"
    assert lint(df) == ["DL3000", "DL3006", "DL3020", "DL3025", "DL4000"]


def test_candidate_set_order_is_irrelevant():
    df = "FROM ubuntu\nMAINTAINER ops\nADD x.txt /x.txt\nWORKDIR rel\nCMD nginx\n"
    assert check_record(df, ["DL4000", "DL3025", "DL3000", "DL3006", "DL3020"]).objective_label == "CORRECT"


def test_computed_answer_is_the_rule_id_set():
    r = check_record("FROM alpine:latest\n", [])
    assert r.computed_answer == ["DL3007"]


# --- full fixture sweep ----------------------------------------------------------------------

def test_all_fixtures_checker_agrees_with_expected_label():
    mismatches = []
    for fx in FIXTURES:
        r = check_record(fx["dockerfile"], fx["candidate"])
        if r.objective_label != fx["expected_label"]:
            mismatches.append((fx["id"], fx["expected_label"], r.objective_label))
    assert mismatches == [], f"checker/fixture disagreement: {mismatches}"


def test_correct_fixtures_candidate_equals_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "CORRECT":
            continue
        assert sorted(set(fx["candidate"])) == lint(fx["dockerfile"]), fx["id"]


# --- structural invariants -------------------------------------------------------------------

def test_fixture_count_in_expected_range():
    assert 20 <= len(FIXTURES) <= 24


def test_fixture_ids_are_unique():
    ids = [fx["id"] for fx in FIXTURES]
    assert len(ids) == len(set(ids))


def test_fixture_label_distribution_balanced():
    dist = Counter(fx["expected_label"] for fx in FIXTURES)
    assert dist["CORRECT"] >= 8
    assert dist["INCORRECT"] >= 8


def test_all_failure_classes_covered():
    required = {
        "none", "missed_violation", "false_violation",
        "wrong_rule_id", "multi_rule", "clean_flagged",
    }
    present = {fx["failure_class"] for fx in FIXTURES}
    assert required.issubset(present), required - present


def test_every_incorrect_fixture_states_its_mutation():
    for fx in FIXTURES:
        if fx["expected_label"] == "INCORRECT":
            assert fx.get("mutation"), f"{fx['id']} is INCORRECT but has no mutation description"


def test_mutation_integrity_incorrect_shares_scenario_with_a_correct_sibling():
    by_key = {}
    for fx in FIXTURES:
        by_key.setdefault(fx["dockerfile"], []).append(fx)
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        siblings = by_key[fx["dockerfile"]]
        assert any(s["expected_label"] == "CORRECT" for s in siblings), fx["id"]


def test_incorrect_candidate_differs_from_computed():
    for fx in FIXTURES:
        if fx["expected_label"] != "INCORRECT":
            continue
        assert sorted(set(fx["candidate"])) != lint(fx["dockerfile"]), fx["id"]
