# Renaming an Env-Var Namespace (CORTEX_* → FLEET_* style)

The namespace lives in host files a repo sweep cannot touch (per-host `.env`,
systemd `Environment=`, launchd plists, service manifests, nginx configs), so a
repo-only literal rename silently falls through to defaults — `CORTEX_DEPLOY_HOME`
unset means the deploy targets the wrong home with no error. The rule: **build the
resolution layer FIRST, rename literals second, remove the fallback in a LATER
release.**

## 1. Python resolver (fleet_lib/env.py pattern)

`get(name)` reads `FLEET_<name>` first, then `CORTEX_<name>` with a LOUD one-time
deprecation warning (never silent — a silent fallback is exactly how a
half-migrated host hides), then the default. `FLEET_` wins when both are set (it
is canonical). Provide `get_bool` for env booleans. Keep a `set_deprecation_logging`
switch so hermetic tests can mute the spam.

Hermetic tests must cover: FLEET wins when both set; CORTEX fallback used and the
deprecation line printed; warned ONCE per name; default when neither set; `get_bool`
truthy/falsy; the module imports no transitive dependency from the runtime you are
migrating away from.

## 2. Shell twin (lib/fleet-env-compat.sh pattern)

Source AFTER the canonical env loads (`set -a; source .env; set +a`), then loop
the environment and export the twin spelling: `CORTEX_<name>` → `FLEET_<name>`
(so new readers work on not-yet-repointed hosts) and `FLEET_<name>` →
`CORTEX_<name>` (so legacy readers keep working after a host repoints). Never
clobber a spelling that is explicitly set — FLEET is canonical but both must
survive an operator's explicit value.

Shell traps that bite:
- Capture the FULL var name from `env`: `sed -n 's/^\(CORTEX_[A-Z0-9_][A-Z0-9_]*\)=.*/\1/p'`
  — require ≥1 char in the suffix or a bare `CORTEX_=` collides. The indirect
expansion `val="${!name}"` must receive the FULL name (`CORTEX_X`), so strip the
prefix ONLY when building the twin: `FLEET_${name#CORTEX_}`. A sed that emits the
bare suffix makes `"${!name}"` look up the wrong variable.
- The `while read ... done < <(env | sed ...)` loop runs in the current shell
  (process substitution is a top-level redirection, not a pipe) — exports persist.
- The compat iterates the WHOLE environment: a hermetic test must clean its own
  test-scoped vars up front and use a unique probe prefix.

## 3. Wire + register

Source the compat at every canonical env-load site (the deploy script's `.env`
source and the per-host loaders), and register the resolver + compat in the deploy
map so they reach all hosts. Watch the register-map count doc/test (see the
SKILL.md register-count pitfall) when you add them.

## 4. Converting in-place Python readers (the repo-literal sweep's file side)

Sweeping the repo literals means editing files that did not previously import the
resolver. Three traps:

- **Adding `from fleet_lib.env import get` to a module that never imported it breaks
  the DIRECT-IMPORT contexts that run it (a package's own hermetic tests import via
  the package dir, not the parent that holds `fleet_lib`).** Before the import, either
  confirm the parent dir is already on `sys.path`, or insert it at the top of the
  module exactly like the entry script does:
  `_scripts_dir = Path(__file__).resolve().parents[N]; sys.path.insert(0, str(_scripts_dir))`
  — `N` is the steps up from the module to `ops/scripts`. A ModuleNotFoundError only
  in the test run, green from the CLI, is this trap.

- **Convert isolated single-var reads first; leave multi-var OR-chains for a
  round-trip-verified cycle.** A read like `CORTEX_BUS_AUTH or CORTEX_BASIC_AUTH or
  CORTEX_INBOX_AUTH` is a stack of LEGACY aliases that do not map 1:1 to one canonical
  `FLEET_<name>` — folding it onto the resolver naively picks the wrong layer and
  breaks auth. The dual-read resolver covers one canonical name's fallback; a
  multi-name alias chain is a separate (riskier) rewrite that needs the live
  transport round-tripped to verify.

- **A static type-checker flagging `Optional` on `Path(resolver.get(..))` is noise,
  not a blocker** — the resolver returns the default at runtime when the name is
  unset; the type signature just does not know that. Runtime tests are the verdict.

