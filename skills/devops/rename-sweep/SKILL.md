---
name: rename-sweep
description: Fleet-wide term/artifact renames and decommission sweeps.
version: 1.0.0
category: devops
platforms: [linux, macos]
metadata:
  hermes:
    tags: [rename, sweep, decommission, migration, bulk-edit]
    related_skills: [change-checklist, survey-before-action, enforcement-change-safety, cortex-bus]
---

# Rename Sweep — Fleet-Wide Terminology/Artifact Renames

Systematic method for removing or renaming a term, product name, or artifact
across an entire repo (decommission, rebrand, terminology migration — e.g.
the 2026-08-21 legacy-system→mycortex purge: 168 files, 0 residual tokens). The
hard part is NOT the replacement — it is the compound tokens the catch-all
misses, the mangled tokens the guards create, and the deleted-file
cross-references that silently break tests and indexes.

## When to Use

- User says "remove ALL references to X", "rename X to Y everywhere", or a
  product/component is decommissioned and must be purged.
- A handoff/proposal claims a rename is "done" but the branch never landed —
  you must verify and often redo the sweep fleet-side.
- Don't use for: single-file renames (patch it), or docs-only path fixes
  (documentation-auditing covers stale paths).

## Prerequisites

- A governance lock (`begin_change`) — write tools block without it.
- Adversarial-verifier loaded (pre-commit blocks changed `ops/` paths).
- Enumerate the surface FIRST: `git grep -i <term>`, files named `*<term>*`,
  deployed copies (`~/.steadfaste-fleet/scripts/`), cron jobs (`jobs.json`),
  live configs. 200+ matches across 100+ files is normal for a real sweep.

## Procedure

### 1. Classify every occurrence into three buckets

| Bucket | Meaning | Action |
|--------|---------|--------|
| Functional | Keys, var names, service names, CLI commands, DB/role names, paths in code | Replace with the new term (exact tokens) |
| Dead artifact | Files/dirs/skills whose whole purpose was the old term | `git rm` |
| Historical | Dated docs, transcripts, review reports, design comparisons | Rephrase to a neutral descriptor ("the legacy brain"), never the new term |

Delete the dead artifacts FIRST (git rm), then sweep references to them — but
verify a suspected DUPLICATE tree is really a duplicate before `rm -rf` (see
Pitfalls: a copy can hold the only copy of some files).

### 2. Replacement engine — guards BEFORE catch-all, in one ordered pass

Blind `term → newterm` breaks everything. Use this order per file:

1. **Exact functional tokens** (case-sensitive, applied first): keys
   (`legacy-system_sources_ok`), var names (`V_LEGACY_SYSTEM`), file/service names
   (`import-legacy-system.py`, `com.legacy-system.autopilot`), env vars
   (`LEGACY_SYSTEM_PG_PASSWORD`), issue keys. A word-boundary catch-all
   (`\blegacy-system\b`) MISSES underscore compounds (`legacy-system_search`,
   `legacy-system_legacy_*`, `FIXED_LEGACY_SYSTEM`) — list them explicitly.
2. **Phrase guards** (case-insensitive): "X decommissioned/deprecated" →
   "legacy …", "(X replacement)" → dropped, "X→new" → "legacy→new".
3. **Mode-aware catch-all**: case-preserving replace of the bare word —
   "mycortex" in functional/active files, "legacy brain" in historical files.
4. **Skip surgical files** (register lines, stub removal, service-check
   deletion, test guards) and edit them by hand after the bulk pass.

### 3. Mangle scan (mandatory after the bulk pass)

Guards with spaces mangle compound tokens. Grep for these and fix:

- Space inside a token: `install-legacy sync.sh`, `~/.legacy brain`,
  `com.legacy brain.sync-watch`, `garrytan/legacy brain` (broken URL),
  `main.legacy brain` — re-slug to `legacy-brain`.
- Case-insensitive label guard swallowed a COMMAND: `"Legacy Sync"` guard
  also matched `legacy-system sync --source X` → `"Mycortex Sync --source X"`.
  Scan for title-case command garbles; the CLI command is lowercase.
- Catch-all hit a path that meant the OLD home dir: `~/.legacy-system` →
  `~/.mycortex` (wrong product!) — paths must map to the neutral slug.
- `\bterm\b` misses `term_compound` — the residual grep catches these;
  fix each with an exact-token replacement.

Finish with `git grep -i <term>` == 0 AND a token-mangle grep
(`legacy brain/`, `install-legacy `, `~/.legacy `) == 0.

### 3b. Sweeping a PATH, not just a bare term

When the target is a file PATH (`~/.dir/old.conf` → `~/newdir/.env`), the
replacement changes BOTH the directory and the basename. A bare-filename
catch-all (`old.conf` → `.env`) then rewrites variable- and call-composed
forms into paths that still aim at the OLD directory:

```python
OLD_HOME / "old.conf"                  →  OLD_HOME / ".env"          # ✗ old dir!
Path.home() / ".dir" / "old.conf"      →  Path.home()/".dir"/".env" # ✗ old dir!
```

Enumerate the FORMS first, then give each its own rule, most specific first:
`$HOME/.dir/old.conf`, `${VAR}/old.conf`, `~/.dir/old.conf`, `%h/.dir/old.conf`,
`Path.home()/".dir"/"old.conf"`, `HOME/".dir"/"old.conf"`, `.dir/old.conf`,
and only then the bare basename.

**A PATH sweep aimed at the home dir must cover the ABSOLUTE spellings too, not just the `~/` form.** A pass that replaces `~/.old` / `.old` rewrites only the tilde spellings; the `${HOME}/.old`, `$HOME/.old` spellings are the SAME physical path under a different token, and a `~/`-grep leaves them untouched — the close reviewer counts them as an incomplete sweep and files a finding. Put every home-expansion spelling in the form list and sweep them ALL in the same pass; then residue-grep each spelling independently.

Finish with the residue grep — the NEW
basename still sitting in the OLD directory
(`grep -rnE '"\.dir" */ *"\.env"'`) — that grep, not the old-name grep, is
what finds these mangles. Dry-run the table and read the plan before writing.

**A file RENAME has the same trap with no path form in it at all.** When the swept token is a
whole path (`docs/templates/skills.yaml` → `skills.yaml`), every site that BUILDS that path
from segments or a variable never contains the token, so a flawless token sweep leaves them
pointing at a file that no longer exists:

```python
template_yaml = REPO / "docs" / "templates" / "skills.yaml"   # survives a token sweep
template_yaml = REPO / "skills.yaml"                          # what it must become
```
```bash
MANIFEST_TEMPLATE="${TEMPLATES_DIR}/skills.yaml"              # survives a token sweep
MANIFEST_TEMPLATE="${REPO_DIR}/skills.yaml"                   # what it must become
```

So the RESIDUE grep must hunt the CONSTRUCTION, not the old token — and it will read clean if
you only grep the old name, because it was clean BEFORE the sweep too. Grep for the last
unchanged segment followed by the basename (`templates.{0,12}skills\.yaml`), for the directory
variable (`TEMPLATES_DIR.*skills\.yaml`), and for the joined-segment form
(`"templates" */ *"skills\.yaml"`). Two consumers were dangling in a nine-reference sweep that
otherwise verified clean; that grep is what found them.

### 4. Deleted-artifact cross-reference sweep

Deleting files breaks things that assert their existence:

- **DOCS-INDEX.md** entries for deleted docs/scripts (remove the rows).
- **Test fixtures** asserting paths to deleted files
  (`test_golden_expected_paths_exist_in_repo` FAILs on stale expected_top3 —
  delete the stale query entry, keep count invariants like "25-30 queries").
- **register() lines** in fleet-update.sh for deleted scripts (remove both
  the line and any update/stub functions that call them).
- **Skills referencing the deleted skill** (governance-sentinel listed
  legacy-system-maintenance) — reword or drop the pointer.
- **Deployed copies**: after deploy, `rm` stale deployed artifacts the
  doctor flags ("Remove: …" in REQUIRED ACTIONS).

**Not every occurrence is a LIVE reference — committed RECORDS must survive the sweep.**
Evidence captures, transcripts and dated reports quote the old path as the thing that WAS run at
that revision. Rewriting one falsifies a record and breaks the checksum it carries, so exclude those
directories from both the replacement pass AND the residue grep — and PRINT the exclusions beside
the count. A bare "0 residuals" that silently skipped a directory reads as coverage of everything,
which is the same class of over-claim as a stale token left in place. Coverage is also
per-PATTERN-CLASS: a content grep covers content, never filenames, and a content-only lint does
not cover what a future reader can rename — an audit report must either check the filename class
or state explicitly that it did not; an unsourced "every remaining reference is accounted for"
with no bucket list invites the probe that finds the miss.

**A PRINTED remedy is a live reference.** A check or warning that tells the operator to run a command
is held to the same standard as code: if the string names a path that does not exist, the operator
lands on a dead command. Resolve every printed fix/warn path against the DEPLOY MAP's target rather
than the repo layout — deploys flatten and nest differently, so `<deploy>/scripts/manage/<x>.py` can
be wrong while `<deploy>/scripts/<x>.py` is right, in the same file. Sibling printed paths may be
correct exactly as they stand: check each one instead of "tidying" the directory component off all
of them.

### 5. Verification battery

