#!/usr/bin/env python3
"""The doctor's task-query-scoping check must DISCRIMINATE — both directions.

Operator rule (2026-10-14, restated 2026-11-23): a task query defaults to the
caller's own repo; when the repo cannot be resolved NOTHING is returned; the
fleet-wide view is a special request. Titus reported a Pi session whose task view
showed every repo's tasks — an engine that predates repo scoping passes every
static check while serving another repo's work as the agent's own.

So the check drives the deployed engine's own default query. A checker that
marks every candidate identically is a decoration: these tests feed it an engine
that answers with ROWS (must FAIL), one that answers "no tasks" (must PASS), and
the cannot-look cases (must WARN as COULD NOT VERIFY, never PASS).

Run: python3 -m pytest tests/test_task_query_scoping.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "ops" / "scripts" / "manage" / "fleet_doctor" / "task_integrity.py"


def _load():
    spec = importlib.util.spec_from_file_location("task_integrity_probe_test", MOD)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["task_integrity_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeResults:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail, fix=None):
        self.checks.append({"name": name, "status": status,
                            "detail": detail, "fix": fix})


def _one(mod, probe_output, probe_rc=0, deployed_runner=True, monkeypatch=None,
         tmp_path=None):
    res = FakeResults()
    assert monkeypatch is not None
    assert tmp_path is not None
    if deployed_runner:
        deploy = tmp_path / "deploy"
        (deploy / "scripts").mkdir(parents=True, exist_ok=True)
        (deploy / "scripts" / "task-db.py").write_text("# fake deployed engine\n")
    else:
        deploy = tmp_path / "no-such-deploy"
    monkeypatch.setattr(mod, "_deploy", lambda: deploy)
    monkeypatch.setattr(mod, "_probe_default_scope",
                        lambda runner: (probe_output, probe_rc))
    mod.check_task_query_scoping(res)
    assert len(res.checks) == 1, res.checks
    return res.checks[0]


def test_an_engine_that_returns_rows_with_no_repo_FAILS(monkeypatch, tmp_path):
    """THE defect: the default query answering with another repo's tasks.

    Both real row shapes are driven (full uuid and the 8-char prefix) — a
    detector that matches neither turns the FAIL into a silent COULD NOT
    VERIFY, which is how this class survived every doctor run."""
    for row in ("f8988944-ef87-40bd-90b4-7ca666584f87     esther     fleet",
                "f8988944 ef8740bd esther"):
        rows = "ID                                     Agent\n" + row + "\n"
        entry = _one(_load(), rows, monkeypatch=monkeypatch, tmp_path=tmp_path)
        assert entry["status"] == "FAIL", (row, entry)
        assert "returned 1 row(s)" in entry["detail"], entry
        assert "fleet-update.sh" in (entry["fix"] or ""), entry


def test_the_no_tasks_answer_is_the_PASS(monkeypatch, tmp_path):
    entry = _one(_load(),
                 "No tasks found.\ntask_list: repo unknown — none of: ...",
                 monkeypatch=monkeypatch, tmp_path=tmp_path)
    assert entry["status"] == "PASS", entry


def test_no_output_is_could_not_verify_never_a_pass(monkeypatch, tmp_path):
    entry = _one(_load(), "", probe_rc=3, monkeypatch=monkeypatch,
                 tmp_path=tmp_path)
    assert entry["status"] == "WARN", entry
    assert "COULD NOT VERIFY" in entry["detail"], entry


def test_unrecognised_output_is_could_not_verify(monkeypatch, tmp_path):
    """A format change must not read as either verdict."""
    entry = _one(_load(), "the store is unreachable", monkeypatch=monkeypatch,
                 tmp_path=tmp_path)
    assert entry["status"] == "WARN", entry
    assert "COULD NOT VERIFY" in entry["detail"], entry


def test_a_missing_deployed_runner_is_reported_not_skipped(monkeypatch, tmp_path):
    entry = _one(_load(), "", deployed_runner=False, monkeypatch=monkeypatch,
                 tmp_path=tmp_path)
    assert entry["status"] == "WARN", entry
    assert "COULD NOT VERIFY" in entry["detail"], entry


def test_the_probe_strips_the_session_repo_and_runs_in_a_non_repo_dir(monkeypatch):
    """The probe's environment IS the probe: with FLEET_SESSION_REPO left in
    place, or a cwd inside a repo, the run measures the invoker — not the
    engine's default."""
    mod = _load()
    captured = {}

    class FakeProc:
        stdout = "No tasks found."
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kw):
        captured.update(kw)
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.os, "environ",
                        {"FLEET_SESSION_REPO": "/some/repo", "PATH": "/usr/bin"})
    out, rc = mod._probe_default_scope(Path("/tmp/deploy/task-db.py"))
    assert out.startswith("No tasks found"), out
    env = captured.get("env") or {}
    assert "FLEET_SESSION_REPO" not in env, env
    assert env.get("PATH") == "/usr/bin", env
    assert captured["cmd"][0] == "python3", captured["cmd"]
    assert "task-db.py" in captured["cmd"][1], captured["cmd"]
    assert captured["cmd"][2] == "list", captured["cmd"]


def test_the_probe_reports_an_unrunnable_engine_as_could_not_verify(monkeypatch):
    mod = _load()

    def boom(*a, **kw):
        raise OSError("no interpreter")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    out, rc = mod._probe_default_scope(Path("/tmp/deploy/task-db.py"))
    assert (out, rc) == ("", 3), (out, rc)
