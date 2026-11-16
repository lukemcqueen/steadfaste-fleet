---
name: verify-and-ship
version: 0.1.0
category: software-development
description: >-
  Pre-ship verification: the mandatory change checklist and the eight-question
  self-critique before end_change() or delivery (absorbed change-checklist +
  reflexion-check). Load before ANY delivery or close.
pinned: true
aliases:
  - change-checklist
  - reflexion-check
related_skills:
  - task-orient
  - execute-right
  - loop-governance
  - governance-closeout
---

# Verify and Ship — Pre-Delivery Validation

The easy path (one file, one test) is never the complete path. Ask: what else
does this touch? Did the SYMPTOM resolve? Did I check siblings? Did I run the
doctor? Did I push so the fleet benefits? Run the self-critique BEFORE
delivering anything; run the checklist before any end_change().

## ⚡ MANDATORY GATE — independent review before user submission

**Moderately complex changes MUST get an independent gap/improvement review
BEFORE the solution is submitted to the user.** This is a gate, not advice —
it exists because the author's own self-audit (the reflexion below) is exactly
the judgment that overclaiming slips past, and it is why Luke put this in the
always-loaded skill.

**What makes a change "moderately complex" (any one of):**
- touches more than one file, or more than one subsystem, or a shared/upstream
  surface;
- changes behaviour an OPERATOR relies on (deploy, config, cron, gateway,
  enforcement, governance, security, backup/restore);
- carries a load-bearing claim to the user ("fixed", "verified", "deployed",
  "cleaned up", "works") beyond a single trivial command;
- any error would cost the user materially (data, availability, trust).

A genuinely trivial change — one file, no operator-facing behaviour, no
load-bearing claim — is exempt. When in doubt between trivial and moderately
complex, treat it as moderately complex.

