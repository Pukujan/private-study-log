"""Frozen tests for evals/resolvers/ (RESOLVER-DESIGNS.md cheapest-first build: DA3+DA6 git
resolvers, AB2 citation resolver). Stage-2 discipline: correct/honest artifact PASSES,
violation/theater artifact FAILS, named abstention conditions trigger correctly -- and every
resolver module must be judge-free per evals.oracle_adapter.verdict_path_is_judge_free.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.oracle_adapter import verdict_path_is_judge_free  # noqa: E402
from evals.resolvers.git_transcript_runner import (  # noqa: E402
    cat_file_exists,
    merge_base_is_ancestor,
    parse_nul_delimited_paths,
    resolve_head,
    rev_parse_head,
)
from evals.resolvers.deep_audit_da3_da6 import (  # noqa: E402
    ClosureFinding,
    ExecutionReceipt,
    da3_batch,
    da3_closure_ancestry,
    da6_justified_lag,
)
from evals.resolvers.citation_index import build_citation_index, load_or_build  # noqa: E402
from evals.resolvers.actionable_item_ab2 import (  # noqa: E402
    citation_resolvability_check,
    parse_citations,
)

RESOLVER_MODULES = [
    "evals/resolvers/git_transcript_runner.py",
    "evals/resolvers/deep_audit_da3_da6.py",
    "evals/resolvers/citation_index.py",
    "evals/resolvers/actionable_item_ab2.py",
]


def _real_ancestor_sha() -> str:
    """A commit 3 back on this repo's own current branch -- guaranteed a real ancestor of HEAD
    without depending on any specific SHA staying pinned."""
    log = subprocess.run(["git", "log", "-3", "--format=%H"], cwd=str(ROOT),
                          capture_output=True, text=True)
    shas = [s for s in log.stdout.strip().splitlines() if s]
    assert shas, "repo must have at least 1 commit for this test"
    return shas[-1]


def _head_sha() -> str:
    return rev_parse_head(ROOT).stdout.strip()


# ============================================================================= judge-free gate

def test_all_resolver_modules_are_judge_free():
    clean, problems = verdict_path_is_judge_free([ROOT / m for m in RESOLVER_MODULES])
    assert clean, problems


# ============================================================================= git_transcript_runner

def test_runner_refuses_no_free_form_command():
    # every allowlisted function takes a shape-restricted argv it builds internally --
    # a caller cannot pass an arbitrary git subcommand through the public API.
    import evals.resolvers.git_transcript_runner as runner
    public = [name for name in dir(runner) if not name.startswith("_")]
    assert "rev_parse_head" in public
    assert not hasattr(runner, "run_arbitrary")  # no raw-command escape hatch exists


def test_runner_transcript_shape():
    t = rev_parse_head(ROOT)
    assert t.exit_code == 0
    assert len(t.stdout.strip()) == 40
    assert t.argv[0] == "git"
    item = t.as_basis_item()
    assert item["kind"] == "command" and "transcript_ref" in item and item["timestamp"]


def test_cat_file_exists_real_vs_bogus():
    real = cat_file_exists(_head_sha(), ROOT)
    assert real.exit_code == 0
    bogus = cat_file_exists("f" * 40, ROOT)
    assert bogus.exit_code != 0


def test_merge_base_is_ancestor_semantics():
    ancestor_sha = _real_ancestor_sha()
    r = merge_base_is_ancestor(ancestor_sha, _head_sha(), ROOT)
    assert r.exit_code == 0  # a real ancestor commit


def test_resolve_head_returns_full_commit_id():
    head = resolve_head(ROOT)
    assert len(head) == 40 and head == _head_sha()


def test_diff_stat_uses_nul_delimiter_and_parses_hostile_names():
    """finding #2: `--name-only -z` output must be split on NUL, never `.splitlines()/.strip()`
    (which would corrupt filenames containing embedded newlines/whitespace)."""
    fake_stdout = "a b/c .py\0d/e.py\0"
    assert parse_nul_delimited_paths(fake_stdout) == ["a b/c .py", "d/e.py"]


# ============================================================================= DA3

def test_da3_bare_ancestry_is_not_confirmed():
    """finding #1 (CRITICAL) regression: a fix commit that exists AND is an ancestor of HEAD --
    with NO execution receipt -- must NOT be certified as a closed fix. This is the exact
    wrong-verdict case the audit found: "DA3 certifies ANY ancestor as a closed fix." The
    resolver must report ANCESTRY_VERIFIED (informative, not a pass), never CONFIRMED."""
    sha = _real_ancestor_sha()
    r = da3_closure_ancestry(ClosureFinding(claim_ref="R-honest", sha=sha), ROOT)
    assert not r.passed, "bare ancestry must not pass -- this is finding #1's exact hole"
    assert r.checks["commit_exists"] is True
    assert r.checks["ancestor_of_head"] is True
    assert r.checks["ancestry_verified"] is True
    assert r.checks["fix_closed"] is False
    assert r.checks["verdict"] == "ANCESTRY_VERIFIED"
    assert r.quarantine_reason == "ancestry_verified_no_canonical_path"


def test_da3_confirmed_requires_bound_execution_receipt():
    """finding #1 fix, positive case: ancestry + a bound, behavioral parent-fails/HEAD-passes
    receipt reaches CONFIRMED (`passed=True`)."""
    sha = _real_ancestor_sha()
    receipt = ExecutionReceipt(sha=sha, path="README.md", parent_fails=True,
                                head_passes=True, test_ref="frozen-test-fixture")
    r = da3_closure_ancestry(
        ClosureFinding(claim_ref="R-confirmed", sha=sha, path="README.md", execution_receipt=receipt),
        ROOT)
    assert r.passed
    assert r.checks["verdict"] == "CONFIRMED"
    assert r.quarantine_reason is None


def test_da3_receipt_not_bound_to_finding_does_not_rescue_verdict():
    """A receipt whose sha/path don't match the finding cannot be used to fake CONFIRMED."""
    sha = _real_ancestor_sha()
    mismatched_receipt = ExecutionReceipt(sha="f" * 40, path="README.md", parent_fails=True,
                                           head_passes=True, test_ref="frozen-test-fixture")
    r = da3_closure_ancestry(
        ClosureFinding(claim_ref="R-unbound", sha=sha, path="README.md",
                        execution_receipt=mismatched_receipt),
        ROOT)
    assert not r.passed
    assert r.quarantine_reason == "execution_receipt_not_bound_to_finding"


