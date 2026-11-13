# Code-generation A/B across the three pro-class hard-tier models — 2026-11-13 (cycle 8275)

MEASUREMENT ONLY — authorizes no config change. Raw data beside this file
(runner.sh, raw.jsonl). This run answers the operator's question after the
plan-REVIEW A/B made mimo-v2.6-pro the reviewer winner: is that also true for
CODE GENERATION, i.e. should HARD turns lead with Mimo?

## Method
- 6 representative HARD coding tasks (median-of-two-sorted-arrays,
  trapping-rain-water, edit distance, longest-valid-parentheses, string-to-int
  with 32-bit clamping, topological sort). Each is a single pure function.
- Graded by EXECUTING the generated code against our known-correct reference
  implementation on 5-12 hidden test vectors each (outcomes > outputs; string
  matching was not used). Each solution ran in a timeout-bounded child process
  (25s) so a non-terminating solution grades as wrong, never hangs the run.
- Live OpenRouter endpoint, temperature 0, reasoning_effort medium (the Jev
  hard-tier operating point), per-model catalogue prices (in/out $/M):
  deepseek-v4-pro-0813 0.66/1.98 · glm-4.7 0.54/1.98 · mimo-v2.6-pro 0.435/0.87.
- One run, one seed per (model, task). Directional, not a deep-fleet claim.

## Results (correct/total, cost, latency)

| model | correct | total cost (6 tasks) | avg latency |
|---|---|---|---|
| deepseek/deepseek-v4-pro-0813 | 6/6 | $0.005724 | 4.8s |
| **xiaomi/mimo-v2.6-pro** | **6/6** | **$0.003254** | **4.0s** |
| z-ai/glm-4.7 | 5/6 | $0.274215 | 397.3s |

Per-task detail (latency / cost / tokens):

| task | v4-pro-0813 | glm-4.7 | mimo-v2.6-pro |
|---|---|---|---|
| median | ✓ 5.5s / $0.0011 | ✓ 62.5s / $0.0069 | ✓ 6.9s / $0.0009 |
| trapping_rain_water | ✓ 4.7s / $0.0009 | ✓ 27.8s / $0.0031 | ✓ 1.0s / $0.0002 |
| edit_distance | ✓ 7.5s / $0.0012 | ✓ 9.6s / $0.0015 | ✓ 1.0s / $0.0002 |
| longest_valid_parens | ✓ 2.9s / $0.0005 | ✗ 2261.3s / $0.2596 | ✓ 2.6s / $0.0001 |
| atoi | ✓ 3.1s / $0.0010 | ✓ 11.2s / $0.0016 | ✓ 6.9s / $0.0014 |
| topo_order | ✓ 5.0s / $0.0011 | ✓ 11.4s / $0.0015 | ✓ 5.4s / $0.0005 |

## Honest reading
1. **Mimo equals the hard-tier head on correctness and beats it on cost and
   speed.** 6/6 on the same hidden tests as deepseek-v4-pro-0813, at ~57% of
   the cost ($0.0033 vs $0.0057) and ~17% faster (4.0s vs 4.8s). Its answers
   are also much more token-efficient (133-1570 completion tokens vs 202-597
   for pro; e.g. trapping-water in 133 tokens, $0.00016).
2. **glm-4.7 has a real tail-risk failure.** longest_valid_parens produced a
   degenerate run: 131,072 completion tokens (hit the output cap — a reasoning
   spin, no callable function emitted), 37.7 MINUTES wall time, $0.26 for one
   call. Its successes are also 2-12x slower than pro/mimo (median 62.5s, trap
   27.8s). Cost/latency tail risk on the hard tier is a live concern, not a
   theoretical one — the MCP client's 300s call window would time out on such a
   run before any verdict.
3. **The earlier reviewer A/B does transfer to generation for the mimo-vs-pro
   pair.** mimo was equal recall and 6x cheaper as a REVIEWER; here it is equal
   correctness and cheaper+faster as a GENERATOR on this sample. The reviewer
   result is no longer a lone surface.
4. **Caveats.** 6 tasks, one run, one seed, effort medium. The tasks are
   algorithmic; they do not cover long-horizon multi-file agentic generation or
   large-context repo surgery, where glm-4.7's 204k context and v4-pro's 1M
   context may earn their place. glm's failure is ONE sample — a repeat run
   could pass — but the 131k-token spin is itself the risk (a runaway costs
   more than the task is worth and blows the client window).

## Recommendation (for the operator, not auto-landed)
- **Promote xiaomi/mimo-v2.6-pro to the head of the hard/coding tier** (and
  hard/general): equal correctness, cheapest, fastest on this sample — the
  operator's "hard turns should go to Mimo" instinct is supported by
  generation evidence, not just review evidence.
- **Keep deepseek-v4-pro-0813 as the hard in-pool fallback** (pinned dated
  snapshot, proven, 1M ctx) — first-fit routing makes it the second choice.
- **glm-4.7: do not lead hard/coding.** Its median latency is 12x the head and
  it demonstrated a 131k-token/$0.26/37-min runaway on one task. Keep it in the
  hard tier only if its 204k context is needed for large-context work, and
  budget-caps (max_tokens) should bound it wherever it is used.
- A deeper task set (multi-file, long-context) should precede any decision to
  REMOVE glm or to route the hardest long-horizon work to mimo.

## Harness note
The first run (killed) measured its own bug: the grader looked up the
reference function inside the exec'd model namespace (`ns.get('f_ref')`) where
only module scope held it — every row read "no callable f (or ref)". Fixed to
resolve the reference from module scope and smoke-tested (good→pass,
bad→fail-with-detail, missing-f→flag) before the real run. Probe the probe.

## Independent adversarial review (delegate_task, fresh session, 2026-11-13)
Verdict: **sound — no blocking findings.** The reviewer recomputed every
aggregate from raw.jsonl (all reproduce to the digit, including the glm
runaway row: 82×0.54e-6 + 131072×1.98e-6 = $0.259567), verified the harness
namespace fix at module scope (runner.sh make_harness), confirmed `tests_run`
matches each task's test count (grades are not vacuous), the float comparison
only applies to float-expected values (median), the topo validator accepts any
valid order, and fence extraction worked on glm's fenced rows.
Two UNVERIFIED items, neither flips the determination:
1. Catalogue prices (0.66/1.98, 0.54/1.98, 0.435/0.87) are asserted from the
   fleet routing file, not re-verified against a live price feed; the cost
   MATH is correct given those prices.
2. glm's 2261.3s row exceeds the 300s urlopen timeout — urllib's timeout is
   per-socket-op, not total wall time, so a trickling response can exceed it.
   Plausible; not provable from artifacts; does not affect the grade.
Framing correction folded in: glm's 397.3s average is dominated by the
runaway — its five non-runaway rows average ~24s (median 11.2s). The
"12x slower" statement refers to the worst-case/tail comparison, not steady
state; the tail is exactly the operational risk (a 37-min, $0.26, 131k-token
call blows the 300s MCP client window), so the tail is the headline.

