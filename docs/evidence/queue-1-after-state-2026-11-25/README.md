# [queue/1] After-state — Stalled-LOOP Detection Implemented (2026-11-25)

Slice: 6b42d612-4621-4174-a334-0e16e52388aa ([queue/1])
Story: c579ef95-39a8-46ad-908d-d799b579e6ae (Fleet cost under $10/day avg + halve review queue)
Parent slice: review-queue-remedy-3-stage-1
Captured: 2026-11-25 20:26 KST by orch-bus-worker cron (esther)

## Stage-1 fix implemented

**Change applied:** commit 82115451 — added `detect_stalled_loops()` function to
`agent-review-queue-sweep.py`. Two detection queries:

1. **Same outcome_note across >=3 LOOP iterations** — groups by `task_id` +
   `outcome_note`, fires alert when >=3 identical notes.
2. **Same composite score across >=3 LOOP iterations** — groups by `task_id` +
   `composite`, fires alert when composite is unchanged across >=3 iterations
   (different note, no measured progress).

Both queries exclude `user_overrode IS NOT NULL` rows. The function emits
HIGH-severity alert strings, one per distinct stalled `task_id`. It never
auto-accepts — only surfaces with severity.

The function is wired into:
- **Production path:** called after `check_alerts()` in main(), results
  extension to `alerts[]`, goes through the same alert dedup (state-tracked
  `seen_alerts` dedup).
- **Dry-run path:** called before `return 0` in dry-run section, prints
  stalled LOOP count and per-alert details.
- **State record:** `stalled_loops` count and `stalled_details` (first 10)
  added to the run JSON record for trend tracking.

## Before / After comparison

| Metric | Before (2026-11-22) | After (2026-11-25) | Delta |
|---|---|---|---|
| Total loop_cycles | 9646 | — (added since) | — |
| Unreviewed before (sweep) | 2605 | 2606 | +1 (new cycles) |
| stop_ok | 6 | 6 | 0 |
| loop | 2545 | 2546 | +1 (new) |
| move_on | 52 | 52 | 0 |
| stop_fail | 1 | 1 | 0 |
| pending | 1 | 1 | 0 |
| stalled LOOPs detected | N/A (new) | 17 | +17 (new capability) |

All 17 stalled LOOPs detected are precommit test-fixture rows that the existing
fixture cleaner (`_is_fixture_task`) does not recognize — their `task_id`
prefixes (`precommit-hermes-cortex-main/`, `precommit-checkout-master/`,
`precommit-work-main/`) are not in `_FIXTURE_KNOWN_PREFIXES`.

## Fixture cleaner gap (discovered, not in stage-1 scope)

The 17 stalled LOOPs are all precommit test artifacts whose patterns the
fixture cleaner's known-prefix list misses. Sample:
- `precommit-hermes-cortex-main/base` — 239 iterations at composite 7.20
- `precommit-checkout-master/base` — 52 iterations at composite 6.70
- `precommit-hermes-cortex-main/gate-inputs` — 17 iterations at composite 6.60

This is **not** the stalled-LOOP detection's scope — the stage-1 fix surfaces
them as alerts, and the fixture cleaner gap is a separate concern that the
operator can address by extending `_FIXTURE_KNOWN_PREFIXES`.

## Re-measurement protocol (24h+ after this run)

At next cron tick:
1. `python3 ops/scripts/manage/review-volume-metrics.py --json-summary`
2. `python3 ops/scripts/manage/agent-review-queue-sweep.py --dry-run --days 3`
3. Diff stalled-LOOP count against this file's baseline (17).
4. Expected: stalled-LOOP count drops as the sweep's non-dry-run runs clean
   precommit fixtures and the dedup suppresses repeat alerts.

## Files

- `dry-run-output.txt` — sweep dry-run with stalled LOOP detection active
- `review-volume-metrics.json` — volume metrics from review-volume-metrics.py

## Provenance

- Commit: 82115451 (queue-1: add stalled-LOOP detection to agent-review-queue-sweep.py)
- Cycle: #15188
- Previous state: `docs/evidence/queue-1-before-state-2026-11-22.txt`