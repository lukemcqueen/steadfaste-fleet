#!/usr/bin/env python3
"""agent-loop-gov-lock-watchdog.py — ALERT-ONLY backstop for loop-gov
daemons that hold a stuck write-lock on loop-governance.db (or run pre-deploy
code). It DETECTS and ATTRIBUTES; it never auto-kills.

Incident class (2026-09-29/30, repeatedly on moses): begin_change fails with
"database is locked" for EVERY session on the host because one loop-governance
MCP daemon holds an UNCOMMITTED/OPEN write transaction on loop-governance.db.
Because the write tools (terminal/patch/execute_code) are gated behind the very
lock that can't be acquired, the whole host deadlocks (the
enforcement-gate-integrity catch-22).

ROOT CAUSE — SECURITY-AUDITED + FIXED 2026-09-30 (NOT daemon age):
  The true cause is a connection leak in _record_review(): adversarial_reviews
  .cycle_id is UNIQUE, so every retried end_change re-INSERTs -> IntegrityError
  -> the old code `pass`ed WITHOUT closing the connection, leaving an open write
  txn in the serving daemon until it died. That bug is now FIXED at the source
  (conn closed on every path), so the DB stops wedging and nothing needs killing.
  Daemon age is an insufficient heuristic (a post-deploy daemon can leak too);
  this watchdog keeps only the WRITABILITY probe as its backstop signal.

Design (alert-only backstop, per operator directive "no brute-force killing"):
  1. Probe DB writability with a real BEGIN IMMEDIATE (busy_timeout ~10s, i.e.
     beyond begin_change's own 5s timeout). A timeout means the txn has been
     stuck >10s — a genuine leak, not a healthy in-flight write (which commits
     in milliseconds).
  2. On a confirmed stuck lock, ATTRIBUTE the holder via /proc/<pid>/fd and
     REPORT every loop-gov daemon in view with its pid — but do NOT kill. The
     leak-fix rolls the txn back on its own once the daemon's write transaction
     completes/aborts; if it persists, the report names the exact pids for a
     human decision. Killing as a working norm was explicitly rejected.
  3. SECONDARY: a daemon is running pre-deploy code iff the module file it
     LOADED no longer hashes to the sha256 it recorded in the daemon stamp
     (~/.steadfaste-fleet/state/loop-gov-daemon.json). CONTENT, not mtime. The
     old "start time < deployed-mtime" test was wrong twice over: it measured a
     file the daemon may never have loaded (a host can point its daemon at the
     repo copy), and every deploy rewrites the deployed copy, so it flagged
     EVERY daemon on EVERY deploy — the moses false-alarm class (2026-11-17: the
     alert read "running pre-deploy code" while the DB probe had SUCCEEDED, i.e.
     nothing was locked). pids the stamp does not know fall back to that mtime
     test, with the grace window.

Watchdog pattern (StateTracker-gated, no_agent):
  - Empty stdout -> silent (DB writable AND no stale-deploy daemon).
  - Text output  -> alert (stuck lock held by loop-gov daemon(s), named by pid).

Cross-platform:
  - ps lstart: Linux GNU ps and macOS BSD ps both emit 'Wed Sep 29 23:47:11 2026'
    (local time); parsed with datetime (naive-local -> epoch, matching mtime).
  - /proc attribution is Linux-only; on non-Linux hosts the alert reports the
    loop-gov daemons in view (mtime-secondary path) with no holder attribution.

Run (no_agent cron or manual):  python3 agent-loop-gov-lock-watchdog.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from state_tracker import StateTracker  # deployed ops lib
    from hermes_tz import format_timestamp
except ImportError:  # pragma: no cover - standalone fallback only on missing lib
    class StateTracker:  # type: ignore
        def __init__(self, *a, **k): pass
        def evaluate(self, fp, has_issues): return "alert" if has_issues else "silent"

    def format_timestamp(fmt):
        return datetime.now().strftime(fmt)


HOME = Path.home()
DEPLOYED_MCP = HOME / ".steadfaste-fleet" / "tools" / "loop-governance" / "loop-gov-mcp.py"
DB_PATH = HOME / ".steadfaste-fleet" / "data" / "loop-governance.db"
# Per-pid daemon stamp written by the loop-gov MCP at startup: pid -> module +
# sha256 of that file at load time. The authoritative source for "is this daemon
# running the code on disk?", because it names the file the daemon ACTUALLY
# loaded rather than the deployed copy we merely assume it loaded.
STAMP_PATH = HOME / ".steadfaste-fleet" / "state" / "loop-gov-daemon.json"
# begin_change uses busy_timeout 5000; our probe must exceed it so a genuinely
# stuck txn is detected (healthy in-flight writes commit in <ms).
PROBE_TIMEOUT_MS = 10_000
# GRACE_SECONDS: on the mtime (secondary) path, a daemon spawned within this
# window of the deploy is itself a clean respawn — never flag it stale.
GRACE_SECONDS = 120.0


def _cron_ts(name: str) -> str:
    return f"{format_timestamp('[%Y-%m-%d %H:%M %Z]')} {name}:"


def _db_write_blocked() -> bool:
    """True iff a BEGIN IMMEDIATE cannot be acquired within PROBE_TIMEOUT_MS.

    Only a genuine "database is locked" timeout counts as blocked. Any OTHER
    failure (file missing, I/O error, corrupt db) is a real problem we must NOT
    silently treat as writable — return True (callers alert on it) rather than
    swallow it as healthy.
    """
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=PROBE_TIMEOUT_MS / 1000.0)
        conn.execute(f"PRAGMA busy_timeout={PROBE_TIMEOUT_MS}")
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return False  # writable — no stuck txn
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "locked" in msg or "busy" in msg:
            return True  # stuck write lock — the incident we chase
        # other OperationalError (can't open / I/O / corrupt) -> also a problem;
        # surface it as blocked so the watchdog ALERTS instead of going silent.
        return True
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _loop_gov_pids() -> list[int]:
    """All running loop-gov-mcp.py daemon PIDs (healthy + stuck)."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "loop-gov-mcp.py"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in out.stdout.split() if p.isdigit()]


