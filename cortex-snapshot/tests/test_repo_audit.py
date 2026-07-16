"""cortex-repo-audit Phase 0 (thinnest useful slice): finding schema + the code-health F821
JSON-literal lane (C1) + baseline read/write + deterministic fingerprints + exit codes.

Success conditions are lifted verbatim from the Fable research report §(f) Phase 0
(docs/research/repo-audit-design-research-2026-07-06.md). Hermetic: tmp_path only (respects the
report's own hazard (e) -- tests must never touch the real repo/log)."""
from __future__ import annotations

from pathlib import Path

from cortex_core import repo_audit as ra


def _write(tmp_path: Path, n: int, name: str = "mod.py") -> Path:
    """A module with `n` JSON-literal sites (`false` used as a bare Python name -> F821)."""
    lines = ["cfg = {"] + [f'    "k{i}": false,' for i in range(n)] + ["}"]
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _json_literal(findings):
    return [f for f in findings if f.rule_id == "F821-json-literal"]


def test_detects_exactly_three_json_literal_sites(tmp_path):
    _write(tmp_path, 3)
    jl = _json_literal(ra.scan_paths([tmp_path]))
    assert len(jl) == 3
    assert all(f.severity == "high" for f in jl)
    assert all(f.fingerprint for f in jl)


def test_fingerprints_stable_and_byte_identical_across_runs(tmp_path):
    _write(tmp_path, 3)
    a = sorted(f.fingerprint for f in _json_literal(ra.scan_paths([tmp_path])))
    b = sorted(f.fingerprint for f in _json_literal(ra.scan_paths([tmp_path])))
    assert a == b and len(a) == 3 and all(a)


def test_baseline_write_then_rerun_suppresses_all_and_exits_zero(tmp_path):
    _write(tmp_path, 3)
    bl = tmp_path / ".repo_audit_baseline.json"
    findings, code = ra.run_audit([tmp_path], baseline_path=bl, write_baseline=True)
    assert bl.exists() and code == 0
    findings2, code2 = ra.run_audit([tmp_path], baseline_path=bl)
    assert code2 == 0  # all known -> within baseline
    assert all(f.suppressed for f in _json_literal(findings2))


def test_new_site_fails_gate_while_baselined_stay_suppressed(tmp_path):
    _write(tmp_path, 3)
    bl = tmp_path / ".repo_audit_baseline.json"
    ra.run_audit([tmp_path], baseline_path=bl, write_baseline=True)
    _write(tmp_path, 4)  # same file, now a 4th site -> one NEW fingerprint
    findings, code = ra.run_audit([tmp_path], baseline_path=bl)
    assert code == 1  # new blocking debt
    new = [f for f in _json_literal(findings) if not f.suppressed]
    assert len(new) == 1  # exactly the 4th site; the 3 baselined stay suppressed


def test_clean_tree_exits_zero(tmp_path):
    (tmp_path / "ok.py").write_text("cfg = {'k': True}\n", encoding="utf-8")
    findings, code = ra.run_audit([tmp_path])
    assert code == 0 and _json_literal(findings) == []


def test_syntax_error_file_does_not_crash_the_scan(tmp_path):
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")  # unparseable
    _write(tmp_path, 2)
    findings, code = ra.run_audit([tmp_path])  # must not raise
    assert len(_json_literal(findings)) == 2


# --------------------------------------------------------------------- Phase 1

def test_fingerprint_stable_when_enclosing_function_moves(tmp_path):
    src = "def f():\n    return {'k': false}\n"
    p = tmp_path / "m.py"
    p.write_text(src, encoding="utf-8")
    fp1 = _json_literal(ra.scan_paths([tmp_path]))[0].fingerprint
    p.write_text("\n\n\nimport os\n" + src, encoding="utf-8")  # push f() down the file
    fp2 = _json_literal(ra.scan_paths([tmp_path]))[0].fingerprint
    assert fp1 == fp2  # symbol-anchored: the line moved, the identity didn't


def test_identical_evidence_lines_get_distinct_fingerprints(tmp_path):
    # the Phase-0 dedup bug: two byte-identical `false,` lines collapsed to 1 (8 files vs 14 sites)
    (tmp_path / "m.py").write_text("cfg = [\n    false,\n    false,\n]\n", encoding="utf-8")
    jl = _json_literal(ra.scan_paths([tmp_path]))
    assert len(jl) == 2 and len({f.fingerprint for f in jl}) == 2


def test_per_path_policy_downgrades_evals_to_non_blocking(tmp_path):
    (tmp_path / "cortex_core").mkdir()
    (tmp_path / "cortex_core" / "core.py").write_text("x = false\n", encoding="utf-8")
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "edge.py").write_text("y = false\n", encoding="utf-8")
    findings, code = ra.run_audit([tmp_path])
    modes = {f.path: f.policy_mode for f in _json_literal(findings)}
    assert modes["cortex_core/core.py"] == "strict" and modes["evals/edge.py"] == "warn"
    assert code == 1  # the strict cortex_core finding blocks; the evals one alone would not


def test_evals_only_finding_does_not_block(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "edge.py").write_text("y = false\n", encoding="utf-8")
    findings, code = ra.run_audit([tmp_path])
    assert len(_json_literal(findings)) == 1 and code == 0  # warn -> non-blocking


def test_ratchet_tightens_on_success_then_fails_on_regrowth(tmp_path):
    bl = tmp_path / ".repo_audit_baseline.json"
    _write(tmp_path, 3)
    ra.run_audit([tmp_path], baseline_path=bl, write_baseline=True)  # ceiling code-health/high = 3
    _write(tmp_path, 2)  # reduce to 2 (subset fingerprints, all baselined)
    _, c2 = ra.run_audit([tmp_path], baseline_path=bl, ratchet=True)
    assert c2 == 0  # within ceiling, no new debt -> tightens ceiling to 2
    _write(tmp_path, 3)  # regrow to 3 (k2 fingerprint still baselined, so not "new" -- pure count regression)
    _, c3 = ra.run_audit([tmp_path], baseline_path=bl, ratchet=True)
    assert c3 == 1  # 3 > tightened ceiling 2 -> ratchet regression
