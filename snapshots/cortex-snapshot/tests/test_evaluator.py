"""Tests for Phase 4.4 evaluator: grading atomic claims against evidence."""

from pathlib import Path

from cortex_core import evaluator as E


def test_extract_claims_from_completed_closeout():
    """Extracting claims from a closeout should pull task + task_type."""
    closeout = {
        "task": "Fix SSRF IP-pinning DNS rebinding",
        "task_type": "bugfix",
        "result": "Added connection-time IP pinning after DNS validation",
        "status": "completed",
        "timestamp": "2026-07-04T10:00:00Z",
    }
    claims = E.extract_claims_from_closeout(closeout)
    assert len(claims) == 1
    assert claims[0].description == "Fix SSRF IP-pinning DNS rebinding"
    assert claims[0].task_type == "bugfix"


def test_extract_claims_ignores_incomplete():
    """Closeouts with status != completed should not yield claims."""
    closeout = {
        "task": "Work in progress",
        "task_type": "feature",
        "status": "in_progress",
    }
    claims = E.extract_claims_from_closeout(closeout)
    assert len(claims) == 0


def test_extract_claims_ignores_missing_task_type():
    """Closeouts without task_type should not yield claims."""
    closeout = {
        "task": "Some task",
        "status": "completed",
        # task_type missing
    }
    claims = E.extract_claims_from_closeout(closeout)
    assert len(claims) == 0


def test_extract_claims_never_includes_actor_prose():
    """Claims should never include the actor's result prose (MARCH asymmetry)."""
    closeout = {
        "task": "Fix parser bug",
        "task_type": "bugfix",
        "status": "completed",
        "result": "Added guards in the parser, updated three test cases to verify the fix",
        "timestamp": "2026-07-04T10:00:00Z",
    }
    claims = E.extract_claims_from_closeout(closeout)
    assert len(claims) == 1
    claim = claims[0]
    # The claim must have only task + task_type, NO approach or result prose
    assert claim.description == "Fix parser bug"
    assert claim.task_type == "bugfix"
    # Verify that actor prose is NOT in the claim object
    assert not hasattr(claim, 'approach') or claim.__dict__.get('approach') is None
    # The evaluator must not see the actor's detailed result


