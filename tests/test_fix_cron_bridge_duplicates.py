#!/usr/bin/env python3
"""fix-cron-bridge-duplicates.py — the pure double-owner detection.

Pins find_doubles() in BOTH directions: it returns exactly the bridged-AND-
hermes-row crons (with ids), and it stays quiet when a cron is only bridged or
only a Hermes row — a single-owner state must NOT be touched. This is the
ROOT-side detector; the destructive removal (hermes cron remove) is exercised
end-to-end on the host by the doctor's Cron bridge ownership check going 0-fail.
"""
from __future__ import annotations

import importlib.util
import sys
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


def test_double_ownership_returns_name_and_id():
    jobs = [_j("agent-a", "id1"), _j("agent-b", "id2"), _j("orch-c", "id3")]
    timers = {"agent-a", "orch-c"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1"), ("orch-c", "id3")]


def test_only_bridged_without_row_is_not_a_double():
    # systemd timer exists but no Hermes row -> correctly migrated, must stay.
    jobs = [_j("agent-a", "id1")]
    timers = {"agent-a", "agent-b"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1")]


def test_hermes_row_without_timer_is_not_a_double():
    # a normal Hermes scheduler job (no bridge timer) must never be removed.
    jobs = [_j("agent-a", "id1"), _j("agent-llm-job", "id9")]
    timers = {"agent-a"}
    assert fix.find_doubles(jobs, timers) == [("agent-a", "id1")]


def test_no_doubles_is_empty():
    jobs = [_j("agent-a", "id1")]
    timers = {"agent-b"}
    assert fix.find_doubles(jobs, timers) == []


def test_missing_id_row_is_skipped():
    jobs = [{"name": "agent-a", "no_agent": True}]  # no id -> jobs_by_name drops it
    timers = {"agent-a"}
    assert fix.find_doubles(jobs, timers) == []
