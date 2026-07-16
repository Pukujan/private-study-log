from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", help="run live 9Router smoke tests")


# --------------------------------------------------------------------------- #
# Fast/unit vs. integration split (CI runs the two lanes as separate steps).   #
# A module is auto-marked `integration` if its source spawns real processes,   #
# launches fixture apps over HTTP, or drives a browser -- i.e. the end-to-end  #
# tests that dominate wall-clock. Detection is by source signal (not a curated #
# filename list) so a NEW process-spawning test is classified automatically.   #
# `pytest -m "not integration"` is the fast lane; `-m integration` the slow    #
# lane; their union is the whole suite (no coverage is dropped).               #
# --------------------------------------------------------------------------- #
_INTEGRATION_SIGNALS = (
    "import subprocess",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check",
    "playwright",
    "AppProcess",
    "run_done_checks",
    "http.client",
)
_module_flags: dict[str, bool] = {}


def _module_is_integration(path: str) -> bool:
    try:
        src = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(sig in src for sig in _INTEGRATION_SIGNALS)


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath)
        flag = _module_flags.get(path)
        if flag is None:
            flag = _module_is_integration(path)
            _module_flags[path] = flag
        if flag:
            item.add_marker(pytest.mark.integration)
