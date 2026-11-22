#!/usr/bin/env python3
"""Cron ownership audit — every ownership mode, reported as COUNTS.

A job scheduled in exactly one place is the invariant. Three ways it breaks, and
the tooling reports at most two of them:

  1. DOUBLE OWNERSHIP  — a bridge timer AND a jobs.json row => the job fires TWICE.
                         The doctor FAILs on this, and the unit's ExecStart is
                         self-contained, so the jobs.json row is the side to drop.
  2. MIGRATION GAP     — a bridge-ELIGIBLE job (no_agent + script, no model pin)
                         with no unit at all. The doctor WARNs.
  3. DANGLING MARKER   — an enable symlink with no unit file: the bridge claims to
                         own a job that can never fire (silently stopped). No
                         check reports this one, so it is counted here.

A job present in jobs.json and NOT bridged is NORMAL, not a defect: LLM / chained
jobs need the agent loop and stay on the Hermes scheduler by design. This script
labels them separately rather than calling them orphans, so the report cannot be
misread as "17 jobs are broken".

Ownership is read from the enable symlink dir (filesystem-only: a cron / install /
doctor context has no DBus session, so `systemctl --user` is unavailable). A unit
file in any state counts as "considered" — a deliberately paused job has a DISABLED
unit (absent from wants/) and must never be reported as un-bridged.

Usage:  python3 cron-ownership-audit.py
Exit:   0 = PASS, 1 = a defect (double ownership, gap, or dangling marker)
"""
import json
import os
import sys

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes"))
JOBS_FILE = os.path.join(HERMES_HOME, "cron", "jobs.json")
UNIT_DIR = os.path.join(os.path.expanduser("~"), ".config", "systemd", "user")
WANTS_DIR = os.path.join(UNIT_DIR, "timers.target.wants")
PREFIX = "fleet-bridge-"


def _names_from_wants():
    """Names the bridge owns, from the enable symlinks. `islink` is deliberate:
a symlink to a unit file that no longer exists is still a claim of ownership (and
is reported separately as a dangling marker)."""
    owned = []
    if not os.path.isdir(WANTS_DIR):
        return owned
    for entry in os.listdir(WANTS_DIR):
        if entry.startswith(PREFIX) and entry.endswith(".timer"):
            owned.append(entry[len(PREFIX):-len(".timer")])
    return sorted(owned)


def _unit_files():
    if not os.path.isdir(UNIT_DIR):
        return set()
    return {
        e[len(PREFIX):-len(".timer")]
        for e in os.listdir(UNIT_DIR)
        if e.startswith(PREFIX) and e.endswith(".timer")
    }


def _jobs():
    with open(JOBS_FILE, encoding="utf-8") as fh:
        return json.load(fh).get("jobs", [])


def _eligible(job):
    """Mirrors the bridge generator's job_is_simple(): a model pin, context_from
or continuity makes a job non-translatable, so it is not a migration gap."""
    return bool(job.get("no_agent") and job.get("script")) and not (
        job.get("context_from") or job.get("continuity") or job.get("model")
    )


def main():
    try:
        jobs_raw = _jobs()
    except (OSError, ValueError) as exc:
        print(f"COULD NOT VERIFY: cannot read {JOBS_FILE}: {type(exc).__name__}: {exc}")
        return 1

    bridged = _names_from_wants()
    units = _unit_files()
    jobs = sorted(j["name"] for j in jobs_raw if j.get("name"))

    double = sorted(set(bridged) & set(jobs))
    hermes_only = sorted(set(jobs) - set(bridged))
    gaps = sorted(
        j["name"] for j in jobs_raw if j.get("name") and _eligible(j) and j["name"] not in bridged
    )
    dangling = sorted(set(bridged) - units)

    print(f"bridge-owned (enable marker): {len(bridged)}")
    print(f"live Hermes jobs (jobs.json): {len(jobs)}")
    print(f"unit files on disk:          {len(units)}")
    print()
    print(f"1. DOUBLE OWNERSHIP  : {len(double)} {double if double else '(none)'}")
    print(f"2. MIGRATION GAP     : {len(gaps)} {gaps if gaps else '(none)'}")
    print(f"3. DANGLING MARKER   : {len(dangling)} {dangling if dangling else '(none)'}")
    print(f"   Hermes-scheduler-owned, not a defect: {len(hermes_only)}")
    for name in hermes_only:
        job = next(j for j in jobs_raw if j.get("name") == name)
        kind = "LLM" if not job.get("no_agent") else "no_agent (review: why not bridged?)"
        print(f"     {name}  [{kind}]")

    failed = bool(double or gaps or dangling)
    print()
    print(f"VERDICT: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