def _fd_points_to_db(pid: int, fd: str) -> bool:
    """True iff fd resolves to the MAIN loop-governance.db inode.

    Only the main database file counts as a hold — the -wal / -shm sidecars are
    not lock holders (a writer takes its write lock on the main db, and they
    exist in WAL even when idle). realpath comparison excludes them.
    """
    try:
        link = os.path.realpath(f"/proc/{pid}/fd/{fd}")
    except OSError:
        return False
    try:
        return link == str(Path(os.path.realpath(str(DB_PATH))))
    except OSError:
        return link == str(DB_PATH)


def _holder_pids() -> tuple[list[int], list[str]]:
    """(loop-gov holders of the DB fd, non-loop-gov holder cmdlines)."""
    loop_holders: list[int] = []
    other_holders: list[str] = []
    for pid in _loop_gov_pids():
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        if any(_fd_points_to_db(pid, fd) for fd in fds):
            loop_holders.append(pid)
    # find NON-loop-gov holders (surface loudly, never kill)
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid in loop_holders:
                continue
            try:
                fds = os.listdir(f"/proc/{pid}/fd")
            except OSError:
                continue
            if not any(_fd_points_to_db(pid, fd) for fd in fds):
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode().strip()
            except OSError:
                cmd = "?"
            other_holders.append(f"pid {pid} {cmd!r}")
    except OSError:
        pass
    return loop_holders, other_holders


def _lstart_epoch(pid: int) -> float | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    s = out.stdout.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp()
    except ValueError:
        return None


def _deployed_mtime() -> float | None:
    try:
        return DEPLOYED_MCP.stat().st_mtime
    except OSError:
        return None