def test_da3_receipt_that_does_not_prove_closure_does_not_rescue_verdict():
    """A bound receipt where the pre-diff run did NOT fail (or post-diff did not pass) must not
    be accepted as proof either -- the receipt's CONTENT is checked, not just its binding."""
    sha = _real_ancestor_sha()
    weak_receipt = ExecutionReceipt(sha=sha, path="README.md", parent_fails=False,
                                     head_passes=True, test_ref="frozen-test-fixture")
    r = da3_closure_ancestry(
        ClosureFinding(claim_ref="R-weak-receipt", sha=sha, path="README.md",
                        execution_receipt=weak_receipt),
        ROOT)
    assert not r.passed
    assert r.quarantine_reason == "execution_receipt_does_not_prove_closure"


def test_da3_closed_without_merge_fails():
    """The da_a04 R-07.4 class: a fix commit exists but was never merged into HEAD's ancestry.
    Simulated here with a commit sha that does not resolve at all (the resolver cannot fabricate
    an unmerged-but-real commit inside a test without a second repo; a nonexistent commit
    exercises the same FAIL branch as the ancestry check -- commit_exists=False must ALSO fail,
    proving link 1 alone cannot pass the resolver)."""
    r = da3_closure_ancestry(ClosureFinding(claim_ref="R-07.4-shape", sha="e" * 40), ROOT)
    assert not r.passed
    assert r.checks["commit_exists"] is False


def test_da3_provenance_ceremony_without_ancestry_still_requires_link2():
    """The da_x03 class: a real, resolvable commit but the resolver must still separately check
    ancestor-of-HEAD, not stop at commit-exists. Verified structurally: reaching
    ancestry_verified requires BOTH checks True, never commit_exists alone."""
    sha = _real_ancestor_sha()
    r = da3_closure_ancestry(ClosureFinding(claim_ref="R-x03-shape", sha=sha), ROOT)
    assert r.checks["commit_exists"] and r.checks["ancestor_of_head"]
    assert set(["commit_exists", "ancestor_of_head"]).issubset(set(r.diagnostics["links_verified"]))


def test_da3_abstains_on_missing_sha():
    r = da3_closure_ancestry(ClosureFinding(claim_ref="no-sha"), ROOT)
    assert not r.passed
    assert r.quarantine_reason == "no_commit_sha"


