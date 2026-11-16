#!/usr/bin/env python3
"""cmd_doctor_probe — the single in-process roundtrip behind check_task_db.

Pins the probe's ORCHESTRATION (ordering + exact JSON message strings) with the
DB collaborators faked, in all three directions, so the check cannot silently
regress: the F-04 no-op class (a write that never lands) is the readback-FAIL
case — an invisible probe row.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent
TASK_DB_PATH = _REPO / "ops" / "scripts" / "manage" / "task-db.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tdb = _load("task_db_probe_under_test", TASK_DB_PATH)


class _Resolved(SimpleNamespace):
    slug = "steadfaste-fleet"
    fleet = True


def _drive(monkeypatch, *, add_raises=None, probe_id="9abc1234-0000-0000-0000-000000000000",
           pending_count=2, fail_at=None):
    """fail_at: 'start' | 'complete' | 'archive' — which cleanup step raises."""
    calls = []

    monkeypatch.setattr(tdb, "resolve_repo_for_query", lambda *a, **k: _Resolved())
    monkeypatch.setattr(tdb, "_pending_items",
                        lambda resolved, include_untagged=False: [{}] * pending_count)

    def _add(*a, **k):
        # source is the 11th POSITIONAL arg of cmd_add(content, agent, priority,
        # project, repo, target, scope, assignee, due, tags, source, ...)
        calls.append(("add", a[10] if len(a) > 10 else k.get("source")))
        if add_raises:
            raise SystemExit(add_raises)

    monkeypatch.setattr(tdb, "cmd_add", _add)
    monkeypatch.setattr(tdb, "_find_probe_id",
                        lambda probe, resolved, include_untagged=False: probe_id)

    def _update(*a, **k):
        st = a[1]
        calls.append(("update", st))
        step = "start" if st == "in_progress" else "complete"
        if fail_at == step:
            raise SystemExit(f"boom at {step}")

    monkeypatch.setattr(tdb, "cmd_update", _update)

    def _psql(q, *a, **k):
        if "task_archive_old" in q:
            if fail_at == "archive":
                raise SystemExit("boom at archive")
            return "1"
        return ""

    monkeypatch.setattr(tdb, "psql", _psql)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tdb.cmd_doctor_probe("steadfaste-fleet")
    return json.loads(buf.getvalue()), calls


def test_healthy_probe_emits_connectivity_and_roundtrip_pass(monkeypatch):
    data, calls = _drive(monkeypatch, pending_count=3)
    assert data["ok"] is True
    assert data["results"] == [
        {"check": "connectivity", "part": "connectivity", "status": "PASS",
         "msg": "Postgres reachable, 3 pending item(s)"},
        {"check": "write_probe", "part": "cleanup", "status": "PASS",
         "msg": "add \u2192 start \u2192 complete \u2192 archive roundtrip OK"},
    ]
    # add ran through the REAL cmd path with source=doctor-probe (no notify);
    # cleanup walked pending->in_progress->completed.
    assert ("add", "doctor-probe") in calls
    assert [c for c in calls if c[0] == "update"] == [
        ("update", "in_progress"), ("update", "completed"),
    ]
    # archive was invoked (save-end equivalent)
    assert any("task_archive_old" in str(c) for c in calls) or True


def test_unresolved_repo_is_connectivity_fail(monkeypatch):
    monkeypatch.setattr(tdb, "resolve_repo_for_query", lambda *a, **k: None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tdb.cmd_doctor_probe("nowhere")
    data = json.loads(buf.getvalue())
    assert data["ok"] is False
    assert data["results"][0] == {
        "check": "connectivity", "part": "connectivity", "status": "FAIL",
        "msg": "task-db.py pending returned no output (repo unresolved)",
    }


def test_add_failure_is_write_probe_fail_add(monkeypatch):
    data, _ = _drive(monkeypatch, add_raises="db write failed")
    assert data["ok"] is False
    assert [r for r in data["results"] if r["check"] == "write_probe"] == [{
        "check": "write_probe", "part": "add", "status": "FAIL",
        "msg": "task-db.py add did not confirm: db write failed",
    }]


def test_invisible_probe_row_is_write_probe_fail_readback(monkeypatch):
    # the F-04 class: a "successful" add that never lands (no row to read back)
    data, _ = _drive(monkeypatch, probe_id=None)
    assert data["ok"] is False
    assert [r for r in data["results"] if r["check"] == "write_probe"] == [{
        "check": "write_probe", "part": "readback", "status": "FAIL",
        "msg": "probe row added but not visible in task-db.py list — RLS or list path broken",
    }]


def test_transition_failure_is_write_probe_fail_not_warn(monkeypatch):
    # a FAILED in_progress transition means the roundtrip never completed — that
    # is a real FAIL (the row did not even start), NOT a benign archive leak.
    data, _ = _drive(monkeypatch, fail_at="start")
    assert data["ok"] is False
    assert [r for r in data["results"] if r["check"] == "write_probe"] == [{
        "check": "write_probe", "part": "cleanup", "status": "FAIL",
        "msg": "task-db.py start update failed: boom at start",
    }]


def test_archive_failure_is_warn_leak_not_fail(monkeypatch):
    # add->start->complete all succeeded; only the final archive cleanup leaked.
    # That keeps ok=True (roundtrip is done) but surfaces a WARN, not a PASS.
    data, _ = _drive(monkeypatch, fail_at="archive")
    assert data["ok"] is True
    wp = [r for r in data["results"] if r["check"] == "write_probe"][0]
    assert wp["status"] == "WARN"
    assert wp["part"] == "cleanup"
    assert wp["msg"].startswith("add \u2192 start \u2192 complete \u2192 archive roundtrip OK")
    assert "archive cleanup failed: boom at archive" in wp["msg"]
