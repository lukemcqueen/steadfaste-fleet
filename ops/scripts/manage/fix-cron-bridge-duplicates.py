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


def find_doubles(jobs: list[dict], timer_names_: set[str]) -> list[tuple[str, str]]:
    """(name, id) of EVERY cron row that is bridged AND has a Hermes row.

    Pure + deterministic for hermetic testing: for every jobs.json row whose
    name names a systemd fleet-bridge timer, yield that row's (name, id).
    Iterates ALL rows (not a name->id dict) so duplicate/stale same-name rows
    are each returned and none silently survives; rows without an id are skipped.
    """
    out = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        name, jid = j.get("name"), j.get("id")
        if name in timer_names_ and jid:
            out.append((name, jid))
    return sorted(out)


def is_owner(enabled: bool, active: bool | None) -> bool:
    """A bridge timer is a real (removable) owner only when ENABLED and ACTIVE.

    `enabled` (symlinked into timers.target.wants/) AND systemd-active. A timer
    that exists on disk but is NOT enabled is not running the cron — its Hermes
    row is the live owner and must not be removed (that would orphan it). For a
    generated-but-not-enabled timer the correct completion is to ENABLE it, then
    the removal is durable (install-crons.sh skips the now-enabled bridge cron).
    """
    return bool(enabled) and bool(active)


def _timer_active(name: str) -> bool | None:
    """True only when the bridge timer is the GENUINELY RUNNING owner.

    A timer is a real second owner only when it is ENABLED (symlinked into
    timers.target.wants) AND systemd reports its state as active — NOT merely
    "exists on disk" or "inactive (dead)". systemctl list-units prints loaded
    units including inactive ones whose state string is 'inactive (dead)'
    (which contains 'active'), so we parse the STATE COLUMN, not substring.
    """
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-units", f"{_PREFIX}{name}.*", "--no-legend",
             "-o", "json"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "XDG_RUNTIME_DIR": _XDG})
        for unit in json.loads(r.stdout or "[]"):
            if not unit.get("unit", "").startswith(f"{_PREFIX}{name}.timer"):
                continue
            load, active = unit.get("load"), unit.get("active")
            if load == "loaded" and active == "active":
                return True
        return False
    except Exception:
        return None


def _timer_enabled(name: str, home: Path | None = None) -> bool:
    """True when the bridge timer is enabled (present in timers.target.wants).

    install-crons.sh's bridge-skip is keyed on this enable symlink, NOT the unit
    file: a generated-but-NOT-enabled timer is treated by the installer as a
    non-bridged job and its Hermes row is (re)created. So enabled is the state
    that both makes this cron 'really bridged' and makes a removal durable.
    """
    home = home or _HOME
    return (home / ".config" / "systemd" / "user" / "timers.target.wants"
            / f"{_PREFIX}{name}.timer").exists()



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
    ok = fail = skipped = 0
    for name, jid in doubles:
        # A cron is ONLY a removable double when its bridge timer is the
        # genuinely running owner (enabled AND active). If the timer file
        # exists but is not enabled/active, the Hermes row is the live owner —
        # removing it would orphan the cron.
        owner = is_owner(_timer_enabled(name), _timer_active(name))
        if dry_run:
            if owner:
                print(f"[dry-run] would remove {name} ({jid})")
            else:
                print(f"[dry-run] SKIP {name} ({jid}): bridge timer not enabled/active "
                      f"(Hermes row is the live owner) — enable the timer, then re-run")
            ok += 1
            continue
        if not owner:
            # Half-migrated: generated-but-NOT-enabled bridge timer. Enable the
            # timer first (completes the migration), then this removal is
            # durable — install-crons.sh then skips the bridged cron on reinstall.
            print(f"SKIP {name} ({jid}): bridge timer not enabled/active — Hermes row is "
                  f"the live owner; enable the timer with 'systemctl --user enable --now "
                  f"fleet-bridge-{name}.timer', then re-run")
            skipped += 1
            continue
        removed = _hermes_remove(jid)
        still = any(x.get("id") == jid for x in json.load(open(_JOBS)).get("jobs", []))
        timer_after = _timer_active(name)
        if removed and not still and timer_after:
            ok += 1
            print(f"OK  removed {name} ({jid}) — timer still active")
        else:
            fail += 1
            print(f"??  {name} ({jid}): removed={removed} still_in_jobs={still} "
                  f"timer_after={timer_after}")
    print(f"done: {ok} ok, {skipped} skipped (half-migrated), {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
