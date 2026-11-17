#!/usr/bin/env python3
"""Regression proof for the loop-gov lock watchdog's SECONDARY (pre-deploy) path.

The watchdog used to flag a daemon as "running pre-deploy code" whenever its
start time preceded the DEPLOYED module's mtime. That test was wrong twice over:
it measured a file the daemon may never have loaded (a host can point its daemon
at the repo copy), and every deploy rewrites the deployed copy — so it flagged
EVERY daemon on EVERY deploy. That is the moses false-alarm class of 2026-11-17:
the alert read "[running pre-deploy code]" while the DB probe had SUCCEEDED
(that reason string only prints when the probe succeeded), i.e. nothing was
locked and the four "stale" daemons were two ordinary sessions x two roles.

The fix verifies CONTENT: the per-pid daemon stamp records the module each daemon
loaded and that file's sha256 at load time, so a daemon is stale only if the file
it loaded no longer hashes to what it recorded. This file is the re-runnable
proof — the watchdog's own integration test (test_loop_gov_lock_watchdog.py)
covers the PRIMARY lock path; this covers the secondary one.

Run:  python3 tests/test_loop_gov_watchdog_content_check.py     (pytest also collects it)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WATCHDOG = REPO / "ops" / "scripts" / "health" / "agent-loop-gov-lock-watchdog.py"


def _load_watchdog(stamp_path: Path, pids: list[int], deployed: Path | None):
    """Import the watchdog from its real path and pin its host-dependent inputs."""
    spec = importlib.util.spec_from_file_location("lock_watchdog_under_test", WATCHDOG)
    assert spec and spec.loader, f"cannot import {WATCHDOG}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    setattr(mod, "STAMP_PATH", stamp_path)
    if deployed is not None:
        setattr(mod, "DEPLOYED_MCP", deployed)
    setattr(mod, "_loop_gov_pids", lambda: list(pids))
    return mod


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_a_deploy_rewriting_the_deployed_copy_does_not_flag_a_current_daemon(tmp_path):
    """THE false positive: the daemon runs current code, but a deploy that just
    finished advanced the deployed copy's mtime past the daemon's start. The old
    mtime rule flagged it; content verification must not."""
    module = tmp_path / "loop-gov-mcp.py"
    module.write_text("# the code this daemon loaded\n")
    deployed = tmp_path / "deployed-copy.py"
    deployed.write_text("# rewritten by the deploy a moment ago\n")
    deployed.touch()  # mtime = now, i.e. AFTER the daemon started

    stamp = tmp_path / "loop-gov-daemon.json"
    stamp.write_text(json.dumps({
        "4242": {"pid": 4242, "started": time.time() - 3600,
                 "module": str(module), "sha256": _sha(module)},
    }))
    wd = _load_watchdog(stamp, [4242], deployed)
    assert wd._pre_deploy_pids() == [], "a daemon whose own module is unchanged is NOT stale"


def test_a_module_changed_after_load_is_flagged(tmp_path):
    """The real signal the secondary path exists for."""
    module = tmp_path / "loop-gov-mcp.py"
    module.write_text("# old\n")
    stamp = tmp_path / "loop-gov-daemon.json"
    stamp.write_text(json.dumps({
        "11": {"pid": 11, "started": time.time() - 3600,
               "module": str(module), "sha256": _sha(module)},
    }))
    module.write_text("# deployed after this daemon loaded the old bytes\n")
    wd = _load_watchdog(stamp, [11], None)
    assert wd._pre_deploy_pids() == [11], "a daemon whose module changed IS pre-deploy"


def test_an_unreadable_module_is_surfaced_not_treated_as_clean(tmp_path):
    """No false green: if the recorded module is gone we cannot verify -> report."""
    stamp = tmp_path / "loop-gov-daemon.json"
    stamp.write_text(json.dumps({
        "7": {"pid": 7, "started": time.time() - 3600,
              "module": str(tmp_path / "gone.py"), "sha256": "0" * 64},
    }))
    wd = _load_watchdog(stamp, [7], None)
    assert wd._pre_deploy_pids() == [7]


def test_a_pid_with_no_stamp_entry_falls_back_to_the_mtime_rule(tmp_path):
    """Old daemons predate the stamp; the mtime rule is all we have for them."""
    deployed = tmp_path / "deployed-copy.py"
    deployed.write_text("# deployed long after this daemon started\n")
    old = time.time() - 10_000
    os.utime(deployed, (old + 5000, old + 5000))

    stamp = tmp_path / "loop-gov-daemon.json"
    stamp.write_text("{}")
    wd = _load_watchdog(stamp, [99], deployed)
    setattr(wd, "_lstart_epoch", lambda pid: old)
    assert wd._pre_deploy_pids() == [99]


def test_a_missing_or_corrupt_stamp_never_raises(tmp_path):
    """A bare host (no stamp) must degrade, not traceback."""
    for bad in (tmp_path / "absent.json", tmp_path / "corrupt.json"):
        if bad.name == "corrupt.json":
            bad.write_text("{not json")
        wd = _load_watchdog(bad, [], None)
        assert wd._pre_deploy_pids() == []
        assert wd._load_stamp() == {}
