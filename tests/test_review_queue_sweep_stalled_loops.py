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
