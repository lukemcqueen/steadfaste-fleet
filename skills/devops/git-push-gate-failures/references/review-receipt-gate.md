# The review-receipt push gate — close, drain, verify

Depth for section 7 of SKILL.md: the gate that requires a CLEAN receipt before a
range touching an always-review path may be pushed, and how to get one.

## Order of operations

The receipt is written by the GOVERNANCE SERVER at a PERMITTED close
(`end_change` → review gate → `_write_review_receipt`), never by
`ops/scripts/orch-bus/adversarial-review.py` (which only records an LLM verdict row).
So the order is **close, then push** — backwards from intuition, and by design: the
review that used to land after the push now gates it.

Push as a LONE `git push`. The enforcer refuses a compound (`git push && …`,
`git push | tail`, `cd X && git push`) because it cannot prove the shape. A CLEAN
receipt authorises a push with NO active lock — the receipt is the authorisation — but
only for the bare command. Verify in a separate call.

## Two paths through a close — simple in bulk, complex a few at a time

One pass is one WAVE of `BLOB_REVIEW_CONCURRENCY` = 4 reviewer calls; a close spends at
most `BLOB_REVIEW_CAP_PER_CLOSE` = 15 blobs. A blob already judged is skipped by CONTENT,
never re-sent, so a close resumes rather than redoing.

- **Many simple or same-shaped edits → ONE pass.** Similar small edits are judged together
  in the wave; a docs-only range (class **A0**) skips the model judge entirely — no call at
  all. Do not split a range of simple edits across cycles.
- **Complex work → a few per pass, then close again.** Class **C** is judged a few blobs at a
  time and the next `end_change` continues from there, so a wide range drains as a SERIES of
  closes. Watch the `CLEAN in the range` count climb; stop when the close is permitted.
- Classes (`_review_class`, computed from the diff's PATH SET alone): **A0** every non-noise
  path is documentation; **B** a code change that also carries a test delta (light judge);
  **C** everything else — always-review/protected surface, untested code, or an unreadable
  path set (deep judge).

## A narrowed description is the usual reason a close keeps refusing

The per-blob requirement is that the task description cover EVERY path in the range, or that
file reads as scope drift — and one session's range is usually several topics. A narrow task
id cannot be repaired by re-closing. The exit:

1. release the cycle — `advance_task_state(task_id=…, new_state='cancelled', reason=…)`; a
   cycle that was already scored holds nothing and leaves no PENDING row;
2. open ONE cycle whose description enumerates the range's areas BY PATH;
3. close again. A fresh cycle also RECOMPUTES the range, so it reports the true uncovered set
   rather than a stale one (it may drop from 30 blobs to the 1 genuinely unjudged).

## What the per-blob reviewer will ask for

The open message lists the session's real requirements. The one that recurs, and the cheapest
to satisfy up front:

> a file must not lean on a sibling the review window cannot see — state the contract in the file.

One window judges ONE file, so put a short provenance/contract header at the top:

- what it loads or calls from a sibling IN THE SAME RANGE;
- the commit providing that sibling — the sha AND its subject, so the pointer survives a
  history rewrite;
- what the tool does when the dependency is ABSENT: a clear refusal or a reported
  "unavailable", never a traceback.

Scripts and test files both carry this well, and stating it pre-empts the finding.

## Diagnosing a close that will not move ONE blob

**Trust the STORE, not the message.** The zero-work line reads "no blob was SENT this pass:
every blob still outstanding already carries a not-clean verdict for its exact content" — that
is FALSE for a blob whose content changed since the verdict, and the same line is printed when
every reviewer call FAILED. Check the content-keyed store by sha:

```sql
-- db: ~/.steadfaste-fleet/data/loop-governance.db  (NOT state/loop-governance.db)
SELECT verdict, rules_rev FROM blob_reviews WHERE blob_sha = '<sha of git rev-parse HEAD:<path>>';
```

NO row means never judged — so the not-clean verdict the message named belongs to an EARLIER
sha of that path.

**Find the real cause in the server log.** The per-blob pass logs one line per attempt:

```
grep "per-blob review" ~/.hermes/logs/mcp-stderr.log | tail
# … <path> answered and judged nothing (unparseable or verdict-less reply)
```

That reply is a reasoning model returning nothing parseable — the same class the module's own
docstring records. It hits large and tiny files alike, so SIZE is not the trigger, and it is
not a defect in the file under review.

**The working remedy is a CONTENT CHANGE.** Identical bytes re-sent cannot produce a different
reply, so re-closing unchanged content is pure waste — the counts do not move. Make a genuine
edit to the file (a new sha is a fresh prompt) and close again; it clears on the next pass.

## Verify the receipt exists — it is a DOTFILE

The receipt lands as `<state>/.reviewed-<slug>-<tip>.json`. A plain `ls` misses it and
`ls | grep reviewed` reports "nothing" on a receipt that exists. Confirm with `ls -a`, or read
the server's own line:

```
grep "review receipt written" ~/.hermes/logs/mcp-stderr.log | tail -1
# review receipt written: .reviewed-<slug>-<tip>.json (tip …, base …, N files)
```

A close only PRINTS about a receipt when writing it FAILED, so silence there is good news.

The receipt is bound to the RANGE (base…tip), but the gate accepts ANY receipt whose
`reviewed_blobs` cover every file the range changes — which is how a rebase that rewrote the
tip can still push, and what `ops/scripts/lib/review-receipt-check.py` `--scan` decides.

## The deploy chicken-and-egg — a deploy-only lock-carrier cycle

The push gate also requires a doctor-clean DEPLOYED state, while the deploy
(`fleet-dogfood.sh --force`) is WRITE-class and itself needs a repo lock. After the owning
cycle closes, the lock is gone and the deploy is blocked, so the two gates appear to deadlock.
The sanctioned exit is a deploy-only cycle:

```
begin_change(task_id="deploy-<thing>",
             description="Lock-carrier cycle for a DEPLOY-ONLY action: deploy and push the "
                         "already-reviewed range <sha> (cycle N, closed CLEAN with a receipt). "
                         "No source change is made on this cycle.")
# then: dogfood --force → lone `git push` → feedback_accept → end_change
```

State plainly in the description that no source changes are made — that is what the cycle's own
review needs to agree with. Background the dogfood run with notify (a foreground call with a
normal timeout is killed mid-deploy) and expect a close warning `receipt NOT written … lists no
files` once the range is already pushed (benign: an empty range has nothing to authorise).

Deploy is the LAST content step: a commit made after the deploy leaves the deployed tree
trailing HEAD, which fails `Deploy sync` and blocks the push.

## Two more traps

- **`adversarial_reviews` has one row per cycle.** A second `--review` for the same cycle answers
  "already reviewed (UNIQUE constraint)" and does NOT re-judge; a fresh judgement needs
  `rereview_change` (requires a note stating what changed plus its evidence).
- **A review whose material was a PATH, not content, returns a hollow CLEAN** — the reviewer
  reports `unverified-claim` on the path itself ("the window contains only this path"). Pass the
  plan/diff CONTENT as material, and treat a CLEAN that names an unreadable window as no approval.