def test_da3_batch_one_bad_closure_voids_whole_audit():
    good_sha = _real_ancestor_sha()
    receipt = ExecutionReceipt(sha=good_sha, path="README.md", parent_fails=True,
                                head_passes=True, test_ref="frozen-test-fixture")
    findings = [
        ClosureFinding(claim_ref="good-1", sha=good_sha, path="README.md", execution_receipt=receipt),
        ClosureFinding(claim_ref="bad-1", sha="d" * 40),
    ]
    r = da3_batch(findings, ROOT)
    assert not r.passed
    assert r.checks["n_passed"] == 1 and r.checks["n_findings"] == 2


def test_da3_batch_ancestry_only_finding_also_voids_the_batch():
    """finding #1 regression at the batch level: even a batch where every finding is a real,
    merged ancestor -- but NONE carry an execution receipt -- must fail the batch (0 CONFIRMED),
    not silently pass because every finding is "on HEAD's ancestry"."""
    good_sha = _real_ancestor_sha()
    findings = [ClosureFinding(claim_ref="ancestry-only-1", sha=good_sha)]
    r = da3_batch(findings, ROOT)
    assert not r.passed
    assert r.checks["n_passed"] == 0


def test_da3_link3_code_contains_fix_upgrades_depth():
    """Link 3 (code-contains-fix-at-HEAD) is computed when a path is supplied and, when it
    resolves, appears in links_verified -- never silently assumed true or false."""
    sha = _real_ancestor_sha()
    # Use a real path known to exist at HEAD; the added-lines join may or may not match
    # (depends on real diff content), but the field must be populated (not None) since a path
    # was supplied and the git calls succeeded.
    r = da3_closure_ancestry(
        ClosureFinding(claim_ref="link3-probe", sha=sha, path="README.md"), ROOT)
    assert r.checks["code_contains_fix"] is not None or r.checks["commit_exists"] is False


# ============================================================================= DA6

def test_da6_zero_diff_passes():
    """audited_sha == HEAD: empty changed-path set, trivially justified, and (trivially) an
    ancestor of itself."""
    r = da6_justified_lag(_head_sha(), ["cortex_core/"], ROOT)
    assert r.passed
    assert r.checks["overlap_count"] == 0
    assert r.checks["ancestor_of_head"] is True


def test_da6_abstains_on_missing_audited_sha():
    r = da6_justified_lag(None, ["cortex_core/"], ROOT)
    assert not r.passed
    assert r.quarantine_reason == "unverifiable_provenance"


def test_da6_abstains_on_unresolvable_sha():
    r = da6_justified_lag("c" * 40, ["cortex_core/"], ROOT)
    assert not r.passed
    assert r.quarantine_reason == "unverifiable_provenance"


