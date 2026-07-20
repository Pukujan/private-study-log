from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "library" / "cortex-library" / "search" / "search.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CORTEX_WORKSPACE"] = str(REPO_ROOT)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_python_dash_m_executes_main(tmp_path: Path) -> None:
    env_path = str(REPO_ROOT)
    env = dict(os.environ)
    env["CORTEX_WORKSPACE"] = env_path
    env["PYTHONPATH"] = env_path
    proc = subprocess.run(
        [sys.executable, "-m", "cortex_core", "--status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert '"workspace"' in proc.stdout


def test_wrapper_runs_without_install(tmp_path: Path) -> None:
    proc = _run([sys.executable, str(WRAPPER), "--status"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert '"workspace"' in proc.stdout
