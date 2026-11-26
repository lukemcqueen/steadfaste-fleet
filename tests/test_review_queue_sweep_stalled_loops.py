#!/usr/bin/env python3
"""Hermetic tests for the stalled-LOOP drain in agent-review-queue-sweep.py.

Why this exists: the loop scorer's decision is a PURE function of the composite
score (core/governance/loop_scorer.py), with no memory of prior iterations — so a
task that settles at one score re-derives `LOOP` forever and nothing terminates
it. Measured: 2548 unreviewed LOOP cycles, 100% precommit-* artifacts, one
task_id carrying 240 identical rows over 10 days. `closable_stalled_loops()` is
the drain that bounds it, so the boundary it draws is worth pinning:

  * stalled (>= N LOOP cycles at ONE composite) AND old  -> drained
  * recent, even if stalled                              -> NOT drained
  * moving (composite still changing)                    -> NOT drained
  * not a precommit-* artifact                           -> NOT drained
  * already reviewed/overridden                          -> NOT drained
  * a second run                                         -> drains nothing (idempotent)

Every case is built from rows shaped exactly as the producer writes them
(decision string 'LOOP 🔄 — keep iterating', emoji and prose included) rather
than a convenient fixture spelling — the sweep's own _decision_bucket() compares
by CLASS for precisely that reason.
"""
import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SWEEP = REPO_ROOT / "ops" / "scripts" / "manage" / "agent-review-queue-sweep.py"

# The literal string loop_scorer.composite_score() writes for a LOOP decision.
LOOP_DECISION = "LOOP 🔄 — keep iterating"


def _load_sweep():
    """Import the hyphenated script by path (it is not a valid module name)."""
    spec = importlib.util.spec_from_file_location("agent_review_queue_sweep", SWEEP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_review_queue_sweep"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sweep():
    return _load_sweep()


@pytest.fixture()
def con():
    """A minimal loop_cycles table — only the columns the drain reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE loop_cycles (
               id             INTEGER PRIMARY KEY AUTOINCREMENT,
               timestamp      TEXT,
               task_id        TEXT NOT NULL,
               composite      REAL,
               decision       TEXT NOT NULL,
               user_overrode  INTEGER,
               outcome_note   TEXT
           )"""
    )
    return conn


def _add(conn, task_id, composite, when, decision=LOOP_DECISION,
         user_overrode=None, n=1):
    for _ in range(n):
        conn.execute(
            "INSERT INTO loop_cycles (timestamp, task_id, composite, decision, "
            "user_overrode, outcome_note) VALUES (?, ?, ?, ?, ?, '')",
            (when, task_id, composite, decision, user_overrode),
        )
    conn.commit()


