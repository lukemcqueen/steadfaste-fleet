---
name: hermes-home-cleanup
description: "Use when cleaning ~/.hermes or ~/.steadfaste-fleet. Verify."
version: 1.0.0
category: devops
author: Esther
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [cleanup, maintenance, hermes, stale-files, disk-space]
    related_skills: [hermes-backup, steadfaste-fleet-maintenance, repo-organization, execute-right]
---
<!-- Backport provenance (deployed -> repo): the DEPLOYED copy of this file was the
     newer side — a SUPERSET carrying lessons the repo lacked (verification step 5, the
     bind-mount trap section, four pitfalls) — so the reconciler's convergence direction
     is deployed -> repo. The two lines it reported as would-be "drops" are superseded IN
     PLACE, not lost: "Zero references in ALL FOUR" became "ALL FIVE" once the docker
     mount check was added as step 5, and the fleet-update.sh pitfall was reflowed to
     backtick the script name. No repo-only line is dropped. Everything below this note
     was NOT authored in the backporting cycle; it is the deployed lesson copied verbatim. -->

# Hermes Home Cleanup — Safe Stale-File Removal

Use when asked to "clean up" `~/.hermes` or `~/.steadfaste-fleet`, reclaim disk,
or audit what's safe to delete on a Hermes Agent server.

## Golden Rule

**`~/.hermes/` is Hermes Agent territory.** Most of it is live agent
machinery. Before labeling anything stale, verify against the **agent source**
(`~/.hermes/hermes-agent/`), not just fleet scripts. Two false "stale"
claims cost real time: `state-snapshots/` and `~/.hermes/hooks/` are both
agent-owned. Grep the agent repo before you rm.

## Verification Chain (run BEFORE deleting anything)

```bash
# 1. Is it referenced by the agent source?
grep -rn '<name>' ~/.hermes/hermes-agent/ 2>/dev/null | grep -v node_modules | grep -v venv | grep -v '\.git/'
# 2. Is it referenced by fleet scripts (repo + deployed)?
grep -rn '<name>' ~/steadfaste-fleet/ ~/.steadfaste-fleet/scripts/ ~/.hermes/plugins/ 2>/dev/null | grep -v '\.git/'
# 3. Is it a live cron's state file? (renamed crons keep old state filenames!)
python3 -c "import json;d=json.load(open('$HOME/.hermes/cron/jobs.json'));jobs=d.get('jobs',d) if isinstance(d,dict) else d;print([j.get('name') for j in jobs if isinstance(j,dict)])"
# 4. Is it a symlink target or consumed by install scripts?
grep -rn '<name>' ~/.steadfaste-fleet/scripts/install*.sh ~/steadfaste-fleet/ops/scripts/install*.sh 2>/dev/null
# 5. Is anything still MOUNTED from it? A Docker bind mount survives the rename
#    that made the path "stale", so a stale PATH can still be a LIVE data directory.
docker ps --format '{{.Names}}' | while read -r c; do
  docker inspect "$c" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
done | grep -F '<path>'
# Also: systemd unit files, crontab -l, and /etc/nginx can hold the same reference.
```

**Zero references in ALL FIVE = candidate. One reference = LIVE. Never guess.**

### A sweep scoped by FILE TYPE is not a consumer sweep

The chain above answers "does anything reference this name" only as widely as you
grep. `grep -rn <name> --include='*.sh' --include='*.py'` is a FOUR-EXTENSION
question, and reporting its result as "no consumer exists" is an overstatement — for
an IRREVERSIBLE delete that overstatement is the entire risk. Enumerate the surfaces
and STATE the enumeration in the report:

```bash
# a. EVERY file under $HOME, no --include filter, skipping noise dirs
#    (expect HUNDREDS of hits — classify them, do not read them as consumers)
grep -rIl '<name>' ~ --exclude-dir=.git --exclude-dir=node_modules \
  --exclude-dir=venv --exclude-dir=__pycache__ --exclude-dir=.cache
# b. systemd, both scopes
grep -rI -l '<name>' ~/.config/systemd/user/ /etc/systemd/system/
# c. the user crontab
crontab -l | grep -c '<name>'
# d. every RUNNING container's environment
for c in $(docker ps --format '{{.Names}}'); do
  docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c '<name>'
done
# e. nginx (sites-available + sites-enabled + conf.d)
grep -rI -l '<name>' /etc/nginx/
```

Classify every hit instead of counting them: a match in `docs/evidence/`, an
archive, a log, a session dump, a **comment line inside a config**, an UNINSTALLED
template, or `state/deploy-backups/` is **not a reader**. Only a code path that
LOADS the name is. The wide sweep is what turns a plausible claim into a measured
one — and the surfaces it adds (systemd, crontab, container env, nginx, dotfiles,
non-standard extensions) are exactly the ones a `--include` filter hides.

### The bind-mount trap (a stale path with LIVE data)

A "stale path" in a doctor or tool report means *nothing references that NAME any
more* — it does not mean the bytes are dead. A container started earlier keeps its
bind mount working after the source directory is renamed, so deleting a
pre-migration home directory can destroy a **running service's database**. Measured:
a pre-migration home holding `victoria-data/` (a root-owned VictoriaMetrics TSDB) was
bind-mounted **RW** into a live container and written to *days after* the path went
stale; the tool's own suggested `rm -rf` would have destroyed the metrics history.

Order of operations for this shape:

1. Run verification step 5 above. Any mount line = LIVE, so stop and migrate; never delete.
2. `migrate-home-dirs.sh` **cannot** handle it when the target already exists — it
   refuses with `target <t> already exists as a real dir; refusing to nest`. That
   refusal is correct: it means the whole-dir rename does not apply, so the LIVE
   subtree needs a targeted move instead.
3. Migrate the subtree to the canonical new path, repoint the consumer, verify it
   still serves, and only then remove the stale dir. See `docker-management` →
   "Migrating a live bind-mount off a stale path" for the reversible recipe.

**The migration script must resolve the INVOKING user's home — never bare `$HOME`.** This
class of script is documented to run as `sudo bash <script>`, and sudo's default
`env_reset` sets `HOME` to ROOT's home, so every default derived from `$HOME` resolves
under root's home and the path/mount assertion refuses with
`expected '<root-home>/...'`. Resolve it as `${SUDO_USER:-$(id -un)}` → `getent passwd`
(Linux) / `dscl` (macOS) → `$HOME` only as a last resort, and keep per-path env overrides
(`<PREFIX>_OLD_DIR` / `_NEW_DIR`) so an operator can correct a wrong guess without editing
the script. Never "fix" such a refusal by relaxing the comparison: the assertion is what
turned a move **from the wrong directory** into a clean error instead of an incident.

**Reproduce a sudo-only failure with NO root and NO password.** The mechanism is just
`$HOME`, so an unprivileged run with a foreign HOME exercises the identical code path —
which makes the RED→GREEN evidence re-runnable by any reader, including on a host where
the agent has no sudo at all:

```bash
HOME=/tmp/not-a-home bash <script>                     # pre-fix: the refusal above, rc=1
SUDO_USER=<user> HOME=/tmp/not-a-home bash <script>    # the real sudo shape
```

## Credential sprawl — `.env.*` backups in the deploy root

Every ad-hoc edit of the deploy-root `.env` conventionally leaves a `.env.bak-*`
copy behind, and each copy is a COMPLETE plaintext duplicate of the entire secret
set. Nothing collects them, so they accumulate without bound — six were found
beside one live `.env`: seven independent exposure paths for one credential set,
six of which carried no information the seventh did not.

