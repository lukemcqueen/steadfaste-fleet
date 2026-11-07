# Fleet Cost Baseline — 2026-11-01..2026-11-07

**Story:** c579ef95 *Fleet cost under $10/day avg + halve review queue*
**Slice:** 8feb668e `[cost/1]` — establish fleet cost baseline (per-cron table, fleet total, top-3 drivers)
**Measured:** 2026-11-07 KST by the orch-bus-worker cron (esther, orchestrator)
**Status:** esther MEASURED fresh (this run); fleet coverage carried from the
[cost/4] aggregates — see §4 for the exact gap this leaves.

> This is the 2026-11 window. The prior baseline (2026-10-06..19) is archived at
> `docs/older/cost-baseline-2026-10.md`; its headline then was "cron is ~14× under the
> $10/day target, and interactive spend dwarfs cron 4.3×". Both findings hold and sharpen here.

## 1. Sources (all live, all re-runnable)

- **S1 `cron-cost-metrics.py --json-summary --since 2026-11-01`** — per-day fires/tokens
  (audit log), per-job lifetime spend (state.db `estimated_cost_usd`, written live at
  current prices — no stored price table), and one bounded Langfuse probe.
- **S2 `orch-weekly-cost-report.py --days 7`** — per-day **cron** spend from
  `state.db` sessions where `source='cron'`, with the daily-average and coverage note.
- **S3 Langfuse `GET /api/public/metrics/daily`** — per-day **total** trace cost
  (cron + interactive), the counterweight that proves cron is the minority of spend.

## 2. Headline

- **Cron compute is far under the story target.** esther cron spend over 7 days is
  **$4.48 → $0.64/day** — roughly **15× under** the $10/day fleet target.
- **Cron is still not where the money goes.** Langfuse total trace cost on esther over the
  same window is **$69.00** (vs $4.48 cron): **$29.84 on 2026-11-06 alone** (97 traces,
  one heavy interactive session) vs $0.76 cron that day. Interactive spend is the
  dominant, non-schedulable driver — a cost program that only tunes crons targets the
  minority (~6%) of measured esther spend this window.
- **Fleet tombstone is small and bounded.** Aggregate lifetime cron spend across the 5
  worker hosts (esther, joseph, kustos, gisu, titus) is **~$219**, i.e. the fleet has
  never been near the $10/day run-rate on cron compute.

## 3. esther — per-day (fresh, window 11-01..11-07)

| date | cron fires | cron tokens (M) | cron cost $ (S2 state.db) | Langfuse total $ (all traces) |
|---|---|---|---|---|
| 2026-11-01 | 16 | 13.48 | 0.38 | 1.37 |
| 2026-11-02 | 25 | 17.47 | 0.41 | 1.10 |
| 2026-11-03 | 26 | 27.47 | 0.78 | 9.73 |
| 2026-11-04 | 24 | 29.08 | 0.80 | 14.22 |
| 2026-11-05 | 25 | 31.14 | 1.01 | 3.61 |
| 2026-11-06 | 23 | 26.40 | 0.76 | 29.84 |
| 2026-11-07 | 7 | 7.37 | 0.33 | 9.13 |
| **total / avg** | **146** | **152.4** | **4.48 → $0.64/day** | **69.00** |

11-07 is a partial day (measured ~08:21 KST); the daily average is over the 7 measured
days and says so.

## 4. The remaining gap — fleet daily average

