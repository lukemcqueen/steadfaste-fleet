# The adversarial review lane — invoke it, then read the verdict back

Load when recording a plan review, interpreting a verdict, or re-reviewing material
you have improved.

## Resolve the script, then invoke it for a single cycle

The deployed and in-repo copies are not both present on every host, so resolve the
first candidate that EXISTS rather than assuming one:

```
~/.steadfaste-fleet/scripts/orch-bus/adversarial-review.py
<repo>/ops/scripts/orch-bus/adversarial-review.py
```

Run it with the fleet interpreter (the one that carries the deps) and hand it the
material as a FILE:

```
~/.steadfaste-fleet/venv/bin/python3 <resolved>/adversarial-review.py \
    --cycle-id <N> --material-file <material> --review --reviewer-id <id>
```

- **`--material-file <path>` over `--material "<text>"`.** The script reads the file, so no
  `$(cat ...)` metachar appears in the invocation — that substitution is itself
  write-class and breaks the exact-match sanctioned lane.
- **`--reviewer-id` is required** with `--review` and `--re-review`.
- **The material must be CONTENT, not a path string.** Passing a path as the material
  gets the reviewer judging the path text and answering a hollow CLEAN.
- **The flags are `--cycle-id`, `--material`, `--material-file`, `--db`, `--reviewer-id`,
  `--limit`, `--review`, `--re-review`, `--sweep`, `--dry-run`.** There is no reason or
  note flag: a re-review takes `--re-review` plus `--reviewer-id` and nothing else.

Also include the CODE MAP, the hard constraints and the REJECTED options in the
material — a plan that only asserts a design gets findings about the design.

## It can be SILENT and still have ruled

A `--review` run may exit 0 with no stdout at all and still have written the verdict.
Empty output is not evidence that nothing happened. Read the row back before
reporting, acting, or re-running.

## Read the verdict back (read-only)

The store is the loop-governance DB. Query it read-only so the DB-mutation gate is
not tripped:

```
~/.steadfaste-fleet/venv/bin/python3 -c "
import sqlite3
c = sqlite3.connect('file:' + __import__('os').environ['HOME'] +
                    '/.steadfaste-fleet/data/loop-governance.db?mode=ro', uri=True)
print([r[1] for r in c.execute('PRAGMA table_info(adversarial_reviews)')])
for r in c.execute('SELECT reviewer_model, verdict, summary, ts'
                   ' FROM adversarial_reviews WHERE cycle_id=<N>'): print(r)
c.close()"
```

**Read the column names first.** `adversarial_reviews` holds ONE row per cycle
(`cycle_id` is UNIQUE) with `review_id, cycle_id, reviewer_id, reviewer_model,
verdict, findings_json, summary, ts, fingerprint`. There is no `finding_count` —
count entries inside `findings_json` instead. Guessing a column costs a round-trip and
reads as a schema problem.

`verdict` is `CLEAN` or `FINDINGS`. Each entry in `findings_json` carries `severity`,
`technique`, `target`, `evidence` and `recommendation` — act on them. A finding that
names a contradiction between two passages of your own material is usually correct:
fix the text, do not re-argue it.

## Re-review after correcting the material

`--re-review` MOVES the current row into `adversarial_reviews_history` (stamped
`superseded_ts`) and records the fresh verdict as the new current row. Nothing is
deleted, so a superseded FINDINGS stays recoverable. It prints one line naming the
supersede; the new verdict lives only in the row, so read it back again.

Re-review is for material you IMPROVED — a corrected claim, committed proof added.
A repeated `unverified-claim` against unchanged prose is terminated by committing a
test that proves the claim, not by re-submitting the same text.