**The retention rule: ONE recovery point — the newest.** That newest copy is what
the deploy runbook names as the repair source for a corrupted `.env`; a second copy
adds exposure and no capability. The doctor enforces this as a check named
`Env backup sprawl` (KEEP_MAX=1): it names the copies and hands over a remedy, and
deliberately **never deletes** — pruning is an owner decision, not a checker's.

Prove nothing is lost before pruning the extras — compare **key NAMES, never values**:

```bash
sed -n 's/^\([A-Za-z0-9_]*\)=.*/\1/p' <file> | sort -u   # names only; no value is read
diff <(sed -n ... '<live .env>' | sort -u) <(sed -n ... '<backup>' | sort -u)
```

- Names unique to a backup are the only thing a copy can hold that the live file
  lacks. **No unique names = the copy is redundant by construction.**
- A unique name still needs a reader check (see the sweep above). A RETIRED
  spelling usually has no reader at all, and its values then already sit in the
  live file under the canonical name.
- Record `sha256sum` of each deleted copy in the evidence file. A hash is not
  reversible, so it is safe to commit and gives a later reader an audit trail; the
  bytes themselves are gone and cannot be re-measured afterwards, so say plainly
  which claims survive ("no reader exists for the 13 names") and which do not
  ("byte-equality with the deleted values") rather than implying both.

**A backup is never a reader.** Code reads `.env`; nothing reads `.env.bak-*` — the
only glob of that shape in the whole tree is a recovery one-liner a HUMAN types. So
deleting backup copies cannot break a running service whatever names they contain,
and the reader sweep is a question about the NAME, not about the copies.

**Verify the live file survived:** compare mode, byte size and distinct key count
before and after (`stat -c '%a %s' .env` plus the key count above). A cleanup that
quietly rewrites the live `.env` is a far worse mishap than the sprawl it cured.

## LIVE — Do NOT Touch (agent-owned)

| Path | Why live |
|------|----------|
| `~/.hermes/hermes-agent/` | The running agent source + venv + node_modules (CLI at `~/.local/bin/hermes` runs from here) |
| `~/.hermes/state.db` (+ `-wal`/`-shm`) | Live session DB, written every turn. Never delete; checkpoint before copy (`PRAGMA wal_checkpoint(TRUNCATE)`) |
| `~/.hermes/state-snapshots/` | **Agent's own pre-update backup.** `hermes update` creates `*-pre-update/`; restore with `hermes backup restore --state pre-update` or `/snapshot restore`. Config: `updates.pre_update_backup` (quick/full/off), `backup_keep` |
| `~/.hermes/hooks/` | **Agent event-hook directory** (`gateway/hooks.py` scans for `HOOK.yaml` + `handler.py` dirs; web UI `/api/ops/hooks`). The DIRECTORY must stay. Plain files inside are ignored by the agent but may be pre-migration copies — verify, don't assume |
| `~/.hermes/cron/` (jobs.json, executions.db, ticker_*) | Live scheduler state |
| `~/.hermes/gateway.*`, `~/.hermes/processes.json`, `auth.json`, `.env` | Live runtime / secrets |
| `~/.hermes/session.id` | **REMOVED vestige (2026-11-12)** — write-only cache since the 2026-09-29 non-Hermes session-id scoping fix; no readers anywhere. Safe to delete; nothing recreates it |
| `~/.hermes/state/bus-audit-watchdog.json` + `.state` | **LIVE** — the renamed `orch-bus-audit-watchdog` cron uses these EXACT filenames. A renamed cron keeps its old state files |
| `~/.hermes/state/worker-pending/`, `worker-completed/` | Live `agent-worker.py` machinery (`is_completed()` reads `.done` files; deleting can re-trigger old steps) |
| `~/.steadfaste-fleet/a2a/` | **DEPRECATED dir, zero consumers** — A2A service merged into fleet-bus (`src/a2a/` → `ops/services/a2a/` → fleet-bus). Bus server serves a HARDCODED card at `/.well-known/agent-card.json`; deployed card is `bus/agent-card.json` (register_orch in fleet-update.sh). `agent-card.json` there was last written 2026-07-27 by a one-off `generate-agent-card.py --output` run — no cron, script, or nginx route reads it. Safe to delete both files (keep the empty dir or remove it; nothing recreates it) |
| `~/.steadfaste-fleet/state/_cron_find.py` | LIVE — `install-crons.sh` + `install-orch-crons.sh` call it |
| `~/.steadfaste-fleet/data/loop-governance.db`, `session-embeddings.db` | Live governance/scoring DBs |
| `~/.hermes/bin/uv`, `uvx`, `tirith` | Python toolchain + security scanner (tirith referenced in `config.yaml` `security.tirith_*`) |
| `~/.hermes/node/`, `~/.hermes/lsp/` | Runtimes for desktop/LSP features |
| `~/.hermes/agent-profiles/` | SOUL.md profile templates (legacy) — `soul-merge.py`/`soul-sync-all.sh` that read them were removed 2026-11-04; `pre-commit-score` no longer syncs SOUL |
| `~/.hermes/AGENTS.md.local`, any `local-*` | Preserved by rule (fleet-update never deletes) |
| `~/.steadfaste-fleet/state/deploy-backups/`, `scripts-unique-backup-*` | Recent deploy rollback safety nets |

