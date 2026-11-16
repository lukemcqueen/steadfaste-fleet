#!/usr/bin/env python3
"""check_task_db collapses 6 `python3 task-db.py` subprocesses into ONE
`task-db.py doctor-probe` call (a single in-process roundtrip). This
test pins the JSON -> verdict mapping in checks._task_db_verdicts_from_probe
BOTH directions with the EXACT strings the old 6-subprocess path emitted, so
the perf change cannot drift the check's verdict text.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops" / "scripts" / "manage"))

from fleet_doctor import checks  # noqa: E402

probe = checks._task_db_verdicts_from_probe


def _healthy():
    return (
        '{"ok": true, "results": ['
        '{"check": "connectivity", "part": "connectivity", "status": "PASS", '
        '"msg": "Postgres reachable, 2 pending item(s)"}, '
        '{"check": "write_probe", "part": "cleanup", "status": "PASS", '
        '"msg": "add \\u2192 start \\u2192 complete \\u2192 archive roundtrip OK"}'
        "]}"
    )


def test_healthy_probe_emits_both_pass_rows_with_exact_text():
    rows = probe(_healthy())
    assert rows == [
        ("Task DB connectivity", "PASS", "Postgres reachable, 2 pending item(s)"),
        ("Task DB write-probe", "PASS", "add \u2192 start \u2192 complete \u2192 archive roundtrip OK"),
    ]


def test_no_output_is_connectivity_fail_no_output_message():
    rows = probe("")
    assert rows == [(
        "Task DB connectivity", "FAIL",
        "task-db.py pending returned no output",
        "Check mycortex Postgres is running: "
        "sg docker -c 'docker ps | grep mycortex-postgres'",
    )]


def test_non_json_output_is_connectivity_fail_invalid_json():
    rows = probe("(timed out)")
    assert rows[0][0] == "Task DB connectivity"
    assert rows[0][1] == "FAIL"
    assert rows[0][2] == "task-db.py output not valid JSON: (timed out)"
    assert "docker exec mycortex-postgres psql" in rows[0][3]


def test_add_failure_is_write_probe_fail_add_message_with_schema_remedy():
    out = ('{"ok": false, "results": ['
           '{"check": "write_probe", "part": "add", "status": "FAIL", '
           '"msg": "task-db.py add did not confirm: db write failed"}]}')
    rows = probe(out)
    assert rows == [(
        "Task DB write-probe", "FAIL",
        "task-db.py add did not confirm: db write failed",
        "Schema may be missing/read-only — run fleet-update.sh, check tasks.schema_version",
    )]


def test_readback_failure_is_write_probe_fail_rls_message_with_rls_remedy():
    out = ('{"ok": false, "results": ['
           '{"check": "write_probe", "part": "readback", "status": "FAIL", '
           '"msg": "probe row added but not visible in task-db.py list — '
           "RLS or list path broken\"}]}")
    rows = probe(out)
    assert rows == [(
        "Task DB write-probe", "FAIL",
        "probe row added but not visible in task-db.py list — RLS or list path broken",
        "Check tasks.tasks RLS policies and tasks.task_list()",
    )]