## 5. Sequencing

1. Resolver + shell compat + hermetic tests for BOTH halves, deployed — the safety
   layer that makes the rest unbreakable.
2. Sweep repo literals to the canonical name — safe now, because legacy hosts
   still resolve via the fallback/mirror.
3. Per-host `.env` repoint (a migration script or documented sed), before the
   fallback has to disappear.
4. Release 2: remove the fallback and the compat, update the doctor/env audit to
   FAIL on the legacy name so it cannot return.

The fallback removal is a separate release — and the ENTIRE namespace may be
operator-deferred; reviving it is its own scope decision (see the deferred-family
pitfall in SKILL.md), not implied by a later "finish the rename" instruction.

## 6b. The universal single-env-file bridge (materialize → convert) — when EVERY runtime boundary reads one canonical env file

The resolver+shell-compat layer above mirrors CORTEX↔FLEET only where it is SOURCED (the deploy script's process). Runtime services do not source it: systemd `EnvironmentFile=` and the cron/bridge runner that sources the `.env` for every job READ THE FILE directly. So a wide literal sweep still broke the runtime (measured twice: 660 doctor warnings) even with the mirror in place — the readers got empty because the file they read carried only the old spelling.

**Survey the runtime env boundaries FIRST** (which units use `EnvironmentFile=`, which runner sources the file for cron jobs, what the deploy script reads). If they all resolve to ONE canonical env file, the universal bridge is THE FILE: make `consolidate-env.sh`-style the migration belong to the file itself —

1. **Materialize (migration phase):** for every `OLD_X=…` line, also emit `NEW_X=…` (same value) into the canonical file. Now every boundary — EnvironmentFile, the runner, the deploy — sees the new spelling, and swapping readers to the new name is safe host-wide, not just inside deploy-script processes. Verify with a live probe: replicate the runner's file-load and assert a `NEW_*` var is present (boolean only; never print secret values).
2. **Convert (release 2 = 'delete the old vars'):** rewrite the same function to CONVERT — every `OLD_X` line becomes `NEW_X` and the OLD spelling is dropped, so a host's file becomes NEW-only on its next deploy, self-migrating peers without a human.

Constraints that keep either mode safe and idempotent:
- **The rewrite must produce EXACTLY ONE line per name (dedupe).** Keeping pre-existing new-name lines AND converting old-name lines duplicates every name (measured: 13 names → 26 lines, stable and invisible to a grep). Drop the old duplicate-generation shape (or dedupe the preservation loop with a seen-list) so output is byte-stable across re-runs.
- **Both spellings present with DIFFERENT values = a data conflict: abort, never pick a winner.** The operator's explicit new-name value is not yours to override; fail with the two values named. FLEET-style canonical precedence applies only to the resolver's runtime lookup, not to silently rewriting a file.
- **The write-mode call must run on the ALREADY-consolidated early-exit path too**, not only after the fresh-consolidation merge — hosts that are up-to-date are exactly the ones that need drift repair on every deploy. The `--check` mode must verify the NEW-only invariant without mutating (FAIL on any surviving old spelling).
- **Keep FLEET-only vars that never had an old sibling** — the converter must not delete a value just because it was never part of the old family.
- **Code-side fallback removal ships with the file conversion** (release 2): delete the resolver's old fallback and the shell mirror's sourcing in the SAME change as the convert mode, so nothing reads the old spelling once the file stops carrying it. External-contract names (injected by a runtime you do not own) stay old-spelled and stay readable directly. **If the operator overrides that deferral and says "fix it", FIRST re-verify the setter is actually still injecting the legacy name** — a claimed external contract goes stale the moment the owning runtime repoints (a session env that now carries `HERMES_SESSION_*` and no `CORTEX_SESSION_*` proves the old names are dead on that host, and a grep of the injector source confirms the setter moved). If the runtime no longer sets the legacy name, the fix is REMOVING the fallback branch entirely (the operator's "no fallbacks" standard) — never a dual-read for a name nothing injects, and never renaming OLD→NEW inside the dual-read (yields `NEW or NEW`). Keep the dual-read shape only when the legacy name demonstrably still crosses the process boundary until the runtime repoints.

**The REVERSE operation — collapsing all dual-reads back to NEW-only once the setter is repointed — has its own regex trap.** A find-replace that matches the dual-read `NEW or OLD` and rewrites `OLD`→`NEW` yields `NEW or NEW` (measured: `os.environ.get("FLEET_X") or os.environ.get("FLEET_X", "")` from a collapse pass that was meant to drop the CORTEX branch) — a duplicated canonical lookup that compiles, passes, and silently turns an OR-of-two-defaults into an OR-of-itself. Collapse by EXACT-replacing the whole legacy branch (drop `or os.environ.get("OLD_X"...)` outright), never by renaming OLD to NEW inside the dual-read, then re-read every site. **Verify the live SETTER is repointed BEFORE removing the fallback:** a clean agent-process env proves nothing about a per-host config or a gateway/unit yaml that still injects the legacy name — grep the config/unit surfaces (the one file that actually set the legacy name into processes is the setter you must repoint), or the removed fallback re-falls to default on the host that still sets the old name.

**Active skills that TEACH the env names are part of the sweep surface, not just code and .env.** Code and the canonical file can be NEW-only while the ACTIVE skill .md files still instruct agents to SET/READ the old names (session-id source tuples, CORTEX_REPO/SESSION_*/OS/PYTHON) — an agent following the skills configures the legacy identifiers, re-seeding the drift you just removed. Sweep the non-archive skill files' old env tokens to the canonical names, then confirm the DEPLOYED copies refreshed: agent skills often live in a fleet-owned skill tree that `~/.hermes/skills` is a SYMLINK to and that the deploy re-syncs checksum-style (skips unchanged), so verify by realpath token-count (`grep -c OLD_ <realpath>`), not by the repo state alone.

**"Fully fix <runtime>" means the per-RUNTIME surfaces, not just the repo + canonical file.** When the operator asks to fully fix a named runtime (Hermes, Pi, a service), the repo and the canonical fleet `.env` being NEW-only is necessary but not sufficient — a residual legacy var can sit in the RUNTIME's OWN env file (`~/.hermes/.env` carried `CORTEX_NGINX_PORT_PREFIX` when every repo reader, the canonical `.env`, and the live gateway were already FLEET), an env file that is neither the repo nor the fleet `.env`. Enumerate and sweep each runtime's surface: (a) its own env/config file (`~/.hermes/.env`, gateway/unit yaml, `mcp.json`, `settings.json`) — the runtime's value, renamed to the canonical name in place; (b) its deployed skill copies, by realpath token-count, not repo state; (c) the external yaml that actually SET the legacy name into processes (the setter to repoint). Then the runtime's own `.` greps to 0 old tokens — that, not the repo count, is the proof the runtime is fully migrated.

**A new-name sweep regex swallows single-letter PLACEHOLDER labels in comments** (`CORTEX_X` in "CORTEX_X ↔ FLEET_X" matches `\bPREFIX_([A-Z][A-Z0-9_]*)` as name `X`) and turns documentation into self-contradiction ("FLEET_X ↔ FLEET_X", "every FLEET_X has a matching FLEET_X") that the close reviewer files as fabrication. Two mitigations: exclude comment lines with the sentinel label from the sweep, or post-sweep grep the corrupted signature (`<new>_X ↔ <new>_X`, `matching <new>_X`) and fix the labels back to `OLD_X ↔ NEW_X` in a comment-only commit.

## 6. Document it so agents implement it correctly — and fix superseded claims

The namespace sweep is a long multi-batch effort, so the RESULT belongs in a
runbook, not only in commit messages. Write
`docs/runbooks/<ns>-sweep.md` and index it in DOCS-INDEX — an agent or the
operator's next session must be able to follow it without re-deriving the
rules. It must carry: why it is NOT find-and-replace (provider/consumer
coupling + the measured failure); the deployed safety layer (resolver, mirror,
the dual-export bridge); the NAME CLASSIFICATION table (local/derived safe now;
`.env` secrets, session/host-injected vars, and external contracts deferred);
the EXPORT MAP (who actually exports a name across a process); the small-batch
loop (inspect -> rename -> syntax -> deploy -> doctor 0-fail -> close -> push);
the sudo/root-owned-copy boundary; phase 2 and release 2.

**A runbook is PROCEDURE, and the procedure changes when the last stage lands — rewrite it then; do not status-bump it.** The safety layer a staged migration describes (dual-read resolver, compat mirror, dual-export bridge) is DELETED by the release that finishes the job, so a runbook still telling agents to source it is worse than no runbook: it re-derives a removed mechanism, and the reader cannot tell from the text that the instruction is obsolete. On the final release, rewrite the file into the verification + CLEANUP procedure — what "converged" means per surface, the per-consumer verify steps (one per harness/runtime), the host self-heal, and the gate — and demote the old narrative to an explicitly labelled *historical* section so the reasoning survives without reading as a current instruction. The signature of a runbook that was never rewritten is a section titled "the safety layer (already deployed — do not rebuild it)" pointing at a file that no longer exists.

The sweep scope must
include every env TEMPLATE/EXAMPLE file (`git ls-files '*.example'
'*.env.example' '*.yaml.example'` — a root `.env.example` outside the deploy
dirs carried 10+ old-name definitions in one fleet) and the ENFORCEMENT/HOOK
layer (deploy scripts, pre-commit/pre-push hooks, and nginx/security templates
both read and COMMENT old env names; hook flag names like `IS_<old>_REPO` /
`_<old>_HOOK_PRESENT` and their env reads rename with the rest, and must stay
in the same batch as their readers). Two further rules:

- **A migration SUPERSEDES the docs' own "kept"/"deferred" claims — patch them in
  the same pass.** The followups/plan/runbook docs that named the family
  "deliberately kept", "not covered", "DEFERRED - spec only", or "keeps its name"
  become FALSE the moment the migration lands, and a future agent reads them as
  instructions to leave the work untouched. Grep those docs for the negative claim
  phrasings (`keeps its name`, `deferred`, `not covered`, `deliberately left`,
  `not renamed`) and rewrite only the rows the migration superseded, dating the
  update, in the same commit as the runbook. Leave genuinely-kept items (the
  detector/migration tools, `mycortex`, external contracts) still listed as keeps.
- **The same applies to STATUS LINES of the migration doc set, and they are what a status query
  reads first.** A plan/runbook header (`Status: PLAN - not started`, `Release 2 - not yet`,
  `IN PROGRESS since <date>`) survives the landing and states the opposite of the truth. Grep
  the migration docs for status headers and `not yet` / `not started` / `in progress` phrasings
  and update them in the RELEASE's own commit - a doc that contradicts the landed state is a
  finding, not context. When asked "where are we", MEASURE rather than read: the sweep commits
  in `git log`, the renamed package/script dirs, the unit names under `~/.config/systemd/user/`,
  the cron names in `jobs.json`, and the old-spelling count in each host's `.env` (names only,
  never values). Scope the claim to the hosts actually measured.
- **After release 2, classify the old-name tokens that remain instead of counting them as one
  number.** The legitimate survivors are: the migration/converter tool itself (it must keep the
  old literal to convert it), comments and docstrings describing the migration, the
  external-contract names a runtime you do not own injects (`CORTEX_SESSION_*`,
  `HERMES_CORTEX_HOME`) — but ONLY WHILE the runtime demonstrably still injects them, so
  re-verify each keep against the live env/setter before the final release and delete the stale
  ones —, tests asserting the legacy behaviour (negative/detector fixtures and fallback probes
  keep the retired spelling ON PURPOSE), and dated records. Report the
  live surfaces separately from the test/record ones - a single repo-wide count mixes deliberate
  keeps with real stragglers and reads as work that is not there. When the operator declares
  "no legitimate <old>, migrate including tests", the keep-list collapses to the converter,
  the detector probes, and dated records — and the sweep must then reach TESTS (patched module
  attrs, env-var exports, planted negative fixtures) and every non-live surface, not just code
  and docs.
- **The measurement that shaped the rules belongs in the doc.** One clause each is
  enough: "provider-only rename -> N doctor warnings (reverted)" tells a future
  reader WHY a whole side of the doc exists instead of reading as ceremony; a rule
  without its why gets re-derived into the same bug.
