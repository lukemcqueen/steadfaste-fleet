#!/usr/bin/env python3
"""agent-review-queue-sweep.py - O5/G-5 review-queue stale-sweep + triage automation.

Measures the loop-governance unreviewed cycle backlog, auto-closes stale
STOP/auto-accepted cycles (decision STOP-check, unreviewed, older than the
age cutoff, clean outcome note), auto-cleans precommit test-fixture artifacts
(temp-repo commits scored into the PROD DB by the global hook — see
tests/conftest.py hermeticity contract), and surfaces everything else
(LOOP, MOVE ON, hard-fail STOP-x, PENDING) for human review. Never touches
overrides.

Safety contract (mirrors the MCP feedback_accept semantics):
  * only rows with user_overrode IS NULL are candidates
  * only the exact auto-accepted STOP-check decision qualifies
  * outcome notes containing fail/error/blocked/warn/escalat/issue/bug block
    auto-close
  * STOP-x (hard fail) and MOVE ON are surfaced, never closed
  * precommit test-fixture cleanup matches only the detached-HEAD / known
    test-repo task_id signatures (zero false positives on real commits) and
    marks them overridden (reversible), never deletes
  * reruns are idempotent: already-accepted rows are skipped

Exit protocol (no_agent cron watchdog pattern):
  * clean run, nothing notable -> no stdout, exit 0 (nothing delivered)
  * anomalies (PENDING leak older than 24h, hard-fail unreviewed older than
    7d) -> compact report on stdout, exit 1 (delivered as alert)

Evidence: every run appends one JSON line to the run log under the Hermes
Cortex state dir, and the latest totals are written to a state JSON.
"""

import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys


# ── Stalled-LOOP drain (2026-11-26) ──────────────────────────────────────────
# The loop scorer's decision is a PURE function of the composite score
# (core/governance/loop_scorer.py: `elif comp >= t["loop"]: "LOOP 🔄 ..."`), with
# no memory of prior iterations — so a task that settles at one score re-derives
# LOOP forever and NOTHING terminates it. Measured cause of the review backlog:
# 2548 unreviewed LOOP cycles, 100% precommit-* artifacts, one task_id carrying
# 240 identical rows (same decision, same composite 7.20, empty note) over 10
# days at ~18s spacing.
#
# STALL_MIN_ITERATIONS: how many LOOP cycles at ONE (task_id, composite) count as
# stalled rather than merely unfinished. Kept EQUAL to detect_stalled_loops' own
# alert threshold (3) so "what the alert reports" and "what the drain clears" are
# the same set — a lower drain threshold than the alert would silently discard
# material the alert never surfaced, and a higher one would leave reported stalls
# accumulating forever.
# LOOP_DRAIN_DAYS_DEFAULT: age gate for the drain. Conservative by default: only
# cycles nobody has looked at for a month are drained, so a live investigation is
# never closed out from under its author.
STALL_MIN_ITERATIONS = 3
LOOP_DRAIN_DAYS_DEFAULT = 30.0

# ── System-level LOOP-rate detector (2026-11-26) ─────────────────────────────
# detect_stalled_loops() is PER-TASK: it needs >= min_iterations LOOP rows on ONE
# task_id. The backlog this was written for was DIFFUSE — 2548 stalled LOOP rows
# spread over 1774 distinct task_ids, 1.4 rows each — so no single task_id ever
# accrued enough rows to trip the per-task alert, and the storm stayed invisible
# for ~6 weeks while the table grew. A rate measured across the WHOLE fleet in a
# short window is the only shape that sees a spread storm.
#
# LOOP_RATE_ALERT_THRESHOLD is deliberately provisional and should be re-tuned
# from recorded numbers rather than by feel. It is NOT derived from the storm
# that prompted this work, because that storm was SLOW AND STEADY rather than
# fast: 1684 LOOP rows across 31 days (~2.3/hour fleet-wide) at its October
# peak. A threshold set low enough to catch that would also fire on ordinary
# LOOP traffic, and no fleet baseline for legitimate LOOP volume has been
# recorded yet. So this reports the rate on EVERY run (accumulating the baseline
# in the state file) and alerts only on a rate well above normal — it catches a
# fast storm today and earns the data to catch a slow one later.
LOOP_RATE_WINDOW_MIN = 60
LOOP_RATE_ALERT_THRESHOLD = 40


