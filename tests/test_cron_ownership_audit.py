"""Tests for cron-ownership-audit.py — verify every ownership mode is reported correctly.

The audit reports four counts:

  - bridge-owned (enable marker)     — names from timers.target.wants/fleet-bridge-*.timer
  - live Hermes jobs (jobs.json)     — names from jobs.json
  - unit files on disk               — names from $UNIT_DIR/fleet-bridge-*.timer
  - the four classes (DOUBLE OWNERSHIP, MIGRATION GAP, DANGLING MARKER,
    Hermes-scheduler-owned)

A defect here means either a miscount (a job fires twice, or stops silently)
or a misclassification (Hermes-scheduler-owned reported as a defect). Each
case is pinned independently.

Hermetic: HERMES_HOME + HOME both pointed at tempdirs; nothing on the real
host is touched.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "devops" / "cron-job-management" / "scripts" / "cron-ownership-audit.py"


def _run_script(home: Path, hermes_home: Path) -> subprocess.CompletedProcess:
    """Run the script with HERMES_HOME + HOME pointed at tempdirs."""
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_jobs(hermes_home: Path, jobs: list[dict]) -> Path:
    jobs_file = hermes_home / "cron" / "jobs.json"
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_text(json.dumps({"jobs": jobs}))
    return jobs_file


def _write_units(home: Path, unit_names: list[str], enabled_names: list[str]) -> None:
    """Create fleet-bridge-<name>.timer files in $UNIT_DIR, and
    fleet-bridge-<name>.timer symlinks in $WANTS_DIR for `enabled_names`."""
    unit_dir = home / ".config" / "systemd" / "user"
    wants_dir = unit_dir / "timers.target.wants"
    wants_dir.mkdir(parents=True, exist_ok=True)
    for name in unit_names:
        # Create the .timer file (the unit on disk)
        (unit_dir / f"fleet-bridge-{name}.timer").write_text(
            "[Unit]\nDescription=test\n[Timer]\nOnCalendar=daily\n[Install]\n"
        )
    for name in enabled_names:
        # Symlink in timers.target.wants/ (the enable marker)
        target = unit_dir / f"fleet-bridge-{name}.timer"
        link = wants_dir / f"fleet-bridge-{name}.timer"
        # The symlink may dangle — that's the DANGLING MARKER case
        if target.exists():
            link.symlink_to(target)
        else:
            # dangling link: target doesn't exist
            link.symlink_to(unit_dir / f"fleet-bridge-{name}.timer")


def test_clean_host_no_overlap_returns_pass(tmp_path: Path):
    """Hermes-scheduler-owned LLM jobs + separate bridge-owned no_agent jobs
    is the normal case. The script must classify them separately and return
    VERDICT: PASS with exit code 0."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes"
    home.mkdir()
    hermes_home.mkdir()

    # 2 LLM jobs in jobs.json (no bridge overlap)
    _write_jobs(hermes_home, [
        {"name": "llm-job-a", "model": "x"},
        {"name": "llm-job-b", "model": "y"},
    ])
    # 2 bridge-owned no_agent jobs (separate from LLM)
    _write_units(home, unit_names=["bridge-job-c", "bridge-job-d"], enabled_names=["bridge-job-c", "bridge-job-d"])

    proc = _run_script(home, hermes_home)

    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}\nstderr={proc.stderr}"
    assert "bridge-owned (enable marker): 2" in proc.stdout
    assert "live Hermes jobs (jobs.json): 2" in proc.stdout
    assert "unit files on disk:          2" in proc.stdout
    assert "1. DOUBLE OWNERSHIP  : 0 (none)" in proc.stdout
    assert "2. MIGRATION GAP     : 0 (none)" in proc.stdout
    assert "3. DANGLING MARKER   : 0 (none)" in proc.stdout
    assert "Hermes-scheduler-owned, not a defect: 2" in proc.stdout
    assert "llm-job-a" in proc.stdout
    assert "bridge-job-c" not in proc.stdout  # bridge jobs are not in jobs.json
    assert "VERDICT: PASS" in proc.stdout