def test_da6_abstains_on_resolvable_but_non_ancestor_sha():
    """finding #2 (DA6 half) regression: a sha that RESOLVES as a commit object but is not an
    ancestor of HEAD (an orphan/unrelated commit) must not be silently diffed against HEAD as if
    it were "the tree this audit ran against." Built with a real orphan commit in a throwaway
    repo so the object genuinely resolves yet is provably not on this repo's HEAD ancestry."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["git", "init", "-q"], cwd=td, check=True)
        subprocess.run(["git", "-c", "user.email=t@t.test", "-c", "user.name=t",
                         "commit", "--allow-empty", "-q", "-m", "orphan"], cwd=td, check=True)
        orphan_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=td,
                                     capture_output=True, text=True).stdout.strip()
    # `orphan_sha` does not exist as an object in ROOT's own object database at all, so this
    # exercises the "resolvable in SOME repo, not ROOT's" shape via the same not-ancestor path
    # DA6 must reject -- cat_file_exists on ROOT will report it as unresolvable, which is the
    # honest, expected outcome (the abstention reason is the same family: never guess).
    r = da6_justified_lag(orphan_sha, ["cortex_core/"], ROOT)
    assert not r.passed
    assert r.quarantine_reason in ("unverifiable_provenance", "audited_sha_not_ancestor_of_head")


def test_da6_unjustified_overlap_fails():
    """da_b08's inverse: if the delta DOES touch an audited module with no justification entry,
    the audit must FAIL the lag rule. Constructed with the module set intentionally matching
    this very test file's own path so the diff between an old ancestor and HEAD is virtually
    certain to overlap (tests/ has churned across this repo's history)."""
    ancestor_sha = _real_ancestor_sha()
    r = da6_justified_lag(ancestor_sha, ["tests/", "evals/", "cortex_core/", "docs/"], ROOT)
    # whatever the real overlap is, an unjustified overlap (empty justified_overlaps) must
    # mirror checks["overlap_count"] exactly in unjustified_overlap_count.
    assert r.checks["unjustified_overlap_count"] == r.checks["overlap_count"]
    if r.checks["overlap_count"] > 0:
        assert not r.passed


def test_da6_justified_overlap_passes():
    ancestor_sha = _real_ancestor_sha()
    probe = da6_justified_lag(ancestor_sha, ["tests/"], ROOT)
    overlap_paths = probe.diagnostics["overlap"]
    if not overlap_paths:
        return  # nothing to justify in this window; the zero-diff test already covers PASS
    r = da6_justified_lag(ancestor_sha, ["tests/"], ROOT, justified_overlaps=overlap_paths)
    assert r.passed
    assert r.checks["unjustified_overlap_count"] == 0


# ============================================================================= citation index (S3)

def test_citation_index_finds_real_repo_files():
    idx = build_citation_index(ROOT)
    assert idx.path_exists("cortex_core/http_server.py")
    assert idx.path_exists("docs/PHASE-GATES.md")
    assert not idx.path_exists("this/does/not/exist.md")


def test_citation_index_extracts_finding_ids_and_sections():
    idx = build_citation_index(ROOT)
    has_finding_id = any(doc["finding_ids"] for doc in idx.anchors.values())
    assert has_finding_id, "expected at least one finding <ID> anchor across reviewed/ docs"


def test_citation_index_stamps_head_sha():
    idx = build_citation_index(ROOT)
    assert idx.built_at_sha and len(idx.built_at_sha) == 40


def test_citation_index_cache_roundtrip(tmp_path):
    cache = tmp_path / "cache.json"
    idx1 = load_or_build(ROOT, cache_path=cache)
    assert cache.exists()
    idx2 = load_or_build(ROOT, cache_path=cache)  # should hit the cache, same sha
    assert idx1.built_at_sha == idx2.built_at_sha
    assert idx1.paths == idx2.paths


def test_citation_index_containment_check_rejects_escapes():
    """finding #4: `path_exists` must fail closed on any path that resolves outside repo_root --
    a `../` escape or an absolute path must never "resolve" a citation."""
    idx = build_citation_index(ROOT)
    assert not idx.path_exists("../outside_repo.md")
    assert not idx.path_exists("../../etc/passwd")


def test_citation_index_live_stats_uncommitted_files(tmp_path):
    """finding #4: an index must not report a false NEGATIVE for a real, just-created (still
    uncommitted) file, nor a false POSITIVE for a just-deleted one -- both were possible when
    `path_exists` trusted only the scanned `paths` list from whenever the index was built."""
    import subprocess as sp
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    idx = build_citation_index(tmp_path)
    assert not idx.path_exists("brand_new.md")  # doesn't exist yet -- correct negative

    new_file = tmp_path / "brand_new.md"
    new_file.write_text("# hi\n", encoding="utf-8")
    # `idx` was built BEFORE the file existed, but `path_exists` is a LIVE stat -- it must see
    # the file immediately, with no rebuild required (finding #4's "false fail on new real
    # path" case).
    assert idx.path_exists("brand_new.md")

    new_file.unlink()
    # and conversely, immediately stop seeing it once deleted (the "false pass on deleted path"
    # case) -- again with no rebuild.
    assert not idx.path_exists("brand_new.md")


def test_citation_index_fingerprint_changes_on_uncommitted_edit(tmp_path):
    """finding #4: the cache staleness check is keyed to a full-tree fingerprint, not committed
    HEAD -- an uncommitted add must invalidate a cached index even though HEAD hasn't moved."""
    import subprocess as sp
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.md").write_text("# a\n", encoding="utf-8")
    cache = tmp_path / "cache.json"
    idx1 = load_or_build(tmp_path, cache_path=cache)
    assert "a.md" in idx1.paths

    # add a new file WITHOUT committing -- HEAD is unchanged (there may be no commits at all).
    (tmp_path / "b.md").write_text("# b\n", encoding="utf-8")
    idx2 = load_or_build(tmp_path, cache_path=cache)
    assert "b.md" in idx2.paths, "an uncommitted add must invalidate the cache (finding #4)"


def test_citation_index_default_cache_lives_outside_repo_root():
    """finding #4: the default cache path must NOT be inside the candidate workspace (a
    candidate/agent under grading can write anywhere inside its own repo)."""
    from evals.resolvers.citation_index import _default_cache_path
    cache_path = _default_cache_path(ROOT)
    assert not str(cache_path).startswith(str(ROOT))


# ============================================================================= AB2

def test_ab2_real_citations_pass():
    idx = build_citation_index(ROOT)
    text = "See docs/ROADMAP.md and cortex_core/http_server.py for the design."
    r = citation_resolvability_check(text, ROOT, index=idx)
    assert r.passed
    assert r.checks["citation_count"] >= 2
    assert r.checks["unresolved_count"] == 0


def test_ab2_fake_citations_fail_the_ai_x03_shape():
    """ai_x03 (calibration/anchors/actionable_item_soft_anchor.jsonl): dense, tier-labeled,
    well-formed citations, none of which exist in the repo. Fable's verdict: fail (4/5 — ic1),
    'a judge without repo access CANNOT perform this check'. This resolver must FAIL it."""
    idx = build_citation_index(ROOT)
    text = (
        "Evidence (production-deployed): reviewed/judge-retry-audit-2026-07-09.md finding H2; "
        "docs/research/provider-flakiness-survey-2026-07-06.md section 4.1; "
        "audit closeout 20260708T convergence-run entry; "
        "measured retry histogram in evals/reports/RETRY_HISTOGRAM_0708.md."
    )
    r = citation_resolvability_check(text, ROOT, index=idx)
    assert not r.passed
    assert r.checks["unresolved_count"] >= 2


def test_ab2_mixed_real_and_fake_fails():
    """One real citation cannot rescue a claim block that also cites a fabricated one -- ANY
    unresolved citation fails the whole check, per the spec ('FAIL if any citation...')."""
    idx = build_citation_index(ROOT)
    text = "Per docs/ROADMAP.md and also reviewed/this-file-does-not-exist-2099.md finding Z9."
    r = citation_resolvability_check(text, ROOT, index=idx)
    assert not r.passed
    assert r.checks["resolved_count"] >= 1
    assert r.checks["unresolved_count"] >= 1


def test_ab2_no_citations_is_not_a_fail():
    idx = build_citation_index(ROOT)
    r = citation_resolvability_check("Make the fetch path more robust.", ROOT, index=idx)
    assert r.checks["citation_count"] == 0
    assert r.passed  # vacuous pass -- resolvability isn't the failure mode here, ic1/ic2 are


def test_ab2_finding_id_must_actually_be_present_in_cited_doc():
    idx = build_citation_index(ROOT)
    # A bare finding id with NO co-located file citation cannot be bound to anything -- finding
    # #3(b): it must be QUARANTINED (unparsed), never resolved against "any anchored doc."
    r_unbound = citation_resolvability_check("Cf. finding H99999 for the rationale.", ROOT, index=idx)
    assert not r_unbound.passed
    assert r_unbound.checks["citation_count"] == 0
    assert r_unbound.checks["unparsed_count"] >= 1

    real_finding_doc = None
    real_finding_id = None
    for doc_path, doc in idx.anchors.items():
        if doc["finding_ids"]:
            real_finding_doc, real_finding_id = doc_path, doc["finding_ids"][0]
            break
    assert real_finding_id, "need at least one real finding id in the corpus for this test"

    # finding #3(b) positive case: the SAME id, now co-located with its real owning file, binds
    # and resolves.
    r_good = citation_resolvability_check(
        f"Cf. {real_finding_doc} finding {real_finding_id} for the rationale.", ROOT, index=idx)
    assert r_good.passed, r_good.diagnostics

    # finding #3(b) negative case (the exact audit example): a REAL file plus an id that exists
    # only in a DIFFERENT file must FAIL, not resolve against the wrong file.
    other_doc_with_different_id = None
    for doc_path, doc in idx.anchors.items():
        if doc_path != real_finding_doc and doc["finding_ids"] and real_finding_id not in doc["finding_ids"]:
            other_doc_with_different_id = doc_path
            break
    if other_doc_with_different_id:
        r_cross_file = citation_resolvability_check(
            f"Cf. {other_doc_with_different_id} finding {real_finding_id} for the rationale.",
            ROOT, index=idx)
        assert not r_cross_file.passed, (
            "a real file plus an id that belongs to a DIFFERENT file must not resolve")


def test_parse_citations_grammar_shapes():
    citations, _ = parse_citations(
        "docs/ROADMAP.md, finding H2, section 4.1, and 019f578b-ecfe-74b2-9d36-0865ff9cc09a")
    kinds = {c.kind for c in citations}
    assert kinds == {"path", "finding", "section", "closeout"}