def test_grade_bugfix_supported(tmp_path):
    """A bugfix with test + file evidence should be SUPPORTED."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "cortex_core").mkdir(exist_ok=True)
    (tmp_path / "cortex_core" / "parser.py").write_text("# parser module")

    claim = E.AtomicClaim(
        claim_id="test:1",
        task_type="bugfix",
        description="Fix the parser crash on empty input",
    )
    evidence = [
        {"type": "test", "ref": "test_parser_empty_input", "detail": "PASSED"},
        {"type": "file", "ref": "cortex_core/parser.py:42-50", "detail": "Added empty-input guard"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED
    assert grade.confidence > 0.85
    assert grade.evidence_count == 2
    assert len(grade.gaps) == 0


def test_grade_bugfix_partially_supported():
    """A bugfix with only test evidence should be PARTIALLY_SUPPORTED."""
    claim = E.AtomicClaim(
        claim_id="test:2",
        task_type="bugfix",
        description="Fix cache invalidation",
    )
    evidence = [
        {"type": "test", "ref": "test_cache_invalidation", "detail": "PASSED"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence)
    assert grade.verdict == E.Verdict.PARTIALLY_SUPPORTED
    assert "file" in str(grade.gaps).lower()


def test_grade_bugfix_unsupported():
    """A bugfix with no relevant evidence should be UNSUPPORTED."""
    claim = E.AtomicClaim(
        claim_id="test:3",
        task_type="bugfix",
        description="Fix bug X",
    )
    evidence = [
        {"type": "command", "ref": "grep 'TODO'", "detail": "Found 5 TODOs"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence)
    assert grade.verdict == E.Verdict.UNSUPPORTED
    assert grade.confidence < 0.5


def test_grade_bugfix_no_evidence():
    """A bugfix with no evidence should be UNVERIFIABLE."""
    claim = E.AtomicClaim(
        claim_id="test:4",
        task_type="bugfix",
        description="Fix bug Y",
    )
    grade = E.grade_claim_rule_based(claim, [])
    assert grade.verdict == E.Verdict.UNVERIFIABLE
    assert grade.confidence == 0.0


def test_grade_test_claim(tmp_path):
    """A test-type claim should require test + file evidence."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_cache.py").write_text("# tests")

    claim = E.AtomicClaim(
        claim_id="test:5",
        task_type="test",
        description="Add tests for cache module",
    )
    # With both test and file evidence
    evidence = [
        {"type": "test", "ref": "test_cache_new_tests", "detail": "5 new tests PASSED"},
        {"type": "file", "ref": "tests/test_cache.py", "detail": "Added 5 test functions"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED
    assert grade.confidence >= 0.9


def test_grade_feature_claim(tmp_path):
    """A feature claim should have file + test/eval evidence."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "cortex_core").mkdir(exist_ok=True)
    (tmp_path / "cortex_core" / "packs.py").write_text("# packs module")

    claim = E.AtomicClaim(
        claim_id="test:6",
        task_type="feature",
        description="Add scope-pack builder",
    )
    evidence = [
        {"type": "file", "ref": "cortex_core/packs.py", "detail": "New module"},
        {"type": "eval", "ref": "mean_context_cut", "detail": "96.1% on golden set"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED
    assert grade.confidence > 0.8


def test_grade_research_claim(tmp_path):
    """A research claim should have file evidence, ideally with eval."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "docs" / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "research" / "deep-research-design.md").write_text("# research")

    claim = E.AtomicClaim(
        claim_id="test:7",
        task_type="research",
        description="Deep research on retrieval engines",
    )
    # With file only (partially)
    evidence_file_only = [
        {"type": "file", "ref": "docs/research/deep-research-design.md", "detail": "Wrote design doc"},
    ]
    grade1 = E.grade_claim_rule_based(claim, evidence_file_only, workspace=tmp_path)
    assert grade1.verdict == E.Verdict.PARTIALLY_SUPPORTED

    # With file + eval (supported)
    evidence_with_eval = evidence_file_only + [
        {"type": "eval", "ref": "coverage", "detail": "100% sub-question coverage"},
    ]
    grade2 = E.grade_claim_rule_based(claim, evidence_with_eval, workspace=tmp_path)
    assert grade2.verdict == E.Verdict.SUPPORTED
    assert grade2.confidence > grade1.confidence


def test_grade_docs_claim(tmp_path):
    """A docs claim should have file evidence."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "cortex_core").mkdir(exist_ok=True)
    (tmp_path / "cortex_core" / "evaluator.py").write_text("# evaluator")

    claim = E.AtomicClaim(
        claim_id="test:8",
        task_type="docs",
        description="Document the evaluator design",
    )
    evidence = [
        {"type": "file", "ref": "cortex_core/evaluator.py:1-10", "detail": "Module docstring"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED
    assert grade.confidence > 0.8


def test_grade_generic_task_type(tmp_path):
    """Unknown task types (chore, refactor, explore) accept any evidence."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("[project]")

    claim = E.AtomicClaim(
        claim_id="test:9",
        task_type="chore",
        description="Update deps",
    )
    evidence = [
        {"type": "file", "ref": "pyproject.toml", "detail": "Bumped version"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED


def test_compute_verified_gap_empty():
    """Empty closeouts should yield a zero gap."""
    result = E.compute_verified_gap([])
    assert result["gap_fraction"] == 0.0
    assert result["total"] == 0
    assert result["verified_count"] == 0


def test_compute_verified_gap_mixed(tmp_path):
    """Compute gap from a mix of supported and unsupported claims."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "module.py").write_text("# module")

    closeouts = [
        {
            "task": "Fix parser bug",
            "task_type": "bugfix",
            "status": "completed",
            "timestamp": "2026-07-04T10:00:00Z",
            "evidence": [
                {"type": "test", "ref": "test_parser_bug", "detail": "PASSED"},
                {"type": "file", "ref": "module.py:10", "detail": "Changed"},
            ],
        },
        {
            "task": "Add module feature",
            "task_type": "feature",
            "status": "completed",
            "timestamp": "2026-07-04T11:00:00Z",
            "evidence": [
                {"type": "file", "ref": "module.py:20", "detail": "Changed"},
                # No test evidence — partially supported
            ],
        },
        {
            "task": "Document module",
            "task_type": "docs",
            "status": "completed",
            "timestamp": "2026-07-04T12:00:00Z",
            "evidence": [],  # No evidence — unverifiable
        },
    ]
    result = E.compute_verified_gap(closeouts, workspace=tmp_path)
    assert result["total"] == 3
    # With semantic relevance checking: parser_bug evidence is semantically relevant,
    # but "module.py" is not (doesn't contain parser/bug keywords), so the bugfix is PARTIALLY_SUPPORTED
    assert result["verified_count"] == 0
    assert result["partial_count"] == 2  # bugfix (test relevant but file not) + feature (file only)
    assert result["unverified_count"] == 1  # the docs (no evidence)
    assert result["gap_fraction"] == 1 / 3  # unverified / total


def test_verdict_values():
    """Verdict enum should have expected values."""
    assert E.Verdict.SUPPORTED.value == "supported"
    assert E.Verdict.PARTIALLY_SUPPORTED.value == "partially_supported"
    assert E.Verdict.UNSUPPORTED.value == "unsupported"
    assert E.Verdict.UNVERIFIABLE.value == "unverifiable"


def test_grade_serialization():
    """Grades should serialize to dicts with all fields."""
    claim = E.AtomicClaim(
        claim_id="test:10",
        task_type="feature",
        description="Test serialization",
    )
    evidence = [
        {"type": "file", "ref": "test.py", "detail": "Added"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence)
    result = E.compute_verified_gap([{
        "task": "Test",
        "task_type": "feature",
        "status": "completed",
        "timestamp": "2026-07-04T00:00:00Z",
        "evidence": evidence,
    }])
    grades_dicts = result["grades"]
    assert len(grades_dicts) == 1
    g = grades_dicts[0]
    assert "claim_id" in g
    assert "verdict" in g
    assert "confidence" in g
    assert "reasoning" in g
    assert "evidence_count" in g
    assert "gaps" in g
    assert isinstance(g["verdict"], str)


def test_march_asymmetry():
    """The evaluator should grade based on evidence alone, not actor prose.

    Closeouts with identical claims+evidence but different self-reported prose
    must get the same grade. The evaluator never sees the result field.
    """
    # Two closeouts with identical task/task_type/evidence but different prose
    closeout1 = {
        "task": "Added scope packs",
        "task_type": "feature",
        "status": "completed",
        "timestamp": "2026-07-04T10:00:00Z",
        "result": "The implementation was straightforward",
        "evidence": [
            {"type": "file", "ref": "cortex_core/packs.py", "detail": "New module"},
            {"type": "eval", "ref": "context_cut", "detail": "96.1%"},
        ],
    }
    closeout2 = {
        "task": "Added scope packs",
        "task_type": "feature",
        "status": "completed",
        "timestamp": "2026-07-04T11:00:00Z",
        "result": "Took three weeks of careful design and iteration to get right",
        "evidence": [
            {"type": "file", "ref": "cortex_core/packs.py", "detail": "New module"},
            {"type": "eval", "ref": "context_cut", "detail": "96.1%"},
        ],
    }

    # Extract claims (should omit the result prose entirely)
    claims1 = E.extract_claims_from_closeout(closeout1)
    claims2 = E.extract_claims_from_closeout(closeout2)

    assert len(claims1) == 1
    assert len(claims2) == 1

    # Grade both claims — MUST get identical grades
    grade1 = E.grade_claim_rule_based(claims1[0], closeout1["evidence"])
    grade2 = E.grade_claim_rule_based(claims2[0], closeout2["evidence"])

    # Same verdict, confidence, reasoning (not influenced by result prose)
    assert grade1.verdict == grade2.verdict
    assert grade1.confidence == grade2.confidence
    # Different prose but same grade proves asymmetry is maintained


def test_gaps_are_actionable(tmp_path):
    """Gaps in the verdict should suggest what's missing."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "issue.py").write_text("# issue")

    claim = E.AtomicClaim(
        claim_id="gaps:1",
        task_type="bugfix",
        description="Fixed issue X",
    )
    # Only file evidence, missing test
    evidence = [
        {"type": "file", "ref": "issue.py", "detail": "Fixed"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict in (E.Verdict.PARTIALLY_SUPPORTED, E.Verdict.UNSUPPORTED)
    assert len(grade.gaps) > 0


def test_bad_file_reference_makes_unverifiable(tmp_path):
    """Evidence with unresolvable file refs should make the grade unverifiable."""
    # Set up a minimal workspace structure so resolve_workspace works
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")

    claim = E.AtomicClaim(
        claim_id="badref:1",
        task_type="bugfix",
        description="Fixed something",
    )
    # File ref that doesn't exist
    evidence = [
        {"type": "file", "ref": "/nonexistent/path/file.py", "detail": "Changed"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.UNVERIFIABLE
    assert "resolve" in grade.reasoning.lower() or "not found" in grade.reasoning.lower()


def test_file_evidence_without_workspace_is_unverifiable():
    """File evidence without a workspace to validate against makes grade UNVERIFIABLE."""
    claim = E.AtomicClaim(
        claim_id="file_no_ws:1",
        task_type="bugfix",
        description="Fixed parser bug",
    )
    evidence = [
        {"type": "file", "ref": "cortex_core/parser.py:10-20", "detail": "Added guard"},
    ]
    # Grade without workspace — file evidence is unverifiable
    grade = E.grade_claim_rule_based(claim, evidence, workspace=None)
    assert grade.verdict == E.Verdict.UNVERIFIABLE
    assert "workspace" in grade.reasoning.lower()


def test_semantic_relevance_prevents_evidence_theater(tmp_path):
    """Evidence that doesn't contain claim keywords should lower confidence (anti-evidence-theater)."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "cortex_core").mkdir(exist_ok=True)
    (tmp_path / "cortex_core" / "config.py").write_text("# config")

    claim = E.AtomicClaim(
        claim_id="theater:1",
        task_type="bugfix",
        description="Fixed crash in parser initialization",
    )
    # Evidence that mentions unrelated items (config, logging) not in claim
    evidence = [
        {"type": "test", "ref": "test_logging_handler", "detail": "PASSED"},
        {"type": "file", "ref": "cortex_core/config.py", "detail": "Updated"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    # The evidence is present and valid but semantically unrelated to the claim
    assert grade.verdict == E.Verdict.UNSUPPORTED
    assert grade.confidence < 0.3  # Very low confidence when evidence is unrelated
    assert "relate" in grade.reasoning.lower() or any("relate" in gap.lower() for gap in grade.gaps)


def test_semantic_relevance_now_fires_on_feature(tmp_path):
    """Regression: the anti-theater check must apply to feature, not just bugfix.

    A feature claim with a file that has nothing to do with the claim used to
    score SUPPORTED (semantic check was only wired into bugfix). It must not."""
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "unrelated.py").write_text("# unrelated")

    claim = E.AtomicClaim(
        claim_id="feat-theater:1",
        task_type="feature",
        description="Add scope-pack builder for retrieval",
    )
    evidence = [
        {"type": "file", "ref": "unrelated.py", "detail": "changed"},
        {"type": "eval", "ref": "some_metric", "detail": "0.9"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.UNSUPPORTED
    assert "relate" in grade.reasoning.lower() or any("relate" in g.lower() for g in grade.gaps)


def test_semantic_relevance_now_fires_on_docs(tmp_path):
    """Regression: the anti-theater check must apply to docs too."""
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "random.py").write_text("# random")

    claim = E.AtomicClaim(
        claim_id="docs-theater:1",
        task_type="docs",
        description="Document the evaluator rubric",
    )
    evidence = [{"type": "file", "ref": "random.py", "detail": "changed"}]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.UNSUPPORTED


def test_lenient_chore_accepts_unrelated_evidence(tmp_path):
    """chore/explore are exempt from relevance (mirrors contract.py)."""
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("[project]")

    claim = E.AtomicClaim(
        claim_id="chore:1",
        task_type="chore",
        description="Bump dependency versions",
    )
    # pyproject.toml shares no keywords with the claim, but chore is lenient
    evidence = [{"type": "file", "ref": "pyproject.toml", "detail": "bumped"}]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED


def test_unknown_task_type_is_flagged_not_silently_supported(tmp_path):
    """An unrecognized task_type must not get a free SUPPORTED grade."""
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "x.py").write_text("# x")

    claim = E.AtomicClaim(
        claim_id="unknown:1",
        task_type="frobnicate",  # not in TASK_TYPES
        description="Frobnicate the thing",
    )
    evidence = [{"type": "file", "ref": "x.py", "detail": "changed"}]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.PARTIALLY_SUPPORTED
    assert any("rubric" in g.lower() for g in grade.gaps)


def test_semantically_relevant_evidence_scores_high(tmp_path):
    """Evidence that mentions claim keywords should score as SUPPORTED."""
    # Set up minimal workspace for file validation
    (tmp_path / "library" / "cortex-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cortex.json").write_text("{}")
    (tmp_path / "cortex_core").mkdir(exist_ok=True)
    (tmp_path / "cortex_core" / "parser.py").write_text("# parser")

    claim = E.AtomicClaim(
        claim_id="relevant:1",
        task_type="bugfix",
        description="Fixed crash in parser initialization",
    )
    # Evidence that mentions the keywords from the claim (parser, crash, initialization)
    evidence = [
        {"type": "test", "ref": "test_parser_initialization_crash", "detail": "PASSED"},
        {"type": "file", "ref": "cortex_core/parser.py", "detail": "Added init guard"},
    ]
    grade = E.grade_claim_rule_based(claim, evidence, workspace=tmp_path)
    assert grade.verdict == E.Verdict.SUPPORTED
    assert grade.confidence >= 0.85
    assert len(grade.gaps) == 0