def _ts(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _ids(rows):
    return sorted(r["id"] for r in rows)


def test_drains_stalled_and_old(con, sweep):
    """The measured case: 240-odd identical LOOP rows, untouched for months."""
    _add(con, "precommit-hermes-cortex-main/base", 7.20, _ts(60), n=5)
    cutoff = _ts(30)
    assert len(_ids(sweep.closable_stalled_loops(con, cutoff, 3))) == 5


def test_recent_stall_is_preserved(con, sweep):
    """A live investigation must never be closed out from under its author."""
    _add(con, "precommit-somerepo-main/base", 7.20, _ts(1), n=5)
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_moving_task_is_preserved(con, sweep):
    """Same task, composite still changing = real progress, not a stall."""
    for i, comp in enumerate((5.0, 6.1, 7.2, 8.0, 8.9)):
        _add(con, "precommit-somerepo-main/work", comp, _ts(60))
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_non_precommit_task_is_preserved(con, sweep):
    """Scope is deliberately narrow — real work is never swept by this drain."""
    _add(con, "real-feature-work", 7.20, _ts(60), n=5)
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_already_reviewed_is_preserved(con, sweep):
    """Override/review is a human decision — never touched."""
    _add(con, "precommit-somerepo-main/base", 7.20, _ts(60), n=5, user_overrode=1)
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_below_min_iterations_is_preserved(con, sweep):
    """Two identical LOOPs is not yet a stall at min_iterations=3."""
    _add(con, "precommit-somerepo-main/base", 7.20, _ts(60), n=2)
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_non_loop_decision_is_preserved(con, sweep):
    """STOP/MOVE_ON rows are not this drain's business."""
    _add(con, "precommit-somerepo-main/base", 7.20, _ts(60),
         decision="STOP ✓ — goal met, quality acceptable", n=5)
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_only_the_stalled_group_is_drained(con, sweep):
    """A mixed table: exactly the stalled+old precommit group comes back."""
    _add(con, "precommit-stalled/base", 7.20, _ts(60), n=4)
    _add(con, "precommit-recent/base", 7.20, _ts(2), n=4)
    _add(con, "precommit-moving/base", 6.0, _ts(60))
    _add(con, "precommit-moving/base", 7.5, _ts(60))
    _add(con, "precommit-moving/base", 8.5, _ts(60))
    _add(con, "real-task", 7.20, _ts(60), n=4)

    got = _ids(sweep.closable_stalled_loops(con, _ts(30), 3))
    expected = _ids(con.execute(
        "SELECT id FROM loop_cycles WHERE task_id='precommit-stalled/base'"
    ).fetchall())
    assert got == expected
    assert len(got) == 4


def test_drain_is_idempotent(con, sweep):
    """After the drain marks rows, a re-run must find nothing — same contract as
    the rest of the sweep."""
    _add(con, "precommit-somerepo-main/base", 7.20, _ts(60), n=5)
    first = _ids(sweep.closable_stalled_loops(con, _ts(30), 3))
    assert len(first) == 5

    # Mark exactly as close_cycles() does, then re-run.
    note = "auto-swept: stalled LOOP drained"
    con.executemany(
        "UPDATE loop_cycles SET user_overrode=0, outcome_note=? "
        "WHERE id=? AND user_overrode IS NULL",
        [(note, cid) for cid in first],
    )
    con.commit()
    assert _ids(sweep.closable_stalled_loops(con, _ts(30), 3)) == []


def test_default_threshold_matches_alert_threshold(sweep):
    """The drain and the alert must agree on what 'stalled' means, or one of them
    silently reports a set the other never acts on."""
    import inspect
    src = inspect.getsource(sweep.detect_stalled_loops)
    assert "min_iterations=3" in src, "alert threshold moved; realign the drain"
    assert sweep.STALL_MIN_ITERATIONS == 3, (
        "STALL_MIN_ITERATIONS drifted from detect_stalled_loops' alert threshold"
    )


# ── System-level LOOP-rate detector (the diffuse-storm signal) ───────────────
#
# detect_stalled_loops() is PER-TASK: it needs >= min_iterations LOOP rows on ONE
# task_id. The measured storm was DIFFUSE — 2548 stalled rows across 1774
# distinct task_ids, 1.4 rows each — so no single task_id accrued enough to trip
# it, and the storm stayed invisible for ~6 weeks. These tests pin the
# system-level signal that CAN see it, AND pin the blindness it compensates for,
# so the pair cannot silently drift back apart.


def test_rate_sees_the_diffuse_storm_the_per_task_detector_is_blind_to(con, sweep):
    """THE discriminating case: one LOOP row each on 60 distinct task_ids.

    A per-task counter can never reach its threshold here. This asserts that
    blindness explicitly, so an implementation that merely re-counts per-task
    rows cannot pass this test by accident.
    """
    for i in range(60):
        _add(con, "precommit-repo%d-main/base" % i, 7.2, _ts(0))

    assert sweep.detect_stalled_loops(con) == [], "per-task must be blind here"
    assert sweep.loop_rate_in_window(con, window_min=60) == (60, 60)


def test_rate_excludes_rows_older_than_the_window(con, sweep):
    _add(con, "precommit-a/base", 7.2, _ts(0), n=3)   # inside the window
    _add(con, "precommit-b/base", 7.2, _ts(2), n=9)   # 2 days old -> outside
    assert sweep.loop_rate_in_window(con, window_min=60) == (3, 1)


def test_rate_excludes_non_loop_decisions(con, sweep):
    _add(con, "precommit-a/base", 7.2, _ts(0), n=2)
    _add(con, "precommit-b/base", 9.0, _ts(0),
         decision="STOP ✓ — goal met, quality acceptable", n=5)
    assert sweep.loop_rate_in_window(con, window_min=60) == (2, 1)


def test_rate_alert_fires_above_threshold_and_reports_the_spread(con, sweep):
    """30 LOOP rows over 10 task_ids: both numbers must reach the operator —
    the row count is the rate, the DISTINCT count is the spread."""
    for i in range(10):
        _add(con, "precommit-repo%d-main/base" % i, 7.2, _ts(0), n=3)
    msg = sweep.rate_alert(con, window_min=60, threshold=20)
    assert msg is not None
    assert "30" in msg, "row count must be visible"
    assert "10" in msg, "distinct task_id count is the spread signal"


def test_rate_alert_is_silent_below_threshold(con, sweep):
    _add(con, "precommit-a/base", 7.2, _ts(0), n=4)
    assert sweep.rate_alert(con, window_min=60, threshold=20) is None


def test_rate_on_an_empty_table_is_zero_not_an_error(con, sweep):
    assert sweep.loop_rate_in_window(con, window_min=60) == (0, 0)
    assert sweep.rate_alert(con, window_min=60, threshold=20) is None