def test_double_ownership_detected_returns_fail(tmp_path: Path):
    """A job in BOTH jobs.json AND timers.target.wants/ is DOUBLE OWNERSHIP —
    it would fire twice. Script must report it as FAIL with exit 1."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes"
    home.mkdir()
    hermes_home.mkdir()

    _write_jobs(hermes_home, [
        {"name": "shared-job", "no_agent": True, "script": "/x.sh"},
    ])
    _write_units(home, unit_names=["shared-job"], enabled_names=["shared-job"])

    proc = _run_script(home, hermes_home)

    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}"
    assert "1. DOUBLE OWNERSHIP  : 1 ['shared-job']" in proc.stdout
    assert "VERDICT: FAIL" in proc.stdout


def test_migration_gap_detected(tmp_path: Path):
    """A no_agent + script job in jobs.json with NO corresponding bridge
    timer is a MIGRATION GAP — the bridge could own it but doesn't. Script
    must report it as a FAIL."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes"
    home.mkdir()
    hermes_home.mkdir()

    _write_jobs(hermes_home, [
        {"name": "bridge-eligible", "no_agent": True, "script": "/x.sh"},
    ])
    # No unit files, no enable markers

    proc = _run_script(home, hermes_home)

    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}"
    assert "2. MIGRATION GAP     : 1 ['bridge-eligible']" in proc.stdout
    assert "VERDICT: FAIL" in proc.stdout


def test_model_pinned_job_is_not_a_migration_gap(tmp_path: Path):
    """A no_agent + script job with a `model` pin is NOT bridge-eligible —
    it's an LLM-driven job, even if it has script+no_agent. Must not be
    reported as MIGRATION GAP."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes"
    home.mkdir()
    hermes_home.mkdir()

    _write_jobs(hermes_home, [
        # no_agent + script + model pin -> not eligible
        {"name": "model-pinned", "no_agent": True, "script": "/x.sh", "model": "sonnet"},
    ])

    proc = _run_script(home, hermes_home)

    assert proc.returncode == 0
    assert "2. MIGRATION GAP     : 0 (none)" in proc.stdout
    assert "model-pinned" in proc.stdout  # reported as Hermes-scheduler-owned
    assert "no_agent (review: why not bridged?)" in proc.stdout


def test_dangling_marker_detected(tmp_path: Path):
    """A symlink in timers.target.wants/ with no unit file on disk is a
    DANGLING MARKER — the bridge claims a job that can never fire. Must
    be reported as FAIL."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes"
    home.mkdir()
    hermes_home.mkdir()

    # Create only the symlink, NOT the unit file
    unit_dir = home / ".config" / "systemd" / "user"
    wants_dir = unit_dir / "timers.target.wants"
    wants_dir.mkdir(parents=True, exist_ok=True)
    target = unit_dir / "fleet-bridge-orphaned.timer"
    link = wants_dir / "fleet-bridge-orphaned.timer"
    link.symlink_to(target)  # target doesn't exist — dangling

    _write_jobs(hermes_home, [])

    proc = _run_script(home, hermes_home)

    assert proc.returncode == 1
    assert "bridge-owned (enable marker): 1" in proc.stdout
    assert "unit files on disk:          0" in proc.stdout
    assert "3. DANGLING MARKER   : 1 ['orphaned']" in proc.stdout
    assert "VERDICT: FAIL" in proc.stdout


def test_could_not_verify_on_missing_jobs_json(tmp_path: Path, capsys):
    """If jobs.json is unreadable, the script reports COULD NOT VERIFY and
    exits 1 — never a silent pass."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes"
    home.mkdir()
    hermes_home.mkdir()

    # No jobs.json file
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1
    assert "COULD NOT VERIFY" in proc.stdout


def test_import_inertness():
    """The module must not execute side effects at import — `import
    cron_ownership_audit` alone must not read jobs.json or invoke main()."""
    spec = importlib.util.spec_from_file_location("coa_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # The module defines main() and helpers — verify they exist and are callable
    assert callable(getattr(mod, "main", None))
    assert callable(getattr(mod, "_eligible", None))
    assert callable(getattr(mod, "_jobs", None))
    assert callable(getattr(mod, "_unit_files", None))
    assert callable(getattr(mod, "_names_from_wants", None))
