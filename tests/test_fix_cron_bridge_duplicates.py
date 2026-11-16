#!/usr/bin/env python3
"""fix-cron-bridge-duplicates.py — the pure double-owner detector.

Pins find_doubles() in BOTH directions: it returns exactly the bridged-AND-
hermes-row crons (with ids), and it stays quiet when a cron is only bridged or
only a Hermes row — a single-owner state must NOT be touched. Also pins the
is_owner() guard: a bridge timer is a removable double ONLY when it is ENABLED
and systemd-ACTIVE. A timer that exists on disk but is not enabled is NOT the
running owner — its Hermes row is the live owner, and removing it would ORPHAN
the cron (the fleet-bus-failover-watchdog / orch-weekly-cost-report case).
The destructive removal (hermes cron remove) is exercised end-to-end on the
host by the doctor's Cron bridge ownership check going 0-fail.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
SCRIPTS = _REPO / "ops" / "scripts" / "manage" / "fix-cron-bridge-duplicates.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fix = _load("fix_cron_bridge_duplicates_under_test", SCRIPTS)


def _j(name, jid):
    return {"name": name, "id": jid, "no_agent": True, "script": "x.py"}


def test_bridged_and_row_is_a_double_with_id():
    jobs = [_j("agent-a", "id1"), _j("agent-b", "id2"), _j("orch-c", "id3")]
    timers = {"agent-a", "orch-c"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1"), ("orch-c", "id3")]


def test_timer_without_row_is_not_a_double():
    # agent-b has a bridge timer but NO Hermes row (correctly migrated) — must stay.
    jobs = [_j("agent-a", "id1")]
    timers = {"agent-a", "agent-b"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1")]


def test_row_without_timer_is_not_a_double():
    # a normal Hermes scheduler job (no bridge timer) must never be removed.
    jobs = [_j("agent-a", "id1"), _j("agent-llm-job", "id9")]
    timers = {"agent-a"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1")]


def test_no_doubles_is_empty():
    jobs = [_j("agent-a", "id1")]
    timers = {"agent-b"}
    assert fix.find_doubles(jobs, timers) == []


def test_missing_id_row_is_skipped():
    jobs = [{"name": "agent-a", "no_agent": True}]  # no id -> not removable
    timers = {"agent-a"}
    assert fix.find_doubles(jobs, timers) == []


def test_duplicate_name_rows_are_returned_individually():
    # Two same-name stale rows (a broken jobs.json) — BOTH are doubles; a
    # name->id dict would keep only the last and leave the first still-double.
    jobs = [_j("agent-a", "id1"), _j("agent-a", "id2")]
    timers = {"agent-a"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1"), ("agent-a", "id2")]


def test_bridge_timer_is_owner_only_when_enabled_and_active():
    # The disabled-timer production case: timer file exists but not enabled ->
    # Hermes row is the live owner. is_owner must be False so main() skips it.
    assert fix.is_owner(True, True) is True         # genuinely running -> removable
    assert fix.is_owner(True, None) is False        # active check failed -> don't touch
    assert fix.is_owner(False, True) is False       # not enabled -> Hermes is the owner
    assert fix.is_owner(False, False) is False      # neither -> not the owner
