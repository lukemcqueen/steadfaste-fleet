# Backlog run — census, top-task classification, and [queue/1] escalation (2026-11-12, `orch-bus-worker`)

Governance cycle **#14476**, session `cron_1954deb5ad76_20261112_082152`.
Host-derived identity: **esther** (orchestrator). Store: `tasks` schema, queried
with `--repo ALL` (the explicit fleet-wide view).

This run pulled pending + in_progress, classified the top of the backlog by
`(priority desc, created_at)`, and worked the one slice whose measurable
criterion is now met. Everything else at the top needs an operator decision or
a multi-day window; those are named below, not actioned.

## 1. Census (measured this run)

Command and raw output committed beside this file:
`docs/evidence/backlog-run-2026-11-12-census.txt`.

| status | rows |
|---|---|
| pending | 36 |
| in_progress | 0 |
| paused | **0** (was 18 — see §3) |
| blocked | 1 (`66914fe1` [queue/1]) |
| waiting | 0 |
| review | 0 |

Of the 36 pending rows, **14 are untrusted `priority=0` stubs** carrying the
returned parked-class signature (`scope=personal`, `project=hermes-cortex`,
content literally `ISSUES` / `PROPOSAL`) — §4.

## 2. Top of the backlog, classified

Top row by `(priority desc, created_at)` = **`c579ef95`, priority 3, kind=story**
(*Fleet cost under $10/day avg + halve review queue*). A story is not executed
directly; its open children are the work:

| child | shape | disposition |
|---|---|---|
| `40cb307d` **[cost/2]** | reduce cron cost drivers, apply env-var changes, measure a **7-day** delta | **needs a reviewed plan + multi-day window**; and its premise is now a policy call — `[cost/1]` (baseline `docs/cost-baseline-2026-11.md`, 2026-11-07) measured cron compute **~15× under the target** and found *interactive* spend is the order-of-magnitude driver. Changing cron provider/pins unattended is a PUSHBACK (no-op default). Not actioned. |
| `66914fe1` **[queue/1]** | return the 18 session-TTL parked rows to a decided state | **measurable outcome now met** — §3. Slice left `blocked` (escalation to Luke owed, §4). |

Next tier, `priority=2` — none unattended-actionable:

- `a6fc254a` gisu bridge-KillMode rollout — explicit **operator decision** (gisu
  repo divergent, 62 local commits; must not be reconciled unattended).
- `7de776dd` / `1e4198ae` / `4beea4e1` — **decisions owed to Luke** (reviewer
  tier/threshold; re-enable the paused `agent-remediate-apply`; a per-job
  attempt cap). All three say "do NOT change unattended".
- `ca282975` / `1e8737fc` + the `kaesa-90day` phases — client deliverables that
  need account access / client input.

`priority=0` untrusted rows: §4.

## 3. Verified progress — [queue/1]'s measurable criterion is met

The 2026-10 review-queue diagnosis (`docs/older/review-queue-diagnosis-2026-10.md`,
task `90b30c9c`) found **no review queue to halve** and identified the real
backlog: **18 session-TTL parked rows** (`paused`, all `project=hermes-cortex`,
`priority=0`, `untrusted=true`, 60.1–65.0 min dwell). Its remedy 2 = return them;
remedy 3 = stop the source.

**Both remedies have landed, and the criterion is verified now:**

- **Source stopped (remedy 3):** commit `c12f14f5` (2026-11-10, *"stop
  parked-row source: stale sweep returns to pending, add re-return routine"*)
  touches the message handler's stale-sweep plus a regression test — the park
  is no longer terminal.
- **Return done (remedy 2):** measured this run, **`paused = 0`** (was 18).
  The 14 rows still bearing the parked-class signature are now `pending`
  (§1/§4), i.e. back in the pool.

`queue/1`'s own plan step (4) is "re-measure: paused count must reach 0" —
**met** (18 → 0, before/after in the census artifact). Step (2) is the remaining
half: the junk batch is **not** cancelled unattended; it must be escalated to
Luke (§4). A prior run set the slice to `blocked` for exactly that operator
call, so this run **respects that block** and does not flip the status — the
slice is one operator call from closure, not a silent completion.

## 4. Escalation owed to Luke — the 14 untrusted `priority=0` rows

All 14 are body-less or truncated fragments — the inbox→task bridge that minted
them lost the payload:

- **13 title-only stubs** — content is literally `ISSUES` (9 rows) or `PROPOSAL`
  (4 rows). They carry no actionable body.
- **1 truncated fragment** — `d946b8e5` *"PROPOSAL: Land orchestrator-blocked
  fixes for joseph fix-all-doctor-items: (1) ops/scripts/manage/cortex_doctor/
  checks.py hoist _p…"*. The content ends mid-token (`_p`); even the store's own
  value is a fragment, so the full proposal body is not recoverable from the
  task row.

**Recommendation (needs Luke's OK — the no-op default blocks unattended
cancellation):**

1. **Cancel the 13 title-only stubs as junk** (`--status cancelled --reason
   stale`). They are the residue of an input path that lost the body.
2. **Retrieve and evaluate `d946b8e5` from its originating inbox message**
   before cancelling: if the full proposal is a real orchestrator-only fix
   (`fleet_doctor/checks.py` is an esther-writable path), land it under a
   governed cycle; otherwise cancel it with the 13.
3. **Fix the mint, not just the rows** — the bridge should either carry the
   body into the task row or refuse to mint a bodyless row. Without this, step 1
   is a treadmill (the same class refilled once already).

## 5. Blockers (explicit, nothing silently dropped)

| task | blocker |
|---|---|
| `66914fe1` [queue/1] | junk-batch cancellation + `d946b8e5` disposition (§4) — operator call |
| `40cb307d` [cost/2] | needs a reviewed plan + 7-day measurement; premise re-scoped by the baseline (interactive, not cron) |
| `7de776dd`, `1e4198ae`, `4beea4e1` | decisions owed to Luke |
| `a6fc254a` | gisu divergent repo — operator pass / authorise stash |
| kaesa engagement + 90-day slices | client input / account access |

## 6. Evidence and re-run

- Census: `docs/evidence/backlog-run-2026-11-12-census.txt`
  (`task-db.py list --status <s> --repo ALL`; paused=0, pending=36, blocked=1).
- Source fix under test: `c12f14f5` — `git show --stat c12f14f5` (5 files,
  incl. `tests/test_task_queue_visibility.py`).
- Prior diagnosis: `docs/older/review-queue-diagnosis-2026-10.md`.

```bash
# re-run the census (orchestrator host)
cd ~/steadfaste-fleet
for s in pending in_progress paused blocked waiting review; do
  echo -n "$s: "; python3 ops/scripts/manage/task-db.py list --status $s --repo ALL | grep -cE '^[0-9a-f]{8}-'
done
```

*Side observation (not a task): the bus shows a `HEALTH_PERSISTENT_ISSUES`
message roughly every 2 minutes, esther→moses (inbox_orchestrator). If that
cadence is not intended, it is a flapping-watchdog candidate for
`watchdog-alert-design` — flagged here, no action taken.*
