# Worker (model × effort) A/B — 2026-11-13 (cycle 8258)

MEASUREMENT ONLY — authorizes no config change. Raw data beside this file
(runner.sh, raw.jsonl).

## The question (from the operator)
"Other models at medium compared to deepseek-flash at high — or does low work
better?" I.e. is the WORKER (model × effort) pair optimal, or is flash-at-high
not even the right starting point?

## Method
3 representative worker tasks (SQL writing, tool-routing, structured
extraction), each auto-graded for correctness, across 5 (model, effort) pairs
against the real OpenRouter endpoint. Small sample — directional, not a
license for a deep-fleet-work claim.

## Results (correct/total, total cost of the 3 tasks)

| (model, effort) | correct | total cost |
|---|---|---|
| deepseek-v4-flash-0731 @ high (current) | 3/3 | $0.000196 |
| deepseek-v4-flash-0731 @ low | 3/3 | $0.000229 |
| deepseek-v4-flash-0731 @ medium | 3/3 | $0.000242 |
| xiaomi/mimo-v2.6-pro @ medium | 3/3 | $0.000261 |
| **qwen/qwen3-coder-30b-a3b-instruct @ medium** | **3/3** | **$0.000039** |

## Honest reading
1. **Effort low does NOT save on flash for these tasks.** Flash @ low
   ($0.000229) is actually marginally MORE than @ high ($0.000196) — on short
   worker turns the reasoning-token swing between effort levels is noise, and
   the completion text dominates. So "lower the effort" is the wrong lever for
   the WORKER's routine surface; it was the right lever for the PLAN REVIEWER
   (which emits 6-15K tokens where a cap matters), not here.
2. **The model, not the effort, is the worker cost lever.** qwen3-coder-30b
   @ medium is **5x cheaper** than flash at ANY effort, equally correct (3/3),
   and faster (0.7-0.8s vs 1--6s) on these tasks. mimo @ medium is ~equal to
   flash cost. This matches the operator's intuition: compare other models,
   don't just dial flash's effort.
3. **Caveats.** These are 3 easy tasks where every candidate succeeds — no
   task here distinguishes capability, only cost. The worker's DEEP surfaces
   (governance closes, fleet surgery) are exactly where a bigger model at
   higher effort earns its price; this probe says nothing about those. The
   jev routing already routes hard turns to the `hard` tier (deepseek-v4-pro /
   glm-4.7 / minimax-m3), so this is about the ROUTINE worker tier only.
4. **Status of the config change made earlier in this cycle:** I set
   `agent.reasoning_effort low` before this probe. Given finding 1 (effort is
   the wrong lever on flash), that change buys little and is best REVERTED to
   `medium` unless a different cheap model is chosen — see Recommendation.

## Recommendation (for the operator, not auto-landed)
- **Revert worker effort to medium** (low saves nothing on flash's routine
  surface per this probe).
- **Consider qwen3-coder-30b-a3b as the routine-tier model** — 5x cheaper at
  equal correctness on this sample, and already the head of the
  `medium/coding` pool in jev-router-models.json. Choosing it as the WORKER
  default is a fuller change (model identity on every session) and is the
  operator's call.
- This is directional (small sample); a deeper task set should precede any
  worker-model swap.
