---
name: sweep-verification
version: 1.0.0
category: devops
description: "Verify a decommission sweep removed every file and ref."
author: Esther
license: MIT
platforms: [linux, macos]
---

# Sweep Verification — proving a removal is COMPLETE

## When to Use

Use when asked to "double check / make sure X is completely removed" after a
rename or decommission sweep (legacy-brain → mycortex, service renames); when a
deploy or installer keeps leaving files behind after renames and removals; or
when an automated cleanup already exists but keeps missing things. Performing the
sweep is `rename-sweep`'s job; THIS skill is the independent verification pass
that finds the leftovers the sweep missed — plus the rules for making the cleanup
itself safe to run unattended (see
`references/provenance-based-reclamation.md`).

## Read the migration's own follow-up doc FIRST

A completed fleet rename (hermes-cortex → steadfaste-fleet, legacy → mycortex)
ships two docs that define what "done" means: `docs/rename-followups-<new>.md`
(the repo-side exclusion list — which skill dirs were kept as load keys, the
enforcer's dual-URL accept list, the migration tools that must keep the old
name literal, the historical/evidence records) and a per-host completion runbook
under `docs/runbooks/` (e.g. `fleet-local-dir-rename.md`) that lists the live
repoint steps (hooks + `core.hooksPath`, cron-bridge units, systemd/launchd unit
paths, stale deployed scripts, root-owned copies) and what is deliberately kept
on the host. Read both BEFORE grepping: a verification pass that starts with
`grep -r <token>` files hundreds of hits that are NOT leftovers — the old token
surviving in the repo is expected, and only what these docs do NOT exempt is
stale. Find them with `git log --oneline -20 | grep -i rename` before trusting a
raw count. The two are different scopes: the follow-up records what the SWEEP
kept in the repo; the runbook is the per-HOST completion check, so a clean repo
can still be an un-migrated host with load-bearing transition symlinks.

## Surface checklist — sweep ALL of these, in this order

