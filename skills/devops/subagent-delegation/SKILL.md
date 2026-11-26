---
name: subagent-delegation
description: "Use when pinning subagent models or multi-role reviews."
version: 1.1.0
category: devops
platforms: [linux, macos]
---

# Subagent Delegation

**Trigger:** "run the party on <model>", "pin subagents to <model>", "Cannot
resolve delegation provider", elicit / architecture-party via subagents,
multi-role design review.

## Pinning the subagent model

Delegation resolution order: `delegation.base_url` (direct endpoint) →
`delegation.provider` (named keychain bundle) → parent provider (inherit).
`delegation.model` applies on every branch.

**To pin subagents to a model on a direct endpoint**, set all three and leave
`provider` empty:

```yaml
delegation:
  model: <model-id>
  provider: ""          # empty — the direct endpoint takes precedence
  base_url: <openai-compatible-base>
  api_key: <key>        # read from the provider's env var, not auth.json
```

**Pitfall — the named-provider branch ignores env keys.** Setting
`delegation.provider: <named>` fails with "not logged into <name> Portal" even
when the matching `<NAME>_API_KEY` env var is exported. The named branch
resolves a credential bundle from `~/.hermes/auth.json` `credential_pool` only
(commonly just deepseek / opencode-zen / openrouter); it never reads the env
var. Fix: use the direct-endpoint form above.

**Secret-safe write:** `hermes config set delegation.model <id>` is fine for
non-secret fields, but set `delegation.api_key` via a Python yaml script
(`yaml.safe_load` → set key → `yaml.safe_dump`) so the key never lands in shell
history or logs. `write_file`/`patch` refuse `config.yaml` (security-sensitive).

**Nous inference API (verified):** base `https://inference-api.nousresearch.com/v1`,
key from `NOUS_API_KEY`; Fable model ids `anthropic/claude-fable-5`,
`anthropic/claude-fable-5.1`, `anthropic/claude-fable-latest`. List models:
`curl -H "Authorization: Bearer $NOUS_API_KEY" <base>/models`.

## Running a multi-role review as parallel subagents

When an elicit / architecture-party asks for a panel of roles, run them as
parallel `delegate_task` subagents — not sequential self-prompting:

1. **Write one design brief first** — the single source of truth every role
   reads (plus existing research docs).
2. **Two waves, not one.** Design roles first (Elicit, Architect,
   Domain/DevEx); then critique roles (Security, SRE, Product, QA) pointed at those sections.
   Critics must critique the actual design, not a strawman re-derivation of it. In a governed repo
   the children cannot WRITE those files themselves unless the brief carries the load-and-lock
   sequence (see the reviewer section below) — otherwise have each return its section as text and
   write the files yourself.
3. **Section-per-role, summary-only return.** Each child returns its full section plus a
   one-paragraph summary; the parent synthesizes the weighted matrix / recommendation itself —
   never delegate the synthesis.
4. **Pass exact read paths + output path** in each child's context; children
   know nothing of the parent conversation.

## Delegating implementation (children that commit code)

When subagents will implement AND `git commit` (not just write review files),
three extra rules apply:

- **One subagent per git repository.** The git index is shared state — two
  parallel subagents in the SAME repo collide on `git add`/`git commit` even
  when they touch different files (staging serializes the whole working tree).
  Parallelize ACROSS repos; within one repo run subagents sequentially, or have
  them do the work without committing and commit serially yourself.
- **Put the full governance sequence in the child's context.** In a governed
  repo (Steadfaste Fleet enforcer), a child's first write blocks until it loads the
  always-skills and holds a lock. Tell it explicitly: load the 7 always-skills
  in one turn → `cache_search` → `begin_change` → work with TDD →
  `feedback_accept` → `end_change`, commit through the hooks (no `--no-verify`),
  and DO NOT `git push` — the parent reviews and pushes.
- **Verify the child's self-report with real tool output — never trust it.**
  "N tests pass" is a claim, not a fact. After it returns: `git log` for the
  commits, run the test suite yourself, read the key diffs. Catch what a weak
  model misses — a script that violates a repo naming convention (the doctor
  flags it),  a graceful-SKIP that is actually an unverified check, a decision the
  child made because a slice left it open.

## Review the DESIGN before implementing it

Luke asks for this by name on any non-trivial governance or scoring change
("review with pro before you implement") — treat it as a standing gate on that
class of work, not a per-task instruction. The ORDERING is the whole point:
review the plan, then implement.

