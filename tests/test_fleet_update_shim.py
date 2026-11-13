#!/usr/bin/env python3
"""The canonical deploy script (ops/scripts/fleet-update.sh) must WORK, not
just exist.

The one-release forwarding shim that the P3 migration left at the OLD
name was deleted when release 2 retired the transition layer (the shim
existed only to keep legacy invocations alive for one release). Now
ops/scripts/fleet-update.sh IS the canonical deploy script. Three facts
pinned here:

1. The canonical script exists and is executable (a script that existed
   but could not run would fail silently in a cron).
2. `bash ops/scripts/fleet-update.sh --status` exits 0.
3. Its output includes the status section — reaching it proves the real
   script ran, not a wrapper echo. --status is deliberately the fast,
   non-mutating invocation; running the full deploy in a unit test would
   mutate the host.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "scripts" / "fleet-update.sh"


def test_canonical_script_exists_and_is_executable():
    assert SCRIPT.exists(), "the canonical deploy script is missing"
    assert SCRIPT.stat().st_mode & 0o111, "cron invocations need the +x bit"


def test_status_reaches_the_real_script():
    run = subprocess.run(["bash", str(SCRIPT), "--status"],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"--status rc={run.returncode}: {run.stderr[-500:]}"
    # The banner + the status section are produced by fleet-update.sh itself —
    # reaching them proves the real script ran.
    assert "Changed files since last update" in run.stdout