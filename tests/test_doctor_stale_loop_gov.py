"""The stale-loop-gov-daemon check must fire BOTH directions.

The doctor check `check_stale_loop_gov_daemon` warns when the running
loop-governance MCP daemon serves a module that changed after the daemon
started (the daemon loads loop-gov-mcp.py once at startup and keeps running it
until a real restart — so a deploy replaces the file on disk while the daemon
serves the old logic). This regenerated-stale-module state produced the
recurring "feedback_accept says MOVE_ON but end_change reads PENDING" friction
(cycle-query-then-retry as the workaround).

These tests drive the check's logic in isolation by monkeypatching the `ps`
output to a controlled listing pointing at a REAL temp module with an explicit
daemon-start epoch — so they assert the check's behaviour, never the live host.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))

import fleet_doctor.checks as chk  # noqa: E402
from fleet_doctor.results import Results  # noqa: E402


@pytest.fixture()
def temp_module(tmp_path):
    mod = tmp_path / "loop-gov-mcp.py"
    mod.write_text("x = 1\n")
    return mod





def _run_check():
    r = Results()
    chk.check_stale_loop_gov_daemon(r)
    return [c for c in r.checks if c["name"] == "loop-gov daemon"]


def _patch_ps_multi(pids_mod_map, daemon_start_epoch: float):
    """A ps listing that names MULTIPLE loop-gov daemons starting at one epoch.

    The sibling-daemon case a single-pid stamp cannot cover: two session-spawned
    MCP daemons (--role review and --role loop-governance) start ~1s apart, and a
    single-object stamp only ever records one of them.
    """
    real_run = subprocess.run
    rendered = "".join(f"{pid} {sys.executable} {mod}\n" for pid, mod in pids_mod_map)

    def fake_run(cmd, **kw):
        if cmd == ["ps", "-eo", "pid,args"]:
            return type("R", (), {"stdout": rendered, "returncode": 0})()
        if (cmd[0:2] == ["ps", "-o"] and "lstart=" in cmd[2:3] and "-p" in cmd):
            start = datetime.fromtimestamp(daemon_start_epoch)
            return type("R", (), {
                "stdout": start.strftime("%a %b %d %H:%M:%S %Y"),
                "returncode": 0})()
        return real_run(cmd, **kw)

    subprocess.run = fake_run
    return lambda: setattr(subprocess, "run", real_run)


def _patch_ps_path(pid: int, argv_mod: Path, daemon_start_epoch: float,
                   exists: bool):
    """Point the check's ps reads at a listing whose argv names `argv_mod`.

    `exists` controls whether the module file exists on disk, so the SKIP case
    (argv names a module that is not a real file) is reachable.
    """
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd == ["ps", "-eo", "pid,args"]:
            line = f"{pid} {sys.executable} {argv_mod}\n"
            return type("R", (), {"stdout": line, "returncode": 0})()
        if (cmd[0:2] == ["ps", "-o"] and "lstart=" in cmd[2:3] and "-p" in cmd):
            start = datetime.fromtimestamp(daemon_start_epoch)
            return type("R", (), {
                "stdout": start.strftime("%a %b %d %H:%M:%S %Y"),
                "returncode": 0})()
        return real_run(cmd, **kw)

    subprocess.run = fake_run
    return lambda: setattr(subprocess, "run", real_run)


def test_reports_pass_when_module_is_older_than_daemon(temp_module):
    """Current daemon: module written before the daemon started ⟶ PASS."""
    now = time.time()
    os.utime(temp_module, (now - 300, now - 300))   # module 300s old
    restore = _patch_ps_path(1111, temp_module, now - 60, exists=True)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no loop-gov-daemon row"
    assert hit[0]["status"] == "PASS"


def test_reports_warn_when_module_is_newer_than_daemon(temp_module):
    """Stale daemon (no startup stamp): module newer than the daemon start must
    STILL warn — COULD-NOT-VERIFY-by-content fires the warning per the
    content-first contract (2026-10-18 refactor: the daemon's sha256 stamp is
    the authority; without one, the mtime-only signal is reported as
    unverified, never as PASS). Fake pid 39999 can never collide with a real
    loop-gov daemon, so the real stamp file never enters this case.
    """
    now = time.time()
    os.utime(temp_module, (now, now))                 # module written NOW
    restore = _patch_ps_path(39999, temp_module, now - 60, exists=True)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no loop-gov-daemon row"
    assert hit[0]["status"] == "WARN", hit[0]
    assert "wrote no startup stamp" in hit[0]["detail"], hit[0]["detail"]
    # The printed fix must name a real, runnable command (change-checklist
    # Phase 4: a check's fix text is pasted verbatim).
    assert "gateway restart" in hit[0]["fix"], hit[0]["fix"]


def test_reports_skip_when_daemon_argv_names_a_missing_module(tmp_path):
    """argv names a module that is not a real file ⟶ SKIP, never a false WARN.

    The check must not guess a module path; if the running daemon's argv names a
    file that does not exist, there is nothing to compare and the check says so.
    """
    missing = tmp_path / "loop-gov-mcp.py"   # never written to disk
    restore = _patch_ps_path(3333, missing, time.time() - 60, exists=False)
    try:
        r = Results()
        chk.check_stale_loop_gov_daemon(r)
        hit = [c for c in r.checks if c["name"] == "loop-gov daemon"]
    finally:
        restore()
    assert hit, "expected at least a SKIP row"
    assert hit[0]["status"] == "SKIP"


def test_per_pid_stamp_map_verifies_every_stamped_sibling(tmp_path, monkeypatch):
    """A per-pid stamp MAP must verify EVERY stamped daemon by content -> PASS,
    never leave a sibling reading COULD-NOT-VERIFY.

    Current code reads a single-object stamp (`stamp.get("pid")`), so a map fed
    to it is ignored and both fake pids read "wrote no startup stamp" -> WARN
    (the RED). The fix makes the check read the map (via `_read_daemon_stamp`),
    so two sibling entries with a matching module sha both `continue` to PASS.
    """
    now = time.time()
    mod = tmp_path / "loop-gov-mcp.py"
    mod.write_text("x = 1\n")
    os.utime(mod, (now, now))            # module written NOW (newer than daemon start)
    sha = hashlib.sha256(mod.read_bytes()).hexdigest()
    # Both siblings carry a stamp entry whose sha matches the module on disk.
    monkeypatch.setattr(chk, "_read_daemon_stamp", lambda: {
        "1111": {"started": now - 60, "module": str(mod), "sha256": sha},
        "2222": {"started": now - 60, "module": str(mod), "sha256": sha},
    }, raising=False)
    restore = _patch_ps_multi([(1111, mod), (2222, mod)], now - 60)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no loop-gov-daemon row"
    assert hit[0]["status"] == "PASS", hit