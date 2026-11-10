# Widening an enforcement grant safely (committers, lesser-privilege roles)

When a change gives a NEW, lesser-privileged actor access to an otherwise
orchestrator-only surface (e.g. 'let Titus, along with orchestrators, update
the fleet repo'), the natural first implementation is usually WRONG and an
independent reviewer catches it. Rules that make it right the first time.

## Grant a NARROW explicit allow-list — never a blanket status flag

A lesser-privileged actor granted by 'set the same IS_ORCH=1 flag the
orchestrator uses' inherits EVERY consequence the orchestrator has — enforcer
auto-deploy, identity-forcing, and every path in the config-driven guard,
including the constitution (SOUL.md, AGENTS.md), plugins/, mcp-servers/,
skills/, tests/, workflows, hooks and the guard script itself. That is a
privilege-escalation hole, not a widening.

Do: keep the actor's privileged flag at 0; inside the one guard loop, exempt it
for an EXPLICIT, narrow allow-list (a subtree like `ops/scripts/manage/`, docs
prose) only, and only when EVERY staged path in that guard's test qualifies
('allow iff every staged file under ops/scripts/ is under manage/'). Everything
else keeps the violation.

## The config files and the guard script self-protect at the TRUE-orchestrator level

A file that grants the capability (the committer list; the orchestrator-only
path list) must be editable ONLY by the true privileged predicate, checked
INDEPENDENTLY of the widened grant — never inside the block the widened grant
short-circuits. Otherwise the new actor edits the list to add its own escalated
rows. Same for the guard script itself: re-cover `pre-commit-score`, tests/,
skills/, AGENTS.md in a SECOND hardcoded array that consults the true predicate,
so the committer cannot rewrite the predicate that admits it.

## Two independent gates is the defense, not a redundancy

A committer passing the config-driven path guard must STILL be refused the
self-editing surface by a second array gated on the true predicate. Document
and TEST both layers — otherwise a reviewer reads the config list, sees
SOUL.md in it, and (correctly) flags that a blanket bypass would open it.

## Real-git test fixtures must sandbox core.hooksPath

On a host whose git hooks are installed globally via `core.hooksPath` (fleet
hooks at ~/.steadfaste-fleet/hooks), any THROWAWAY fixture repo (tmpdir) runs
the fleet pre-commit hook inside it. The hook refuses BARE fixture commits
(`git commit` with no pathspec) — so the fixture repo ends up EMPTY and every
assertion that needs committed history fails SILENTLY (the test's `_git` helper
swallows the non-zero rc).

Fix in every fixture that creates a real git repo: point that repo's OWN
`core.hooksPath` at its `.git/hooks` (repo-level `git config core.hooksPath
<repo>/.git/hooks`, never per-invocation `-c` which the enforcer blocks as a
bypass). This is a HARNESS-environment bug, not a probe defect — the tests that
'fail for no reason' after an unrelated enforcement change are often this.

## Identity in enforcement predicates: absolute paths, EXACT match, fail closed

When the grant is keyed on host identity (home dir, hostname): resolve every
identity command by ABSOLUTE path (/usr/bin/getent, /bin/hostname, ...) so PATH
shadowing can't flip the verdict; match the committed list EXACTLY (no prefix);
fail CLOSED on unresolved identity (never fall back to $HOME/env, which an
agent can set). Read the capability list from `git show HEAD:` (committed copy),
never the working tree, so an agent cannot edit its way in.

## A wrong EXACT-match identity anchor fails SILENT — tether it

When the grant is keyed on an EXACT equality match against a committed row, a
WRONG row does not error — it silently disables the capability. The row exists,
the hook runs, nothing is marked broken, and the grantee's edits keep getting
refused with no signal the anchor is the problem. Safe, but invisible, which is
the worst kind of grant bug: it costs weeks of mysterious refusals instead of
one clear commit.

- Resolve the anchor from committed identity evidence or the operator, never
  from a side-channel inference (a home path read out of the grantee's own EXEC
  output is not the directory-services home).
- Mind the OS convention: macOS dscl resolves to `/Users/<user>`, Linux getent
  to `/home/<user>` — a bare `/<user>` row matches neither and fails silent.
- Tether the committed row with a sync test that asserts it equals EXACTLY the
  expected value, so a wrong or changed row fails LOUDLY in CI instead of
  silently disabling the capability in the live hook.

## Prove it to a reviewer with COMMITTED run-log artifacts, not prose

The close-gate and adversarial reviewer judge blob CONTENT and flag a pasted
'69 tests passed' transcript as unverified-claim. Before close, commit the
actual re-runnable logs: a `pytest -v` output file and an A4-gate output file
under the evidence dir, reference them by path, and name specific COVERED BY
test IDs in the plan. A prose claim of green tests is not committed proof.
