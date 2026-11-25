# Adversarial review brief (template)

Copy this file and fill it in. It is the MATERIAL you hand the adversarial reviewer.

## Rule zero: pass CONTENT, never a path

`adversarial-review.py --material` must receive the **text itself**, inline. Handing it a file PATH
makes the reviewer see one string (the path), so it returns a hollow `CLEAN` that covers nothing — a
review row that records no review. A cycle holds ONE review row (`adversarial_reviews.cycle_id` is
UNIQUE), so a hollow submission CANNOT be redone on that cycle: recover by closing that cycle honestly
(say the review was hollow in the accept note) and submitting the real material inline under the NEXT
cycle. Never delete or overwrite the row.

## What the brief must contain

1. **What changed, in one paragraph.** The behaviour, not the diff.
2. **The exact claim being made.** What you assert is now true — the reviewer's job is to falsify it.
3. **The evidence, quoted.** Real command output: the command, and its result. Not a summary of it,
   and not an assertion that you ran it.
4. **Blast radius.** What else touches this code path; what could break that you did not test.
5. **Known limits.** What you did NOT verify, and why. State it here rather than letting the reviewer
   discover it — an acknowledged gap is a fact; a hidden one is a finding.
6. **Files touched, with their role.** Name the blob each claim belongs to, so a per-blob window can
   see the claim and its evidence together.

## Authoring rules that prevent avoidable findings

- **Never write a claim you did not measure.** A count, a date range, or "measured from X" is
  load-bearing: run the command, cite it, and quote its output. A plausible number stated from memory
  reads as rigor while being unchecked.
- **Never claim a fix for a bug that was not there.** If you disproved a finding's mechanism, say the
  original was not broken and that you adopted the other form only for clarity.
- **Make each claim verifiable from the window.** Prefer "these strings appear in the committed log"
  plus the reproducing `grep` over an aggregate computed elsewhere.
- **State recovery behaviour per step.** "data returned: yes/no", "service: restored and running /
  restored but NOT started" — not one unconditional success sentence.

## Output shape

Blameless and factual: what the change does, the evidence, the limits, the residual risk. If nothing is
wrong, say so plainly rather than padding.
