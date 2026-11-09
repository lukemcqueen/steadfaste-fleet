#!/usr/bin/env python3
"""Tests for the classify CLI: the fast access layer over write_class.py.

`ops/scripts/loop-gov-classify.py <command>` returns ONE JSON verdict:
  {"read_only": bool, "sanctioned": "fleet_update"|"tmp_cleanup"|"plan_review"|null,
   "db_mutation": bool}
with rc 0 on success and rc 3 ("COULD NOT VERIFY", fail closed) when the
contract cannot be loaded. `--contract <path>` is the test/ops seam that
pins which write_class.py copy is used (default: repo layout, then deployed).

Run: python3 -m pytest tests/test_loop_gov_classify.py -q
"""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "ops" / "scripts" / "loop-gov-classify.py"
_CONTRACT = _REPO / "ops" / "services" / "fleet-governance" / "write_class.py"


def _run(command: str, contract: Path | None = None):
    argv = [sys.executable, str(_CLI)]
    if contract is not None:
        argv += ["--contract", str(contract)]
    argv.append(command)
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def _verdict(command: str, contract: Path | None = None) -> dict:
    r = _run(command, contract)
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:400]}"
    return json.loads(r.stdout)


REQUIRED_KEYS = {"read_only", "sanctioned", "db_mutation"}


@pytest.mark.parametrize("command,read_only", [
    ("ls -la", True),
    ("git status --short", True),
    ("cat README.md | grep -i x", True),
    ("systemctl status nginx", True),
    ("journalctl -u x | tail -5", True),
    ("curl -sI https://example.com", True),
    ("rm -rf /tmp/x", False),
    ("git push origin main", False),
    ("python3 -c 'print(1)'", False),
    ("echo x > /tmp/file", False),
    ("find . -exec rm {} +", False),
    ("mkdir newdir", False),
    ("", False),
])
def test_verdict_shape_and_readonly(command, read_only):
    v = _verdict(command)
    assert set(v) == REQUIRED_KEYS, f"unexpected keys: {set(v) - REQUIRED_KEYS}"
    assert v["read_only"] is read_only, (command, v)
    assert v["db_mutation"] is False


@pytest.mark.parametrize("command,sanctioned", [
    ("bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh --status", "fleet_update"),
    ("~/steadfaste-fleet/ops/scripts/fleet-update.sh --dry-run", "fleet_update"),
    ("rm -f /tmp/x /tmp/y && echo cleaned", "tmp_cleanup"),
    ("rm -rf /tmp/templates", "tmp_cleanup"),
    ("python3 ops/scripts/orch-bus/adversarial-review.py --cycle-id 1 "
     "--material-file /tmp/p.md --review --reviewer-id e", "plan_review"),
])
def test_sanctioned_lanes(command, sanctioned):
    v = _verdict(command)
    assert v["sanctioned"] == sanctioned, (command, v)
    assert v["read_only"] is False


@pytest.mark.parametrize("command", [
    "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh && echo hi",
    "sudo ~/steadfaste-fleet/ops/scripts/fleet-update.sh",
    "rm -rf /tmp",
    "rm -rf /srv/app/data",
    "python3 adversarial-review.py --sweep",
])
def test_not_sanctioned(command):
    assert _verdict(command)["sanctioned"] is None, command


@pytest.mark.parametrize("command", [
    "sqlite3 ~/.steadfaste-fleet/data/loop-governance.db \"DELETE FROM adversarial_reviews;\"",
    "python3 /tmp/adv.py --db ~/.steadfaste-fleet/data/loop-governance.db",
])
def test_db_mutation(command):
    v = _verdict(command)
    assert v["db_mutation"] is True, (command, v)


def test_db_read_not_mutation():
    v = _verdict("sqlite3 ~/.steadfaste-fleet/data/loop-governance.db \"SELECT 1;\"")
    assert v["db_mutation"] is False


def test_contract_seam_changes_verdict():
    """The --contract seam proves a rule edit reloads on the NEXT process (CR5)."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "write_class.py"
        shutil.copyfile(_CONTRACT, copy)
        before = _verdict("fleetdoctor-check", contract=copy)
        assert before["read_only"] is False
        # Append one rule making the probe command read-only.
        extra = "\nREADONLY_COMMAND_PATTERNS.append(r\"^\\s*fleetdoctor-check(?:\\s|$)\")\n"
        with copy.open("a") as f:
            f.write(extra)
        after = _verdict("fleetdoctor-check", contract=copy)
        assert after["read_only"] is True
        # Nothing was restarted: a FRESH process loaded the edited contract.
        assert before != after


def test_fail_closed_on_missing_contract():
    r = _run("ls", contract=Path("/nonexistent/write_class.py"))
    assert r.returncode == 3, f"rc={r.returncode} stdout={r.stdout[:200]}"
    assert "COULD NOT VERIFY" in (r.stdout + r.stderr)


def test_cli_is_fast_enough():
    """The performance gate from the CR2 plan: cold-start to verdict, p50 <= 150 ms."""
    times = []
    for _ in range(20):
        t0 = time.monotonic()
        r = _run("git status --short")
        elapsed = (time.monotonic() - t0) * 1000
        assert r.returncode == 0
        times.append(elapsed)
    p50 = statistics.median(times)
    p95 = sorted(times)[int(len(times) * 0.95) - 1]
    print(f"classify cold-start latency: p50={p50:.1f}ms p95={p95:.1f}ms "
          f"n={len(times)}")
    assert p50 <= 150, f"p50 {p50:.1f}ms exceeds the 150ms gate"