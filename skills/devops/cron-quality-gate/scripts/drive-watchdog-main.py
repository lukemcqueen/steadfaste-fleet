#!/usr/bin/env python3
"""Drive a no_agent watchdog script's real main() against LIVE state, with its dedup
tracker stubbed to always alert.

WHY: a watchdog dedups an unchanged issue set, so a repeat manual run prints nothing
whether or not the defect is fixed — delivery is suppressed, detection is not. This
harness measures DETECTION: it loads the script by path, replaces the tracker module,
and prints every issue found. Use it to compare BEFORE and AFTER on the same live data
(run it against the deployed pre-fix copy and the repo copy).

Usage:
    python3 drive-watchdog-main.py <path-to-watchdog.py> [<repo-root>]

Exit status is the script's own SystemExit code, else 0.
"""
import importlib.util
import os
import sys
import types


class _AlwaysAlert:
    """Stand-in for state_tracker.StateTracker: never suppress a report."""

    def __init__(self, *a, **k):
        pass

    def evaluate(self, *a, **k):
        return "alert"


def load(path, repo=None):
    """Import a watchdog by path with its dedup tracker stubbed.

    Watchdogs import shared helpers (e.g. hermes_tz) from ops/scripts, which is on
    the cron's path but not on a bare interpreter's. Without those entries the import
    raises ModuleNotFoundError and every downstream claim reads EMPTY — a wall of
    failures against a correct fix. So the probe fixes its own path first.
    """
    for cand in (os.path.join(repo, "ops", "scripts") if repo else None,
                 os.path.expanduser("~/.steadfaste-fleet/scripts")):
        if cand and os.path.isdir(cand):
            sys.path.insert(0, cand)
    stub = types.ModuleType("state_tracker")
    stub.StateTracker = _AlwaysAlert
    sys.modules["state_tracker"] = stub
    spec = importlib.util.spec_from_file_location("watchdog_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    mod = load(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    try:
        mod.main()
    except SystemExit as e:
        print(f"[exit {e.code}]", file=sys.stderr)
        return e.code or 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