def _state_dir():
    override = os.environ.get("REVIEW_STATE_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".steadfaste-fleet", "state")


def _db_file():
    override = os.environ.get("REVIEW_DB_PATH")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".steadfaste-fleet", "data", "loop-governance.db")


def _run_log():
    return os.path.join(_state_dir(), "review-queue-sweep.log")


def _state_json():
    return os.path.join(_state_dir(), "review-queue-sweep-state.json")


STOP_OK = "STOP \u2713"
NEGATIVE_MARKERS = (
    "fail", "error", "blocked", "warn", "escalat", "issue", "bug",
    "rollback", "regress", "stuck", "unresolved",
)

# Precommit test-fixture task_id signatures (slice 74f2ac46). The global git
# core.hookspath fires pre-commit-score on EVERY commit, including commits in
# temp/test repos; those rows land in the PROD loop-governance DB with
# task_id "precommit-<testrepo>-<branch>/<msg>". Two reliable signatures:
#   1. detached-HEAD: "-HEAD" (uppercase) in the task_id. Real commit-message
#      slugs are lowercased by the hook (tr '[:upper:]' '[:lower:]'), so
#      uppercase HEAD can only come from BRANCH=HEAD — the detached-HEAD state
#      pytest temp repos commit in. Zero false positives on real commits.
#   2. known test-repo names (master/feat-branch variants of the same repos).
_FIXTURE_KNOWN_PREFIXES = (
    "precommit-repo-", "precommit-commitme-", "precommit-dry-",
    "precommit-existing-", "precommit-idem-", "precommit-noagents-",
    "precommit-client-repo-", "precommit-enforcer-test-proj",
    "precommit-ext-repo-test", "precommit-hook-image-test",
    "precommit-tdd-gate-test", "precommit-test_",
    # STALE cycle-name literals: precommit-hc-scope-test / precommit-nonhc-test
    # are historical governance-DB test-cycle names (from the hc-era scope tests)
    # — kept verbatim so the sweep still matches those rows.
    "precommit-hc-scope-test", "precommit-hooktest", "precommit-nonhc-test",
    "precommit-omz-sim-test", "precommit-project-repo-test",
    "precommit-sleak-test", "precommit-tdd-final", "precommit-tdd-no-infra",
    "precommit-steadfaste-fleet-private-",
)


def _is_fixture_task(task_id):
    """True when a loop_cycles task_id is a precommit test-fixture artifact."""
    if not task_id or not task_id.startswith("precommit-"):
        return False
    if "-HEAD" in task_id:
        return True
    return task_id.startswith(_FIXTURE_KNOWN_PREFIXES)