def _load_stamp() -> dict:
    """{pid_str: {"module": path, "sha256": hex, "started": epoch}} — {} if absent.

    READ-ONLY by design: the stamp is shared state (every loop-gov daemon appends
    its own pid), and we never write it, so a concurrent session cannot be
    corrupted by us. A torn/partial write raises ValueError, which we swallow to
    {} — callers then fall back to the mtime rule. Never a traceback, never a
    false green.
    """
    try:
        data = json.loads(STAMP_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _module_sha(path: str) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _pre_deploy_pids() -> list[int]:
    """Daemons whose loaded module no longer hashes to what they recorded.

    Content verification, per pid, against the file that daemon ACTUALLY loaded.
    A deploy rewriting the deployed copy is not evidence of stale code and must
    never flag a daemon whose own module is unchanged. A recorded module we
    cannot read is reported (never treated as clean — no false green). pids with
    no stamp entry fall back to the mtime rule, which is all we have for them.
    """
    stamp = _load_stamp()
    deployed = _deployed_mtime()
    stale: list[int] = []
    for pid in _loop_gov_pids():
        entry = stamp.get(str(pid)) or {}
        module = entry.get("module")
        recorded = entry.get("sha256")
        if isinstance(module, str) and isinstance(recorded, str):
            current = _module_sha(module)
            if current is None or current != recorded:
                stale.append(pid)
            continue
        if deployed is not None:
            start = _lstart_epoch(pid)
            if start is not None and (start + GRACE_SECONDS) < deployed:
                stale.append(pid)
    return stale


def main() -> int:
    db_blocked = _db_write_blocked()

    # SECONDARY: pre-deploy daemons run stale code that will leak on next write.
    secondary_stale = _pre_deploy_pids()

    if db_blocked:
        # PRIMARY: DB is locked -> surface its loop-gov fd-holders (alert-only).
        loop_holders, other_holders = _holder_pids()
        alert_targets = sorted(set(loop_holders) | set(secondary_stale))
    else:
        # DB writable -> only the stale-code (secondary) path applies.
        alert_targets = sorted(set(secondary_stale))
        other_holders = []

    if not alert_targets:
        if db_blocked:
            _, other_holders = _holder_pids()
            print(
                f"{_cron_ts('loop-gov-lock-watchdog')} loop-governance.db is write-BLOCKED "
                f"but no loop-gov daemon holds the fd. Other holders: "
                f"{'; '.join(other_holders) if other_holders else 'none attributed (off-Linux?)'}. "
                f"DB={DB_PATH}")
            return 1
        return 0  # silent — clean

    still_blocked = _db_write_blocked() if db_blocked else False

    fp = "|".join(str(p) for p in sorted(alert_targets)) or "none"
    action = StateTracker("loop-gov-lock-watchdog").evaluate(
        fp, has_issues=bool(alert_targets))

    if action == "silent":
        return 0

    lines: list[str] = []
    if alert_targets:
        reason = "DB was write-blocked" if db_blocked else "running pre-deploy code"
        lines.append(
            f"{_cron_ts('loop-gov-lock-watchdog')} lock/anomaly DETECTED — alert-only "
            f"(no auto-kill): {len(alert_targets)} loop-gov daemon(s) in view [{reason}]: "
            f"pids {', '.join(str(p) for p in alert_targets)}. "
            f"The DB write-lock resolves itself once the daemon running the "
            f"_record_review leak-fix rolls its txn back; if it persists, inspect "
            f"these pids (do NOT blindly SIGTERM — killing as a working norm is "
            f"rejected).")
    if still_blocked:
        other = f"{'; '.join(other_holders) if other_holders else 'none'}"
        lines.append(
            f"{_cron_ts('loop-gov-lock-watchdog')} loop-governance.db STILL write-blocked. "
            f"Non-loop-gov holders: {other}. "
            f"Requires operator attention — do NOT bypass the enforcer.")
    elif db_blocked and not still_blocked:
        lines.append(
            f"{_cron_ts('loop-gov-lock-watchdog')} lock recovered — DB writable.")

    print("\n".join(lines))
    return 1 if still_blocked else 0


if __name__ == "__main__":
    sys.exit(main())