"""Gap J4: closeout = *index* of evidence, not evidence.

Every checkable claim in a closeout must link to a MECHANICALLY RECORDED
artifact (an exit code, a file sha256, a git diff ref, an objective-oracle
output id, or a trace span id) -- never prose or an LLM's say-so. The evaluator
abstains (`UNVERIFIABLE`) on any checkable claim whose only backing is
narration. This is the anti-circular property: the closeout story can never
over-state past what the trace mechanically shows. NO LLM sits in this path.

The requirement is versioned: it applies at closeout `schema_version >=
EVIDENCE_REF_SCHEMA_VERSION` (v4) going FORWARD; legacy (older/absent version)
closeouts still validate unchanged, so the historical audit log is never
retroactively failed.
"""

from cortex_core import evaluator as E
from cortex_core.audit import CLOSEOUT_SCHEMA_VERSION


V4 = E.EVIDENCE_REF_SCHEMA_VERSION


def _mkworkspace(tmp_path):
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "cortex_core").mkdir(exist_ok=True)
    (tmp_path / "cortex_core" / "parser.py").write_text("# parser module")
    return tmp_path


# --- mechanical_ref_kind: what counts as a mechanically-recorded artifact ----

def test_exit_code_is_a_mechanical_ref():
    assert E.mechanical_ref_kind({"type": "test", "ref": "t", "exit_code": 0}) == "exit_code"


def test_sha256_is_a_mechanical_ref():
    assert E.mechanical_ref_kind({"type": "file", "ref": "x.py", "sha256": "ab12"}) == "sha256"


def test_explicit_evidence_ref_dict_kinds():
    for key in ("git_diff", "oracle_id", "span_id"):
        item = {"type": "eval", "ref": "m", "evidence_ref": {key: "abc123"}}
        assert E.mechanical_ref_kind(item) == key


def test_prose_only_evidence_has_no_mechanical_ref():
    """A claim whose only backing is a detail string -> NOT mechanical."""
    assert E.mechanical_ref_kind({"type": "test", "ref": "t", "detail": "all passed"}) is None


def test_llm_assertion_is_not_a_mechanical_ref():
    """Anti-circular: an LLM verdict / model say-so is not a mechanical ref."""
    item = {"type": "eval", "ref": "m", "evidence_ref": {"llm_verdict": "looks good", "model": "x"}}
    assert E.mechanical_ref_kind(item) is None


# --- versioned enforcement in the grading path ------------------------------

def test_v4_tests_passed_with_exit_code_ref_validates(tmp_path):
    """A v4 'tests passed' bugfix backed by a real exit code + sha256'd file
    change validates (not forced UNVERIFIABLE)."""
    _mkworkspace(tmp_path)
    claim = E.AtomicClaim(claim_id="j4:1", task_type="bugfix",
                          description="Fix the parser crash on empty input")
    evidence = [
        {"type": "test", "ref": "test_parser_empty_input", "exit_code": 0, "passed": True},
        {"type": "file", "ref": "cortex_core/parser.py:42-50", "sha256": "deadbeef",
         "detail": "Added empty-input guard"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path, schema_version=V4)
    assert grade.verdict == E.Verdict.SUPPORTED


def test_v4_tests_passed_without_ref_is_unverifiable(tmp_path):
    """SAME claim, but the test result is prose-only (no exit code / ref) ->
    UNVERIFIABLE at v4. The narration can't stand in for the trace."""
    _mkworkspace(tmp_path)
    claim = E.AtomicClaim(claim_id="j4:2", task_type="bugfix",
                          description="Fix the parser crash on empty input")
    evidence = [
        {"type": "test", "ref": "test_parser_empty_input", "detail": "PASSED"},
        {"type": "file", "ref": "cortex_core/parser.py:42-50", "detail": "Added guard"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path, schema_version=V4)
    assert grade.verdict == E.Verdict.UNVERIFIABLE
    assert "evidence_ref" in grade.reasoning or "mechanical" in grade.reasoning.lower()


def test_legacy_closeout_without_refs_still_validates(tmp_path):
    """A legacy (pre-v4 / absent version) closeout with prose-only evidence
    grades exactly as before -- the new requirement is NOT applied retroactively."""
    _mkworkspace(tmp_path)
    claim = E.AtomicClaim(claim_id="j4:3", task_type="bugfix",
                          description="Fix the parser crash on empty input")
    evidence = [
        {"type": "test", "ref": "test_parser_empty_input", "detail": "PASSED"},
        {"type": "file", "ref": "cortex_core/parser.py:42-50", "detail": "Added guard"},
    ]
    # No schema_version (legacy default) -> unchanged behavior.
    legacy = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert legacy.verdict == E.Verdict.SUPPORTED
    # Explicit old version -> same.
    v3 = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path, schema_version=3)
    assert v3.verdict == E.Verdict.SUPPORTED


def test_compute_verified_gap_reads_per_closeout_schema_version(tmp_path):
    """compute_verified_gap enforces the ref requirement per-closeout, from each
    closeout's own schema_version -- a legacy row and a v4 row in one batch are
    graded under their own rules."""
    _mkworkspace(tmp_path)
    prose_evidence = [
        {"type": "test", "ref": "test_parser_empty_input", "detail": "PASSED"},
        {"type": "file", "ref": "cortex_core/parser.py:42-50", "detail": "Added guard"},
    ]
    closeouts = [
        {  # legacy: prose-only evidence still SUPPORTED
            "task": "Fix the parser crash on empty input", "task_type": "bugfix",
            "status": "completed", "timestamp": "t1", "schema_version": 3,
            "evidence": prose_evidence,
        },
        {  # v4: same prose-only evidence -> UNVERIFIABLE (no mechanical ref)
            "task": "Fix the parser crash on empty input", "task_type": "bugfix",
            "status": "completed", "timestamp": "t2", "schema_version": V4,
            "evidence": prose_evidence,
        },
    ]
    result = E.compute_verified_gap(closeouts, workspace=tmp_path)
    assert result["total"] == 2
    assert result["verified_count"] == 1     # the legacy one
    assert result["unverified_count"] == 1   # the v4 one


def test_closeout_schema_version_is_v4():
    """The closeout writer declares v4, so newly written closeouts carry the
    forward requirement."""
    assert CLOSEOUT_SCHEMA_VERSION == V4 == 4


def test_v4_lenient_chore_still_needs_a_mechanical_ref(tmp_path):
    """The ref requirement is about MECHANICAL backing, orthogonal to the
    substantive/lenient relevance split -- even a chore's evidence must be
    mechanically recorded at v4."""
    _mkworkspace(tmp_path)
    claim = E.AtomicClaim(claim_id="j4:chore", task_type="chore",
                          description="Bump dependency versions")
    prose = [{"type": "file", "ref": "pyproject.toml", "detail": "bumped"}]
    (tmp_path / "pyproject.toml").write_text("[project]")
    grade = E.grade_claim_rule_based(claim, prose, workspace=tmp_path, schema_version=V4)
    assert grade.verdict == E.Verdict.UNVERIFIABLE