## Truly Stale — Safe to Delete

> **Log policy (Luke, 2026-08-03): keep logs unless actually out of disk space.**
> Rotated log archives are NOT routine cleanup targets — only remove them when
> `df -h` shows real pressure (e.g. < 10% free). Same for any log under
> `~/.hermes/logs/` or `~/.steadfaste-fleet/logs/`.

| Path | Evidence | Saves |
|------|----------|-------|
| `~/.steadfaste-fleet/state/loop-governance.db` (0 bytes) | All scripts reference `data/loop-governance.db`, none reference `state/` copy. **Do NOT touch `data/loop-governance.db` — that is the live governance DB.** | 0 bytes (dead file) |
| `~/.hermes/hermes-agent/__pycache__/`, `~/.steadfaste-fleet/{scripts,offline}/__pycache__/` | Regenerable bytecode (Python rebuilds on import) | ~2.5 MB, 800+ dirs |
| `~/.steadfaste-fleet/a2a/task-state.db` | Zero references in agent source or fleet scripts (agent-card.json in same dir IS live) | 20 KB |
| `~/.hermes/state/post-commit-notify` + `.log` | Pre-migration leftovers: old hook wrote to `~/.hermes/state/`, current script writes to `~/.steadfaste-fleet/state/` | 3.5 KB |
| `~/.hermes/cron/output/<hash>/` subdirs older than 30 days | Scheduler run artifacts; doctor does NOT read cron/output. **Keep loose named deliverables** (e.g. `sustainability-briefing-*.md/.docx`) | several MB, 1000s of files |

## Verify-First (do NOT delete without owner OK)

