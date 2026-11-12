---
name: enforcer-modification-considerations
version: 1.0.0
category: devops
description: "Use before modifying any enforcer/governance code."
author: Moses
platforms: [linux, macos]
---

# Enforcer Modification Considerations

**Load this skill BEFORE modifying `plugins/governance-enforcer/__init__.py`, `mcp-servers/loop-gov-mcp.py`, or any governance enforcement code.**

---

## 1. Session Types — Know Your Prefixes

Hermes creates sessions with different ID prefixes depending on the session type. Every guard in the enforcer must cover ALL non-interactive prefixes.

| Prefix | Source | Marker behaviour (per-session, 2026-08-01+) |
|--------|--------|-----------------------------------------------|
| `20260730_...` (date-based) | Interactive Telegram/DM / CLI | Owns its own marker file — never touches other sessions' |
| `cron_...` | Scheduled cron job | Owns its own marker (bootstrap-created) |
| `bg_...` | Background subagent (delegate_task, etc.) | Owns its own marker |
| `cli`-source date-based | CLI-invoked session (cron/terminal `hermes` runs) | Owns its own marker |

**Per-session markers (2026-08-01):** the shared `.skills-loaded` file race is
**structurally eliminated** — each session writes its own proof at
`state/skills-loaded/<session_id>`, so no session can ever stomp another's.
The entire guard class bolted onto the shared file (daemon guard, subagent
guard, sticky-marker-per-governance-lock) is obsolete and was removed.
This was gap-doc P1-A candidate #3 ("per-session marker files"), the
documented structural fix.

**Rule:** When adding a session-type guard, use `session_id.startswith("cron_") or session_id.startswith("bg_")`. Do NOT create separate `is_cron` and `is_bg` booleans — future prefixes will be missed.

**Guard locations in the enforcer:**
- `_auto_create_skills_marker()` — writes `state/skills-loaded/<session_id>` (atomic temp+rename)
- `_check_skills_loaded_marker()` — reads THIS session's own marker; exact content match
- `_on_session_start()` — non-interactive bootstrap creates the session's OWN
  marker (`_session_type(session_id) in ("cron","bg")` — cron_ OR bg_ prefixes)

**Checklist when modifying:**
- [ ] Search for ALL existing prefix guards: `grep -n 'cron_\|bg_' plugins/governance-enforcer/__init__.py`
- [ ] Does the guard also apply to `bg_` sessions?
- [ ] Is there a NEW session prefix we don't know about yet? Make the guard generic if possible (e.g., `not session_id.startswith("2026")` as fallback).

---

## 2. The DOGFOOD Chicken-and-Egg

The `begin_change()` MCP tool has a DOGFOOD check: if the deployed enforcer (`~/.hermes/plugins/governance-enforcer/__init__.py`) differs from the repo source (`plugins/governance-enforcer/__init__.py`), `begin_change()` rejects the request.

**The problem:** This blocks a new change when the mismatch is caused by ANOTHER agent's commit arriving via `git pull` — not by un-deployed local work.

**How to break the loop (fixed 2026-08-04 — the sanctioned command is now lock-free):**

