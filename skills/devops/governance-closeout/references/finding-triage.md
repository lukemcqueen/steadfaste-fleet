# Triaging an adversarial finding

How to work a per-blob finding without burning rounds.

## A verdict is keyed to the CONTENT of the blob that was judged

- The gate stores a verdict against the exact bytes it reviewed. Re-submitting identical bytes
  cannot produce a different verdict, so "close again and hope" is never the fix.
- **Consequence that costs rounds: answering a finding about file A by adding or editing file B does
  NOT clear A.** The range keeps failing on A while B looks fixed. If a finding says "this module's
  seam has no committed test", adding a test *file* leaves the flagged module byte-identical — plan to
  change BOTH the flagged file and add the artifact.
- When the gate names a blob as *judged and NOT clean*, change THAT file substantively. A comment-only
  edit produces a new blob (so it is re-judged), but it does not answer a finding about behaviour —
  make the file actually do the right thing.
- A seam that is *documented* but not the only code path is not fixed: if the flagged defect is "all
  callers must go through the interface", route EVERY caller (including the write/projection path and
  any self-test) through it. One bypassing caller keeps the defect alive while the check reports it
  handled.

## Measure the finding's mechanism before you "fix" it

- A finding can be right about the symptom and **wrong about the mechanism**. Confirming the mechanism
  matters: fixing a non-bug writes a false claim into the commit message.
- Settle it in isolation first — a three-line probe often decides it:

  ```bash
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'f(){ return 1; }' \
    'OUT=""; OUT="$(f)" || { echo RAN; exit 0; }' 'echo did-not-run' > t.sh && bash t.sh
  ```

- If your probe **disproves** the finding: still adopt the reviewer's clearer form if it is strictly
  clearer, but state plainly in the record that the original was NOT broken — "kept for clarity, not
  because the old one was broken". Do not claim a fix you did not make.
- If your probe **confirms** it: fix the root cause and name the measurement that proved it.

## Track outcomes; never announce a result you did not verify

- Recovery, rollback and verification paths must report per-step FACTS ("data returned: yes/no",
  "container: restored and running / restored but NOT started"), never an unconditional success
  sentence sitting after a silenced failure.
- A recovery message is the one thing an operator acts on blind, so overstating it is worse than
  saying nothing.
- Do not discard a failed step's stderr — the reason it failed is exactly what the operator needs.
- Re-check health **immediately before** destroying a rollback artefact: an earlier gate ran seconds
  ago and is a stale observation by the time you delete the escape hatch.

## Make load-bearing claims checkable

- If a comment, doc or evidence file asserts a count, a date range, or "measured from X", **count it**
  and cite the command that reproduces the count. A number whose only job is to justify a default is
  load-bearing; an unverified one is a false claim and the gate will find it. Stating a plausible
  count from memory is the failure: it reads as rigor while being unchecked.
- Prefer a claim the reviewer's window can verify — "these strings appear in the committed log",
  plus the reproducing `grep` — over an aggregate computed somewhere outside the artifact.

## The loop is meant to be productive

- Expect several rounds, and expect each round to change real content. A round that changes nothing
  substantive means either the content-keying rule above applies, or the reviewer's own constraints are
  unsatisfiable (see the SKILL.md section on a window demanding another window's content).
- Never delete, weaken, silence or bypass the check to make it pass. Fix the artifact or fix the
  reviewer's constitution — not the enforcement.