def _connect():
    con = sqlite3.connect(_db_file(), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _live_lock_task_ids():
    """Map live governance lock files to the session ids they protect."""
    live = set()
    pattern = os.path.join(_state_dir(), ".governance-*.json")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            sid = data.get("session_id") or data.get("session")
            if sid:
                live.add(str(sid))
            base = os.path.basename(path)
            # fallback: lock filenames embed the session id after the prefix
            if base.startswith(".governance-"):
                live.add(base[len(".governance-"):].rsplit(".json", 1)[0])
        except Exception:
            continue
    return live


def _decision_bucket(decision):
    if decision is None:
        return "unknown"
    d = str(decision)
    if d.startswith(STOP_OK):
        return "stop_ok"
    if d.startswith("STOP"):
        return "stop_fail"
    if d.startswith("LOOP"):
        return "loop"
    if d.startswith("MOVE"):
        return "move_on"
    if d.startswith("PENDING"):
        return "pending"
    return "other"


def _note_is_clean(note):
    if not note:
        return True
    low = str(note).lower()
    return not any(m in low for m in NEGATIVE_MARKERS)


def measure(con, cutoff_ts):
    rows = con.execute(
        "SELECT id, task_id, decision, outcome_note, timestamp, session_id "
        "FROM loop_cycles WHERE user_overrode IS NULL ORDER BY id"
    ).fetchall()
    buckets = {}
    closable = []
    surfacing = []
    for r in rows:
        b = _decision_bucket(r["decision"])
        buckets[b] = buckets.get(b, 0) + 1
        if b == "stop_ok" and r["timestamp"] < cutoff_ts and _note_is_clean(r["outcome_note"]):
            closable.append(r)
        else:
            surfacing.append(r)
    return buckets, closable, surfacing


def closable_stalled_loops(con, cutoff_ts, min_iterations=STALL_MIN_ITERATIONS):
    """LOOP cycles that are provably stalled and past the age gate.

    "Stalled" is measured, not asserted: the (task_id, composite) group must
    already carry >= min_iterations LOOP cycles, i.e. the same task settled at
    the same score again and again with no progress. A task with a single bad
    LOOP, or one whose score is still moving, is never returned.

    Scope is deliberately narrow — precommit-* artifacts only. Those are minted
    by the global git hook on every commit, so an unproductive repo can emit
    hundreds of identical LOOP rows (measured: 240 for one task_id over 10 days,
    all at composite 7.20). The scorer's decision is a pure function of the
    composite, so it re-derives LOOP forever and nothing terminates it.

    Safety contract matches the rest of the sweep: only user_overrode IS NULL
    (never a human override), never deletes, idempotent — the next run finds
    nothing. Returns rows carrying just `id`, which is all close_cycles reads.
    """
    return con.execute("""
        SELECT id FROM loop_cycles
        WHERE user_overrode IS NULL
          AND task_id LIKE 'precommit-%'
          AND decision LIKE 'LOOP%'
          AND timestamp < ?
          AND (task_id, composite) IN (
              SELECT task_id, composite FROM loop_cycles
              WHERE user_overrode IS NULL
                AND task_id LIKE 'precommit-%'
                AND decision LIKE 'LOOP%'
              GROUP BY task_id, composite
              HAVING COUNT(*) >= ?
          )
        ORDER BY id
    """, (cutoff_ts, min_iterations)).fetchall()


def close_cycles(con, closable, note):
    ids = [r["id"] for r in closable]
    if not ids:
        return 0
    con.executemany(
        "UPDATE loop_cycles SET user_overrode=0, outcome_note=? WHERE id=? AND user_overrode IS NULL",
        [(note, cid) for cid in ids],
    )
    con.commit()
    return con.total_changes


def clean_fixtures(con, note):
    """Mark precommit test-fixture cycles overridden (reversible).

    Safety net for temp/test-repo commits scored into the PROD DB by the
    global git hook (see tests/conftest.py hermeticity contract). Same
    contract as the rest of the sweep: only user_overrode IS NULL; idempotent
    (already-marked rows are skipped); never deletes — flipping user_overrode
    keeps history recoverable. Returns the list of marked ids.
    """
    rows = con.execute(
        "SELECT id, task_id FROM loop_cycles WHERE user_overrode IS NULL"
    ).fetchall()
    ids = [r["id"] for r in rows if _is_fixture_task(r["task_id"])]
    if not ids:
        return []
    con.executemany(
        "UPDATE loop_cycles SET user_overrode=1, decision=?, outcome_note=? "
        "WHERE id=? AND user_overrode IS NULL",
        [(STOP_OK, note, cid) for cid in ids],
    )
    con.commit()
    return ids


def loop_rate_in_window(con, window_min=LOOP_RATE_WINDOW_MIN):
    """Return (loop_rows, distinct_task_ids) created in the last window_min.

    The SYSTEM-level companion to detect_stalled_loops(). That function needs
    >= min_iterations LOOP rows on ONE task_id, so it is structurally blind to a
    storm spread thinly over many task_ids — the shape actually measured (2548
    stalled rows over 1774 distinct task_ids, 1.4 rows each).

    Timestamps are stored as local '%Y-%m-%d %H:%M:%S' text, the same format
    check_alerts() and the drain parse, so the cutoff is built the same way.
    """
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M:%S")
    row = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT task_id) FROM loop_cycles "
        "WHERE timestamp >= ? AND decision LIKE 'LOOP%'",
        (cutoff,),
    ).fetchone()
    if not row:
        return (0, 0)
    return (row[0] or 0, row[1] or 0)