1. Run the sanctioned recovery — the enforcer allows this EXACT command
   without a governance lock:
   ```bash
   bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh
   ```
   (No sudo, no chaining, no other flags beyond
   `--dry-run/--status/--delta/--clean-stale` — the
   enforcer's `_is_sanctioned_cortex_update_command()` matches exactly.)
2. Load all 8 always-section skills: `skill_view()` calls
3. `begin_change()` should now succeed (repo == deployed)

**If the skills gate blocks the terminal call** (no skills marker yet):
- Load the 8 always-skills first, then re-run the command above.

---

## 2a. Hooks Fire in Project Repos Too — Never Assume the Cortex Tree

`core.hooksPath ~/.steadfaste-fleet/hooks` is global: the pre-commit/pre-push
hooks run in EVERY git repo on the host, including project repos (client-alpha,
client-works, and other client repos) that have no `ops/` tree and no `.steadfaste-fleet/`
directory inside them. Any hook/enforcer path built on `$REPO_ROOT` (the repo
being committed IN) and assuming cortex layout will break every commit there.

**Regression 2026-08-04 (`faa0e929` → fix `72d6cdc3`):** the adversarial gate
hard-resolved to `$REPO_ROOT/ops/scripts/quality/adversarial-verify.py` —
project repos lack that path, so ALL their commits failed fail-closed.
Fix pattern: candidate loop, repo-local first, then the canonically-deployed
copy at `$HOME/.steadfaste-fleet/scripts/` (registered by fleet-update.sh on
both Linux and macOS). Fail closed only if BOTH are missing. See
`enforcement-change-safety` Rule 6 for the full pattern and the 3-branch
verification (fleet repo / project repo / tool-missing).

When modifying hooks or the enforcer, test in a scratch project repo
(`git init /tmp/t && git config core.hooksPath ~/.steadfaste-fleet/hooks`)
before shipping — a cortex-repo-only test proves nothing about project repos.

## 2b. Deploy ≠ Load — Settle It by OBSERVATION, Not by Assumption

`fleet-update.sh` copies the new enforcer to
`~/.hermes/plugins/governance-enforcer/` and re-locks it. Whether the RUNNING
gateway already executes it is a separate question, and the deploy's own
`GATEWAY RESTART REQUIRED` banner is emitted when the deploy CANNOT determine the
gateway's start time — a conservative warning, not a verdict on what is in memory.
**MEASURED counter-example:** an enforcer whose new lock carve-out was already IN
FORCE right after a deploy (a lock-free push of a receipt-covered range was
allowed, with no restart) while the banner still read restart-required.

- **Verify by OBSERVATION:** drive the path the change adds and watch the behaviour
  it is supposed to produce. Grepping the deployed file proves the CODE landed, never
  that the process loaded it.
- **The symptom that a restart really IS needed:** repo == deployed (SHA256 match)
  yet the changed behaviour simply never appears — the sanctioned
  `bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh --status` still returns
  `GOVERNANCE LOCK REQUIRED`, or a new block-message string never shows.
- **Fix when it is needed:** `hermes gateway restart` — agents cannot run it
  (lifecycle guard); the host operator must. Verify with the positive control
  (`--status` passes lock-free) and a negative control (`... && echo hi` blocked).
- **Don't** re-run fleet-update.sh repeatedly or hand-edit the deployed copy to
  force activation, and never write "a restart is required" into a doc, note or issue
  without the observation behind it: a doc that makes the next agent wait for a restart
  it does not need costs more than the restart would have.

## 2c. Severing the Write-Gate — the in-process plugin is the coupling to move

"Restart the Hermes gateway to load new enforcer code" happens because the enforcer
is a Hermes PLUGIN loaded in-process at gateway startup (deploy ≠ load, 2b). Decoupling
it so the changing RULES reload by restarting OUR unit, not the incumbent, is pinned by
one architectural fact:

- **An MCP server cannot take over the write-gate.** MCP servers PROVIDE their own
  tools; they never intercept (block) the gateway's native `terminal`/`write_file`/
  `patch` calls. So "make the enforcer a standalone MCP server" is not a viable shape
  for a PRE-write block — only commit/push-time hooks fire outside the gateway, and
  those lose the pre-write lock discipline. Do not plan S2c-style "MCP-ify the enforcer"
  on that model; it cannot gate the native tools.
- **Prior art FIRST — check whether the adapter already exists before designing one.**
  A one-implementation/per-host-adapter pattern is usually already in the tree: a
  shared CLI + MCP surface over one module, at least one host wired to it, and a
  harness registry declaring each host's capability. Grep for the predicate the gate
  needs (a `check_repo_lock`-shaped one) and for a per-host adapter directory BEFORE
  writing anything. When a sibling host is already an adapter, the work is *make this
  host the next adapter*, not *build a service* — a plan that says "build" where
  "extend" was correct overstates the change and invites standing up a second
  implementation of something live.
- **Viable shape: a thin, stable in-process shim that delegates the allow/deny
  decision OUT OF the host process.** The shim keeps the plugin hook (so native writes
  stay gated as they happen) and must change almost never; the RULES + DB logic — the
  part that actually changes — live outside the host process. The trade-off to surface
  to the owner: keeping the in-process shim means one small dependency on the incumbent
  remains; dropping it to a hooks-only gate loses pre-write enforcement. Settle which
  with the owner before building — it is a security semantic, not a style choice.
- **PREFER a subprocess-per-decision over a daemon — that is what makes the reload
  automatic.** A fresh subprocess per gate call always loads the current file, so a
  rule change takes effect on the NEXT call with no restart of ANY unit; a long-lived
  daemon reaches the same freshness only by restarting its own unit, and adds a port, a
  health check and a listener to secure for no extra benefit. A daemon is the fallback
  when per-call spawn cost is measured to matter, never the default.
- **The sanctioned RECOVERY command's recognition must stay FROZEN in-process and be
  checked BEFORE the delegated call — the recovery lane must not depend on the system
  it recovers.** When the classify/lock decision moves out of process and fails closed
  (contract missing, CLI unreachable), fail-closed would also block the sanctioned
  `fleet-update.sh` invocation itself — recreating the DOGFOOD deadlock the lane exists
  to cure. Keep ONE tiny, stable, exact-match regex for the recovery invocation in the
  plugin, evaluated before the subprocess. It never changes, so it does not undermine
  the reload goal. Acceptance: with the contract deleted and the CLI unreachable, the
  recovery invocation still passes and a non-recovery write still blocks — both
  directions asserted.
- **The delegated subprocess must be a fast, stdlib-only access layer — never the full
  governance CLI.** A governance CLI that imports the whole MCP server module per call
  costs hundreds of ms on the hot path; the classifier CLI must import ONLY the
  contract (stdlib-only) so one cold start stays in the tens of ms. Measure the
  cold-start-to-verdict latency (p50/p95 over ~20 runs, recorded in the commit) and
  GATE the subprocess-per-call wiring on it (a 150 ms p50 bound is a sane gate) — do
  not adopt the architecture on an unverified "it's fast" estimate; data-driven rules
  with an mtime-fingerprint refresh (the skills.yaml manifest pattern) is the fallback
  when the cost measures badly.
- **Look for the SECOND implementation before extracting the first.** An adapter that
  correctly delegates the lock/coverage decision can still carry its own copy of a
  DIFFERENT gate — a read-only command allowlist, a write-class classifier, a path
  matcher. Two tables that must agree, with nothing asserting that they do, is a live
  drift risk; it is in scope for the extraction, so unify it and add a drift test that
  drives BOTH against one corpus.
- **Extract with a PARITY gate FIRST — the original is the spec.** Before wiring anything,
  build a test that loads the ORIGINAL enforcer from its real file (importlib) and asserts the
  extracted contract returns the identical verdict on a corpus spanning the whole surface
  (allowlists, write-class rules, every sanctioned lane, the evasions, tool-name dispatch).
  "Works as well as the enforcer" is then a passing test, not a promise. When a corpus case
  diverges and parity otherwise holds, the ORIGINAL is the spec: fix the test case, not the
  extraction — the diverging case is usually a latent quirk of the original (e.g. an allowlist
  that anchors the subcommand position, so a flag between the head and the subcommand keeps a
  read command write-class) that the migration must PRESERVE. Full ordering:
  cross-agent-design's migrate-a-host-bound-capability section.
- **Verify the severance by observation, same as 2b:** when a rules change lands via
  fleet-update.sh, the load-bearing symptom is the new rule firing (or the old one
  still firing) — never "I restarted our unit so it must be live."
- **Keep function NAMES as thin delegating wrappers — gate tests import internals by
  NAME, and removing a name breaks pytest collection before a table ever would.**
  Test files bind module internals at import time (`_is_terminal_write =
  enforcer._is_terminal_write`, `enforcer.WRITE_COMMAND_PATTERNS`); delete a name and
  the whole file dies with `AttributeError` during collection, fast and before any
  assertion runs. When a table moves to the contract, keep every exported predicate
  as a small delegating wrapper over the new source (same name, same call shape), and
  purge the now-dead imports from the tests as part of the SAME move — a symbol
  imported but never used still kills collection if it is gone.
- **Count parents from the PLUGIN's own depth when resolving a sibling CLI — the
  plugin file is three levels under the repo root.** `plugins/governance-enforcer/
  __init__.py` is `parent` -> `plugins/`, `parent.parent` -> the repo root, so a
  `parent.parent` from the plugin file actually lands in `plugins/` and the sibling
  never resolves. Failure fingerprint: the resolver returns None, the delegation
  fails CLOSED, and every classification test fails in about a second with
  "loop-gov-classify.py not found" in the log — a fast, silent, misleading red.
  Repo candidate from the plugin = `parent.parent.parent`; keep the deployed copy
  as the second candidate for hosts where the plugin was copied rather than
  symlinked (resolve() follows the symlink on symlinked hosts, so this belt is
  real).
- **Doctor source-grep coverage checks move WITH the code — re-point them in the same
  change.** The doctor's bypass-coverage check greps the enforcer source for closure
  markers (`python3 -c`, `bash -c`, script-exec, passwd-group patterns). Move a
  table to the contract and the markers vanish from the enforcer, so the doctor
  FAILs the deploy that ships the move. Re-point the check at the new home, then
  verify with the DEPLOYED doctor (`~/.steadfaste-fleet/scripts/fleet-doctor.py` —
  `ops/scripts/manage/fleet_doctor.py` is a package, not a CLI entry) AFTER
  redeploying: editing the repo checks.py changes nothing the deployed doctor reads
  until fleet-update.sh runs.
- **Doctor coverage checks do SUBSTRING matching, not regex search — quote the
  marker text AS THE SOURCE WRITES IT, escape-for-regex silently fails.** The check
  is `r"...\s" in src`-shaped: the pattern string is matched literally against
  the source, so a marker the source spells `\s` (one backslash) must be written
  `\s` in the check — writing the regex-source escape `\\s` (two backslashes)
  matches nothing and the check fails while looking correct. When a re-pointed
  check still reports a gap, `cat -A` the contract line and compare the literal
  backslash count before touching the logic.
- **After an enforcer commit, every FOLLOW-UP commit is DOGFOOD-blocked until the
  deploy lands.** The pre-commit hook refuses any further commit while the deployed
  enforcer differs from the repo source. Sequence it: commit the enforcer change,
  run `fleet-update.sh` immediately (it is the sanctioned lock-free lane), THEN make
  the follow-up commits — never a gap of enforcer-commit-first, fix-later.

---

## 3. The Skills-Loaded Marker Race (FIXED — per-session files, 2026-08-01)

**The old design:** `.skills-loaded` at `~/.steadfaste-fleet/state/` was a single
file. ALL concurrent Hermes sessions wrote to it. When session B loaded skills,
it overwrote session A's marker. Session A's writes were then blocked because
`_check_skills_loaded_marker()` saw session B's ID.

**The fix:** per-session marker files at `~/.steadfaste-fleet/state/skills-loaded/<session_id>`,
plus per-session state at `state/skills-state/<session_id>.json`. Each session
writes and reads only its OWN files — concurrent sessions (telegram, cli 1,
cli 2 on one server) can never stomp each other. The daemon guard, subagent
guard, and sticky-marker-per-lock rules were removed; they're no longer needed.

**If your writes are blocked (marker missing or wrong content):**
```
cat ~/.steadfaste-fleet/state/skills-loaded/<your-session-id>
# → session:<your-session-id>  (valid)
# → empty / missing            (skills not loaded this session)
```

**Fix:**
1. Load all 8 always-section skills via `skill_view()` → your session's marker
   is auto-created
2. Write tools unblocked

**Legacy files:** the old `~/.steadfaste-fleet/state/.skills-loaded` and
`skills-state.json` files are inert — the enforcer no longer reads them. They
can be deleted once all sessions have restarted with the new enforcer.

---

## 4. Multiple Repos, One Hermes

When a single Hermes installation manages multiple repos (e.g., steadfaste-fleet + other projects), the `repo_slug` field in governance lock files must be derived dynamically, not hardcoded.

**The problem:**
- Lock file at `~/.steadfaste-fleet/state/.governance-{session_id}.json` contains `"repo_slug": "steadfaste-fleet"`
- The pre-push hook expects `"steadfaste-fleet"`
- If the agent cloned the repo as `hermes-agent` (different name), the slug won't match

**Fix:**
- Shell scripts that create manual locks: derive slug from `basename "${CORTEX_REPO:-${HOME}/steadfaste-fleet}"`
- Python/MCP: use `_derive_repo_slug()` which runs `git rev-parse --show-toplevel` and takes the directory basename

**Files to check:**
- `ops/scripts/manage/agent-nginx-threat-pipeline.sh` — hardcoded slug (fixed)
- `ops/scripts/agent/agent-ip-submission.sh` — hardcoded slug (fixed)
- `plugins/governance-enforcer/__init__.py` — dynamic via `_derive_repo_slug()` ✓
- `ops/scripts/pre-commit-score` — dynamic via `git rev-parse` ✓

**Search pattern for hardcoded slugs:**
```bash
grep -rn '"repo_slug"' ops/scripts/ plugins/
```

---

## 5. Stale Hooks Allow Orchestrator Path Bypass

The pre-commit hook enforces orchestrator-only paths (from `docs/orchestrator-only-paths.txt`). If an agent has stale hooks (hasn't run `fleet-update.sh` after the guard was added), non-orchestrators can modify restricted paths like `plugins/`, `skills/`, `ops/scripts/`.

**The fix chain:**
1. Doctor's `check_hook_drift()` detects deployed hooks that differ from repo source
2. Agent runs `fleet-update.sh` to redeploy hooks
3. Pre-commit hook now blocks non-orch modifications to restricted paths

**If you're adding a new restricted path:**
1. Add to `docs/orchestrator-only-paths.txt`
2. Verify it's covered by `AGENTS.md` which lists the canonical restricted paths
3. The pre-commit hook reads the file at commit time — no code change needed

---

## 5a. Changing a fleet-committer capability — the deny-list boundary and its tests

The committer boundary in `ops/scripts/pre-commit-score` is a DENY-LIST,
not an allow-list: a committer may write ANY fleet path EXCEPT
`_COMMITTER_DENY_FILES` (exact names: core mcp-servers, deploy/guard
scripts, review pipeline, both config files, AGENTS.md/SOUL.md) and
`_COMMITTER_DENY_DIRS` (prefixes). Identity lives in
`docs/fleet-committers.txt`, keyed by the DIRECTORY-SERVICES home
(resolved via getent/dscl — a macOS NFS home like `/Users/<name>` is the
format, NOT the Unix username), exact-match against the COMMITTED copy. A
second hardcoded `ORCHESTRATOR_ONLY_PATHS` array is committer-RELAXED by
design; `docs/orchestrator-only-paths.txt` governs plain non-committer
agents only — opening a path for committers needs no change to the config
list.

Moving a path out of (or into) the deny boundary:

- **Flip the tests FIRST.** `tests/test_orchestrator_identity.py` extracts
the REAL deny arrays and the matching loop from the committed hook and
pins an expectation for every deny dir and every ALLOWED/DENIED verdict —
TDD RED on the test change, then the hook edit, then GREEN. A hook change
without its test flip breaks the suite.
- **Audit the describers in the same commit.** AGENTS.md
§Orchestrator-Only Domains, the header comments of
docs/orchestrator-only-paths.txt, and any skill row describing who may
write the path all go stale the instant the deny-list moves — edit them
in the SAME change.
- **Opening a path is not opening the gates.** The committer's commit on
the opened path still runs the full chain (adversarial scan, review
receipt, doctor, PII).
- **The two config files are self-protecting** (`docs/fleet-committers.txt`,
`docs/orchestrator-only-paths.txt`): editable ONLY by a true orchestrator
(`_detect_orch`), never by the committer-widened IS_ORCH — otherwise a
committer adds its own home row or lifts a restriction (privilege
escalation). Keep that gate where it is.

---

## 6. DOGFOOD Auto-Deploy vs `fleet-update.sh`

Two mechanisms deploy the enforcer:

| Mechanism | Trigger | How | Caveat |
|-----------|---------|-----|--------|
| Pre-commit DOGFOOD | `git commit` | Copies repo → deployed + unlock/lock | Only runs during commits |
| `fleet-update.sh` | Manual run | `deploy_governance_plugin()` function | Runs at end of deploy cycle |

**The DOGFOOD auto-deploy is faster but only works during commits.** If another agent's enforcer change arrives via `git pull`, the DOGFOOD check in `begin_change()` blocks you because the deployed copy is stale. Run `fleet-update.sh` to sync.

---

## 7. Testing Enforcer Changes

When modifying the enforcer, test these scenarios:

- [ ] **Interactive session:** write tools work with correct marker
- [ ] **Concurrent interactive sessions (multi-session):** session A and session B both load skills — neither blocks the other (regression test: `TestSkillsMarkerPerSession::test_second_session_does_not_invalidate_first`)
- [ ] **Cron session:** bootstrap creates its OWN marker without touching interactive sessions
- [ ] **Background session (`bg_`):** owns its own marker
- [ ] **Touch bypass closed:** empty/whitespace marker file → rejected
- [ ] **DOGFOOD sync:** deploy via `fleet-update.sh` — does `begin_change()` work?
- [ ] **Cross-machine pull:** another agent's enforcer commit arrives via pull — does DOGFOOD handle it?

### Test a gate predicate against the REAL payload, not a fixture you wrote

A predicate that CLASSIFIES material is only as good as its inputs, and a hand-written fixture is
where it lies to you: build the case from the record the server actually stores — the row in
`adversarial_reviews`, the cycle's real note, the actual `git log -p` diff the review composes —
and evaluate the OLD and the NEW predicate side by side on it in the committed test. A fixture that
spells a marker the way you remember it, rather than the way the producer writes it, passes while
real material still fails; a length-only diff filter written against a small change lets control
files through the moment they are large enough. Composition matters too: material built with
`git diff` differs from material built with `git log -p`, so verify the predicate against the
composition the review itself uses, not a convenient one.

### Put the durable half of the rule in the PROMPT, and keep the predicate narrow

The reviewer's constitution (`docs/templates/adversarial-reviewer-prompt.md`) is the half that
survives: it INSTRUCTS the judge, so it applies to material the predicate never sees. The predicate
only classifies what the judge returns. When a class of finding must not block a close, say so in
the prompt AND keep the predicate narrow enough that it cannot swallow a genuine finding — a
classification that quietly stops enforcing is worse than the friction it removed.

**Quick test for per-session markers:**
```bash
echo "session:test" > ~/.steadfaste-fleet/state/skills-loaded/test-session
# Check: does _check_skills_loaded_marker("test-session") accept only exact content?
```

---

## 8. Cron/bg Sessions Are Exempt From the Domain-Skill Gate (2026-08-07)

The domain-skill gate (`_check_domain_skill_gate`) is an EDUCATIONAL mechanism
for interactive sessions: it makes the agent load the craft skill (e.g.
`documentation-auditing` for `.md`) before writing. Cron sessions (`cron_`
prefix) and background subagent sessions (`bg_` prefix) execute pre-vetted
prompts whose write targets were declared at install time, and their
`enabled_toolsets` may exclude the skills toolset entirely (e.g.
`["terminal","file"]`) — `skill_view()` is NOT in the tool registry, so the
gate is structurally unsatisfiable and every write deadlocks.

**Regression 2026-08-06:** `agent-mycortex-dream-nightly` (toolsets
`[terminal,file]`) was blocked writing `~/brain/<agent>/dreams/*.md` because
the `.md` extension demands `documentation-auditing`, which the cron cannot
load. Same class affects memory-pruning (MEMORY.md), agents-md-prune-apply
(AGENTS.md), soul-refinement (SOUL.md), orch-skill-lifecycle (skills/).

**Fix (commit b8bbaf52):** `_check_domain_skill_gate` returns None early when
`_session_type(session_id) in ("cron", "bg")`. This mirrors the always-skills
cron bootstrap rationale (cron agents may not have skill_view()). Security is
NOT weakened: PII content gate, adversarial commit gate, bypass-debt mandate,
and governance lock still apply to cron/bg writes.

**Checklist when modifying the domain gate:**
- [ ] Do NOT add path exemptions for specific cron write targets (dreams/,
      MEMORY.md, etc.) — the session-type exemption is the general fix.
- [ ] Keep interactive enforcement: `_session_type()` returns "interactive"
      for date-format and empty session IDs — the gate still fires for them.
- [ ] Tests: `tests/test_runtime/test_governance_bypass.py::TestDomainSkillGate`
      — cron/bg pass, interactive blocks, empty/None session still enforced.
- [ ] The gate's educational purpose lives in the BLOCK message — if you ever
      re-enable it for crons, crons MUST have the skills toolset in
      `enabled_toolsets` or every write deadlocks again.

## 9. A Gate That Demands a Record Its Target Cannot Write Is a Deadlock, Not a Gate

When a gate requires a record (a review row, a receipt) that is written by an
ORCHESTRATOR-side tool — and producing it resolves to a second repo/lock the target
cannot hold while its own cycle is pending — the target structurally cannot satisfy
the gate that gates ITS OWN commit. The deadlock is real and the operator's fix
direction is explicit: the review's PURPOSE is the value (a review before building is
ALWAYS better, plus one after to check), so NEVER delete the gate to unblock the
worker — give the worker a sanctioned, lock-free way to produce the record
THEMSELVES. Three moves, in order:

1. **Sanctioned write lane (exact-match carve-out).** Mirror the existing patterns
   (`_is_sanctioned_cortex_update_command`, the brain-write carve-out): a NARROW
   predicate matching only the reviewer's single-cycle record invocation
   (`python3 .../adversarial-review.py --cycle-id N --material-file <plan> --review
   --reviewer-id <id>`), rejecting `--sweep`, sudo, metachars, separators and
   chaining; wired BEFORE the lock and no-repo refusals. Rationale that survives
   review: the loop-governance DB is shared governance DATA, not a git repo —
   governance is repo-scoped, so demanding a lock for a data-store write is an
   impossible demand for the identity the gate itself targets. Naming your carve-out
   and its rationale in the same comment block is what keeps the next editor from
   widening it.
2. **The refusal message must carry the exact self-serve command, with the script path
   RESOLVED at run time — never one hardcoded location.** The deployed
   (`~/.steadfaste-fleet/scripts/...`) and in-repo (`ops/scripts/orch-bus/...`) copies
   are not both present on every host, so resolve the first candidate that EXISTS and
   never print a path you have not confirmed — a worker handed a path that cannot
   resolve concludes the gate is unsatisfiable and loops. Then the `--material-file`
   form (never `--material "$(cat ...)"` — the `$(...)` is itself write-class and
   breaks the exact-match lane), and a plain statement that the agent runs it
   themselves with no orchestrator and no second lock. An agent looping "why can't
   I do this myself?" is the gate telling it the wrong path or a false capability
   claim.
3. **Sweep stale availability claims in the same change.** A docstring or skill line
   saying "orchestrator-side — the worker cannot invoke it" becomes the agent's own
   conclusion that the gate is unsatisfiable, even when the script was always
   deployed to the shared path for everyone. Grep `worker cannot invoke|orchestrator-
   side trigger` across ops/ plugins/ skills/ and fix each hit to state the real
   availability and the self-serve invocation.

Keep the close-time reviewer (the "one after to check" that gates what actually
lands) MCP-owned and OUT of the worker lane, and say that split explicitly in the
delivery: the plan review is self-recordable by design; the close-time code review
is not. A worker run of the close-time reviewer would be the self-report the design
names as the trap.

## 10. The Sanctioned Review Lane Makes Its Store Append-Only

Opening a worker write lane to a review store creates a NEW hole the moment the
worker can also DELETE a row it dislikes: delete the FINDINGS, re-record, done —
the re-roll-until-CLEAN loophole and an audit-tamper, because adversarial rows are
the review-of-record and a verdict a worker disagrees with must not cease to exist.
When a worker wants a fresh verdict on IMPROVED material (a legitimate need — they
added committed proof), that is a RE-REVIEW, and the sane path is the operator's
own wording: "no dependence on the orchestrator; review/governance self-contained
on the agent's server." So:

1. **Sanctioned re-review = move to append-only history, never DELETE.** A
   `--re-review` flag that atomically MOVES the current row into a
   `adversarial_reviews_history` table (with `superseded_ts` + a reason) then records
the fresh verdict as the new current row. A plain re-record on an already-reviewed
cycle is a benign no-op with a "use --re-review" hint (the UNIQUE constraint keeps
it that way). Nothing is ever deleted; a superseded FINDINGS stays recoverable.
2. **Keep ONE current row per cycle — do NOT make the primary table multi-row to
   "simplify" history.** `loop-gov-mcp.py` writes `adversarial_reviews` with
   `cycle_id UNIQUE` + `ON CONFLICT(cycle_id) DO UPDATE` and reads it with a bare
   `fetchone()`; multi-row-per-cycle under that schema silently breaks the close
   gate. History lives in a SEPARATE table via move-then-upsert in one transaction,
   and the UPSERT must bump `review_id` (`review_id=excluded.review_id` in the DO
   UPDATE SET) or the second re-review's history INSERT collides on the `review_id`
   PRIMARY KEY.
3. **Block raw mutation of the store; keep reads open.** A DB-write gate (`_is_
   governance_db_mutation_command`) refuses a write-class terminal command that
   names the store and is NOT a sanctioned writer (raw `sqlite3 <db>
   "DELETE/UPDATE/DROP..."`, an inline python heredoc mutating it, or any `python3
   <script> --db <store>` that is not the reviewer/record script — the /tmp-copy
   trick, whose body the command text cannot see). SELECT/PRAGMA diagnostics (the
   doctor's own reads) are NOT blocked. Wire it AFTER the sanctioned carve-out
   (so the reviewer is never mistaken for a tamper) and BEFORE the no-repo refusal
   (so the message names the append-only rule, not the generic lock demand).
4. **The block message is itself an instruction the worker follows — say exactly
   what the sanctioned alternative is.** "never DELETE rows... use --re-review" with
   the deployed-path command named, not a generic lock refusal. Every gate that can
   block a real worker must tell it how to proceed; an agent looping on a gate is
   the gate giving a wrong path or an impossible-looking remedy.
5. **A `--review` run can exit 0 and print NOTHING while still having recorded a
   verdict — never read empty output as "no review happened".** Read the row back
   from the store before acting on the silence: the row is the record of what
   happened, stdout is not. A re-review is quiet too — it prints one supersede line
   and the fresh verdict exists only in the row. A re-review is for material you
   IMPROVED, never to shop for a verdict you disagree with. Invocation, the read-back
   query, the column list and the history table: `references/adversarial-review-lane.md`.

## 11. The Lock-Free Lane Extends to Scratch Deletion — Resolve, Don't Trust the Text

A finished task (all cycles scored and closed) cannot hold a repo lock, but its cleanup
step (`rm -f /tmp/x /tmp/y && rm -rf /tmp/sub && echo cleaned`) is still write-class, so
it was gated by an impossible demand: delete scratch that lives OUTSIDE any repo under a
lock that exists, in this task, only before the work runs. The operator's fix direction is
the same as section 9's: a NARROW lock-free carve-out for the scratch-cleanup write class,
and the design is where the correctness lives:

1. **Trust ONE scratch root, and never a variable.** Only `/tmp` (realpath'd) is a root.
   Do NOT fold `$TMPDIR` into the trusted-root list — a hostile or just-buggy
   `TMPDIR=/home/<user>` would hand the carve-out a home directory to delete. A
   `$TMPDIR/...` OPERAND is fine (allowed and resolved), but its RESOLVED path must still
   land strictly under `/tmp`; the env value is never promoted to a root.
2. **Resolve operands, don't regex them.** `os.path.realpath(operand)` must be a STRICT
   child of a scratch root (`startswith(root + "/")`), and reject `..` components in the
   text before resolving (`/tmp/../repo/x` must not pass). A naive `[A-Za-z0-9._-]+`
   operand allowlist is the escape the reviewer will (correctly) flag as a HIGH.
3. **Shape-match the whole command, fail closed.** Only `rm`/`rmdir` (with only
   -f/-r/-rf/-d), `&&`-chained, at most one trailing `echo <word>`. Refuse the bare
   scratch root itself (`rm -rf /tmp`), wildcards, sudo/mv/cd, `/home` and repo paths,
   `~`, unpinned `/bin|/usr/bin|/usr/local/bin` prefixes and any other metachar,
   `;`, pipes, redirects, `$(...)`. One non-scratch operand in a chain poisons the whole
   command. Any unknown head/flag -> False (still needs a lock) — the carve-out exists to
   REMOVE the lock on the agent's own scratch files, never to widen what an agent may
   delete.
4. **Place it with the other sanctioned lanes** (before the no-repo refusal), but AFTER
   the DB-mutation gate — a mutation can never count as scratch cleanup, and the ordering
   proves it.
5. **Pin escapes with hermetic tests, including the worked shape.** The observed command
   and close variants are sanctioned; every escape variant (bare root, `/home`, wildcard,
   `..`, symlink-ish double-slash, sudo, `;`, pipe, redirect, `cd`, `mv`, a poisoned
   chain, an echo-not-last, `--`-poison, a non-rm command) is blocked. And test that the
   `$TMPDIR` operand does not grant a home-directory delete when `TMPDIR` points there.

Send the changed enforcer through the adversarial A4 gate and diff its finding count against
HEAD — new mediums are your change's fault, an unchanged count is pre-existing baseline.
## References

- `references/friction-reduction-candidates.md` — the enforcement-friction taxonomy (which gates
  cost what, measured from logs), the prioritised candidates for reducing that friction, each with
  what still holds security-wise, and the explicit **do-not-change** list. Read it before touching
  the write-classifier or a gate's timing: most friction is TIMING (the gate fires at the write
  while the information needed to satisfy it arrives late), not strictness.
- `references/adversarial-review-lane.md` — how to invoke the single-cycle adversarial reviewer and
  READ ITS VERDICT BACK from the store: the resolved CLI path across candidate roots, the flags
  (and which one does not exist), the read-only query that returns the row and its columns, the
  history table a re-review moves the superseded row into, and the verdict/finding shape. Load it
  before recording or interpreting a plan review.
- `references/rule-delegation-reload.md` — the hermetic acceptance test for "a rule change needs
  no restart": copy-the-contract + wrapper-CLI `--contract` seam + monkeypatch the resolver, both
  directions (rule edit reloads, broken contract fails closed while the recovery lane survives),
  plus the close-gate pins that keep the test self-verifying. Load it when writing or reviewing
  the delegation acceptance test or extending the delegation pattern in §2c.
- `plugins/governance-enforcer/__init__.py` — the enforcer itself
- `docs/orchestrator-only-paths.txt` — restricted paths
- `ops/scripts/pre-commit-score` — pre-commit hook with orchestrator guard
- `ops/scripts/manage/fleet_doctor/checks.py` — doctor checks (hook drift, enforcer permissions)
- `ops/scripts/fleet-update.sh` — deploy script with `deploy_governance_plugin()`
- `skills/devops/fleet-preflight/SKILL.md` — Pitfall 3 covers skills-loaded marker
