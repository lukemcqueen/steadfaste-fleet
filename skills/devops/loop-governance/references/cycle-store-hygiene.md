# Cycle Store Hygiene — why the review backlog grows, and how to bound it

<!-- COVERED BY tests/test_cycle_store_hygiene_claims.py (2026-11-26): the
     stateless-decision claim (~L20) and the no_progress-unreachability claim
     (~L28) are pinned by tests 1-4 against core/governance/loop_scorer.py.
     If the scorer changes, the tests fail loudly instead of this reference
     silently going stale. -->

Depth for the `loop-governance` SKILL.md. Load this when the review-queue sweep
reports a large unreviewed count, when a `LOOP` decision looks like it will never
resolve, or before changing anything in `core/governance/loop_scorer.py` or
`core/governance/loop_db.py`.

## The root cause: the decision is STATELESS

`composite_score()` in `core/governance/loop_scorer.py` derives the decision from
the composite score ALONE:

```python
if   comp >= t["stop"]:    decision = "STOP ✓ — goal met, quality acceptable"
elif comp >= t["loop"]:    decision = "LOOP 🔄 — keep iterating"
elif comp >= t["move_on"]: decision = "MOVE ON → — skip or escalate to human"
else:                      decision = "STOP ✗ — hard fail, escalate"
```

It receives `(completeness, quality, progress)` and nothing else — no `task_id`,
no history. So a task that settles at one composite re-derives the SAME decision
forever. There is no iteration counter, no "I have said this before", and
consequently **nothing in the scoring path can ever terminate a loop**. A LOOP is
not a step toward a STOP; it is a fixed point.

Two aggravating details:

- **`no_progress` almost never fires on real work.** `score_progress()` floors any
textual diff at 5.0 ("a small commit deserves a floor"), and `no_progress` is
`progress < 2.0`. So the one signal that could mark a cycle unproductive is
structurally unreachable for a commit that changes anything.
- **The live proof is on every commit.** The pre-commit hook prints its own
`score-cycle:` line; it can read `LOOP 🔄 — keep iterating (… pass-pct=100)` —
100% of checks passing and the decision still "keep iterating", because the
composite landed in the loop band and nothing consults `pass_pct` toward the
decision.

## What the backlog actually is

`agent-review-queue-sweep.py` reports the unreviewed count. When it is large,
measure the composition before theorising:

```sql
-- by class
SELECT CASE WHEN decision LIKE 'STOP %' THEN 'stop_ok'
            WHEN decision LIKE 'STOP%'  THEN 'stop_fail'
            WHEN decision LIKE 'LOOP%'  THEN 'loop'
            WHEN decision LIKE 'MOVE%'  THEN 'move_on'
            ELSE 'pending' END AS bucket,
       COUNT(*), MIN(timestamp), MAX(timestamp)
FROM loop_cycles WHERE user_overrode IS NULL GROUP BY bucket;

-- is it precommit-hook art ifacts? (the usual answer: yes, ~100%)
SELECT SUM(task_id LIKE 'precommit-%') FROM loop_cycles
WHERE user_overrode IS NULL AND decision LIKE 'LOOP%';
```

A run of rows sharing the SAME `(decision, composite, outcome_note)` triple at
~tens-of-seconds spacing is a runaway producer, not many considered judgements —
`feedback_accept` (or the hook) firing in a tight loop for one `task_id`. Confirm
with `SELECT task_id, COUNT(*) … GROUP BY task_id ORDER BY 2 DESC`.

## The three permissiveness layers that let it grow

1. **The sweep auto-closes STOP-OK only.** `measure()` puts a cycle in `closable`
   only for `b == "stop_ok"` past the age cutoff with a clean note. LOOP cycles
   are surfaced and alerted on forever, never closed.
2. **The fixture cleaner matches a hardcoded prefix list.** `_is_fixture_task()`
   accepts `precommit-*-HEAD` and `_FIXTURE_KNOWN_PREFIXES`; artifacts from real
   repos (`precommit-<repo>-<branch>`) fall straight through it.
3. **`log_cycle()` / `feedback_accept` have no per-task cap.** Nothing refuses the
   Nth identical row, so growth is unbounded by construction.

## Bounding it: the drain, and the threshold-pairing rule

`closable_stalled_loops(con, cutoff_ts, min_iterations)` selects LOOP cycles that
are (a) `precommit-*`, (b) past the age gate, and (c) in a `(task_id, composite)`
group already carrying `>= min_iterations` LOOP cycles — i.e. provably stalled,
not merely unfinished. It drains through the existing `close_cycles()` path, so it
inherits that path's safety contract.

**Keep the drain threshold EQUAL to the alert threshold, and pin that with a test.**
`detect_stalled_loops()` alerts at `min_iterations=3`; the drain defaults to the same
3. A drain threshold BELOW the alert would silently discard material the alert never
surfaced; ABOVE it, reported stalls accumulate forever. A test asserting both the
constant and the alert's default keeps the two from drifting apart — the failure mode
is silent, so only an assertion catches it.

**Keep the age gate on by default.** Draining a live investigation out from under its
author is worse than a slow drain. 30 days is the default; `--loop-days 0` means NO age
gate (every matched cycle however recent) — document that as non-default, because
"0 disables the feature" is the natural reading and it is wrong. Verify by running the
flag and reading the count rather than trusting the help text.

**Prove a store mutation on a COPY first.** The sweep resolves its DB from
`REVIEW_DB_PATH` and its state from `REVIEW_STATE_DIR`, so the whole drain can be
exercised against a copy with prod untouched:

```bash
cp ~/.steadfaste-fleet/data/loop-governance.db "$S/test.db"
REVIEW_DB_PATH="$S/test.db" REVIEW_STATE_DIR="$S" python3 ops/scripts/manage/agent-review-queue-sweep.py
# then re-run the same command: it must drain 0 (idempotent)
```

Read `last_run` from `$S/review-queue-sweep-state.json` for the counts
(`stalled_drained`, `before_total`, `after_total`, `buckets_before/after`) — they are
not all on stdout.

## `feedback_accept` — pass ALL THREE scores, always

`feedback_accept` takes `completeness`, `quality` and `progress`. **Omitting one does
not default it to the composite or to anything sensible — it scores 0.0** and drags
the composite to the floor (a thorough investigation scored `completeness=10,
quality=0, progress=0 → composite=4.0`). There is no error and no warning; the
`PENDING → MOVE_ON` acceptance succeeds and the bad number is already recorded.

So always pass all three explicitly, including on cycles that changed no files (an
investigation, a verification pass). A follow-up `feedback_accept` on the same
`cycle_id` re-scores it, which is how a bad number gets corrected — but writing it
right the first time is the fix.

## The store is SHARED — a mutation must name its guard

The loop-governance DB is one file on the host that several producers write:
the pre-commit hook, `feedback_accept`, the sweep cron, the doctor's disposition
writes. Two sessions can act on it at once. State the concurrency guard in anything
that writes it — an atomic `WHERE … AND user_overrode IS NULL`, the advisory flock
`LoopDB._lock()`, or an explicit statement that a specific write is idempotent and why.
A sweep that resolves conflicts by "last writer wins" silently discards the other run's
work.

## Do not weaken the gate to shrink the backlog

Deleting rows, relaxing the close gate, or suppressing the alert are all `enforcements
fixes fail closed` violations. The backlog is a SYMPTOM of a stateless decision; the
legitimate responses are (a) bound the rows, (b) drain what is provably stalled under an
age gate, and (c) surface it. Any change that makes the number smaller without changing
what produced it is a bypass.
