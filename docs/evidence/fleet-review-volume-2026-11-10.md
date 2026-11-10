# Fleet review-volume measurement — esther + titus + moses (2026-11-10)

Read-only volume measurement of the close-gate (blob_reviews) and plan-review
(adversarial_reviews) surfaces, per host, using the deployed instrument
`review-volume-metrics.py` (committed 199224b7, registered in fleet-update.sh).
Pricing applied by the caller at today's live OpenRouter catalogue rates.

## How these numbers were produced

- **Esther** (this host): ran locally,
  `AGENT_NAME=esther python3 ~/.steadfaste-fleet/scripts/review-volume-metrics.py --json-summary`
- **Moses**: `ssh mosesaaron "AGENT_NAME=moses python3 ..."` — the UPDATE_REQUEST
  dispatch (corr=rvm-moses-6914b16652) had already landed; verified the script
  file exists on the deployed tree and `git log -1` = 17c80ddf.
- **Titus**: `fleet exec titus review-volume-metrics.py --json-summary` over the
  EXEC bus (UPDATE_REQUEST corr=rvm-titus-645f610d90); returned stdout JSON from
  the deployed copy.

## blob_reviews (close-gate) by judge model — all time

| host   | v4-pro | v4-pro-0813 | (empty) | total |
|--------|--------|-------------|---------|-------|
| esther | 1283   | 1188        | 144     | 2615  |
| titus  | 2317   | 97          | 0       | 2414  |
| moses  | 58     | 30          | 22      | 110   |
| SUM    | 3658   | 1315        | 166     | 5139  |

Titus is the dominant close-gate surface and is almost entirely on the OLD
`deepseek-v4-pro` judge (2317 of 2414; its -0813 rows start 2026-11-05). Esther
switched to -0813 on 11-04. Moses is mixed. (empty) rows predate the 2026-10-18
fix that made the writer name the judge (same artifact the 10-29 baseline noted.

Recent per-day (non-spike): titus 11-09=63 11-06=23 11-04=114 11-03=119 11-02=19
11-01=57. esther 11-09=45 11-07=27 11-06=955 (incident spike) 11-04=309 11-03=55.
moses 11-07=3 11-06=6 11-04=33.

## adversarial_reviews (plan reviews) by judge model — all time

| host   | v4-pro | v4-pro-0813 | v4-flash | agent:pi | total |
|--------|--------|-------------|----------|----------|-------|
| esther | 379    | 33          | 28       | —        | 440   |
| titus  | 371    | 122         | 22       | —        | 515   |
| moses  | 43     | 1           | 7        | 24       | 75    |
| SUM    | 793    | 156         | 57       | 24       | 1030  |

## Modeled dollar cost (all-time, ~20K in + 4K out per close review)

Per-review at today's live catalogue rates:
- deepseek/deepseek-v4-pro: $0.0267
- deepseek/deepseek-v4-pro-0813: $0.0200
- deepseek/deepseek-v3.2: $0.0067 (~3x cheaper)

Fleet close-review spend ~ $124 modeled all-time — esther $58 (47%), titus $64
(51%), moses $2 (2%). Titus is half the fleet review bill and still pays the
v4-pro rate on 2,317 rows.

Daily at ~125 reviews (titus 60 + esther 50 + moses 15) on -0813 ~ $2.50/day;
the same volume on v3.2 for the cheap class ~ $0.84/day. Caveat: per-review
token shape is MODELED, not measured — blob_reviews carries no token columns;
daily $ figures are directionally right, not ledger-accurate.

## Superseded — the ledger (2026-11-10, commit e635af74)

The modeled caveat above is now historical. `review_calls` is a per-call token
ledger written at BOTH reviewer call sites (loop-gov close reviews,
source='close'; adversarial-review plan reviews, source='plan') from the
provider's OWN `usage` in the API response. One row per model call, review_id
uuid4; a retry is a new spend and gets a new row. Telemetry only — a ledger
write failure logs and the review proceeds.

Ledger-accurate cost = SUM(review_calls.prompt_tokens) × live prompt price +
SUM(review_calls.completion_tokens) × live completion price. New
measurements should read review_calls, not the modeled $ above.

Deploy note: the table materializes on each host at the NEXT loop-gov daemon
spawn (schema-init runs on first _db() call; the running gateway daemon keeps
its old code until restart — the documented deployed ≠ loaded boundary). Code
verified deployed on esther (module loads, `_record_review_call` present),
moses (grep hit in deployed tree, HEAD e635af74), titus (UPDATE_REQUEST
rvm2-titus dispatched; EXEC still healthy). Plan pre-reviewed CLEAN by the
governed adversarial reviewer (cycle 14416); 6 tests (RED→GREEN); A4 gate
passed on all three files.

## Re-run

    AGENT_NAME=<host> python3 ~/.steadfaste-fleet/scripts/review-volume-metrics.py --json-summary
    # remote hosts: fleet exec <host> review-volume-metrics.py --json-summary
