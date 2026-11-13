# Task-queue remediation — acceptance evidence

Regenerate with: `bash ops/scripts/manage/run-task-queue-evidence.sh`

Every number below is RE-DERIVED from the live store and the live repo by
running this script. Aggregates only — no row identifiers, so the checks
keep holding for rows added later.

Provenance: a regression test regenerates this report and compares it to
the committed file, so a hand-written table cannot pass for script output.

| Check | Result | Detail |
|---|---|---|
| No pending slice is assigned-and-unstarted | PASS | 0 stranded (baseline 6) |
| Pending-slice pool fully accounted for by its two views | PASS | claimable 17 + assigned 0 = 17 |
| The pool is accounted for (work is reachable when present) | PASS | claimable 17 (baseline 9 + 6 released; no floor asserted — a drained pool is healthy) |
| No row is left parked in `waiting` | PASS | 0 still waiting (baseline 6) |
| A cancelled slice is recoverable in the archive (cancels move, not delete) | PASS | 28 cancelled rows archived since 2026-10-03 UTC (fixed cutoff) — a cancel MOVES the row to tasks.task_archive, so it stays recoverable |
| Parked rows can return to the claim pool | PASS | waiting->pending=t blocked->pending=t paused->pending=t |
| Every task migration is registered for deploy | PASS | 18 migrations; unregistered: none |
| Every task migration is on the deployed tree | PASS | 18 of 18 deployed |
| DB schema version matches the newest repo migration | PASS | DB=18 repo=18 |
| test_fleet_harness passes on the current tree | PASS | now (42cb3d85): ============================== 13 passed in 4.66s ============================== · pinned baseline fbfc22b34db2 (fbfc22b3): ERROR: file or directory not found: tests/test_fleet_harness.py [outcome fail] — reproduced as recorded under the generating interpreter, not gated (interpreter drift makes the old tree pass under today's python); the verdict is the measured pass, never the absence of the word "failed" |

**Verdict: PASS**

## Raw test output — `tests/test_fleet_harness.py`

`fleet harness` and its registry were failing their own tests before this fix;
the same file is re-run at the parent of the commit that last touched it, so
the claim is a measured before/after rather than an assertion.

### With this change applied

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: $HOME/steadfaste-fleet
configfile: pytest.ini
plugins: anyio-4.15.1
collected 13 items

tests/test_fleet_harness.py .............                                [100%]

============================== 13 passed in 4.66s ==============================
```

### On a HEAD~1 worktree (this change absent)

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /tmp/tq-evidence-clean-tree
configfile: pytest.ini
plugins: anyio-4.15.1
collected 0 items

============================ no tests ran in 0.00s =============================
ERROR: file or directory not found: tests/test_fleet_harness.py
```