**Run the gate BEFORE presenting the solution to the user:**
1. Get an INDEPENDENT second read — not your own second pass. In order:
   (a) the open-cycle adversarial review —
   `ops/scripts/orch-bus/adversarial-review.py --cycle-id <id> --review`;
   (b) a delegated fresh-session reviewer via `delegate_task` with an
   adversarial brief ("You did NOT do this work; find real gaps and errors,
   not praise; flag any claim you cannot verify as UNVERIFIED"); (c) a peer
   agent. Never a self-review by the author — that is the reflexion, and it is
   NOT this gate.
2. Give the reviewer the CHANGE material (plan, diff, or the claim being
   delivered) AND, where the claim is behaviour, the session tool log / the
   evidence refs that would substantiate it — so it judges substantiation, not
   internal consistency.
3. Fold every real finding in BEFORE submitting. A review that returns only
   praise was not adversarial — do not treat it as clearance.
4. Show the review to the user when you submit:
   `↗ Reviewed independently for gaps (N findings, all resolved)` — or, if a
   true independent read was not possible (no peer, no reviewer, structural
   bar), say so and mark the gap:
   `↗ Independent review NOT possible — own-work only, treat claims as UNVERIFIED`.

**Read the verdict back from the STORE — the runner prints nothing on its success path, and an
EMPTY reply can still record a verdict.** Both look like a review from the command's output, so
the DB is the only place to settle it:
- `adversarial_reviews` carries ONE current row per cycle (`cycle_id, reviewer_id,
  reviewer_model, verdict, findings_json, summary`), with superseded rows in
  `adversarial_reviews_history`; `review_calls` records `prompt_tokens / completion_tokens /
  `reasoning_tokens` per call. Query both. Never report "no review ran" — or "CLEAN" — from stdout.
- **The row's `verdict` column reads `FINDINGS` whenever ANY finding exists — including info-only
  findings whose JSON verdict is `CLEAN`.** So take the verdict AND the severities from the JSON you
  parse out of `summary`; the column alone cannot tell you whether you are blocked. A finding whose
  own `recommendation` says it is a property of the reviewer's window (the window cannot see the
  diff, the sibling `docs/evidence/` file, or git history) is non-blocking — fix the change, not the
  window, fold it if a cheap fold exists, and do NOT re-roll the reviewer over it.
- **`completion_tokens` equal to the caller's `max_tokens`, with the content empty, means the
  reviewer reasoned to the cap and answered NOTHING** — yet a verdict may still be recorded from
  that empty reply, which is not a review. The signature is in `review_calls` (reasoning tokens at
  or near the cap). Re-run the same invocation with the reasoning effort turned down for that call
  (`REVIEWER_PLAN_REASONING_EFFORT=none`); the same material then completes in a few hundred
  tokens. Material SIZE is not the lever — a 6 KB prompt burns the cap exactly like a 60 KB one —
  so do not shrink the material to work around it. Disclose the override in the material AND in the
  close note: it is still an independent read, and the operator needs to know which knob was turned.
- **`--re-review` SUPERSEDES the current verdict**, so a diagnostic re-run replaces a good review
  with whatever the new reply contained. Do not experiment on a cycle holding a real verdict; and
  when you do re-review, the material must be a full rewrite of the FINAL state (see the folding
  rule above) — never a folding note appended under the old description.

**Two hard rules:**
- An independent review MUST happen before the solution reaches the user. If
  you are one tool call from submitting with no review done, stop and get one.
- A review the AUTHOR writes is not the gate. Either it is genuinely
  independent (fresh session / peer / established reviewer), or the submission
  carries the `↗ Independent review NOT possible` marker and its load-bearing
  claims are labeled UNVERIFIED. There is no middle state.

## The eight reflexion questions

1. **Did I complete everything the user asked?** Re-read the original request,
   including implicit items. If NO: finish before delivering.
2. **Did I verify every claim with real tool output?** "It should work" is not
   verification; "curl returned 200" is. A claim that a suite passes is a claim
   about a RUN — commit the run. A claim about a guard is verified AT the
   guard, never inferred from the caller. Name the BUILD, not just the run (a
   demo of `target/release/<bin>` measures the last build, not the source).
   A runner missing here is solved by installing it into an isolated venv, not
   by softening the claim; only if it genuinely cannot run HERE, verify the
   substance another way and LABEL which route you used. Cite evidence the
   reader can REACH (a path they can open).
3. **Did I follow governance for every change?** Every file created/modified:
   begin_change → work → feedback_accept → end_change, scored, no orphans. If
   NO: score the missing cycle or confess the gap.
4. **Did I handle all edge cases and failure paths?** Error path, empty state,
   malformed input, sibling call sites. Probe the GATE itself: point the
   checker at input it must reject and show it failing non-zero. A failure is
   only a finding once you know the check RAN (run it with the runner it was
   written for). Probe the PROBE: when a check fails, verify its own
   assumptions before blaming the artifact.
5. **Anything I would do differently?** Document it; if recurring, save as a
   lesson.
6. **Irony check:** does my execution contradict my change? (shipping a
   "be thorough" rule without running the verifier on it). If so: undo the
   bypass, do the rule, then ship.
7. **Anti-sycophancy:** did I push back when I should have? Silence with a bad
   idea is a trust violation. If overridden, note the override and execute
   faithfully.
8. **Visibility:** did the operator see progress during a long run? Send a
   short progress line at each phase boundary of anything lasting minutes.

Score: HIGH (all 8 pass) / MEDIUM (minor gaps, flag them) / LOW (fix before
delivering) / ZERO (cannot verify core claims — investigate, do not deliver).

## Change checklist (before end_change)

**Phase 0 — survey the surface (before begin_change):**
- Pushback check: wrong/harmful/better-alternative? Object BEFORE begin_change;
  silence is not consent. Override is final; execute faithfully.
- **Plan review before building (operator standing rule)** — non-trivial
  builds get the PLAN pro-reviewed FIRST via
  `ops/scripts/orch-bus/adversarial-review.py --cycle-id <id> --review
  --reviewer-id <name>`. `--reviewer-id` is REQUIRED for `--review`.
  Pass the material via `--material-file <path>` (read the file — a worker
  avoids `$(cat ...)` metachar by using the file form, which is what the tool's
  own help recommends); do NOT paste material inline on the command line. Read
  the stored row back — a CLEAN verdict validates CLAIMS, never predicted
  consequences (the RED run is what falsifies those). The verdict row lives in
  the `adversarial_reviews` table (not `reviews`). A first review that returns
  FINDINGS is expected — fold each finding into the plan, then re-review the
  SAME cycle with `--re-review` to supersede the prior verdict. Never phrase the material as a REQUEST (filed as
  injection); include the code map, hard constraints, rejected options.
  **RE-SUBMITTED corrections must ground every MEASURED claim and phrase every
  forward claim as what the test asserts.** When a battery RED run exposed a
  flaw in a CLEAN-reviewed plan and you re-review the correction, a claim like
  "the probe verified its OWN slice, trigger allowed it" needs the committed
  transcript path in the material (the reviewer files unverified-claim on a
  described run); and "own work will be refused" reads as future-as-fact / a
  design that must work — write "AC-L1-28 asserts own work is refused on the
  function AND the raw-UPDATE trigger path" so the reviewer sees it is
  verified by test, not asserted by plan.
  **The PLAN reviewer judges the MATERIAL, and it files on internal
  contradictions and bare prose.** A design bullet that contradicts a test
  expectation listed in the SAME plan is filed fabrication — word the design
  to say exactly what the test asserts ("a retry is a new spend and records a
  new row", never "INSERT OR REPLACE prevents double-counting" beside a test
  that expects two rows). A prose-only plan with no code is filed
  unverified-claim: the reviewer wants the committed diff + executed test
  output IN the material, so the loop is RED-test-first, commit, then
  re-review with the diffstat and the test run appended — same shape as the
  close-gate rule below. Re-review the SAME cycle with `--re-review
  --review --reviewer-id <new>` — it supersedes the prior verdict into the
  append-only history and records the fresh one; loop until CLEAN (info
  findings are non-blocking).
  **A re-review after FOLDING findings must be a FULL REWRITE of the material to
  the final state — never a "folding" note appended under the old description.**
  The reviewer's window cannot see the repository: it judges only the material
  you hand it. If the upper section still describes the pre-fix code (the old
  commit ref, the removed heuristic) while a folding paragraph below says the
  code changed, the reviewer reads the stale upper text as the current state and
  files the contradiction as FABRICATION — costing you a round re-reviewing your
  own inconsistency, not the code. Rewrite so ONE commit ref and ONE code
  description appear anywhere; embed the ACTUAL committed file verbatim for the
  lines the reviewer must verify (the constants a guard imports, the filter logic);
  let the folding note state only what changed and why.
  **Enumerate EVERY edit the range contains in the material's change list —
  prose, doc and evidence edits included.** The window sees only the material,
  so a fix you genuinely made but never listed reads as scope-drift ("the write
  is described but absent from the change"), and that finding costs a round the
  diff had already answered. When a finding's remedy is deliberately DEFERRED,
  say so AND record it as a tracked issue in the cycle (`record_issue`) — an
  unrecorded deferral reads as a dropped finding, while a recorded one shows the
  reviewer the deferral is governed.
- Name the cycle for the diff it will CONTAIN, not the instruction that started
  it; sized so the close review can read it (blob count, not line count).
- grep the repo for the old name/term; check cron jobs.json; cron install
  arrays; updater register() calls; doctor expected-cron lists; deployed path.
- PII scan — `secret-leak-detector.sh` before pushing.

**Phase 1 — test the change (no simulated output):**
- Scripts run with real inputs; DOGFOOD: the deployed copy runs its real
  invocation. Syntax: .sh bash -n; .py py_compile; .yaml safe_load.
- A scripted bulk edit must ASSERT its match count, then be scanned for
  artefacts (escaping residue, duplicates, self-contradicting never-clauses).
- Run the SIBLING tests that exercise the seams you changed; prove
  pre-existence of a failure at the range base in a worktree (run both sides
  the same way, absolute paths).
- Run the FULL suite WITHOUT `-x`/`--maxfail` when triage is the goal: a
  first-failure stop hides the other reds you need to separate pre-existing
  from introduced, so the report answers nothing about the rest of the change.
- **A red test right after a `pull --rebase` onto a peer's parallel commit is
  often the PEER'S OLDER TEST FILE still on disk, not your regression.** When a
  peer lands a commit that also edits the tests (e.g. a guard test re-pointed to
  derive its expected value from the code while your tree still holds its
  first-pass version hardcoding the old literal), the mid-flight rebase leaves
  the stale test in place and it fails against your CORRECT code — signature: the
  assertion compares the new correct value to the old literal. Do NOT "fix" the
  code; `git fetch` + rebase onto the newest origin (which carries the peer's
  fixed test) and re-run — it goes green. Same class: a `git stash pop` after a
  rebase can conflict in a PIPELINE-MANAGED file (a generated evidence/drift
  artifact an auto-commit on origin already updated) — take the origin version
  (`git checkout HEAD -- <f>`), not your stale local copy, then drop the stash.
- A suite a prior session reported 'still running' may be a ghost — the
  background process died with that session and the notification never comes.
  Check for a captured log artifact; if none exists, re-run into a log file
  (`pytest -q > /tmp/<name>.log 2>&1; echo EXIT=$? >> …`, backgrounded with
  notify) instead of waiting on a handle the restart invalidated.
- Changing a DEFAULT CONSTANT breaks hardcoded old literals in tests — sweep
  both shapes (fixture AND assertion), run to prove both moved.
- **An assertion that something is EMPTY must compare to the empty value.**
  `assert fn()` is always FALSE when the case under test is "nothing reported",
  so the test calls itself "silent" while passing exactly when the code is
  WRONG — it only ever goes green on a false positive. Write
  `assert fn() == []`, and treat a test named for silence whose body asserts
  truthiness as an inverted assertion, not a passing test.
- **A regression test whose failing mechanism you SIMULATE needs an
  anti-vacuity sibling.** When the test must fabricate the condition it protects
  against (a hostile `sys.path`, a stub module, an injected env var, a poisoned
  config), nothing yet proves the fabrication CREATES the condition — a green
  then means the simulation was inert. Add one sibling test asserting the stub
  really does reproduce the failure, so the pair shows the fix is load-bearing.
  This is Phase 1.5's "assert the baseline reproduces the defect" applied to a
  unit test, and it is the difference between a regression test and a decoration.
- **A test that guards a whole-interpreter condition must run in a SUBPROCESS.**
  Once `sys.modules` holds a module, an in-process test of import/loader
  behaviour passes on the already-correct module and proves nothing; run the
  import in a child interpreter whose FIRST action is the hostile setup.
- **Exercise a NEW check on the DEPLOYED path before claiming it works,** and
  treat its own "COULD NOT VERIFY" output as a bug report about the check. A
  check written against the repo layout can be structurally unable to run where
  it ships — a module it imports that the deploy never copies, a path derived
  from `__file__` depth that resolves differently. A fail-closed check announces
  this instead of passing, which is the only reason the defect is findable; the
  fix belongs in the check, never in the message.

**Phase 1.5 — adversarial verification (mandatory, no bypass):**
- Static gate per changed script: `adversarial-verify.py --file <f> --level
  A2 --gate`; A4 for plugins/, hooks/, mcp-servers/, ops/scripts/manage/,
  fleet_doctor/, enforcer, tests. Critical/high → block. No --no-verify.
- Pin before/after comparisons to an immutable revision (the RANGE BASE —
  `git merge-base origin/main HEAD`), name both shas in the artifact; assert
  the baseline reproduces the defect or the comparison proves nothing. **Put
  that assertion INSIDE the verifier, not in prose** — read the pre-fix file
  straight out of git (`git show <base>:<path> > <tmp>/<same-basename>.py`) and
  drive the SAME probe at it, so the before-claim is re-runnable by anyone and
  the reviewer's "the RED run is not replayable" info finding is disposed by
  construction. Two traps that cost a round each: `mktemp` returns a
  SUFFIX-LESS path and loading a module from it yields empty output, so give
  the temp file the real `.py` name; and a probe FIXTURE must satisfy the
  code's PRE-EXISTING invariants or your control case measures a different
  rule (a synthetic pool head priced EQUAL to the baseline trips the
  'head not below baseline' rule and looks exactly like over-firing — fix the
  fixture, never the code). Recipe: `references/evidence-verifier-recipes.md`.
- A captured check transcript carries its EXIT CODE and WARNING COUNT.
- Evidence scripts are READ-ONLY — never repair what they measure; never
  commit a query's output before seeing it return rows.
- A test judged by the close reviewer DERIVES expected values from the
  system's own surface (never a hand-copied constant-equals-constant echo, a
  magic bound, or a message substring).
- Commit the EVIDENCE artifact BEFORE you close — a load-bearing claim that
  lives only in the close note is filed unverified-claim and refuses the
  close. Run the checks, capture real output (command + its own rc, measured
  unpiped) into `docs/evidence/`, commit it in the SAME cycle.
  - `ops/scripts/make-evidence.sh <out> -- <command...>` writes exactly that shape (timestamp,
    the command, its own BARE rc, sha256) and `--verify <out>` recomputes the hash, so prefer it
    to a hand-assembled log; `-k` keeps the file when the command exits non-zero. Normalise this
    host's home prefix to `<HOME>` on every emitted line (`sed "s|$HOME|<HOME>|g"`) — the repo is
    public and the PII gate refuses a literal home path in a committed file. Put the whole capture
    behind a runnable verifier script whose OWN exit code is the NUMBER OF FAILED CLAIMS (never a
    trailing `echo`, which always exits 0), so `rc=0` carries information a reader can trust.
  - **In the verifier itself, CAPTURE a command's output before grepping it — `set -o pipefail` makes a
    `cmd | grep -q <expected>` claim FAIL even when grep matched.** The pipeline's status is the
    leftmost non-zero, so a command that legitimately exits non-zero (the doctor exits 1 on warnings,
    a linter exits 1 on findings) turns a passing claim red and sends you hunting a defect in the
    artifact that is not there. Assign first (`out="$(cd "$REPO" && "$PY" <cmd> 2>&1 || true)"`), then
    grep the variable; where a claim is genuinely ABOUT the exit code, assert that code explicitly
    rather than inheriting it through a pipe. This is the Q4 "probe the probe" rule at the level of
    mechanics: a failing claim is a hypothesis about your probe until you have seen it fail for the
    right reason and fixed it.
  - **Run the verifier's commands with the PROJECT's interpreter, not a bare `python3`.** A bare
    interpreter lacks the suite's dependencies, so "the tests pass" fails for a reason unrelated to the
    change. Resolve the fleet venv once at the top (`PY="${FLEET_VENV_PYTHON:-$HOME/.steadfaste-fleet/venv/bin/python}";
    [ -x "$PY" ] || PY=python3`) and use `"$PY"` everywhere.
  - **The PII gate refuses a literal home path in ANY file you write, fixtures included** — not just in
    emitted evidence lines. A test fixture holding `/home/<name>/repo` is blocked at write time, so use
    a neutral path (`/srv/example-repo`) or `Path.home()` in fixtures and generated data.
  - **Normalise the body BEFORE the hash, or repair it after.** The recorded sha256 covers
    everything except the final `sha256:` line, so a `sed` that rewrites a home prefix AFTER the
    capture leaves `--verify` failing on otherwise-correct content. Repair is three commands on
    that same file: `sed -i '$d'` (drop the stale hash line), recompute, re-append
    `sha256: <hash>`, then run `make-evidence.sh --verify <out>` and show the `OK`.
- ONE note, ONE run — every number in the close note must come from the SAME
  run as the committed artifact beside it.
- **The 'unverified-claim' reviewer loop terminates with a COMMITTED test, not
  more material.** When a re-review keeps returning unverified-claim against
  your prose even after the code fix, the reviewer is judging the MATERIAL you
  pass each round — stale or abridged material produces fresh findings on every
  pass. Stop re-writing the material: commit a hermetic test that derives its
  expectation from the artifact's OWN structure (an ordering test that greps
  for definition-before-call, a parser test asserting the full-surface count),
  run it red-and-green, and re-review citing that committed test as the proof.
  A reviewer will accept 'asserted by test X, runnable with pytest' where it
  will never accept 'verified: offsets were compared'.
- **A committed test alone is re-filed as unverified-claim unless a CAPTURED RUN ARTIFACT rides in the same range — and the ACCUSED file must itself change to point at the evidence.** Verdicts stay keyed to the blob's un-changed bytes forever, so adding a test elsewhere (or claiming it in the commit message) never clears the accused file: edit that file to reference the test, and commit `docs/evidence/<topic>-<date>.txt` carrying the exact command, output and exit code. For a script that iterates the whole environment (`env | sed` mirrors, prompt samplers), its hermetic test must CLEANSE its own test-scoped vars up front or the reviewer files an ambient-leak finding.
- **The CLOSE gate is a per-blob reviewer with a limited window — pin the test's
  SUBJECT inside the test.** A test importing module internals from a file the
  close reviewer cannot see is filed FABRICATION; hardcoded expectations are
  filed unverified-claim. Assert the subject file exists + functions callable,
  derive expectations from the in-range source of truth, and exit a refused
  close via `rereview_change` (NEW note + evidence), never an unchanged
  `end_change`. Mechanics: `references/close-gate-review.md`.
- **The per-blob window cannot see git rename pairs** — a rename-heavy range makes
  EVERY renamed file come back as 'is this a rename? / confirm provenance'
  (scope-drift), and a constitution rule claiming 'the file list shows both
  paths' does not fix it because the window's file list genuinely lacks the
  pair. The window CAN read the file's own text: put an HTML-comment provenance
  line in each renamed file ('renamed from <old path> in the <sweep> sweep,
  commit <sha>; content predates the range') and re-review — the finding resolves
  on the file's own statement. Bulk-annotate all renamed files at once (git diff
  --name-status -M against the range base) instead of fighting one rereview at a
  time; the note is not in the per-blob material.
- **Documented keeps still get flagged** — the reviewer reads the keep contract
  literally: a kept token must carry its keep label ON ITS LINE ('kept name — …'
  / STALE), not only in a central keep-list doc. Label the token inline and
  verify the claim it rests on (e.g. the script that actually writes the kept
  path) before re-reviewing.

**Phase 2 — multi-OS:** hardcoded paths → portability helpers; systemctl vs
launchctl vs brew by host.
**Phase 3 — multi-role:** hook/enforcer changes tested in a throwaway repo;
role matrix (Titus/Joseph/Esther/Kustos) — verify none broken.
**Phase 4 — docs:** never document a command you have not executed; new script
→ register in fleet-update.sh; new auto-loaded skill → manifest; agent
workflow change → AGENTS.md/SOUL.md.
- **Protected agent-instruction files (AGENTS.md, CLAUDE.md, SOUL.md) are
  operator-gated writes.** A write to one fires an operator-approval prompt;
  if it times out without a response that is SILENCE, not consent. Do NOT
  retry the write, and never route it around the gate through terminal /
  execute_code / skill_manage. Draft the edit, hold it, and report it as
  BLOCKED pending explicit operator approval — an un-landed change is
  incomplete, not deferred.
**Phase 5 — final verification:**
- No dangling leftovers — DECIDE: land it, or state a decision backed by a
  measurement. Never land a peer's in-flight edits.
- Symptom proof — the specific error/alert/blocker is gone, not just "compiles".
- Stale expected-lists cleaned (removed crons also removed from uninstall arrays).
- Doctor clean — `fleet-doctor.py --quiet`; record its EXIT CODE and warning
  count, not just the ✅ lines. Warnings and info are not a licence to move on:
  fix or dispose (`--disposition ... --as deferred|deliberate`), BATCHED and
  LAST, after the push.
- Deploy, THEN push — that order, nothing committed in between (deploy-map
  files must match deployed tree). Run the doctor after deploy.
  **Evidence that can only be captured POST-deploy is the exception that proves
  the order:** push the code first, then record the deployed-path exercise and
  commit that evidence AFTER the push. The rule forbids commits BETWEEN deploy
  and push (which would desync the deploy-map), not after it — and the evidence
  still lands before the close, which is what the close gate requires.
- Verify the landing: `verify-landed.py --repo . --base <previous tip>`
  (receipt + deploy-map copies + origin equals tip).
- Pushed — `git log --oneline -1 origin/main` shows your commit; load
  `shared-repo-push-gates` BEFORE the first push attempt.
- Gate scoping: a host-wide gate whose remedy requires a Hermes session stays
  repo-scoped; widen only when any user's remedy exists.

**Phase 6 — reflexion:** run the eight questions; LOW/ZERO → fix before
delivering. Ask: "What is the most likely thing I got wrong?"

## Anti-patterns

"I tested it manually" · "agents won't see that message" · "the rest is
trivial" · "docs later" · "the hook didn't catch it" · "I'll write a migration
script" (the commit IS the migration) · "worked from my repo terminal" (test
from the deployed path).