- **Review the design, not a finished diff, when the change is SEMANTIC** — when
  it alters what a label MEANS, or what a metric actually MEASURES. Plan-time
  review costs one round trip; the same discovery at diff time costs a rewrite
  of already-committed semantics.
- **Hand the child your own diagnosis as the thing to refute**, and ask it
  explicitly to test the proposal as a possible WEAKENING of enforcement whenever
  it touches a gate, a label or a threshold. A review asked only for correctness
  will politely confirm a plausible-but-wrong design.
- **Dispatch one review child at a time.** Two pro children in flight at once can
  402 (credits / in-flight limits) — serialise them rather than fanning out.
- **A design review is read-only work.** Say so in the brief and tell the child
  to return the review as the TEXT of its final answer (see the write-gate note
  below), so it does not burn its passes on refused file writes.
- **Persist the returned review immediately** — see the persistence rule in the
  reviewer section below; a design review that is compacted away is scope lost.

## Using children as external reviewers (their findings are CLAIMS)

A review child sees a bounded window — its own context, a truncated diff, no terminal — so its
findings are claims to verify, not conclusions to act on:

- **One concern per child, exact read paths in, one output out.** Two focused roles
  beat one general reviewer (e.g. the process/evidence side and the technical-correctness side),
  and each must be told explicitly not to duplicate the other's mandate.
- **A child cannot write ANY file — not even to the scratch dir — without a governance lock.** The
  write gate is keyed on the TOOL CLASS, not on the path, so "write it outside the repo" does not
  exempt it: measured, two children dispatched for a design pass were both refused, one after the
  other, and returned their artifacts as TEXT instead. Two workable shapes, pick one in the brief:
  (a) put the full governance sequence in the child's context (load the 7 always-skills in one turn →
  `cache_search` → `begin_change` → work → `feedback_accept` → `end_change`) when it must persist an
  artifact; or (b) tell it plainly to RETURN THE FULL TEXT as its final answer and not to fight the
  gate, which costs nothing for a design note, a review or a slice list. Do not write a brief that
  assumes the scratch write will land.
- **Classify every finding before acting: confirmed / refuted-by-measurement / environment.** Fix
  only the confirmed ones; for a refutation state the measurement and change no code, because
  "fixing" a non-defect to appease a finding is a defect in itself. A finding about the
  reviewer's own transport or credentials is environment — fix the setup, not the change.
- **Persist a review/scan deliverable to a file the MOMENT it returns.** A subagent's
  structured output arrives as a message in the parent session; if the session is compacted
  before you act on it, that deliverable is gone — a scan brief, a candidate table, a spec is
  not re-available from `session_search` (the archive may hold only the transcript frames, not
  the child's final JSON). Write it to `docs/evidence/` (or scratch) as the first action after
  the child returns, so the scope of the work survives the next compaction.
- **Re-run the scan, do not improvise, when a brief was compacted away.** If a subagent
  deliverable that DEFINES a task (candidate list, ranked findings, spec) was compacted out and
  the archive no longer carries it, do not reverse-engineer scope from half-recalled titles —
  re-spawn the same read-only review subagent against the current code to regenerate it, and
  bind the work to that. This is especially true for mode-changing refactors on live systems
  (a bus client, an env resolver, a deploy path): guessing the seam from a title risks breaking
  a deliberate capability the brief explicitly protected.
- **Refute with the smallest decisive artifact, not with prose.** A finding that misreads material
  the child could only partly see ("the range diff puts the copy-back in the wrong commit") is
  settled by the per-commit listing — `git show --stat <sha>` for each commit in the range — which
  also disproves it for the NEXT reviewer. Commit that listing as evidence; re-explaining it in the
  note is what the gate grades as an unverified claim.
- **A child's probe can be the bug.** When a finding's evidence is a count, a path or a regex the
  child wrote, verify the probe before rewriting the code it accuses — a wrong finding belongs to
  the check, and the same discipline applies to a child's claims as to your own.
- **Every question you need answered must be a REQUIRED `output_schema` field.** A brief that asks
  eight questions but requires four fields gets four answers: the child fills the schema, answers
  those in full, and silently drops the rest — the free-text summary does not reliably carry them
  either. Measured: the one question a session most needed answered (a blocked close gate) came
  back absent while seven easier ones were answered thoroughly. Make each question a required field,
  and treat an unanswered question as UNASKED — dispatch a focused follow-up child rather than
  assuming it was read and declined.
