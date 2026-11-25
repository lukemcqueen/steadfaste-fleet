"""Tests for fleet_doctor.checks.check_collector_health.

Why this exists: the agent-learning-collector is the fleet's lesson pipeline.
Its `last_run` epoch advances ONLY on a successful send — so a cron that fires
but silently fails (auth 401, network error, swallowed subprocess rc) leaves
the state file frozen while the schedule keeps running. That is the
2026-11-19 blind window: five days of the cron wrapper recording "silent",
the collector writing nothing, and the fleet receiving zero new learning
reports — discovered only after the fact, in a pitfall paragraph.

The check reads the state file's `last_run` and calls PASS / WARN / FAIL /
SKIP based on its age. This test drives every branch against a tmpdir state
file so a future refactor cannot quietly change the thresholds or turn a
SKIP (torn read) into a FAIL.

Concurrency contract pinned here too: the collector writes the state file
with a plain `write_text` (no tmp+rename), so a reader can observe a partial
file mid-write. The check must therefore report a JSON parse error as SKIP,
not FAIL — a torn read is not a real failure of the collector.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))

from fleet_doctor.checks import (  # noqa: E402
    _LEARNING_COLLECTOR_MAX_AGE_FRESH_H,
    _LEARNING_COLLECTOR_MAX_AGE_WARN_H,
    check_collector_health,
)
from fleet_doctor.results import Results  # noqa: E402


LABEL = "Learning collector freshness"


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run(state_path: Path) -> dict:
    """Run the check and return the single row it produced."""
    res = Results()
    check_collector_health(res, state_path=state_path)
    rows = [c for c in res.checks if c["name"] == LABEL]
    assert len(rows) == 1, f"expected exactly one row named {LABEL!r}, got {len(rows)}"
    return rows[0]


def test_fresh_state_passes(tmp_path):
    """A last_run within the fresh threshold is a PASS, not a warning."""
    p = tmp_path / "state.json"
    _write_state(p, {"last_run": time.time() - 3600})  # 1h ago
    row = _run(p)
    assert row["status"] == "PASS", row
    assert "1.0h ago" in row["detail"], row["detail"]


def test_warns_when_between_fresh_and_warn_threshold(tmp_path):
    """Between the two thresholds, the check must WARN — not PASS and not FAIL."""
    mid_h = (_LEARNING_COLLECTOR_MAX_AGE_FRESH_H + _LEARNING_COLLECTOR_MAX_AGE_WARN_H) / 2
    p = tmp_path / "state.json"
    _write_state(p, {"last_run": time.time() - mid_h * 3600})
    row = _run(p)
    assert row["status"] == "WARN", row
    assert "missed cycles" in row["detail"].lower() or "missed" in row["detail"].lower(), row["detail"]
    # A WARN must carry a suggestion — the operator needs a next step.
    assert row["fix"], "WARN row must carry a fix suggestion"


def test_fails_when_past_warn_threshold(tmp_path):
    """Past the warn threshold is the blind-window failure mode: FAIL."""
    p = tmp_path / "state.json"
    _write_state(p, {"last_run": time.time() - (_LEARNING_COLLECTOR_MAX_AGE_WARN_H + 4) * 3600})
    row = _run(p)
    assert row["status"] == "FAIL", row
    assert "blind" in row["detail"].lower(), row["detail"]
    assert row["fix"], "FAIL row must carry a fix suggestion"


def test_missing_state_file_skips(tmp_path):
    """A missing state file is a SKIP (the collector never ran here), not a FAIL.

    This distinction matters: a host that does not run the collector must not
    be reported as failing the doctor.
    """
    row = _run(tmp_path / "absent.json")
    assert row["status"] == "SKIP", row
    assert "absent" in row["detail"].lower() or "never completed" in row["detail"].lower(), row["detail"]


def test_malformed_json_skips_not_fails(tmp_path):
    """A torn read (the collector writes non-atomically) must SKIP, never FAIL.

    Pinned here as a contract: turning a parse error into a FAIL would make
    the doctor flap every time the collector writes while the doctor reads.
    """
    p = tmp_path / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"last_run": 17', encoding="utf-8")  # truncated mid-write
    row = _run(p)
    assert row["status"] == "SKIP", row
    assert "json" in row["detail"].lower() or "torn" in row["detail"].lower(), row["detail"]


def test_missing_last_run_skips(tmp_path):
    """A state file without a numeric last_run is SKIP, not FAIL.

    The collector's first write may predate the key being added; a missing
    key is not evidence the pipeline is broken.
    """
    p = tmp_path / "state.json"
    _write_state(p, {"other_key": 1})
    row = _run(p)
    assert row["status"] == "SKIP", row
    assert "last_run" in row["detail"], row["detail"]


def test_non_numeric_last_run_skips(tmp_path):
    """A string or None last_run must SKIP (parse or corruption), not FAIL."""
    p = tmp_path / "state.json"
    _write_state(p, {"last_run": "yesterday"})
    row = _run(p)
    assert row["status"] == "SKIP", row
    assert "last_run" in row["detail"], row["detail"]


def test_future_last_run_skips_as_clock_skew(tmp_path):
    """A last_run in the future is clock skew (NTP correction, host resume),
    not a collector failure — must SKIP, never FAIL."""
    p = tmp_path / "state.json"
    _write_state(p, {"last_run": time.time() + 2 * 3600})  # 2h in the future
    row = _run(p)
    assert row["status"] == "SKIP", row
    assert "future" in row["detail"].lower() or "skew" in row["detail"].lower(), row["detail"]


def test_thresholds_are_ordered():
    """Sanity: fresh must be strictly less than warn, else the elif chain breaks."""
    assert _LEARNING_COLLECTOR_MAX_AGE_FRESH_H < _LEARNING_COLLECTOR_MAX_AGE_WARN_H


def test_check_is_read_only(tmp_path):
    """The check must not modify the state file it inspects."""
    p = tmp_path / "state.json"
    _write_state(p, {"last_run": time.time() - 3600})
    before = p.read_bytes()
    _run(p)
    after = p.read_bytes()
    assert before == after, "check_collector_health must be read-only"
