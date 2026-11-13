# Closing a wide mechanical-sweep range (the per-blob grind)

When a close routes to "REQUIRED per-blob coverage" over a large range (many files,
e.g. a rename sweep), one deep review per file at ~4/pass means dozens of close
attempts. This recipe completes that grind.

## Drive the close with the fleet CLI, and use ONE code path the whole way

The long-lived MCP gate daemon loaded its module at gateway start, so after a deploy it runs
STALE code. `rules_rev` is derived from the loaded module, so a pass recorded under the
daemon's revision is INADMISSIBLE to the current module (and vice versa); mixing the two paths
makes the same blobs get re-judged and the uncovered count jump around (237 vs 195 for the same
range, and verdicts under the old revision stay "uncovered"). `ops/scripts/loop-gov.py` imports
the repo module FRESH on every call — choose it, and stay with it for the entire grind.

## The CLI bypasses the enforcer injection — pass `session_id` explicitly

Without it `end_change` answers "No governance session active. Nothing to release." even though
your lock is live (the enforcer normally stamps session_id onto MCP calls; the CLI has no enforcer
hook). Read the id from `~/.steadfaste-fleet/state/.hermes-session-current.id` (it equals the
lock's `session_id`):

```
loop-gov.py end_change '{"task_id": "<task>", "session_id": "<id>"}'
```

## Background-loop the close; stop on clean close, a refusal, or a stall

A `240s` window fits ~4 concurrent blob calls. Loop `end_change`, and break when the output: (a)
no longer contains "Cannot close yet" (closed → receipt written, or a refusal), or (b) says
"reviewed this pass : 0 blob" (reviewer down, or a blocking finding stalled the pass). Persist the
loop (`persist_on_release=true`) — a wide range is ~N/4 passes, minutes each, and it must survive
the session.

## A blocking finding on ONE file stops the WHOLE pass — fix, commit, resume

The pass refuses as soon as a judged blob returns a blocking finding, so a flagged file blocks the
entire close until its blob changes. Fix that file, commit it (a new blob is re-judged fresh),
resume. Verdicts are content-keyed, so a cleared finding never returns. A doc-heavy sweep is
typically N separate fix→commit→resume rounds — where you can, batch the obvious blockers into ONE
commit instead of one per round.

## The per-blob window cannot see most sibling files

A `COVERED BY tests/x.py::test` pointer only helps if that file is among the ≤6 siblings whose
added lines the window samples. Citing an in-range-but-unsampled test is read as "not in the range"
and re-flagged on the same claim. Cite only test files you can show appear in the window
(`test-consolidate-env.sh` was always visible; `test_fleet_env_resolution.py` never was), or reframe
the doc's claim as a RECORD (provenance pointer to a runbook/commit) instead of an assertable
behaviour.

## Your fix commits ride in the same range

Each fix commit grows the range, and the final CLEAN receipt covers base..HEAD including them — so
there is no need to close them separately, only to keep grinding until the whole (grown) range is
covered.