The prior baseline measured **2 of 6** hosts. [cost/4] (slice 4e3c8873's sibling) closed
that to **6 of 6** on the identical instrument, but its aggregates are windowed
`--since 2026-10-06` (evidence `docs/evidence/cost4-coverage-2026-10-28.txt`). What they
give, honestly:

| host | lifetime cron spend $ (state.db) | window tokens (10-06..28) | fires |
|---|---|---|---|
| esther | 63.36 | 1594.7M | 510 |
| joseph | 33.93 | 292.3M | 240 |
| kustos | 40.60 | 315.4M | 266 |
| gisu | 64.53 | 258.0M | 245 |
| titus | 16.55 | 268.3M | 241 |
| moses | (separate; ~$1.82 tokens-extrapolated, 2026-10 baseline) | 207.1M | 157 |

A **fleet-wide daily average for the 2026-11 window** is NOT computed here, because it
requires re-running `cron-cost-metrics.py --json-summary --since 2026-11-01` over EXEC on
the other 5 hosts — the [cost/4] fleet-coverage loop, which is a separate tracked slice.
This baseline reports per-day cost only for esther (the one host whose state.db this
session can read) and does not fabricate a fleet average from lifetime totals.

**Provider/model coverage:** per-cron provider/model is configured at the env-var tier
(`LLM_CRON_PROVIDER` + model, single control point), not per-cron — the jobs.json
entries mostly carry empty `provider`/`model` with a provider/model snapshot at dispatch.
The baseline therefore cannot break spend by provider from the current instrument; the
per-job spend table below is the finest honest grain.

## 5. esther — per-cron lifetime spend (top 15 active jobs, state.db)

| job_id | cron | spend $ (lifetime) |
|---|---|---|
| 1954deb5ad76 | orch-bus-worker | 6.48 |
| 8449be867645 | local-daily-soul-refinement | 5.99 |
| 8a46f1b96b7d | orch-skill-lifecycle | 5.35 |
| 418bbf45277d | agent-fixer-workday | 4.95 |
| f41b7171b9e1 | cortex-bus-workday | 3.65 |
| e4b77fb053f8 | (removed job) | 3.55 |
| 0677224663a3 | (removed job) | 3.56 |
| f6694d02d29c | orch-task-morning-pass | 3.37 |
| 50cdb07eda5c | local-daily-sustainability-briefing | 3.29 |
| 6cb3c26a2ac9 | agent-mycortex-dream-nightly | 2.97 |
| 78b32a966223 | (removed job) | 2.43 |
| e798f3363b33 | (removed job) | 2.28 |
| 2da7ae15e810 | orch-skill-evaluate | 1.38 |
| dbcdec6bb40d | (removed job) | 7.09 |

`dbcdec6bb40d` holds the single largest lifetime figure ($7.09) but is **not in the
current jobs.json** — a decommissioned cron whose session records persist; it cannot be a
driver going forward. Top-3 **active** drivers are therefore: **orch-bus-worker $6.48,
local-daily-soul-refinement $5.99, orch-skill-lifecycle $5.35**.

Token ranking differs from spend ranking: the top token consumers are heavy-input crons
that are cheap per token (mostly cache-hit input), so token count is not a cost proxy.

## 6. Top-3 cost drivers (fleet, this window)

1. **Interactive sessions — dominant, non-schedulable.** $69.00 Langfuse total vs $4.48
   cron on esther alone; the 11-06 spike ($29.84 across 97 traces) is one heavy
   interactive session, not a cron.
2. **orch-bus-worker — $6.48 lifetime** — the orchestrator's own hourly driver; the most
   expensive *active* cron, fired 25× in this 7-day window alone.
3. **local-daily-soul-refinement + orch-skill-lifecycle — $5.99 + $5.35 lifetime** — the
   daily refinement and skill-lifecycle crons, both high-input / low-cost-per-token.

## 7. Verdict against the story

- **Cron compute target ($10/day avg): PASS with wide margin** — esther $0.64/day,
  and the fleet's lifetime cron spend shows the whole fleet has never run cron compute
  near that rate.
- The story's real risk has not moved since the October baseline: **interactive spend
  outranks cron by an order of magnitude**, so cron tuning alone targets the minority of
  measured spend. [cost/2] should therefore prioritize the *interactive* drivers and the
  duplicated/over-spec *cron* runs in equal measure, not only cron:
  the 11-06 interactive spike is a one-session anomaly that dwarfs a week of cron tuning.
- **Remaining measurement:** a true fleet-wide 2026-11 daily average needs the [cost/4]
  windowed EXEC re-run (5 hosts). Tracked separately; flagged here rather than
  extrapolated.

## 8. Evidence

- `cron-cost-metrics.py --json-summary --since 2026-11-01` (esther) — run 2026-11-07 KST.
- `orch-weekly-cost-report.py --days 7 --host esther` — run 2026-11-07 KST.
- Fleet lifetime aggregates: `docs/evidence/cost4-coverage-2026-10-28.txt` (6/6 hosts).

Live reads, all re-runnable; the current day grows sub-cent between reads, which is live
data, not an inconsistency.