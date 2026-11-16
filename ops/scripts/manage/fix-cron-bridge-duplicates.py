#!/usr/bin/env python3
"""Remove duplicate Hermes jobs.json rows for fleet-bridge-owned crons.

A bridge-owned cron's REAL owner is its systemd fleet-bridge-<name>.timer; the
Hermes jobs.json row left behind at migration means the job fires TWICE (two
independent dispatches in the same minute -> Errno 17 state collisions). This
fixer removes those stale Hermes rows via `hermes cron remove <id>` for every
cron that is BOTH bridged (a fleet-bridge-<name>.timer exists) AND still has a
Hermes jobs.json row.

The systemd timer — the real owner — is never touched, and each removal is
verified to leave the timer active and the Hermes row gone. Idempotent and safe
on any host (no-op when there are no doubles); run it after a fleet-update so
this fixer and the doctor's check_cron_bridge_migration agree.

EXIT: 0 = clean (nothing to do, or all removed); 1 = some removal failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from glob import glob
from pathlib import Path

_HOME = Path.home()
_JOBS = _HOME / ".hermes" / "cron" / "jobs.json"
_XDG = "/run/user/%d" % os.getuid()
_PREFIX = "fleet-bridge-"


def timer_names(home: Path | None = None) -> set[str]:
    """Names of the fleet-bridge-*.timer units present on this host."""
    home = home or _HOME
    out = set()
    for u in glob(str(home / ".config" / "systemd" / "user" / f"{_PREFIX}*.timer")):
        stem = Path(u).stem  # fleet-bridge-<name>.timer
        out.add(stem[len(_PREFIX):-len(".timer")])
    return out


def jobs_by_name(jobs: list[dict]) -> dict[str, dict]:
    return {j.get("name"): j for j in jobs if isinstance(j, dict) and j.get("id")}


def find_doubles(jobs: list[dict], timer_names_: set[str]) -> list[tuple[str, str]]:
    """(name, id) of crons that are bridged AND still have a Hermes row.

    Pure + deterministic for hermetic testing: the intersection of the systemd
    fleet-bridge timers and the Hermes jobs.json names, giving each double's id.
    """
    by_name = jobs_by_name(jobs)
    out = []
    for name in sorted(timer_names_ & set(by_name)):
        out.append((name, by_name[name]["id"]))
    return out


def _timer_active(name: str) -> bool | None:
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-units", f"{_PREFIX}{name}.*", "--no-legend"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "XDG_RUNTIME_DIR": _XDG})
        out = r.stdout
        return f"{_PREFIX}{name}.timer" in out and "active" in out
    except Exception:
        return None


def _hermes_remove(jid: str) -> bool:
    try:
        r = subprocess.run(["hermes", "cron", "remove", jid],
                           capture_output=True, text=True, timeout=60)
        return "Removed job" in r.stdout or "removed" in (r.stdout + r.stderr).lower() or r.returncode == 0
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    dry_run = "--dry-run" in argv
    doubles = find_doubles(json.load(open(_JOBS)).get("jobs", []), timer_names())
    if not doubles:
        print("no double-owned crons to remove")
        return 0
    ok = fail = 0
    for name, jid in doubles:
        if dry_run:
            print(f"[dry-run] would remove {name} ({jid})")
            ok += 1
            continue
        timer_before = _timer_active(name)
        removed = _hermes_remove(jid)
        still = any(x.get("id") == jid for x in json.load(open(_JOBS)).get("jobs", []))
        timer_after = _timer_active(name)
        if removed and not still and timer_before and timer_after:
            ok += 1
            print(f"OK  removed {name} ({jid}) — timer still active")
        else:
            fail += 1
            print(f"??  {name} ({jid}): removed={removed} still_in_jobs={still} "
                  f"timer_before={timer_before} timer_after={timer_after}")
    print(f"done: {ok} removed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
