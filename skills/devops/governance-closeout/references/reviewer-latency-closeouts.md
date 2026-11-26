# Close-gate refusals caused by reviewer LATENCY

## The shape

A close refusal can arrive with **every outstanding blob unjudged**, the message stating the reviewer
timed out before the deadline. That is a latency condition, not a defect in the range — the gate
says so itself, and explicitly says not to edit the files. Do not go hunting for a content problem
that the refusal has already told you is not there.

## Why leaving it pending is safe

Verdicts are keyed by blob CONTENT, so the next close continues from where it stopped instead of
re-judging what is already covered, and the lock's TTL releases the cycle on its own. Work that is
already committed and scored is **not** lost by an unclosed cycle — so this is a scheduling problem,
not an emergency.

## The trap: the documented remedy can become a retry loop

"Call `end_change` again for the next batch" is correct advice for a refusal that names SOME judged
and some never-judged blobs. When instead **everything** timed out, repeated immediate retries meet
the same latency: identical calls can return byte-identical results, and the harness will eventually
state that this is the nth identical call and not to repeat it.

- **Retry ONCE.** If the second reply is byte-identical, do not issue a third.
- **Never respond** by rewriting the material, re-rolling the review, opening a new cycle, or
  editing a file merely to mint a new blob — none of those address latency, and each costs a round.
- **Report the true state** instead: committed and scored, close pending on reviewer latency, lock
  releases on TTL, and the next session's close resumes it. An unclosed-by-latency cycle is an
  honest report, not a failure to conceal.

## Related

A `--re-review` SUPERSEDES the current verdict, so never experiment with it on a cycle that already
holds a real verdict.
