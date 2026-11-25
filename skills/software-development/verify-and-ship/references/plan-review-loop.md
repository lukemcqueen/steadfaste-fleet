# The plan-review loop — material, verdicts, folding

How to run a PLAN through the governed adversarial reviewer and reach CLEAN. The standing
operator rule is: non-trivial builds get the plan pro-reviewed BEFORE any code is written.

```bash
<repo>/venv/bin/python3 ops/scripts/orch-bus/adversarial-review.py \
    --cycle-id <id> --review --reviewer-id <name> --material-file <path>
# after folding findings, supersede the verdict (full rewrite of the material):
<repo>/venv/bin/python3 ops/scripts/orch-bus/adversarial-review.py \
    --cycle-id <id> --review --re-review --reviewer-id <name> --material-file <path-r2>
```

- `--material-file` is required in practice: the reviewer's window sees ONLY that file, and pasting
  material inline on the command line is read as a request (filed as injection).
- The runner prints NOTHING on its success path. A supersede line ("superseded <verdict> into
  history") is the only stdout you can trust, and even that tells you nothing about the verdict.
- The cycle must be open (`begin_change`) — the review is keyed to the cycle id, one row per cycle.

## Reading the verdict from the store

```python
import sqlite3
c = sqlite3.connect("<home>/.steadfaste-fleet/data/loop-governance.db")
print(c.execute("SELECT verdict, summary FROM adversarial_reviews WHERE cycle_id=?", (cid,)).fetchone())
print(c.execute("SELECT count(*) FROM adversarial_reviews_history WHERE cycle_id=?", (cid,)).fetchone())
print(c.execute("SELECT reviewer_model, prompt_tokens, completion_tokens, reasoning_tokens "
                "FROM review_calls ORDER BY ts DESC LIMIT 5").fetchall())
```

- Run it with the project venv's python — the `sqlite3` MODULE is always present even where the CLI
  is not, so this is the portable read.
- The `verdict` COLUMN reads `FINDINGS` whenever any finding exists, including info-only ones whose
  JSON verdict is `CLEAN`. Read the severities out of the JSON in `summary` before deciding whether
  you are blocked.
- `summary` is truncated by the store; the tail of a long finding can be cut. What you can read is
  enough to act on; do not assume the text ends where the field does.
- `completion_tokens` at the caller's cap with empty content = the reviewer reasoned to the cap and
  said nothing, and a verdict may still have been recorded from that empty reply.

## Material anatomy that reaches CLEAN

The reviewer cannot open the repository, so a plan doc that cites committed files reads as
unverified-claim. Build the material by CONCATENATION, and inline the raw output:

1. **Header**: cycle, HEAD commit, range base (`git merge-base origin/main HEAD`), and a one-line
   statement of the change class ("docs only, no behaviour change") plus a REVISION NOTE when
   superseding (state that the material is a full rewrite of the final state, with no pre-fix
   wording left anywhere).
2. **File list**: `git show --stat --oneline HEAD` — every file in the range, docs included.
3. **The plan verbatim.**
4. **The committed evidence transcripts, inline** (not by path) — the raw command output with bare
   rc values.
5. **The code the load-bearing claims rest on**, quoted with line numbers: the test body, the
   delegation, the CLI, the constants.
6. **Identity of the artefacts you cite**: `sha256sum` + line counts for a file whose line numbers
   the plan uses, and say plainly when repo and deployed copies are byte-identical.

Findings that keep coming back, and the fold that clears each:

- **unverified-claim on a quoted row/status** → point it at its record: the cycle id that measured it
  and the committed transcript, in the same sentence.
- **unverified-claim on line-numbered claims** → state the ANCHOR: the revision sha the numbers came
  from, and an instruction that a later reader must re-run the probe rather than trusting them.
- **unverified-claim on internals claims** → pin each one with the actual lines (a short fenced
  block of `line  text`), not a paraphrase.
- **unverified-claim on an execution claim you inherited** → RUN it and inline the result. A plan
  that says a test "passes vacuously" is asserting a run; a green run plus the code that makes the
  guard tautological is a stronger finding than the assertion was.
- **scope-drift** → enumerate every edit the range contains, prose and doc edits included.

## Discipline that keeps the loop short

- Fold IN PLACE. Never append an "UPDATE: the above is stale" paragraph — the reviewer reads the
  upper section as the current state and files your own inconsistency as fabrication.
- Disclose a measurement you discarded: keep the wrong probe in the evidence file, labelled wrong,
  next to the corrected one. A disclosed bad measure is read as methodology; a silently replaced one
  is read as a claim.
- Disclose a pre-existing violation of a constraint your plan states (an existing module-level
  mutable counter under a "no shared mutable state" rule) and scope the constraint explicitly
  instead of leaving it to be discovered as a contradiction.
- A finding whose own `recommendation` says it is a property of the reviewer's window is
  non-blocking: fix the change, fold it if the fold is cheap, and do not re-roll the reviewer.
- Budget two rounds: one FINDINGS round is normal, and an info finding on the first CLEAN is common.
  Re-review costs one call, and a plan shipping with a fake guard in it costs the whole build.
