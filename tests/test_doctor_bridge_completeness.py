#!/usr/bin/env python3
"""A bridged job has exactly ONE owner — the doctor must report both ways that fails.

The fleet cron bridge hosts eligible `no_agent` jobs on user-scope systemd
timers (`fleet-bridge-<name>.timer`) and REMOVES the Hermes row so one scheduler
owns the job. `check_cron_bridge_migration` already warns about the
migration-gap direction (an eligible job with NO unit). Two failure modes of the
same rule were UNCHECKED, and each has a documented real-world harm:

1. **Orphan timer** — a `fleet-bridge-*.timer` whose job no longer exists at all.
   The generator only CREATES units (it has no prune step), so a retired job
   keeps firing from its timer and keeps delivering to the user with the cron
   gone from every listing. Real case (2026-10-02): `orch-task-board-digest` was
   retired and Luke kept receiving its daily digest.

   Detection needs the manifest, not `jobs.json`: a MIGRATED job is
   *deliberately* absent from `jobs.json` (the timer is its owner), so
   "unit but no job" is the healthy migrated state. The distinguishing fact is
   that a migrated job is still DECLARED in `cron-manifest.yaml`, while a retired
   one had its manifest entry dropped. So: unit present + name NOT declared =
   orphan timer.

2. **Double-ownership** — a bridge-owned name that is ALSO still present in
   `jobs.json`. Two owners means the job runs twice; the second run collides on
   shared state (`Errno 17 / File exists`), and a double-owned watchdog surfaces
   as a host health flap rather than an obvious double-fire.

Run:  pytest tests/test_doctor_bridge_completeness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(REPO / "ops" / "scripts"))

import fleet_doctor.checks as C  # noqa: E402 — after sys.path setup


def _verdicts(units, declared, live):
    return C.classify_bridge_ownership(
        unit_names=set(units), declared=set(declared), live=set(live)
    )


def _fixes(units, declared, live) -> str:
    return " ".join(v["fix"] for v in _verdicts(units, declared, live))


# ── 1. orphan timer: the unit outlived its job ──────────────────────────────

def test_orphan_timer_is_reported():
    vs = _verdicts(units=["orch-board-digest"], declared=[], live=[])
    assert vs, "a timer whose job is declared NOWHERE keeps firing and must be reported"
    v = vs[0]
    assert "orch-board-digest" in v["name"]
    assert "disable --now" in v["fix"] or "rm -f" in v["fix"], (
        "an orphan timer's remedy is to retire the unit, not to create a cron"
    )


def test_orphan_timer_remedy_never_creates_a_cron():
    """The harm is that the timer keeps firing — the remedy must not be to
    create a Hermes cron (that would give the job a SECOND owner)."""
    fixes = _fixes(units=["orch-board-digest"], declared=[], live=[])
    assert "create" not in fixes.lower(), (
        "the remedy told the operator to create a job for a timer that already runs"
    )


# ── 2. double-ownership: timer AND Hermes row ───────────────────────────────

def test_double_owned_job_is_reported():
    vs = _verdicts(units=["agent-watchdog"], declared=["agent-watchdog"],
                   live=["agent-watchdog"])
    assert vs, "a name with both a timer and a Hermes row fires twice — report it"
    assert "agent-watchdog" in vs[0]["name"]
    assert "cron remove" in vs[0]["fix"], (
        "the fix is to drop the Hermes row; the timer is the owner"
    )


# ── 3. the healthy shapes must stay silent (no false positives) ─────────────

def test_migrated_job_is_not_reported():
    """unit + declared + absent from jobs.json is the CORRECT migrated shape."""
    assert _verdicts(units=["agent-watchdog"], declared=["agent-watchdog"], live=[]) == []


def test_hermes_job_without_a_unit_is_not_reported():
    assert _verdicts(units=[], declared=["agent-llm-job"], live=["agent-llm-job"]) == []


def test_empty_world_is_silent():
    """'Silent' means NO verdicts — assert the list is empty, not truthy.

    (An earlier draft asserted `assert _verdicts(...)`, which is FALSE for the
    empty list — the test would only pass when the check wrongly reported a
    problem. Asserting emptiness is the point of the test.)
    """
    assert _verdicts(units=[], declared=[], live=[]) == []


# ── 4. the single-owner rule is stated as the invariant ─────────────────────

def test_each_name_is_reported_at_most_once():
    """One name, one verdict — a double-owned name must not also be called an
    orphan timer."""
    vs = _verdicts(units=["agent-watchdog"], declared=["agent-watchdog"],
                   live=["agent-watchdog"])
    names = [v["name"] for v in vs]
    assert len(names) == len(set(names)), "the same name was reported twice"
    assert len(vs) == 1, f"expected exactly one verdict for a double-owned name, got {len(vs)}"