- `bash -n` every changed .sh; `py_compile` every changed .py; validate
  JSON/YAML (deleted files show as FAIL — ignore them, they're gone).
- Adversarial gate A2 on every changed script.
- Run the test suites that touch deleted paths (parity/golden tests).
- Live-service tests fail on deploy-order lag: a running service still
  serves the old label until `fleet-update.sh` redeploys — verify against
  the repo, not the live service, then re-test after deploy.
- `git grep -i <term>` == 0, doctor clean, deploy via fleet-update,
  then push.

### 6. Land it fleet-wide — 0 residuals is NOT done

A sweep that only changes files is finished on YOUR host and broken
elsewhere. A fleet-wide rename/config move is done when every other host
converges without a human, and agents can see what changed. Ship all four
with the sweep, in the same change:

1. **An automatic per-host migration** on the update path — make the deploy
   script RUN the consolidation (idempotently) before anything reads the moved
   thing, and make the migration script seed the new location from the old one
   rather than failing when it is absent. A host that still has only the old
   layout must self-heal on the next `fleet-update.sh`.
2. **An enforcement check in the doctor** — FAIL on the new invariant (target
   exists, right perms, old path absent or a symlink to it, and a `fix:` line
   naming the exact script to run). A sweep with no check silently regresses
   the first time someone recreates the old artifact.
3. **A runbook** (`docs/runbooks/<topic>-migration.md`) — the convention table
   (which path is canonical, which are symlinks, which are not ours), how to
   apply on Linux AND macOS, how to verify, the changes to implement, and a
   symptom→cause→fix table. Index it in DOCS-INDEX.
4. **Agent-facing skill notes** — update the setup/troubleshooting skill the
   agents actually load, and fix any prose your own sweep mangled (check every
   line you rewrote reads sensibly; a rename can turn a sentence into a
   tautology).

5. **A per-host file the deploy APPENDS to is never pruned.** A deploy that writes a
   line only when absent (`grep -q '^NEW=' f || printf 'NEW=…\n' >> f`) cannot remove a
   line an earlier release wrote, so a retired name survives every deploy forever and the
   host reads as half-migrated indefinitely (measured: a legacy interpreter line sat in a
   per-host env file through months of deploys). The deploy must REWRITE that file to the
   new invariant — read it, drop the retired lines, write it back — and the doctor needs a
   FAIL naming the retired name with the repair command.
6. **Fix the GENERATOR, not its output — then gate the generator.** When the swept names
   live in GENERATED artifacts (per-harness READMEs, unit files, generated manifests),
   editing the artifact is a no-op: the next regeneration restores the old names. Fix the
   generator's template, regenerate, and commit both. Then add the generator's own
   `--check` to the lint, because a generator whose input was renamed but that was never
   re-run keeps shipping the retired names behind a GREEN repo grep — the generated file
   exists and reads plausibly. A content sweep of generated files is not coverage of the
   generator.
7. **Classify the surviving names, and let only EXTERNAL CONTRACTS survive.** After the
   final release the legitimate keeps are: the converter/forwarder tool that must name the
   spelling it converts, comments describing the migration, and the names a runtime you do
   NOT own injects across the process boundary. Whitelist those BY NAME in the lint; every
   other surviving token is residue. Report live surfaces separately from test/record ones
   so one repo-wide count does not mix deliberate keeps with real stragglers.

The user WILL ask "will agents be able to implement this?" if you skip 1–7 —
treat that as part of the deliverable, not a follow-up.

## Pitfalls

- **A file rename is not done until the CONSUMER layers that reference the artifact by name are swept — doctor's harness-verify + MCP-wiring checks are the canaries.** Renaming a deployed artifact (an MCP script, a harness extension file) while its registry/verify/installer entries and the per-host MCP registration still name the old path leaves a GREEN repo grep and a RED doctor: `Harness (x)` FAILs on the missing old path, `MCP wiring (y)` FAILs because a registered script path no longer exists. Sweep EVERY layer that writes the name in the same change — `harnesses/registry.yaml`, the verify/governance-check scripts, installer `resolve_ext`/OURS tool lists, tests, docs, skills — plus the per-host `~/.hermes/config.yaml` MCP `args` path (`hermes config set mcp_servers.<name>.args.0 <new-path>`, never a raw config write; hosts converge on next install). Then read the FAIL taxonomy: a checksum FAIL on an edited file is just "not yet deployed," but a harness/`MCP wiring` FAIL is real drift that survives deploy.
  **A verifier that PATH-RESOLVES the renamed artifact is part of the sweep, and it only FAILS on hosts that can execute it.** A harness verifier whose default-under-test path joins `extensions/<renamed>.ts` (or the mjs entry that imports it) breaks at the import on the hosts that have the loader (an npm-managed pi install), while a host without that loader SKIPS before reaching the path — so a LOCAL green (or a SKIP) is not evidence the sweep reached the verifier, and neither is a repo grep that finds no old token in your own files. Fix the verifier SOURCE path AND re-run it on a loader-capable execution; a verifier that silently skips on the host that ran it leaves the class broken for every host that can.
  **Rename-aware TEST FIXTURES assert the tool's CANONICAL name list, not the pre-rename spelling.** An audit fixture that registers the old server/extension name (or an installer test that probes preservation through the old name) makes a healthy synthetic host read FAIL once the tool under test requires the new name — and the reverse: a fixture asserting the pre-rename name passes vacuously when the tool no longer writes it. Re-derive fixture names from the tool's REQUIRED/owners list (the audit's required-set, the installer's written-server loop, the preservation probe's canonical names), not from the history of what the names used to be.
- **Renaming an MCP server registration name is a sweep with its own traps** — every name
  writer in one commit (installers, audits, watchdog maps, enforcer text, doctor wiring maps,
  tests), rename-not-add by probe binary, `hermes mcp add` is interactive (pipe `printf 'Y\n' |`),
  and the rename kills the live session's own `mcp__<old>__*` tools mid-cycle (CLI close-out
  with an explicit session_id is the fallback). Full recipe: `references/mcp-server-name-rename.md`.
- **A directory that LOOKS like a duplicate copy is not provably one — diff it
  file-by-file before deleting anything.** The same sub-path appearing twice
  (`<dir>/docs/…` alongside `<dir>/docs/new/<project>/docs/…`) usually means a
  stale copy, but the copy can hold files the canonical tree lacks. Hash both
  trees (`find <a> <b> -name '*.md' -exec md5sum {} + | sort`) and print BOTH
  the duplicate hashes AND the files present in only one tree — in the real
  case 15 of 38 files were byte-identical while **23 existed only in the copy**,
  so deleting on the assumption of duplication would have destroyed the only
  copy of real content. Procedure: merge the uniques up into the canonical tree
  (preserving relative paths — some may land OUTSIDE the subtree you are
  keeping, e.g. a sibling `spec/`), then delete only the residual duplicates.
  The proof is `duplicate hashes == 0` afterwards, not the deletion itself.
- **A prefix-INSERTION sweep is not idempotent — the new text still contains the
  old pattern.** Renaming a bare term never re-matches itself; INSERTING a path
  segment does (`docs/<x>/` → `docs/older/<x>/`), so a second rule in the same
  pass — or any re-run — matches inside the already-rewritten string and produces
  `docs/older/older/<x>/`. Guard the replacement with a negative lookbehind
  (`(?<!older/)docs/<x>/`) or match only the un-prefixed form, then assert exactly
  one pass's worth of change before writing.
- **git stash pop without `--index` silently unstages everything** — the
  staged index is lost; re-stage with `git add -A` before re-committing.
- **`git checkout --ours/--theirs <file>` during rebase restores the WHOLE
  file from that side** — auto-merged hunks in that file are reverted (a
  `--ours` on install.sh reverted an entire 16-file sweep's changes to it).
  Prefer per-hunk resolution or re-apply and re-verify with grep after.
- **A sibling push can land mid-sweep** (Moses committed Titus's resend
  while the sweep ran). The pre-push dogfood gate then blocks with
  "Deploy sync — HEAD ahead of last deploy". Fix: `git pull --rebase`,
  resolve (the sweep is usually a superset — origin's hunks are fine where
  cosmetic, re-apply yours where they conflict on substance), deploy,
  doctor, re-push. Check `git log origin/main -3` before pushing a big
  change. If `git pull --rebase` refuses on UNSTAGED changes a peer left in
  flight, `git pull --rebase --autostash` carries them through and re-applies
  them automatically — never `checkout`/`stash` the peer's working tree away,
  and never commit their edits alongside your sweep. After the rebase,
  `git status` should still show their edits uncommitted.
- **Fence corruption is pre-existing, not yours**: the pre-commit fence
  check may flag an unbalanced file you merely touched. Verify at HEAD
  (`git show HEAD:<file> | grep -c '^```'`) before fixing; a dangling
  opener at EOF gets removed, not closed.
- **The word count in PII scans includes pre-existing patterns** — diff
  the flagged lines against what you actually changed before claiming
  your sweep introduced PII.
- **Exclude the tool that PERFORMS the migration.** A script that merges the
  old artifact into the new one legitimately references the old name (its
  idempotency check depends on it). A blanket rename breaks the very mechanism
  you are migrating to — add it to the skip list explicitly, and say so in the
  commit message so a later reader does not "fix" it.
- **Exclude the tool that DETECTS and REPORTS the old artifact.** A checker or
  doctor check whose *message* names the old path must keep that literal — there
  the old name is the SUBJECT of the sentence, not a reference to a path. A
  blanket replace turns `stray ~/old/dir/.env` into a message naming the
  CANONICAL file: a tautology that now misreports which path is wrong.
- **Exclude the skill that TEACHES the sweep itself.** A how-to/rename skill whose worked example uses the old term as the EXAMPLE (a mapping line, a migration command) must be kept out of the blanket sweep: a path-token replace rewrites BOTH sides of its `old → new` mapping to the new term — the mapping inverts to `new → new` and the worked example silently disappears (measured: a device's own rename-sweep example corrupted this way; revert the file and re-exclude it). Pull such a file to a fixed baseline before sweeping, keep it out of the residue grep, and say so in the commit message so a later reader does not "finish" the sweep on it. **Keep it out of the residue LINT too — and expect that lint to re-flag the file long after the sweep.** A gate that greps `skills/` for the retired token has no concept of a teaching reference, so it fires on this file on every run; when the sweep is long finished and the ONLY failure is that reference, the choice between generalizing its sample tokens to the wildcard family form (which does not match a `PREFIX_[A-Z0-9_]+` pattern) and exempting it by path in the lint is an operator gate-scoping call — report it with the one action that clears it, never re-scope the gate yourself and never wave the file through as obviously-fine residue.
  **Nothing re-reddens a finished sweep faster than a later skill-lesson BACKPORT.** A lesson file copied into the repo from the deployed tree carries whatever tokens it was written with, so it can turn the brand lint red long after a green sweep with every host surface still clean — attribute a fresh lint failure with `git log --diff-filter=A --oneline -- <flagged file>` before suspecting the original sweep.
- **A stranded (deployed-only, repo-source-missing) skill you inherit is a PRE-RENAME SNAPSHOT until the grep says otherwise — classify DELETE vs sweep+backport per file before "making it local".** A dir in `~/.steadfaste-fleet/skills/<cat>/<name>/` with no `git ls-files` source is exactly what the deploy never pruned. Classify each: a dir whose frontmatter duplicates a live repo skill (same `description`, a rename runbook documents it) or that teaches a fully-superseded layout is DEAD — delete the deployed dir, ship no backport; a genuinely-distinct skill keeps its value but its body carries the old brand (`~/old-name`, `OLD_*` env vars, `old-<tool>.sh`, `author: Old Brand`), so sweep the tokens to the new spelling, VERIFY each new path/flag/env actually exists (the sanctioned unlock flag is `--<new>-update`, the env `FLEET_DEPLOY_HOME`, the script `<new>-dogfood.sh` — never invent an equivalent), then backport and RE-RUN the brand lint: a reused old env token turns the retired-spelling gate red on the newly added files. `reconcile-stranded-skills.py` will NOT list these — it reports same-file SKILL.md DRIFT (a deployed superset to backport), never a whole dir missing from the repo; find missing dirs with `comm -13 <(ls <repo>/skills/<cat>) <(ls <deployed>/skills/<cat>)` plus `git ls-files | grep -i <name>`.
- **A stale CLI reference in a doc is fixed with the ACTUAL entry point, not the doc's claim — grep the tool's own usage and the dispatcher before writing the corrected command.** `git grep` the alleged subcommand in the real command module; if absent, read the implementing script's own `--help`/usage line and write that invocation verbatim.
- **A clean residue grep does not prove the surviving text is TRUE.** It only
  proves no old token survives. This is the code-side twin of the prose-mangle
  check in step 6.4 — re-read every string the run changed, error/warning
  message strings included, and confirm each still describes what it claims.
- **Tool output can REDACT credential-shaped strings, so a replacement that reports 0 matches for a string you can SEE is a red herring — compare BYTES before believing the file differs.** An env-var name such as `$FLEET_BUS_TOKEN` renders intermittently redacted (e.g. `$CORTE...KEN`) across tool outputs, and trusting the rendered read made a sweep mis-target a replacement for several turns. Assert every replacement count against the RAW read (`txt.count(needle)`), and when any comes back 0, re-read the exact bytes (`repr(segment)` / `needle.encode()`) — the count, not the display, is what proves whether the token is present and where.
- **When the swept term is a REAL WORD (not an acronym/slug), the catch-all renames
  it inside unrelated domain language — and the residue sits under the NEW token,
  so the old-name grep reads clean.** `cortex` is a medical/neurological term as
  well as a product name: a `cortex → fleet` sweep rewrote "auditory cortex",
  "motor cortex", "prefrontal cortex" into "auditory fleet", "motor fleet", … in
  an EEG/BioSignal reference doc — a healthy-looking mangle the old-token grep
  cannot see because the token is gone. Grep the NEW token in NON-code/prose and
  domain-specific files (`skills/health/…`, any doc full of the plain noun) and
  re-read occurrences that were never the product token; better, scope the
  catch-all to the token CLASS (uppercase `CORTEX_*`, the slug `cortex-`
  compounds) and sweep the bare lowercase word only in code/var contexts, never
  in prose. A word-boundary catch-all on a shared English word is the highest-
  entropy mangle class because the residue is invisible to its own proof grep.
- **Renaming a BARE IDENTIFIER (a var/function name) needs a full word-boundary
  on BOTH sides — a space-anchored replace splits assignments from usages into
  NameErrors you introduced.** `term ` (word + trailing space) rewrites `x = …`
  assignments but not `x.attr`, `x)`, `{x}`, or `x.method(…)` usages (no space
  after the token), so the renamed assignment and the untouched usages diverge.
  Rewrite with `(?<![A-Za-z0-9_])term(?![A-Za-z0-9_])`, then AUDIT for split
  files: grep every touched code file for the NEW identifier in an assignment
  position AND a bare OLD identifier, and fix both sides consistently (a sweep
  that changes the same local name across 60+ tests and scripts is exactly where
  this bites).
- **When the swept token is a DIFFERENT abbreviation in some files, don't force
  the target's new name — give the colliding local variable a DESCRIPTIVE
  name.** A token that is the product/CLI name in most files (e.g. `hc` the bus
  CLI) can be an unrelated local abbreviation elsewhere (`hc` = a HealthCheck
  instance, `hc` = hymn count). Renaming those to the CLI's new name (e.g.
  `fleet`) is misleading scope-drift an adversarial reviewer will flag; use a
  name that states the variable's meaning (`healthcheck`, `hymn_count`) instead.
- **Exclude same-basename files in other directories.** `~/.dir/conf.d/old.conf`
  and `~/.other/old.conf` are DIFFERENT files; a basename rule rewrites them
  happily. Guard per-line on the full distinguishing path, and verify the
  genuine ones survived.
- **After a path sweep the repo can still WORK while being wrong.** A symlink
  at the old location hides every missed reference until the symlink is
  removed — so residue-grep the source, then remove the symlink deliberately,
  never assume "the tests pass" means the sweep is complete.
- **A home-dir rename leaves PHANTOM old paths in compiled bytecode — a traceback naming the
  old dir with `??` source lines is `.pyc`, not source.** Python's `__pycache__` caches modules
  compiled before the rename; the bytecode embeds the pre-rename absolute path, so a failing
  test or import after a clean sweep still raises `FileNotFoundError: …/old-dir/…` (or a
  failing test asserts a deliberately-removed constant and reads as a phantom regression).
  Confirm with `strings <file>.pyc | grep <old-path>` — a source grep returns 0 while the pyc
  greps hit — then delete the `__pycache__` dirs: they regenerate on next import, and removal
  is the only fix that makes the phantom disappear. Sweep host-wide, not just in the repo: the
  deployed tree's stale pyc files are what a live traceback actually shows.
- **A home-dir rename leaves venv console-script shebangs pointing at the OLD
  interpreter — every wrapper dies with `bad interpreter` on first use.** A venv
  re-created at the new path still has `bin/*` wrappers whose `#!` line names
  the pre-rename interpreter, so `pytest`, `pip3`, `uvicorn`, … fail with exit
  127 while the venv's own `bin/python3` is fine — and the first tool a
  verification run reaches (pytest) is exactly the one that dies, so the suite
  looks broken before it starts. Immediate workaround: `bin/python3 -m <tool>`
  (the interpreter itself is unaffected). Fix in place, never re-create the
  venv: `grep -rl '<old>/venv/bin/python3' ~/.steadfaste-fleet/venv/bin | xargs
  sed -i "s|#!.*<old>/venv/bin/python3|#!${HOME}/.steadfaste-fleet/venv/bin/python3|"`,
  then re-grep → 0. The sed replacement must be DOUBLE-QUOTED so the shell
  expands `${HOME}` — a literal `${HOME}` can never stand in a shebang (the
  kernel does not expand variables there). Add this to the migration runbook's
  cleanup list; it is a distinct residue class from stale `__pycache__`.
- **A background result can be SUPERSEDED — check it is the latest run before
  acting.** A long test/deploy run finishing late reports the tree from when it
  STARTED. Two completion notices can disagree (an older 7-failure run landing
  after the fixed 5-failure run). Compare against the current HEAD and re-run
  the pair/suite if in doubt; never report a stale FAIL list as the state, and
  never "fix" what a superseded run complained about.

- **`git mv` needs the destination's PARENT directory to exist, and the error names the SOURCE.**
  `git mv a/b/c a/b/bin/c` answers `fatal: renaming 'a/b/c' failed: No such file or directory` —
  pointing at a file that is right there. `mkdir -p` the new directory first. Once the rename is
  staged, the OLD path can no longer appear in a pathspec (`git add <old>` / `git commit -- <old>`
  → `pathspec '…' did not match any files`): stage/commit the live paths, or omit the pathspec and
  let the staged rename carry the deletion.
- **`git diff --cached --name-only` collapses renames to their NEW path — a
  pathspec commit of a rename sweep silently leaves the deleted sources staged
  and uncommitted.** When you commit with `git commit -m … -- $(git diff
  --cached --name-only)` (the name-your-paths gate route), rename detection
  drops the deleted `old/…` entries from the list, so HEAD still contains them
  and the old dir survives the commit. Collect the pathspec with
  `--no-renames` (`git diff --cached --name-only --no-renames`) so deletions
  are included, then verify after the commit that `git ls-tree HEAD
  -- <old-dir>` is empty and `git status` is clean.
- **A launcher or executable moved into a subdirectory needs its self-relative module resolution
  changed in the SAME commit.** See `shell-scripting` → "Moving an executable": without it the
  script silently runs a different tree's module through its known-path fallback, and every test
  that runs the installed copy still passes.

- **A green doctor does NOT mean a home-directory rename ran.** Renaming a live
  deployment's dirs (`~/hermes-cortex` → `~/steadfaste-fleet`, `~/.hermes-cortex`
  → `~/.steadfaste-fleet`) ships a guarded migration script + a golden-path
  verifier, but deploying the code does not execute the rename. Verify on the
  host: `FLEET_DEPLOY_HOME` still unset ⇒ `fleet-update.sh` still defaults to
  the OLD `~/.hermes-cortex`; no transition symlink yet; the old dirs are real
  dirs, not symlinks. Those symptoms mean "the code landed, the rename has NOT".
  A host that still runs everything off the old paths is not a failed rename —
  it is a host that is correctly NOT yet migrated.
- **The local-dir rename is gated: dogfood host FIRST, fleet in a SEPARATE rollout
  cycle, and the code-default path change is held back until the fleet migrates.**
  A rename plan written for one dogfood host (verifier named `verify-rename-
  <host>.sh`) deliberately does NOT apply to the rest yet. Before "finishing" a
  rename on any non-dogfood host, check the plan's rollout step: it may mandate
  esther-green first and a separate fleet dispatch, with the deploy default in
  `fleet-update.sh` (`FLEET_DEPLOY_HOME="${HOME}/.hermes-cortex"`) deliberately
  unchanged until every host has migrated (so the 4 agents' next deploys stay
  consistent).
- **Confirm the repo rename landed by `git ls-remote` on BOTH the old and new
  URLs returning the same HEAD** — GitHub auto-redirects the old name, so equal
  HEADs prove the native rename happened and the old URL is an alias, not a
  second repo. But note the renamed repo's HEAD can be AHEAD of a given host's
  last pull; pull to current before running any migration script so you deploy
  from the latest version.
- **Never run a home-dir migration under an in-flight deploy writing to that same
  dir.** A `fleet-update.sh` mid-run is reading/writing `~/.hermes-cortex`;
  moving the directory out from under it breaks both the deploy and the rename.
  Wait for the deploy to finish (poll its log for `UPDATE_EXIT=0` and the doctor
  verdict) before `migrate-home-dirs.sh`. `migrate-home-dirs.sh` also refuses to
  run from inside a source dir (`cwd` guard) — run it from a stable workdir.
- **`install-crons.sh` regenerates the bridge/bus units but NOT the Hermes-owned
  gateway + dashboard units — hand-patch those, or the golden-path "0 old tokens"
  unit scan stays red.** After regenerating units with `FLEET_DEPLOY_HOME`
  overridden to the new path, `grep -Rl '\.hermes-cortex' ~/.config/systemd/user`
  still returns the `fleet-gateway.service` + `hermes-cortex-dashboard.service`
  live units (plus the gateway's `default.target.wants` symlink), which the cron
  installer never writes. Rewrite their `ExecStart`/`WorkingDirectory`/log paths
  to the new deploy-home directly. Discrimination that matters: the verifier
  greps the DOTTED deployed-home token (`\.hermes-cortex`), so a bare repo-path
  token (`%h/hermes-cortex`, no leading dot) is ALLOWED through the transition
  — it resolves via the symlink; only the dotted token must reach zero.
- **A golden-path verifier FAIL can be a host-layout difference, not a rename
  regression — verify the service by port/process before treating a unit-state
  FAIL as a break.** A verifier written for the dogfood host assumes a service
  runs as a systemd unit; the same service can run as a loose process on another
  host with its unit never enabled (`systemctl --user is-enabled …` → disabled,
  pre-existing). Before calling the rename failed on an `inactive` unit check,
  confirm the service is actually alive its own way (`pgrep -f <server>.py`,
  `curl -fsS` to its port → 200) and note the difference as an environmental
  host difference, not a regression.
- **Residual old-name tokens are CLASSIFIED, never blanket-swept.** After the bulk pass, the leftover tokens in NON-record files fall into two classes: FUNCTIONAL keeps (one-release shim files, doctor checks that verify the deployed legacy name still works, the enforcer's dual-accept regex, restore/migration utilities, a legacy exception flag the enforcer still accepts) and DOCUMENTARY stragglers (a prose line whose sibling on the same line was renamed, a docstring still naming the pre-rename function or command). A blind word-sweep of the residue breaks the migration contract it exists to preserve — the close reviewer flags exactly the documentary class, file by file, and passes the functional keeps. Fix the flagged lines precisely, never the keeps.
- **A transition shim is a DEADLINE, not a fixture — when the operator declares the migration
  done, remove the shim and every layer that registers it in the SAME change.** A compat alias
  kept "for one release" outlives its purpose and keeps the old name alive: the file, its
  register line, brand-lint fixtures, and any skill lesson teaching "keep the alias" must all
  be inverted (transition alias REMOVED) in one commit, or agents keep recreating it from the
  docs. The definitive completion proof is destructive and operator-driven: delete the old
  artifact (shim, folders) and re-run the whole battery — a green old-name grep is not proof
  the old path is not still load-bearing somewhere bytecode or config names it.
- **A residue grep that matches CONTENT cannot see a FILENAME — enumerate the filename class separately and guard it in the lint.** A brand lint whose patterns are all content (`ops/scripts/<old>/`, `scripts/<old>.py`, `\b<old> <verb>\b` invocations) leaves every file NAME carrying the old brand untouched: live tests named `test_<old>_*.py` whose docstrings already say "Tests for `<new>`" pass the whole battery — only the name is stale (measured: three `test_hc_*` files survived a hermes-cortex→steadfaste-fleet sweep whose lint was 5/5 green). Sweep filenames with a name-based pass (`find tests -name 'test_<old>_*'`, `git ls-files | grep -i <old>`), migrate every reference that names them (evidence-runner subprocess lines, comments pinning "pinned by tests/<old>.py"), then ADD a filename guard to the lint itself (`find ... -name 'test_<old>_*'` fails the suite) so the class cannot silently return. Content coverage does not imply name coverage — say which one the verification actually checked.
- **A residue COUNT is inflated by every KEEP that CONTAINS the swept term — anchor the family pattern, and check the guard's CASE.** A swept term that is a SUBSTRING of a deliberately-kept sibling is the normal shape (`cortex` inside `mycortex`, `hc` inside a longer word), so a loose pattern counts the keep as residue and overstates the work: the family pattern `CORTEX_[A-Z0-9_]+` matches the `CORTEX_` inside `MYCORTEX_COMPOSE_DIR` and `_MYCORTEX_CACHE`. The fix is a left boundary a word character blocks — `\bCORTEX_`, equivalently `(?<![A-Za-z0-9_])CORTEX_` — because the character before that `C` is itself a word character, so the keep no longer matches. The same trap survives a negative-lookbehind guard whose CASE does not match the family's: a guard written `(?<!my)` never fires against the `MY` in `MYCORTEX_X`, silently re-inflating the count it was added to prevent. Report counts as `N raw - M keep-substring` and NAME the keep, so a reader can check the subtraction instead of trusting the total.
- **"Where are we with the migration?" is answered by MEASURED SURFACES, never by a plan's status header.** A rename lands in commits, package dirs, unit names, cron entries and per-host env files; the plan / runbook / followups docs keep whatever status line they were written with (`Status: PLAN - not started`, `Release 2 - not yet`, `IN PROGRESS since <date>`) long after the landing, so reading them reports the migration backwards. Measure, then report with the source named: `git log --oneline` (did each sweep land, and when), the renamed package/script dirs, the unit names under `~/.config/systemd/user/`, the cron names in `jobs.json`, and a COUNT of the old spelling in each host's `.env` (grep names only, never values). Scope the answer to the hosts actually measured and say which ones you did not reach — a single-host measurement is not a fleet-wide claim.
  **A doc whose status header contradicts the landed state is a FINDING, not context** — the same class as a superseded "kept"/"deferred" row. Patch the header and any `not yet` / `not started` section in the sweep's own commit (see `references/env-namespace-rename.md` section 6), or the next reader re-derives work that is already done.
- **An exemption token is PER-LINE — it must sit on the same line as every reference it
  protects, and the sentence must stay on ONE physical line.** A lint that ignores lines carrying a date token (a migration-record line) judges
  each line independently: wrapping a long reference onto a continuation line moves the token
  off the flagged line and the lint fails with no text change. Keep the token on every line
  that names the old path and re-run until the whole surface is 0 — the clean count is the sum
  of the per-line decisions. When fixing a flagged record, write the sentence as a single
  un-wrapped line so a re-flow can never separate the date from the old-path token again.
- **Live systemd units are part of the sweep surface — and the generator does NOT repair them.** The deploy's orphan-sweep deletes old-named deployed scripts; any live unit whose ExecStart names them fails en masse (one renamed bridge-run script took down 26 generated bridge units at once). Generators that wrote those units SKIP existing ones, so re-running the generator changes nothing. Hand-patch the unit files (new script path + renamed `--script` names), `systemctl --user daemon-reload`, `reset-failed`, then assert 0 failed units and the timers active.
- **Closing a large rename range is a long governed grind — budget for it, and only the lock-owning session can drive it.** The `end_change` blob-close loop judges ~4 blobs per wave (one 240s window), so a 350-file range is ~90 serial waves (an hour-plus), not minutes. Verdicts are content-keyed: blobs already CLEAN are never re-judged, so each further swept commit only adds its own new blobs to the grind. The per-blob reviewer REFUSES the close on any documentary straggler it finds — fix the named file (a content change re-opens only that blob), commit, call `end_change` again; the loop continues from where it stopped. A subagent CANNOT grind it: a child session's `end_change` with your task_id finds no pending cycle in its own session and mints empty MOVE_ON cycles (ledger pollution) instead of judging — the loop only advances from the session that holds the lock.
  **A close-gate FINDING may be stale — recorded against an OLDER blob than the current tree — verify the current content before grinding a fix.** Verdicts are keyed to blob content, so a finding the reviewer filed against a pre-fix version (evidence citing a duplicated var, a `CORTEX` literal, a mangled comment) can describe code your later sweep commits already corrected. Before fixing a named finding, compute the file's current sha (`git hash-object <file>`) and look up its verdict in the review store — if the current sha has no verdict (or a CLEAN one), the finding is stale and the file may need no change at all; re-emit the close to have the gate re-judge the changed blobs. Checking `blob_reviews` against `git hash-object` first separates the real remaining defects from the historical ones and stops a grind that would otherwise "fix" already-fixed files.
- **A close-gate finding can be CURRENT but its RECOMMENDATION mechanically wrong — verify it against the live function before adopting.** The per-blob reviewer audits the DIFF with quotes stripped, so its "revert to the original form" and "restore the removed line" suggestions can be wrong or even break the test (it asked to restore a duplicate fixture line that the converter demonstrably does not dedupe — restoring it fails the idempotence assertion the test exists to check). Before adopting a recommendation, run the test against the LIVE function (extract it into a harness with `sed -n '/^func()/,/^}/p' <src>`, add the `warn()` stub, feed the proposed fixture) and adopt only what holds; when you deviate deliberately, say so in a test comment so the re-review sees intent instead of re-flagging the deviation.
- **A blanket rename damages code and tests in PREDICTABLE shapes — audit for all of them, not just residual tokens.** (a) DEAD SUBSTITUTION: placeholder-`sed`/template code whose placeholder the sweep renamed away replaces nothing, and may read a STALE duplicate copy of the artifact — a sweep that fixed the kept template copy but not the consumer's source path leaves the installer consuming the un-swept copy (which can still hardcode a per-user absolute path). Point consumers at the kept copy and drop the dead substitution; flag an out-of-range stale copy as a separate issue rather than growing the range. (b) DUPLICATE COLLAPSE: a two-name fallback tuple `("LEGACY_X", "NEW_X")` collapses to `("NEW_X", "NEW_X")` when the legacy name matches the replacement — the lookup silently checks one var twice and error messages duplicate the name; grep for doubled tokens. (c) CONVERSION TESTS: a test that exercises a converter must keep the OLD spelling in its FIXTURES — blanket-renamed fixtures make it test a no-op — and a "OLD dropped" assertion must grep for the OLD spelling (renaming the pattern to the NEW token inverts it into always-fail). Stale headers and labels ("X ↔ X" tautology, "mirror" labels on a conversion) read as fabrication findings on re-judged blobs — fix them in the same commit as the fixtures.
- **The close-loop window has NO rename detection — a git-mv file reads as a BRAND-NEW file, and that refusal is un-fixable in content.** The per-blob reviewer materialises a rename pair as delete+add and judges the add as "new file added to a rename task" (fabrication/scope-drift). Every fix commit re-emits that blob and re-audits the SAME artifact, so feeding changes to a renamed file is a loop that does not converge. The resolutions that terminate: (a) commit metadata + an in-range authorization/evidence record (a committed note that the renames are `git mv` token-swaps and which names are intentionally deferred, with the operator's one exception) for scope-drift findings, (b) operator acceptance of the tooling limitation for the pure new-file artifact, and (c) fix the REVIEWER'S CONSTITUTION when the findings are the reviewer's own blindness, not your content — see the pitfall below. Do NOT keep re-emitting the blob hoping for a fresh verdict on the artifact.
- **A reviewer-constitution edit is the terminating resolution for a rename-range review flood — and it is an enforcement-layer change in its own right.** When the per-blob reviewer keeps filing the renamed files' PRE-EXISTING teaching as unverified-claim/fabrication ("new file, unverified install claims", "file teaches the old brand") and its recommendations drift into renaming live contracts, the loop does not converge no matter how much evidence you paste — each rereview clears 2 blobs, each close pass re-judges the next batch under the same blind rules. The fix (governance-closeout's "fix the reviewer's constitution"): add a rule to the reviewer prompt template — `docs/templates/adversarial-reviewer-prompt.md` (ORCHESTRATOR-ONLY) AND its deployed copy `~/.steadfaste-fleet/templates/` (the reviewer reads deployed-first; the two must be byte-identical), in the same commit. The rule shape that works: "a renamed file's pre-existing content is CONTEXT, not a claim — judge only what THIS range changed about it (old-brand tokens swept, STALE labels correct, renamed identifiers consistent); the keep-list authority doc (e.g. docs/rename-followups-*.md) is the contract, and tokens it documents as deliberately kept (a live short-name family, a separate live product name, a deliberately-kept service name) are NOT findings." The template's text is sha256-fingerprinted as the rules revision, so ANY template edit re-sends every previously-refused blob under the new rules — that is the unblock. Plan-review the template change first (it is enforcement-layer), and note that the new rule must NOT weaken the existing fabrication/unverified-claim standards for content the range actually authored.
- **Never put a rename-provenance claim in a renamed file's DOCSTRING — the window reads it as fabrication.** A header comment "renamed from `<old>` via git mv" contradicts the window's new-file view ("the diff shows ADD, the claim says rename") and is refused even on the exact wording that cleared once. The rename record belongs in the commit metadata (`git log --follow`) and an in-range evidence doc — point the docstring there, or say nothing about the rename at all.
- **The close reviewer audits renamed scripts' LEGACY internals, and each fix re-opens that file's whole re-audit.** Renamed .sh/.py that predate the sweep get their pre-existing error-swallowing, unverified claims, and external docstring references raked in full — and fixing any one re-emits the blob, re-auditing the rest. Batch the concrete fixes (real trailing-newline, missing dependency pre-check, swallowed `2>/dev/null`, docstring cites to sibling files the window cannot verify) into ONE commit per file, make docstrings self-contained with NO cross-file citations (the window cannot verify a sibling file), and stop after the concrete ones — the deterministic artifacts will not clear.
- **A gate's KEEP-LIST goes stale the moment the migration lands — sweep it in the same change.** A reviewer constitution, brand lint, or allow-list that still names the swept family as a "deliberate keep" is a gate that AUTHORIZES the exact drift it exists to catch: the close reviewer passes the residue, and the residue lint stays green. The only legitimate keeps after the final release are the EXTERNAL CONTRACTS (names a runtime you do not own injects across the boundary), the converter tool itself, and dated records — whitelist those BY NAME and let everything else fail. When a family is renamed, grep the gate surfaces for its name as part of the sweep; a keep entry is not neutral documentation, it is enforcement.
- **A residue lint must prove it is not blind — and must never swallow its own failure.** Three cheap properties, each of which has been the difference between a real gate and a green tick: (a) ASSERT the surfaces it greps exist — a missing directory or a renamed search root returns no matches, which reads as a clean sweep over nothing; (b) PLANT the exact shape in a temp fixture and require a hit — a detector that never fires is indistinguishable from a clean tree; (c) never `2>/dev/null` the grep — a failed search reads as zero residue. Scope the lint to the INSTRUCTION surface and NAME the excluded directories (dated records, plans, design rationale) beside the result, so the coverage claim is checkable rather than implied.
- **Red tests are residue too — and a rename-caused stale test can end up testing NOTHING.** Two mechanisms worth sweeping: a test that patches a RETIRED module attribute (`c.OLD_HOME = tmp`) silently CREATES a new attribute while the code reads the renamed one, so the test then exercises live host state and its verdict means nothing (the check reads the real host — the failure detail names real paths, which is the tell); and tests asserting retired log markers or strings go red and STAY red unnoticed when no runner references them (a test file nothing invokes is not coverage). Before declaring any failure pre-existing, reproduce it in a pristine worktree of the range base (`git worktree add --detach <tmp> HEAD`, same interpreter, same command, both sides) — most rename-caused failures are the test encoding the OLD contract, and rewriting them to the new one converts a long-red suite green. Remove the temporary worktree when done.
- **A blanket sweep silently breaks the detector's own NEGATIVE FIXTURE — restore the PLANTED token, not the lint.** A lint that proves it is not blind by planting the retired spelling in a temp fixture and requiring a hit gets its plant RENAMED by the same catch-all (the planted `OLD_X=` becomes `NEW_X=`), so the negative test then plants a legitimate token the detector rightly ignores and reports "the lint is BLIND" on a healthy gate. The lint's own grep PATTERN usually survives (the regex literal `OLD_[A-Z0-9_]+` is not itself a match), but the planted string is — after any bulk pass, grep the negative fixtures for the retired spelling and restore the plants.
- **A token-pattern sweep misses three shapes by construction — enumerate them in the residue grep.** (1) EXTENSIONLESS files (a git hook with no suffix) are skipped by `*.py`/`*.sh` include globs; (2) the asterisk PROSE form — a doc that says "set your `OLD_*` vars" survives because `*` is not in the token class `[A-Z0-9_]`; (3) the sweep's own REGEX LITERAL in the lint (`OLD_[A-Z0-9_]+` as a pattern string) is not a match. Fix each class with its own grep, never a re-run of the same pattern.
- **Before choosing the REPLACEMENT token, grep for it — if it already means something else, pick a specific name.** A mechanical `OLD_HOME → NEW_HOME` can collide with a pre-existing `NEW_HOME` convention meaning a DIFFERENT thing (user home vs deploy home across a dozen scripts). Verify the proposed target's current meaning; on a collision, disambiguate per concept (`NEW_USER_HOME`, `NEW_DEPLOY_HOME`, `NEW_REPO_HOME`) — the standard is ONE specific name per meaning, never one generic name for two. A rename that folds two concepts under one token is a naming bug the sweep itself introduces.
- **A residue lint's FALSE POSITIVE is fixed in the lint, not by renaming the artifact it over-matches.** A substring marker (`"_CANDIDATES = ["`) flags a legitimate prefixed list (`_ENV_CANDIDATES` = host env-file paths, not the repo-resource list the check exists to catch). Word-bound the marker (`\b_CANDIDATES\s*=\s*\[`) and keep the artifact; renaming the variable to dodge the marker hides the check's over-matching and is a probe fix in the wrong layer.
- **A sweep that touches the enforcement chain (plugins/governance-enforcer) commits it, then every LATER commit is blocked by the DOGFOOD deploy-sync gate** ("deployed copy differs from committed repo source") until the enforcer is deployed — the sanctioned unblock is the full `fleet-update.sh`, never a manual copy. Budget the deploy before the next commit, or land the enforcer change separately and deploy it immediately.
- **Commit a large sweep in LOGICAL CHUNKS (source → tests → docs), not one giant commit — the operator's explicit preference.** Per-chunk commits keep each blob reviewable by the close gate and give the operator checkpoints to react to; when a deploy gate blocks one chunk, the rest can still land and the deploy happens once.
- **A host-wide core.hooksPath fires the fleet pre-commit inside pytest temp repos and REFUSES bare/AGENTS.md-less commits — isolate the suite with a session-autouse GIT_CONFIG_GLOBAL fixture.** Point `GIT_CONFIG_GLOBAL` at a minimal config (identity only, no hooksPath) so throwaway repos commit freely; the fleet repo is unaffected (its hooksPath is repo-local), and hook-behaviour tests opt back in per-repo (`core.hooksPath = …/hooks`). This one fixture clears the whole class of temp-repo setup failures that look like a dozen unrelated test bugs.

- **`git fetch origin` is write-class and trips the lock gate — don't pre-check
  for new commits with it.** Opening a full governance cycle just to see whether
  the remote moved is overkill; `git rev-parse origin/main` reads the last
  fetched ref and the pull itself fetches. If you must compare before opening a
  cycle, read the lock-free primitives (`git rev-parse HEAD origin/main`,
  `git status -sb`) and treat that ``behind N`` as "open the cycle and pull", not
  a reason to fetch first.

## Verification

- `git grep -i <term>` returns 0 (tracked tree).
- Token-mangle grep returns 0.
- Tests that assert paths/fixtures pass.
- Deployed copies clean; doctor shows no ❌; push landed on origin.

See `references/legacy-system-to-mycortex-2026-08-21.md` for a worked example:
token list, guard table, every mangle class with its fix, and the
concurrent-push rebase sequence. See `references/package-rename-with-shim.md`
for renaming a python package with a one-release compatibility shim.
