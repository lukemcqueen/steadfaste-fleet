# Plan-reviewer model A/B — 2026-11-13 (cycle 8258)

MEASUREMENT ONLY — this file authorizes nothing. Switching
ADVERSARIAL_REVIEWER_MODEL to a cheaper winner is a separate, operator-approved
pin. Runner + raw data beside this file (runner.sh, raw.jsonl).

## Method
- Same fixed reviewer template (docs/templates/adversarial-reviewer-prompt.md)
  as production; material appended below the marker, exactly like
  adversarial-review.py.
- Budgets as shipped in Task 1 of this cycle: max_tokens 24000,
  reasoning.effort low (matches the new production defaults).
- Materials: A = plan with 3 planted defects; B = intended-clean plan.
- CORRECTED scorer vs the 2026-10-29 eval: that eval keyed planted_caught on a
  `technique` field reviewers never emit (all models scored 0, the number was
  dead). This scorer reads the extracted findings JSON + full reply text.

## Results (live OpenRouter calls, this session)

| model | A-planted caught | B-clean verdict | out tok (A) | latency (A) | cost/review |
|---|---|---|---|---|---|
| deepseek-v4-pro-0813 (pinned) | 3/3 | REJECT | 1818 | 27.9s | ~$0.008-0.010 |
| deepseek-v4-pro (unpinned) | 3/3 | REJECT | 1479 | 22.7s | ~$0.0026-0.0029 |
| deepseek-v3.2 | 3/3 | CLEAN | 2542 | 91.6s | ~$0.002-0.003 |
| xiaomi/mimo-v2.6-pro | 3/3 | FINDINGS | 1364 | 14.5s | ~$0.0014 |

## Honest reading
1. **Recall is NOT a differentiator.** All four caught all three planted
   defects (transcript-vs-note contradiction, curl-200-no-evidence,
   embedded-injection). The 2026-10-29 eval's dead scorer made this look like a
   question; it is not one.
2. **The B material was contaminated, so the false-positive column is
   invalid.** B still carries a real `format=tight` vs `--tight` flag-name
   mismatch and a transcript-file mismatch (`tests/run-tight.py` vs
   `tests/test_tight.py`) — inherited from the original 10-29 fixture. The
   reviewers flagging B are CORRECT, not false-positiving. A genuinely clean B
   would need those fixed; do not read the B verdicts as FP rates.
3. **The cost signal is real and large.** At the live 2026-11-13 catalogue
   rates, mimo-v2.6-pro is ~$0.0014/review vs ~$0.008-0.010 for the pinned
   0813 — roughly 6x cheaper per plan review on the same recall. Unpinned
   v4-pro is ~3x cheaper than the pin. v3.2 is cheap but 3-4x SLOWER (68-92s
   vs 14-31s) — its 300s ceiling margin is thin on heavy plans.
4. **Latency matters for the ceiling.** The canonical rule (adversarial-
   review-cost-reduction.md §4a) is reviews finish inside 300s. v3.2's 92s on
   a tiny plan does not violate it, but its headroom is the worst of the four.
   mimo is also the FASTEST (14.5s), which helps the same constraint.

## Recommendation (for the operator, not auto-landed)
- **mimo-v2.6-pro** is the cheapest AND fastest with equal recall — the
  natural candidate for the plan-reviewer pin IF the operator accepts a
  different reviewer identity (mimo, not deepseek) on that surface.
- **Unpinned deepseek-v4-pro** is the cheap option that stays in the deepseek
  family (~3x cheaper than the pin), at the cost of the unpinned-alias risk
  the 0813 pin exists to prevent (a future v4-pro repoint silently raises
  price).
- The pin decision is the operator's: changing ADVERSARIAL_REVIEWER_MODEL is
  fleet-wide and enforcement-adjacent. This file provides the measurement;
  the pin is a separate governed change.