def rate_alert(con, window_min=LOOP_RATE_WINDOW_MIN,
               threshold=LOOP_RATE_ALERT_THRESHOLD):
    """Alert when fleet-wide LOOP traffic exceeds threshold in the window.

    Returns None when quiet. Reports the DISTINCT task_id count beside the row
    count because that ratio is what separates a diffuse storm (many task_ids,
    ~1 row each) from ordinary iteration (few task_ids, many rows) — a bare row
    count cannot tell the operator which they are looking at.
    """
    count, distinct = loop_rate_in_window(con, window_min=window_min)
    if count < threshold:
        return None
    return ("LOOP storm: %d LOOP cycles across %d distinct task_ids in the last "
            "%d min (threshold %d) — a diffuse machine-driven loop, not human "
            "iteration. Look for a cron/hook re-firing; the per-task stall "
            "detector cannot see this shape."
            % (count, distinct, window_min, threshold))


def check_alerts(con, surfacing, live_locks, pending_hours=24, hardfail_days=7):
    now = datetime.datetime.now()
    alerts = []
    for r in surfacing:
        b = _decision_bucket(r["decision"])
        try:
            ts = datetime.datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if b == "pending":
            sid = r["session_id"] or ""
            if sid and sid not in live_locks and (now - ts) > datetime.timedelta(hours=pending_hours):
                alerts.append("PENDING leak: cycle %d (%s) unscored %s, no live lock" % (r["id"], r["task_id"], r["timestamp"]))
        elif b == "stop_fail":
            if (now - ts) > datetime.timedelta(days=hardfail_days):
                alerts.append("hard-fail STOP unaddressed: cycle %d (%s) %s" % (r["id"], r["task_id"], r["timestamp"]))
    return alerts


