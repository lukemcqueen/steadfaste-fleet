"""The stale-enforcer-plugin check must fire BOTH directions.

`check_stale_enforcer_plugin` warns when the Hermes gateway is enforcing a
PREVIOUS revision of the governance enforcer. The enforcer is a gateway PLUGIN:
the gateway loads `__init__.py` at START and keeps that module for the life of
the process, so a deploy that replaces the file on disk leaves every session on
that host answered by the OLD enforcement code until a real restart.

Measured 2026-11-17 (moses): the enforcer content changed 2026-11-16 23:11 while
the gateway had started 2026-11-14 10:31. The agent was therefore refused by the
pre-change block message, read it as a permission wall, and reported to the
operator that `fleet-update.sh` "needs someone with administrative access" —
while the sanctioned invocation was in fact allowed and worked. Nothing on the
host said "deployed, not loaded".

The signal is CONTENT-derived, never mtime: `fleet-update.sh`'s
`_restart_pending()` records WHEN the activation-only content last changed as
`<combined sha256> <epoch>` in `state/governance-enforcer-deployed`. These tests
drive the check in isolation by monkeypatching that state file (via
FLEET_DEPLOY_HOME) and the `ps` reads to a controlled listing — so they assert the
check's behaviour, never the live host.
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))

import fleet_doctor.checks as chk  # noqa: E402
from fleet_doctor.results import Results  # noqa: E402

CHECK_NAME = "Stale enforcer plugin"


def _write_state(deploy_home: Path, epoch: float, digest: str = "a" * 64) -> Path:
    state_dir = deploy_home / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = state_dir / "governance-enforcer-deployed"
    state.write_text(f"{digest} {int(epoch)}\n")
    return state


def _patch_ps(pid: int, argv: str, start_epoch: float, gateway: bool = True):
    """Fake `ps`: one process with a controlled argv and start epoch."""
    real_run = subprocess.run
    listing = f"{pid} {argv}\n" if gateway else f"{pid} /usr/bin/something-else\n"

    def fake_run(cmd, **kw):
        if cmd == ["ps", "-eo", "pid,args"]:
            return type("R", (), {"stdout": listing, "returncode": 0})()
        if cmd[0:2] == ["ps", "-o"] and "lstart=" in cmd[2:3] and "-p" in cmd:
            start = datetime.fromtimestamp(start_epoch)
            return type("R", (), {
                "stdout": start.strftime("%a %b %d %H:%M:%S %Y"), "returncode": 0})()
        return real_run(cmd, **kw)

    subprocess.run = fake_run
    return lambda: setattr(subprocess, "run", real_run)


def _run_check():
    r = Results()
    chk.check_stale_enforcer_plugin(r)
    return [c for c in r.checks if c["name"] == CHECK_NAME]


def test_warns_when_gateway_started_before_the_enforcer_changed(tmp_path, monkeypatch):
    """THE measured regression: gateway older than the recorded content change.

    This is moses' 2026-11-17 condition — a host that looks deployed and current
    while the running gateway still enforces the previous revision.
    """
    monkeypatch.setattr(chk, "FLEET_DEPLOY_HOME", tmp_path, raising=False)
    now = time.time()
    _write_state(tmp_path, epoch=now - 60)          # content changed 60s ago
    restore = _patch_ps(4242, f"{sys.executable} ... gateway run", now - 300)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no row"
    assert hit[0]["status"] == "WARN", hit[0]
    assert "previous revision" in hit[0]["detail"] or "not loaded" in hit[0]["detail"], hit[0]["detail"]
    # The fix is pasted verbatim by operators, so it must name the real command.
    assert "gateway restart" in hit[0]["fix"], hit[0]["fix"]


def test_passes_when_gateway_started_after_the_enforcer_changed(tmp_path, monkeypatch):
    """A gateway that started AFTER the content change IS running the current code."""
    monkeypatch.setattr(chk, "FLEET_DEPLOY_HOME", tmp_path, raising=False)
    now = time.time()
    _write_state(tmp_path, epoch=now - 300)         # content changed 5 min ago
    restore = _patch_ps(4242, f"{sys.executable} ... gateway run", now - 60)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no row"
    assert hit[0]["status"] == "PASS", hit[0]


def test_passes_when_a_redeploy_moved_the_file_but_content_is_unchanged(tmp_path, monkeypatch):
    """ANTI-MTIME: identical bytes re-copied must not re-warn.

    A deploy rewrites files whose content did not change, so an mtime-based
    comparison would warn on every deploy and be clearable only by restarting a
    process that is already running the right code. The recorded CONTENT-change
    epoch is the authority; the deployed file being freshly written says nothing.
    """
    monkeypatch.setattr(chk, "FLEET_DEPLOY_HOME", tmp_path, raising=False)
    now = time.time()
    # Content last CHANGED long ago (before the gateway started) even though the
    # deployed copy was touched a second ago by a re-copy.
    _write_state(tmp_path, epoch=now - 3600)
    deployed = tmp_path / "enforcer-freshly-written.py"
    deployed.write_text("x = 1\n")
    restore = _patch_ps(4242, f"{sys.executable} ... gateway run", now - 60)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no row"
    assert hit[0]["status"] == "PASS", hit[0]


def test_reports_could_not_verify_when_the_record_is_absent(tmp_path, monkeypatch):
    """No recorded content-change epoch ⟶ COULD NOT VERIFY, never a silent PASS.

    Silence must never mean "no problem": a host whose deploy never wrote the
    record cannot be judged, and the check says so.
    """
    monkeypatch.setattr(chk, "FLEET_DEPLOY_HOME", tmp_path, raising=False)
    now = time.time()
    restore = _patch_ps(4242, f"{sys.executable} ... gateway run", now - 60)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no row"
    assert hit[0]["status"] == "WARN", hit[0]
    assert "COULD NOT VERIFY" in hit[0]["detail"], hit[0]["detail"]


def test_reports_could_not_verify_when_the_record_is_corrupt(tmp_path, monkeypatch):
    """A corrupt/unparsable record is COULD NOT VERIFY, not a PASS."""
    monkeypatch.setattr(chk, "FLEET_DEPLOY_HOME", tmp_path, raising=False)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "governance-enforcer-deployed").write_text("not-a-hash not-an-epoch\n")
    now = time.time()
    restore = _patch_ps(4242, f"{sys.executable} ... gateway run", now - 60)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no row"
    assert hit[0]["status"] == "WARN", hit[0]
    assert "COULD NOT VERIFY" in hit[0]["detail"], hit[0]["detail"]


def test_skips_when_no_gateway_process_is_running(tmp_path, monkeypatch):
    """No gateway to judge ⟶ SKIP, never a false WARN."""
    monkeypatch.setattr(chk, "FLEET_DEPLOY_HOME", tmp_path, raising=False)
    now = time.time()
    _write_state(tmp_path, epoch=now - 60)
    restore = _patch_ps(4242, "/usr/bin/something-else", now - 300, gateway=False)
    try:
        hit = _run_check()
    finally:
        restore()
    assert hit, "check produced no row"
    assert hit[0]["status"] == "SKIP", hit[0]
