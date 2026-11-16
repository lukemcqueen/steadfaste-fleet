"""Regression tests for the cron-quality-gate watchdog PROBE harness.

Subject: skills/devops/cron-quality-gate/scripts/drive-watchdog-main.py — the harness
the cron-quality-gate skill prescribes for measuring a watchdog's DETECTION with its
dedup tracker stubbed to always alert.

Why it needs pinning: BOTH of its documented traps fail silently. A harness that never
inserts ops/scripts on sys.path makes every watchdog import die with ModuleNotFoundError,
so a correct fix reads as a wall of failures; and an unstubbed tracker suppresses
delivery, so a watchdog that still detects the defect prints nothing and looks fixed.
Neither trap has a visible symptom in the probe's own output.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "skills" / "devops" / "cron-quality-gate" / "scripts" / "drive-watchdog-main.py"


@pytest.fixture(autouse=True)
def _restore_interpreter_state():
    """load() mutates sys.path and installs a fake `state_tracker` globally.

    Snapshot both, so a probe run in one test cannot leak into the next (a leaked stub
    would make a later watchdog's tracker import silently resolve to the fake).
    """
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    yield
    sys.path[:] = before_path
    for name in set(sys.modules) - before_modules:
        sys.modules.pop(name, None)


def _load_harness():
    spec = importlib.util.spec_from_file_location("drive_watchdog_main_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_watchdog(tmp_path, body):
    path = tmp_path / "fake_watchdog.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_harness_exists_and_exposes_its_contract():
    """The skill prescribes this path; a moved probe must fail loudly here."""
    assert HARNESS.is_file(), f"probe missing at the path the skill prescribes: {HARNESS}"
    harness = _load_harness()
    assert callable(harness.main)
    assert callable(harness.load)


def test_stub_tracker_never_suppresses():
    """Anti-vacuity: the always-alert stub IS the mechanism under test.

    If this stub returned anything else, every 'detection' measurement the skill
    prescribes would silently start reporting the dedup layer's answer instead.
    """
    harness = _load_harness()
    assert harness._AlwaysAlert().evaluate(object(), []) == "alert"
    assert harness._AlwaysAlert("any", kwargs="accepted").evaluate(None) == "alert"


def test_no_arguments_prints_usage_and_returns_2(capsys, monkeypatch):
    harness = _load_harness()
    monkeypatch.setattr(sys, "argv", ["drive-watchdog-main.py"])
    assert harness.main() == 2
    assert "Usage:" in capsys.readouterr().out


def test_returns_the_watchdogs_own_exit_code(tmp_path, capsys, monkeypatch):
    """A watchdog that exits non-zero must not read as a clean probe run."""
    harness = _load_harness()
    ok = _write_watchdog(tmp_path, "import sys\n\n\ndef main():\n    print('(no issues detected)')\n    sys.exit(0)\n")
    monkeypatch.setattr(sys, "argv", ["drive-watchdog-main.py", str(ok)])
    assert harness.main() == 0
    assert "(no issues detected)" in capsys.readouterr().out

    bad = tmp_path / "bad_watchdog.py"
    bad.write_text("import sys\n\n\ndef main():\n    sys.exit(3)\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["drive-watchdog-main.py", str(bad)])
    assert harness.main() == 3


def test_ops_scripts_is_put_on_the_path_so_shared_helpers_import(tmp_path, capsys, monkeypatch):
    """The documented trap, asserted directly.

    Watchdogs do `from hermes_tz import format_timestamp`; that resolves from ops/scripts
    on the cron's path but not from a bare interpreter. Without the harness's own path
    fix this raises ModuleNotFoundError and every downstream claim reads EMPTY.
    """
    assert (REPO / "ops" / "scripts" / "hermes_tz.py").is_file()
    harness = _load_harness()
    watchdog = _write_watchdog(
        tmp_path,
        "from hermes_tz import format_timestamp\n\n\ndef main():\n    print('IMPORT OK')\n",
    )
    monkeypatch.setattr(sys, "argv", ["drive-watchdog-main.py", str(watchdog), str(REPO)])
    assert harness.main() == 0
    assert "IMPORT OK" in capsys.readouterr().out


def test_loaded_watchdog_sees_the_stubbed_tracker_module(tmp_path, capsys, monkeypatch):
    """The stub must be installed BEFORE the watchdog module executes."""
    harness = _load_harness()
    watchdog = _write_watchdog(
        tmp_path,
        "from state_tracker import StateTracker\n\n\ndef main():\n    print(type(StateTracker()).evaluate(None))\n",
    )
    monkeypatch.setattr(sys, "argv", ["drive-watchdog-main.py", str(watchdog), str(REPO)])
    harness.main()
    assert "alert" in capsys.readouterr().out
