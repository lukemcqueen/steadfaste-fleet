"""Tests for review-volume-metrics.py — the per-host review-volume instrument.

The script reads a loop-governance DB read-only and reports review counts by
judge model and by day. Tests drive the real module's functions against a
THROWAWAY temp DB (never the live store), both directions:

  present  -> counts by model + per-day window render correctly
  absent   -> a missing table is reported as absent, never as a zero

The module is importable with no side effects (argparse lives in main()).
"""

import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "scripts" / "manage" / "review-volume-metrics.py"


def _load_rvm():
    """The filename carries dashes (deploy convention) so plain import fails:
    load the module by spec like the sibling dogfood scripts do."""
    spec = importlib.util.spec_from_file_location("review_volume_metrics", SCRIPT)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def rvm():
    return _load_rvm()


@pytest.fixture()
def tmp_db(tmp_path):
    """A loop-governance-shaped DB: blob_reviews + adversarial_reviews + rows."""
    db = tmp_path / "loop-governance.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE blob_reviews (
            blob_sha TEXT, verdict TEXT, findings_json TEXT, summary TEXT,
            reviewer_id TEXT, reviewer_model TEXT, ts TEXT, rules_rev TEXT
        );
        CREATE TABLE adversarial_reviews (
            review_id TEXT, cycle_id INTEGER, reviewer_id TEXT,
            reviewer_model TEXT, verdict TEXT, findings_json TEXT,
            summary TEXT, ts TEXT, fingerprint TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO blob_reviews "
        "(blob_sha, verdict, reviewer_model, ts) VALUES (?,?,?,?)",
        [
            ("a", "CLEAN", "deepseek/deepseek-v4-pro-0813", "2026-11-09T09:00:00Z"),
            ("b", "CLEAN", "deepseek/deepseek-v4-pro-0813", "2026-11-09T10:00:00Z"),
            ("c", "FINDINGS", "deepseek/deepseek-v4-pro", "2026-11-08T09:00:00Z"),
        ],
    )
    con.executemany(
        "INSERT INTO adversarial_reviews "
        "(review_id, cycle_id, reviewer_model, verdict, ts) VALUES (?,?,?,?,?)",
        [
            ("r1", 1, "deepseek/deepseek-v4-pro-0813", "CLEAN", "2026-11-09T11:00:00Z"),
        ],
    )
    con.commit()
    con.close()
    return db


def test_counts_by_model_and_day(tmp_db, monkeypatch, rvm):
    """Present table: per-model counts and per-day window match the fixture."""
    monkeypatch.setenv("AGENT_NAME", "testagent")
    summary = rvm.build_summary("testagent", tmp_db, window_days=5)
    assert summary["agent"] == "testagent"
    blob = summary["blob_reviews"]
    assert blob["present"] is True
    assert blob["total"] == 3
    by_model = {r["model"]: r["n"] for r in blob["by_model"]}
    assert by_model == {
        "deepseek/deepseek-v4-pro-0813": 2,
        "deepseek/deepseek-v4-pro": 1,
    }
    per_day = {r["date"]: r["n"] for r in blob["per_day"]}
    assert per_day == {"2026-11-09": 2, "2026-11-08": 1}
    adv = summary["adversarial_reviews"]
    assert adv["present"] is True
    assert adv["total"] == 1


def test_missing_table_reported_absent_not_zero(tmp_path, rvm):
    """An older host without adversarial_reviews is 'absent', never 0 reviews."""
    db = tmp_path / "loop-governance.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE blob_reviews (blob_sha TEXT, reviewer_model TEXT, ts TEXT)"
    )
    con.execute(
        "INSERT INTO blob_reviews VALUES ('x','deepseek/deepseek-v4-pro','2026-11-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    summary = rvm.build_summary("testagent", db, window_days=5)
    assert summary["blob_reviews"]["present"] is True
    assert summary["adversarial_reviews"]["present"] is False
    assert summary["adversarial_reviews"]["total"] is None
    assert summary["adversarial_reviews"]["by_model"] == []
    # A pre-ledger host: review_calls absent, never a zero
    assert summary["review_calls"]["present"] is False
    assert summary["review_calls"]["total"] is None


def test_ledger_reads_measured_tokens(tmp_path, rvm):
    """review_calls sums the MEASURED tokens by source and by day."""
    db = tmp_path / "loop-governance.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE review_calls (
            review_id TEXT PRIMARY KEY, source TEXT NOT NULL,
            reviewer_model TEXT NOT NULL, reviewer_id TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER,
            reasoning_tokens INTEGER, ts TEXT NOT NULL
        );
        """
    )
    con.executemany(
        "INSERT INTO review_calls (review_id, source, reviewer_model, prompt_tokens,"
        " completion_tokens, reasoning_tokens, ts) VALUES (?,?,?,?,?,?,?)",
        [
            ("a", "close", "deepseek/deepseek-v4-pro-0813", 3456, 789, 200,
             "2026-11-09T09:00:00Z"),
            ("b", "close", "deepseek/deepseek-v4-pro-0813", 1000, 500, 0,
             "2026-11-09T10:00:00Z"),
            ("c", "plan", "deepseek/deepseek-v4-pro", 2000, 300, 0,
             "2026-11-08T09:00:00Z"),
        ],
    )
    con.commit()
    con.close()
    summary = rvm.build_summary("testagent", db, window_days=5)
    ledger = summary["review_calls"]
    assert ledger["present"] is True
    assert ledger["total"] == 3
    by_source = {r["source"]: r for r in ledger["by_source"]}
    assert by_source["close"]["prompt_tokens"] == 4456      # 3456 + 1000
    assert by_source["close"]["completion_tokens"] == 1289  # 789 + 500
    assert by_source["plan"]["prompt_tokens"] == 2000
    per_day = {r["date"]: r for r in ledger["per_day"]}
    assert per_day["2026-11-09"]["n"] == 2
    assert per_day["2026-11-09"]["prompt_tokens"] == 4456


def test_cli_json_summary_roundtrip(tmp_db, monkeypatch):
    """The EXEC_RESULT contract: --json-summary prints ONE valid JSON object."""
    monkeypatch.setenv("AGENT_NAME", "testagent")
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(tmp_db),
         "--json-summary"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    payload = json.loads(out.stdout)
    assert payload["agent"] == "testagent"
    assert payload["blob_reviews"]["total"] == 3
    assert payload["adversarial_reviews"]["total"] == 1
    assert "error" not in payload


def test_exit_2_when_db_missing(tmp_path, monkeypatch):
    """A missing DB is 'no data' (exit 2), never an empty success."""
    monkeypatch.setenv("AGENT_NAME", "testagent")
    gone = tmp_path / "no-such.db"
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(gone), "--json-summary"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 2
    assert "no loop-governance DB" in out.stderr