def detect_stalled_loops(con, min_iterations=3):
    """Detect LOOP cycles stalled at the same outcome_note or composite score.

    A stalled LOOP is one where the same outcome_note repeats across >=
    min_iterations LOOP cycles of the same task_id (dominant alert), or
    where >= min_iterations LOOP cycles settle at the same composite score
    with a different note (no measured progress despite iteration).

    Returns a list of alert strings, one per distinct stalled task_id.
    Never auto-accepts — only surfaces with severity.
    """
    alerts = []

    # Check 1: same outcome_note across >= min_iterations LOOP cycles
    by_note = con.execute("""
        SELECT task_id, outcome_note, composite,
               COUNT(*) as cnt,
               MIN(timestamp) as first_ts,
               MAX(timestamp) as last_ts
        FROM loop_cycles
        WHERE decision LIKE 'LOOP%'
          AND user_overrode IS NULL
          AND outcome_note IS NOT NULL
          AND outcome_note != ''
        GROUP BY task_id, outcome_note
        HAVING cnt >= ?
    """, (min_iterations,)).fetchall()

    task_ids_reported = set()
    for r in by_note:
        task_ids_reported.add(r["task_id"])
        alerts.append(
            "STALLED LOOP: task '%s' — same outcome_note across %d LOOP "
            "iterations (%s to %s); composite=%.2f; reviewer attention "
            "recommended" % (
                r["task_id"], r["cnt"], r["first_ts"], r["last_ts"],
                r["composite"])
        )

    # Check 2: same composite score across >= min_iterations (different notes
    # but no measured progress).
    by_composite = con.execute("""
        SELECT task_id, composite,
               COUNT(*) as cnt,
               MIN(timestamp) as first_ts,
               MAX(timestamp) as last_ts
        FROM loop_cycles
        WHERE decision LIKE 'LOOP%'
          AND user_overrode IS NULL
        GROUP BY task_id, composite
        HAVING cnt >= ?
    """, (min_iterations,)).fetchall()

    for r in by_composite:
        if r["task_id"] not in task_ids_reported:
            alerts.append(
                "STALLED LOOP: task '%s' — %d LOOP iterations at composite "
                "%.2f (%s to %s); no score progress" % (
                    r["task_id"], r["cnt"], r["composite"],
                    r["first_ts"], r["last_ts"])
            )
            task_ids_reported.add(r["task_id"])

    return alerts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=3.0, help="age cutoff in days (default 3)")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    ap.add_argument("--report", action="store_true", help="always print summary")
    ap.add_argument(
        "--loop-days", type=float, default=LOOP_DRAIN_DAYS_DEFAULT,
        help="age cutoff in days for draining provably-stalled precommit LOOP "
             "cycles (default %.0f). 0 means NO age gate — every matched cycle "
             "is drained however recent, which is NOT the default and only "
             "sensible for a one-off catch-up" % LOOP_DRAIN_DAYS_DEFAULT,
    )
    ap.add_argument(
        "--stall-min", type=int, default=STALL_MIN_ITERATIONS,
        help="LOOP cycles at one (task_id, composite) before a task counts as "
             "stalled (default %d; keep >= the alert threshold in "
             "detect_stalled_loops)" % STALL_MIN_ITERATIONS,
    )
    ap.add_argument(
        "--rate-window", type=int, default=LOOP_RATE_WINDOW_MIN,
        help="look-back window in minutes for the system-level LOOP-rate check "
             "(default %d min). This check exists because the per-task stall "
             "detector cannot see a storm spread over many task_ids."
             % LOOP_RATE_WINDOW_MIN,
    )
    ap.add_argument(
        "--rate-threshold", type=int, default=LOOP_RATE_ALERT_THRESHOLD,
        help="LOOP cycles in the window before the rate alert fires "
             "(default %d). PROVISIONAL: not derived from the storm that "
             "prompted this work (that one was slow — ~2.3/hour at peak), and no "
             "legitimate-LOOP baseline has been recorded yet. Re-tune from the "
             "per-run rate recorded in the state file." % LOOP_RATE_ALERT_THRESHOLD,
    )
    args = ap.parse_args()

    if not os.path.exists(_db_file()):
        print("review-queue-sweep: loop-governance DB not found; nothing to do")
        return 0

    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=args.days)
    cutoff_ts = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    con = _connect()
    try:
        # Precommit test-fixture safety net (never touches overrides).
        # Cleaned BEFORE measure so fixtures never enter the reported buckets.
        fixture_ids = sorted(
            r["id"] for r in con.execute(
                "SELECT id, task_id FROM loop_cycles WHERE user_overrode IS NULL"
            ).fetchall()
            if _is_fixture_task(r["task_id"])
        )
        fixture_note = (
            "auto-swept by agent-review-queue-sweep: precommit test-fixture "
            "artifact (temp-repo commit scored by the global git hook; "
            "test-DB isolation 74f2ac46); no real change"
        )

        buckets_before, closable, surfacing = measure(con, cutoff_ts)
        n_closable = len(closable)
        n_before = sum(buckets_before.values())
        note = (
            "auto-swept by agent-review-queue-sweep (O5/G-5): STOP auto-accepted, "
            "older than %.0fd, clean outcome note" % args.days
        )

        loop_cutoff_ts = (
            now - datetime.timedelta(days=args.loop_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        stalled_closable = closable_stalled_loops(con, loop_cutoff_ts, args.stall_min)
        stalled_note = (
            "auto-swept by agent-review-queue-sweep: stalled LOOP drained — "
            "%d+ LOOP cycles at the same composite for this task, older than "
            "%.0fd, no score progress. The scorer's decision is a pure function "
            "of the composite, so it re-derives LOOP indefinitely; closed here "
            "so the backlog cannot grow unbounded. Reopen if the work is live."
            % (STALL_MIN_ITERATIONS, args.loop_days)
        )

        if args.dry_run:
            ids = sorted(r["id"] for r in closable)
            print("review-queue-sweep DRY-RUN: would close %d cycles (ids %s..%s)" % (
                n_closable, ids[0] if ids else "-", ids[-1] if ids else "-"))
            print("  would clean %d precommit test-fixture cycles (ids %s..%s)" % (
                len(fixture_ids), fixture_ids[0] if fixture_ids else "-",
                fixture_ids[-1] if fixture_ids else "-"))
            sids = sorted(r["id"] for r in stalled_closable)
            print("  would DRAIN %d stalled LOOP cycles >=%.0fd old (ids %s..%s)" % (
                len(sids), args.loop_days, sids[0] if sids else "-",
                sids[-1] if sids else "-"))
            print("  unreviewed before: %d" % n_before)
            for k in ("stop_ok", "loop", "move_on", "stop_fail", "pending"):
                print("    %-10s %d" % (k, buckets_before.get(k, 0)))
            stalled_dry = detect_stalled_loops(con)
            if stalled_dry:
                print("  stalled LOOPs: %d" % len(stalled_dry))
                for s in stalled_dry:
                    print("    " + s)
            return 0

        if fixture_ids:
            con.executemany(
                "UPDATE loop_cycles SET user_overrode=1, decision=?, outcome_note=? "
                "WHERE id=? AND user_overrode IS NULL",
                [(STOP_OK, fixture_note, cid) for cid in fixture_ids],
            )
            con.commit()

        n_closed = close_cycles(con, closable, note)
        n_drained = close_cycles(con, stalled_closable, stalled_note)

        buckets_after, _, surfacing_after = measure(con, cutoff_ts)
        n_after = sum(buckets_after.values())

        alerts = check_alerts(con, surfacing, _live_lock_task_ids())
        stalled = detect_stalled_loops(con)
        alerts.extend(stalled)
        # System-level rate check. detect_stalled_loops() above is PER-TASK and
        # structurally blind to a storm spread thinly across many task_ids — the
        # shape this backlog actually had (1.4 rows per task_id). This is the
        # only check here that can see that shape.
        _rate = rate_alert(con, args.rate_window, args.rate_threshold)
        if _rate:
            alerts.append(_rate)

        # Alert dedup: deliver each anomaly once (state-tracked seen set),
        # otherwise the daily cron spams the same backlog every tick.
        prev_state = {}
        if os.path.exists(_state_json()):
            try:
                with open(_state_json(), "r", encoding="utf-8") as fh:
                    prev_state = json.load(fh)
            except Exception:
                prev_state = {}
        seen = set(prev_state.get("seen_alerts", []))
        if prev_state.get("last_run", {}).get("alerts"):
            seen.update(prev_state["last_run"]["alerts"])
        new_alerts = [a for a in alerts if a not in seen]
        seen.update(new_alerts)

        record = {
            "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
            "dry_run": False,
            "days": args.days,
            "before_total": n_before,
            "closed": n_closed,
            "fixture_cleaned": len(fixture_ids),
            "stalled_drained": n_drained,
            "fixture_ids": fixture_ids,
            "after_total": n_after,
            "buckets_before": buckets_before,
            "buckets_after": buckets_after,
            "alerts": alerts,
            "new_alerts": new_alerts,
            "stalled_loops": len(stalled),
            "stalled_details": stalled[:10],  # cap detail to keep record compact
        }
        os.makedirs(_state_dir(), exist_ok=True)
        with open(_run_log(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        with open(_state_json(), "w", encoding="utf-8") as fh:
            json.dump({"last_run": record, "seen_alerts": sorted(seen)}, fh, indent=1)

        if args.report or new_alerts:
            lines = []
            lines.append("review-queue-sweep: closed %d/%d unreviewed (%d remain)" % (n_closed, n_before, n_after))
            if fixture_ids:
                lines.append("  fixtures: cleaned %d precommit test-fixture cycles (test-repo commits)" % len(fixture_ids))
            if n_drained:
                lines.append(
                    "  drained: closed %d stalled LOOP cycles (>=%d iterations at one "
                    "composite, untouched >=%.0fd) — backlog cannot grow unbounded"
                    % (n_drained, args.stall_min, args.loop_days))
            for k in ("stop_ok", "loop", "move_on", "stop_fail", "pending"):
                lines.append("  %-10s %d" % (k, buckets_after.get(k, 0)))
            for a in new_alerts:
                lines.append("  ALERT: " + a)
            print("\n".join(lines))
            return 1 if new_alerts else 0
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