1. **Repo tracked content**: `git grep -il <token> -- .` (count + list)
2. **Repo tracked file NAMES**: `git ls-files | grep -i <token>`
3. **Repo worktree** (incl. untracked): `grep -rli` excluding `.git/__pycache__/.venv/node_modules`, plus `find . -iname '*<token>*'`
4. **Deployed tree** `~/.steadfaste-fleet`: grep excluding logs/__pycache__/backups. Deploy never prunes → stale files linger here (see pitfall 1)
5. **~/.hermes live config**: `cron/jobs.json` (0 expected), `cron/scripts`, `plugins/`, `config.yaml`, `memories/`, `repos.yaml` (a repo-rename leaves the OLD path here even after the code is swept), `SOUL.md`, `AGENTS.md`, `skills/` (deployed copies), **`agent-profiles/*/SOUL.md`** (profile templates seed other hosts' SOULs — a stale ref here ships to the fleet). A transition symlink at the old home keeps these resolving, so a running host is NOT proof they were repointed — grep each surface explicitly.
6. **~/.hermes historical** (count, do NOT delete): logs, `sessions/`, `state-snapshots/`, `cron/output/` (old run records)
7. **~/.steadfaste-fleet/data + state**: `loop-events/*.jsonl`, `loop-governance.db` (audit trail — task IDs legitimately mention the token), `state/` (dead state files with old keys, e.g. agent-health-data.json — check readers before deleting). Two classes here are keyed by the REPO NAME in the FILENAME, so a content grep never sees them — count both spellings explicitly: `.reviewed-<repo_slug>-<sha>.json` (per-blob review receipts) and `.session-<repo_slug>.id` (session-id stamp). See pitfall 15.
8. **~/brain**: lesson files (historical knowledge — keep)
9. **Tasks DB**: `SELECT ... WHERE content ILIKE '%<token>%' AND status IN ('pending','in_progress')` (active rows should be 0)
10. **Bus archives** (primary host): count of subjects/bodies mentioning token (historical messages — keep)
11. **Ignore rules and VCS config**: `grep -n <token> .gitignore` plus `.git/info/exclude`
    and any tracked ignore file. A rule matching a path you REMOVED is itself a leftover
    reference, and a worse one than the others — see pitfall 10.

## Classification — the load-bearing rule

Every hit is one of three buckets; treat them differently:

| Bucket | Examples | Action |
|---|---|---|
| **LIVE** | deployed scripts, cron, config, SOUL profiles, registry/health-vector keys, stale deployed files, dead state files | **Fix/remove now** |
| **HISTORICAL** | brain lessons, dated skill event records, logs, session dumps, backups (dumps, deploy-backups, scripts-unique-backup-*), loop audit trail, bus archives, curator metadata | **Keep — deleting destroys the fleet's record** (the file itself may even say "it IS the record") |
| **CREDENTIAL** | `.env` values whose NAME contains the token (e.g. `FLEET_BUS_PG_PASS=<legacy>_pg_pass`) | **Flag only — never touch a live credential mid-task**; rotation is a deliberate multi-host change |

The audit's own files classify stale refs as "historical doc/log/skill text →
leave as-is (it IS the record)". Deleting history to reach zero violates the
keep-logs precedent and permanently destroys knowledge that exists nowhere
else (many such files are deployed-only, never in git).

## Pitfalls

**`git add -A` does NOT mean the file was added.** `*.log` is in `.gitignore`, so
a "committed execution log" was silently skipped and a commit message claimed a
file that did not exist — the audit caught it, not the author. Any sweep or
evidence artifact must confirm tracking from the tree, not from intent:
`git ls-tree --name-only HEAD <dir>` (or `git diff --cached --name-only` before
committing). Prefer the repo's existing artifact extension (`.txt` here) and check
`git check-ignore -v <path>` when a file mysteriously does not stage. (all hit in real sweeps)

1. **Deploy registers but NEVER prunes.** Files removed from the repo stay
   deployed forever: `~/.steadfaste-fleet/services/mycortex/import-<legacy>.py`,
   stale skill `references/*.md`, old `__pycache__/*.pyc`. Find them with
   `comm -13 <(ls repo-dir | sort) <(ls deployed-dir | sort)` — deployed-only
   extras are stale unless proven otherwise. Delete stale deployed files even
   though git status stays clean.
   - **Manual `rm` is the FALLBACK; the class fix is PROVENANCE, not a
     better-tuned scan.** A scan for "deployed files not in the register map"
     cannot tell a destination WE deployed and later renamed from a file that
     exists only on that host — and guessing wrong has destroyed a host-local
     script, which is why such cleanups are deliberately warn-only. Record what
     the deploy WROTE (an ownership inventory) and reclaim only
     `old_inventory − this_deploy` — provably ours — by QUARANTINING (moving,
     restorable) rather than deleting. Three ways this automation silently does
     nothing, all worth checking before trusting any existing cleanup: it is
     wired into a rare mode (`--force-all`) instead of the common deploy path;
     it is scoped by file SUFFIX or one directory (`scripts/*.py|sh`) so
     `config/*.json` and other destinations are invisible to it; it only warns.
     Design, invariants, and test shape:
     `references/provenance-based-reclamation.md`.
   - **Deleting a file without deleting its `register()` line is a doctor FAIL**
     (`Deploy source missing`). After the sweep, assert every registration still points
     at a real file: parse the `^register(?:_orch)?\s+"<source>"\s+"<dest>"` lines from
     `ops/scripts/fleet-update.sh` and stat each source path. One missing source means
     either a leftover line to drop or a file to restore — never leave it standing, and
     re-run the doctor (it exits FAIL, so the push is blocked too).
     **A retired tool leaves THREE surfaces: the deployed copy, its `deploy-manifest.tsv`
     row, and (in the repo) the file + its test.** The manifest at
     `~/.steadfaste-fleet/deploy-manifest.tsv` gets a row per `register()` call, so removing
     only the `register()` line and the source file still leaves the deployed copy AND its
     manifest row lingering (deploy never prunes). Belt-and-braces: `rm` the deployed copy,
     `sed -i '/<tool>/d'` the manifest row, and re-run `fleet-update.sh` (it regenerates the
     manifest from the surviving `register()` lines). Prove retirement with the repo tree,
     the deployed tree, AND the manifest all showing 0 hits.
   - **Deployed SKILLS are preserved, not pruned.** A skill deleted from the repo stays
     deployed, and the next deploy reports it as a stale "deployed-only skill" instead of
     deleting it (the deploy cannot tell an orphan from a local skill someone depends on).
     The explicit cleanup must remove the deployed skill directory as well, or the
     warning — and the doctor's stale-skill WARN — reappears on every run.
1b. **Removing the artifact is not removing the FEATURE — revert the key that
   selects it.** A component wired by config (e.g. `cron.provider: <plugin>`) keeps
   pointing at something that no longer exists once its directory is deleted: the
   consumer falls back to its built-in default but logs a warning on every tick, and
   the user reads that warning as "that thing is still causing issues". Grep
   `~/.hermes/config.yaml` for the component name and drop the selecting keys
   (provider + its sub-blocks) in the same sweep.
1c. **Consumers are part of the sweep.** A component usually outlives itself in its
   readers — a watchdog that queries its DB, a report script that imports it, a cron
   manifest entry, an installer that re-creates it, a doctor check that asserts on it.
   Grep the repo for the artifact names and fix every consumer in the same pass; one
   left behind keeps firing (or re-creates the artifact at the next deploy), which is
   how a "removed" feature generates fresh alerts.
7. **The deletion set comes from a grep, not from memory.** Enumerate the names across
   the repo (`grep -rl`) AND the deployed tree first, then check dependents
   (`~/.hermes/cron/jobs.json`, `ops/install/cron-manifest.yaml`, installers, doctor
   checks, tests) before removing anything. Report the surviving mentions explicitly
   (dated reports, advisory docs) so the user can decide on those rather than
discovering them in a later grep.
8. **A destructive sweep is one APPROVED step, and consent is per-command.** A compound
   `git rm` of many paths (or one `rm -rf` over several artifact dirs) can trip the
   approval gate; if that prompt times out, NOTHING ran — do not retry it, do not reword
   it, do not reach the same outcome another way. Report the exact removal set plus the
   edit sites, and wait for an explicit go. Confirm the tree is untouched
   (`git status --porcelain`) so "nothing ran" is evidence rather than assumption.
9. **Do not hold a governance lock while you wait on the user.** A decommission spanning
   turns leaves the cycle's lock held, which blocks every peer session from taking one.
   Score the cycle you have (a low completeness score and a factual "inventory only,
   blocked on consent, no files changed" note is honest and correct), release it, and
   take a fresh lock for the actual removal.
10. **Removing a path but KEEPING its ignore rule makes its return invisible.** When a
   directory is deleted by design, delete the `.gitignore` rules matching it in the SAME
   sweep. While those rules remain, the directory can reappear and `git status` stays
   clean — so the recurrence you are verifying absence of is the one thing the ignore
   rule conceals, and the sweep reports "clean" over a directory that is back. Leave a
   one-line comment where the rules were, saying they were removed on purpose, so a
   future reader does not helpfully restore them. **Name directories by their FULL path
   when reporting**: a runtime directory and a same-named directory inside the repo
   differ by one leading dot, so "the directory came back" without the path reads as a
   claim about the runtime copy — which is correct and expected — and alarms the reader
   for no reason.

   **A removed directory can survive as an EMPTY SHELL — verify with `find`, not `git
   status`.** `git rm -r` deletes TRACKED files only, so a directory still holding an
   untracked file (a lock, a log, a state file) is left standing, and the worktree
   reports clean because the remainder is untracked — and if an ignore rule is also
   still in place, invisible on top of that. Confirm the removal with an explicit
   existence check after the sweep (`test -e <path>`, or the `search_files` tool with
   `target='files'`); a clean `git status` is not evidence the directory is
   gone. This is also why "it reappeared" can be the wrong diagnosis: a directory the
   sweep never actually emptied reads exactly like a directory something recreated.

   **Never wrap the removal in `try: rmdir(d) except OSError: pass`.** That construction
   swallows precisely the partial removal above: the loop finishes, the report says
   removed, and the directory is still there. Report what was removed from a POST-STATE
   check, never from the loop having run to completion.

   **Give the removal a check that FAILS on its return.** A one-off deletion is a
   snapshot; absence that nothing asserts is absence only until the next process
   recreates it. Add a check — doctor or CI — that PASSes when the path is absent and
   FAILs naming the offending entries when it is present (including whether the
   state/governance file inside it is back), and prove the check CAN fail by driving it
   against a temp fixture in every state. Then the recurrence reports itself instead of
   waiting for a hand audit.
2. **Guard tests must self-exclude.** A `test_no_<token>_refs` test that runs
   `git grep -il <token>` fails on its OWN source. Fix:
   `git grep -il <token> -- . ':(exclude)tests/<guard-file>.py'`.
3. **Stale state files carry old keys.** Dead state JSON (mtime weeks old, no
   readers) can still hold old service keys. Verify no reader
   (`grep -rln "<name>" scripts/`) before deleting.
4. **Profile copies seed the fleet.** `~/.hermes/agent-profiles/<agent>/SOUL.md`
   is a template merged into that agent's SOUL — a stale ref there reaches
   another host. Check ALL profiles, not just your own.
5. **Post-sweep, retrieval goldens drift.** A mass content rewrite changes FTS
   ranking: parity/known-answer fixtures fail on ranking (expected docs still
   exist; new top hits are relevant). Refresh the stale golden with evidence
   and a note — it's drift from an intentional change, not a regression.
6. **Validation tests exercise the DEPLOYED copy.** L2 fleet tests
   (test-task-fleet.sh, test-mycortex-schema.sh) run against
   `~/.steadfaste-fleet/scripts/`, not the repo. Unit tests pass against repo
   source while L2 fails → the fix isn't deployed. Run `fleet-update.sh`
   (ops/scripts/) before L2, then re-run.
12. **A deployed file's frozen `# SOURCE:` header is the stale-file fingerprint.**
    Deployed `.py`/`.sh` carry a generated `# SOURCE: <repo-path>` header from the
    deploy that wrote them; when that header names a path absent from the current
    repo, the deployed copy is a leftover (deploy never prunes it) whose body still
    carries the OLD name while the codebase moved on. Before deleting, find the
    artifact's CURRENT home — the component often moved within the repo
    (`ops/services/agent-bus/server.py` → `core/fleet_bus/server.py`, repo-served
    and already clean). A deployed file whose source is gone AND that is not
    registered in the deploy map is stale and removable.
13. **A stale WORKTREE survives under the old name as a `.git` FILE, not a
    directory.** A linked worktree's `.git` is a one-line file reading
    `gitdir: <path>`, so `.git/config` is not found there and repo content-greps
    never see it — its tree predates the sweep and was never touched. Inspect that
    pointer (its gitdir may resolve through the transition symlink) and remove it
    with `git worktree remove <dir>`; do this BEFORE removing the transition
    symlink the pointer rides on.
14. **A verifier's own assertions can be MID-MIGRATION expectations — a FAIL there is proof of COMPLETION, not a regression.** A rename's per-host verifier (e.g. `verify-rename-local.sh`) asserts the OLD paths still EXIST as symlinks to the new ones (`[[ -L $OLD ]] && readlink == NEW`), because its job is to prove the transition is in place. On a host that has fully migrated and then removed the transition symlinks (`rm -f ~/old ~/.old` per pitfall 15), those assertions FAIL while the host is in the cleanest possible state — the symlinks being gone IS the end-state the runbook's asynchronous section prescribes. Before treating a verifier FAIL as "the migration regressed", read the verifier's own expectation: does it require a state the runbook LATER removes? Pair it against the final-state checks (old dirs absent, `consolidate-env.sh --check` clean, brand lint 0 fail, doctor `Stale pre-migration home folders` PASS) and prefer the completion check over the mid-transition one. Same shape: a verifier that greps for `cortex`-named launchd/systemd labels counts 0 after the rename repointed everything — 0 cortex labels IS the migrated state.

    **Transition symlinks mask live config that was never repointed — running
    services are not proof of completion.** A symlink at the old location keeps
    `~/old-name` resolving, so the host works while a live surface still loads the
    old path. "Fully migrated" is false until EVERY live surface is repointed, not
    just the code: enumerate them (`repos.yaml`, `config.yaml`, `gateway.yaml`,
    `agent.env`, `cron/jobs.json`, shell rc, root-owned copies) and grep each for
    the token; only when they are empty are the symlinks genuinely removable
    (`rm -f ~/old ~/.old`).
    **`git remote -v` is write-class under the governance classifier** — the
    lock-free read allowlist is `ls, cat, grep, find, git status/log/diff`, and
    `git remote` is not in it. When verifying a repo's origin was repointed, read
    `.git/config` via `read_file` instead of opening a lock for a read.
    15. **Repo-name-keyed state files are a stale class of their own — sweep them when the
    repo is renamed.** A rename leaves behind state files whose NAME embeds the old repo
    slug, not just files whose CONTENT mentions it: the per-blob review-receipt caches
    (`.reviewed-<repo_slug>-<sha>.json`, written as `REVIEW_RECEIPT_PREFIX + slug + "-" +
    sha`) and the session-id stamp (`.session-<repo_slug>.id`). Once the slug changes,
    nothing reads the old-keyed set, so it survives every deploy forever and a content
    grep reports clean. Count BOTH spellings side by side (`ls -1 .reviewed-<old>-*.json |
    wc -l` beside the new one), delete only the old-keyed set, and confirm the new count
    is unchanged afterwards. These are a CACHE, not the ledger — the audit trail is the
    governance DB — so removing them loses no record.
    16. **A finished rename can be re-reddened by a LATER content backport — run the
    migration's own lint during verification and attribute the failure with `git log`.**
    A skill or lesson backported into the repo AFTER the sweep can reintroduce the retired
    token and turn the brand lint red while every host surface stays clean, so a green
    original sweep is not permanent. Run the lint (`bash tests/test_fleet_cli_brand.sh`)
    as part of the pass, and find the introducing commit with `git log --diff-filter=A
    --oneline -- <flagged file>` before blaming the sweep. A lint that is not wired into
    the doctor or the hooks blocks nothing — report it as a finding with the one action
    that clears it, rather than letting a red gate read as a broken host.
    17. **A residue lint and a skill that TEACHES the sweep legitimately conflict — report
    the ONE action that clears it; never re-scope a gate unilaterally.** A lint that
    greps `skills/` for the retired token flags the methodology reference whose worked
    example uses that token (`old → new` mappings, shell and regex samples), even though
    the sweep's own rule says such files stay out of the residue grep. Two fixes exist —
    generalize the sample tokens to the documented family notation, or exempt the
    reference by path — and choosing between them is a gate-scoping judgement for the
    operator. Token-class fact to use when proposing the doc fix: a wildcard family form
    (`PREFIX_*`) does NOT match a lint pattern shaped `PREFIX_[A-Z0-9_]+`, because `*` is
    outside that class — and the fleet's authoritative docs already write a retired
    family that way.
    18. **A migration's own "converged when the token count is 0" criterion can be
    over-broad — classify every hit before chasing zero.** The follow-ups doc may state
    convergence as a blind grep (`git grep -i "<old brand>"` == 0 outside historical
    dirs), but proposal/design docs and dated records legitimately quote the old brand as
    the CLI or story they once described. Bucket every hit (record vs live instruction),
    report the live ones, and name the surfaces you did not reach — a doc that merely sits
    outside `docs/older|evidence` is not automatically live.
    19. **A deployed-only entry is not automatically stale — separate "orphan" from "repo
    gap".** The deploy never prunes, so a repo-vs-deployed dir diff lists extras; most are
    real leftovers, but a deployed-only SKILL can be a LIVE capability whose repo source
    is absent (created on the host, or stranded when its repo copy was moved). Confirm the
    artifact is still loaded before removing it, and recover instead of deleting —
    `ops/scripts/manage/reconcile-stranded-skills.py` (report first) copies pure supersets
    back into the repo as its own governed change.

    20. **Never `.resolve()` a path when deciding what you OWN or CONTAIN.** Resolving
    follows symlinks, so a managed tree's intentional `hook → registered-script`
    symlink is rewritten into its TARGET — and a reclaimer then moves the live script
    the hook points at, which is the exact loss the mechanism exists to prevent.
    Normalise lexically (`os.path.abspath(os.path.normpath(p))`) on BOTH sides of the
    containment check, and detect symlinks separately (`is_symlink()`) as themselves,
    skipping them: a link's target is never yours to move. A negative test with a
    symlink entry is what catches this — a happy-path suite never will.
21. **Prove the NEGATIVE cases, or the suite certifies a dangerous tool.** A removal
    mechanism needs tests asserting a file SURVIVED: host-local and `local-*` files
    untouched, a path OUTSIDE the managed tree refused (a corrupt or hostile record
    must not reach it), an absent or empty ownership record = no-op (never "everything
    is an orphan"), `--dry-run` moves nothing, and a failed move leaves the ownership
    record UN-ADVANCED (fail closed, so an unrecorded reclaim cannot become invisible).
    Add a grep-level test that the caller invokes the mechanism OUTSIDE the rare-mode
    block, and bound any source-window assertion by the next top-level `def` — never a
    fixed character count, or the test silently stops testing the moment someone adds a
    comment above the target line.
22. **A diagnosis that prescribes a bare `rm` is itself a defect.** When a doctor or
    audit check tells the operator to delete a file with no recovery path, fix the
    REMEDY in the same pass as the detection: prescribe deciding by hand and moving the
    file aside into a quarantine directory, and name the restorable path. This is the
    same class as a remedy whose command cannot run (wrong interpreter, unreachable
    path) — a remedy is part of the check's contract, not cosmetic text.

## Steps

1. Begin governance cycle (terminal needs a lock).
2. Run the surface checklist (batch greps; count AND list).
3. Fix/remove every LIVE hit; leave HISTORICAL; flag CREDENTIAL in the report.
4. Re-verify: re-run the live-surface greps → 0.
5. Run the guard test (`pytest tests/test_repo_structure.py -q`) + full suite.
5b. Run the migration's own consistency lint (`bash tests/test_fleet_cli_brand.sh`) and
   the doctor (`fleet-doctor.py --quiet`) — record the pass/warn/fail counts and the exit
   code, not just the ✅ lines. A red lint on a freshly-swept tree is usually a LATER
   backport (pitfall 16); a red doctor FAIL is real drift that must be fixed in-cycle.
6. Post-sweep validation: run the component's own battery (e.g. mycortex:
   CLI doctor, sources/stats, schema battery, crons, parity fixture, doctor
   Deploy+Repo sync) to prove the component still works after the purge. When the
   sweep removed config, exercise the path that REPLACED it — e.g. after deleting a
   cron provider, run the scheduling surface (`hermes cron list`) and confirm no
   fallback warning, which is the only proof the default is really in charge.
7. Report: verified-clean surfaces, what was removed, what was kept (counts),
   flagged items. Score + close cycle. **Score BEFORE close, never in the same
   parallel batch.** Call `feedback_accept` on the cycle and wait for its
   confirmation, THEN `end_change`; firing both in one batch lets `end_change` race
   ahead and it refuses with "cycle is NOT scored — it was never closed out" (AGENTS.md
   RULE 2: score before moving on), leaving the lock held and forcing a retry.

Session detail (legacy-brain sweep, 2026-08-21): `references/legacy-brain-sweep-2026-08-21.md`