| Path | Why |
|------|-----|
| `~/.hermes/hooks/post-commit` + `pre-commit` | Pre-migration copies of fleet git hooks (migration-2026-07-08 moved hooks to `~/.steadfaste-fleet/hooks/`). Agent ignores them (not hook dirs); git uses hooksPath → `~/.steadfaste-fleet/hooks`. But the DIRECTORY is agent infrastructure and files may be referenced manually — confirm with owner |
| `~/.hermes/state/worker-completed/*.done` | Live machinery reads them; old entries look stale but deleting can re-trigger steps in `agent-worker.py` |
| `~/.hermes/config.yaml.bak.*` | Config backup — "never destroy what you cannot restore." 76 KB, trivial; keep unless disk-critical |
| `post-commit-notify.sh` wiring | Deployed (registered in fleet-update.sh) but NOT the active post-commit hook (that's `post-commit-audit` via symlink). State files are stale, but the script may be intended to be wired — flag, don't delete |
| `~/.steadfaste-fleet/state/.hermes-session-*.id` | Auto-managed: `purge-stale-governance-locks.py` Phase 2 removes markers older than 24 h. Run that script, don't hand-delete |
| `~/.hermes/models_dev_cache.json` | Regenerable cache BUT `hermes-backup` skill copies it — part of backup surface. Tiny (3 MB); optional |

## Vestigial Profiles — `~/.hermes/profiles/<name>/`

A named profile is a whole second Hermes home (its own `.env`, `config.yaml`,
`SOUL.md`, skills, memories, sessions, cron), so it appears in NONE of the grep
tables above. Its evidence is STRUCTURAL, and the question is not "who references
it" but "did it ever serve a turn":

| Evidence | What it means |
|---|---|
| no `config.yaml`; `memories/` and `sessions/` holding no files; no `cron/jobs.json`; a stock `SOUL.md` rather than the agent's edited identity | created and never configured — no turn ever ran there |
| zero hits in `~/.config/systemd/user/`, `~/.hermes/cron/jobs.json`, `~/steadfaste-fleet` | nothing serves it — not a unit, not a cron, not a deploy step |
| a file whose mtime PREDATES the directory containing it | the home was populated by an mtime-preserving copy, not written in place (`--clone` copies `config.yaml`, `.env`, `SOUL.md`, `skills/`, `memories/MEMORY.md`, `memories/USER.md`) |

- **A copied profile carries a copy of `.env`, token included — that is not inert.**
  A duplicate platform credential blocks the host gateway from multiplexing
  (`hermes-gateway-operations` → the standalone-only section). A dead profile is
  therefore never merely dead weight: decide it — delete, or strip the shared
  credential — rather than leaving it in place.
- **`onboarding.seen.profile_build_offered` is NOT evidence a profile was built.**
  That flag records the offer to build a USER MEMORY profile; it has nothing to do
  with a Hermes profile directory.
- **Deletion is the owner's call.** Present the structural evidence plus the fix
  options; never `rm -rf` a profile directory on your own judgement.

### Deleting one (exercised 2026-10-14)

```bash
# 1. Prove nothing is lost — compare key NAMES only, then the shared credential's hash:
diff <(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' ~/.hermes/.env | sort -u) \
     <(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' ~/.hermes/profiles/<name>/.env | sort -u)
grep '^TELEGRAM_BOT_TOKEN' <each file> | tail -1 | sha256sum    # same digest on both sides

# 2. Delete (owner-approved), then confirm:
hermes profile delete -y <name>
hermes profile list        # the duplicate-credential warning must be gone
```

- A right-only diff (every line prefixed `<`) means the copied `.env` is a strict
  SUBSET — no key is unique to the profile, so no secret is destroyed. The hash line
  proves the shared credential is byte-identical without ever printing it.
- `hermes profile delete -y <name>` prints the skill count it is about to destroy, then
  removes the tree; a `.deleted/` tombstone directory stays under `profiles/`, and the
  profile is never enumerated again.

## Procedure

1. **Survey** — `du -sh ~/.hermes/* ~/.steadfaste-fleet/* | sort -rh`; identify top consumers
2. **Verify chain** — for each candidate, run the 5-step grep above
3. **Classify** — LIVE (skip) / STALE (delete) / VERIFY-FIRST (flag to owner)
4. **Delete stale only**, from the table above. Never `rm -rf` a LIVE path
5. **Re-verify** — `du -sh` before/after; `hermes doctor` / `fleet-doctor.py --quiet` still clean
6. **Report** — table of what was removed, evidence, space reclaimed
7. **Share** — push any new knowledge (new live/stale entries) back into this skill

## Pitfalls (learned the hard way)

- **Renamed crons keep old state filenames.** `bus-audit-watchdog` → `orch-bus-audit-watchdog` but the script still writes `~/.hermes/state/bus-audit-watchdog.*`. Grep the SCRIPT, not the cron name.
- **`state-snapshots/` is not orphaned** — it's the agent's pre-update rollback. No fleet script references it; the AGENT does.
- **`~/.hermes/hooks/` ≠ git hooks** — it's the agent's event-hook dir. Git hooks live under `~/.steadfaste-fleet/hooks/` (core.hooksPath).
- **Grep the agent source, not just fleet** — the false-stale items all had agent-side consumers.
- **Never delete while the gateway is writing** — `state.db` needs WAL checkpoint first if copied; never deleted.
- **A profile directory shows up in none of the four greps — and still blocks the host.**
  Verify it STRUCTURALLY (missing `config.yaml`, file-less `memories/`+`sessions/`, no
  `cron/jobs.json`, zero unit/cron/repo references), because "zero references" alone reads
  as a green light while its copied `.env` keeps the gateway standalone.
- **`hermes gateway migrate --multiplex` is a NO-OP that does NOT restart on a
  one-profile host.** It exits 0 with "Only one profile exists: nothing to multiplex" and
  leaves `MainPID` unchanged — so the STALE boot-time `multiplex_standalone_reason` in
  `gateway_state.json` keeps naming the deleted profile, and the dashboard keeps showing
  it. Clear it with `systemctl --user restart hermes-gateway.service`, then verify the
  restart LANDED (`systemctl --user show <unit> -p MainPID -p ExecMainStartTimestamp` —
  both must change). The new boot log then reads `Cron scheduler will tick 1 profile(s):
  ['default']` where it previously said 2.
- **A stranded deployed skill needs its content COMMITTED before a deploy can move it.**
  `check-skill-drift-parity.py` calls a file stranded when its deployed content appears
  in no committed revision; copying the deployed content into the repo does NOT clear it
  while you also add to it in the same commit, because no revision then holds the
  deployed bytes verbatim. Land the deployed content as its own commit first, then add
  your lesson on top. Use `ops/scripts/manage/reconcile-stranded-skills.py` (reports by
  default, `--apply` copies only the pure-superset class) rather than copying by hand.
- **`fleet-update.sh` purges governance locks mid-run** — if you run it during a cleanup session, re-acquire `begin_change` before continuing.
- **A tool's `rm -rf` suggestion is a hypothesis, not a clearance.** It was computed
  from path NAME references and knows nothing about open mounts, running processes, or
  service-owned data. Re-run the five-step chain yourself before acting on it.
- **A checker must never print an unconditionally destructive remedy — the other side of
  the same handoff.** When you author or repair a doctor/scan check whose branch says
  "remove this", the check's OWN premise ("stale = dead weight") can be false for the exact
  path it names, and an operator will trust the command *because a doctor printed it* — a
  checker that emits a destructive command launders data loss as maintenance. State the
  PRECONDITION to establish first, name the purpose-built tool for the live case, and offer
  plain removal only for a path confirmed inert. Guard the wording with a test asserting
  the bare destructive form is ABSENT, plus a positive control that the safe tool IS named
  so the test cannot pass by the advice merely going empty; prove it discriminates by
  driving the same assertion at the pre-fix module read out of git.
- **Do not work around a restricted access boundary.** When the target is owned by
  another user and non-interactive privileged access is limited to a short allowlist,
  the delete needs the operator: hand over the exact command, or ask for a rule scoped
  to the single script. Reaching the write through some other root-capable path is
  circumventing a deliberate access control, not resourcefulness. And silence is not
  consent — an unanswered request leaves the destructive step UNDONE.
- **Never dump raw container-inspect output into a repo-tracked path.** It prints the
  container's full command line, and container args routinely carry credentials. Redact
  first, then verify the redaction landed before staging.

## Related

- `hermes-backup` — full backup procedure (use BEFORE aggressive cleanup)
- `steadfaste-fleet-maintenance` — update workflow
- `repo-organization` — canonical directory structure
