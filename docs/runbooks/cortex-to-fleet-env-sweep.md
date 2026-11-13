# Runbook — `CORTEX_*` → `FLEET_*` env-name sweep (COMPLETE)

> **Audience:** any agent verifying or cleaning up a host after the
> `cortex` → `fleet` migration.
> **Status: COMPLETE.** The sweep finished 2026-11-13 — release 2 landed
> (`62630b02`, `0deb362f`): every consumer reads `FLEET_*`, and the whole
> transition layer was removed. This runbook is now the **verification and
> cleanup** procedure, not a migration procedure.
> **Companion:** `docs/runbooks/fleet-env-migration.md` (the ONE canonical
> `.env` — where values live) · `docs/rename-followups-steadfaste-fleet.md`
> (what is deliberately kept) · `docs/plans/cortex-to-fleet-rename-plan.md`
> (the plan, now historical).

## 1. What "converged" means (check these, in this order)

| # | Surface | Converged when | How to check |
|---|---|---|---|
| 1 | Repo tree | no retired spelling in a live instruction surface | `bash tests/test_fleet_cli_brand.sh` → block `B8` is `ok` |
| 2 | Canonical env file | `~/steadfaste-fleet/.env` is `FLEET_`-only | `bash ops/scripts/manage/consolidate-env.sh --check` (exit 0) |
| 3 | Host agent env | `~/.steadfaste-fleet/agent.env` is `FLEET_`-only | `grep -c '^CORTEX_' ~/.steadfaste-fleet/agent.env` → `0` |
| 4 | Deployed tree | doctor clean | `python3 ~/.steadfaste-fleet/scripts/fleet-doctor.py --quiet` → 0 fail |
| 5 | Legacy homes | old paths absent, or symlinks to the new ones | `bash ops/scripts/manage/verify-rename-local.sh` |
| 6 | Harness wiring | each harness's own manifest names the fleet paths | §3 below |

**Only surface 2 self-heals.** The rest are checks you run; when one is red, the
doctor's `fix:` line names the command.

## 2. Historical — why the rename was NOT find-and-replace

Kept because the reasoning governs any *future* env-namespace move, and a rule
without its why gets re-derived into the same bug.

`fleet-update.sh` **provides** the env family to every script it runs. Renaming
only the provider, or only some consumers, leaves the other side reading an
**empty** variable — measured 2026-11-13: renaming `fleet-update.sh` alone
produced **660 doctor warnings** in one deploy. That is why the change ran on a
dual-export bridge for one release, then converted.

The transition layer that made it safe — the Python dual-read in
`fleet_lib/env.py`, the shell mirror `ops/scripts/lib/fleet-env-compat.sh`, and
the `FLEET_DEPLOY_HOME` dual-export bridge in `fleet-update.sh` — was **removed by
release 2**. Do not re-create any of it: if a doc tells you to, that doc is stale
— fix it in the same commit.

## 3. Per-harness verify + cleanup

The env family is `FLEET_*` only (release 2, 2026-11-13): readers, hosts'
`.env`/`agent.env`, harness manifests and this runbook all use the FLEET_
spelling. The Hermes harness injects `FLEET_SESSION_*` session identity and
`FLEET_DEPLOY_HOME` directly — already FLEET-spelled, nothing to convert. There
is NO legacy spelling and NO fallback: any `CORTEX_*` token anywhere is a
regression this runbook's gate exists to catch.

**Hermes**
- MCP args must name fleet paths: `hermes mcp list`, then
  `hermes mcp add fleet-context --command ~/.steadfaste-fleet/venv/bin/python3 --args ~/steadfaste-fleet/mcp-servers/fleet-context-mcp.py`
  (config.yaml is security-sensitive — use `hermes config set`, never a file write).
- `~/.hermes/.env` is Hermes-owned and is **not** the canonical env, but it can
  carry a retired spelling (e.g. the nginx port prefix). Rename that key to its
  `FLEET_` form **in place** — never merge the file into the fleet env.
- `~/.hermes/cron/jobs.json`: no `cortex`-named job. `mycortex` jobs are a
  different product — leave them.

**Pi**
- `~/.pi/agent/mcp.json` — server args point at `…/fleet-context-mcp.py`; the
  governance server registers `from: loop-gov-mcp.py`.
- `~/.pi/agent/settings.json` — extensions are `fleet-context.ts` /
  `fleet-governance.ts`.
- Both are Pi-owned (non-repo): hand-edit, then run `pi mcp list` and one real
  `mem_context` call — a registration you have not exercised is not verified.

**Claude Code**
- `.mcp.json` (project) or `~/.claude.json` (user) — the `fleet-context` server
  args must name the fleet MCP script.
- `~/.hermes/hermes-agent/.claude/settings.local.json` permission `Read(...)`
  rules: a rule naming a `cortex` path grants nothing once the path moved
  (measured empty for the short name on this host — check every host anyway).

**Codex**
- `~/.codex/config.toml` → `[mcp_servers.fleet-context]` args name the fleet
  script.

**steadfaste-tui** — declared `layer: none` (unsupported): nothing to wire,
nothing to clean.

Shared pieces are identical for every harness and are never forked: the CLI
`~/.steadfaste-fleet/scripts/fleet-context.py`, the server
`~/steadfaste-fleet/mcp-servers/fleet-context-mcp.py`, the trigger
`~/.steadfaste-fleet/scripts/session-autocheckpoint.py`, and the single
implementation `ops/services/mycortex-mem/context_tools.py`.

## 4. Cleanup — removing what the migration left behind

The deploy **registers but never prunes**, so a host accumulates leftovers the
repo has moved on from. Remove, in this order:

1. **Legacy home dirs** — `~/hermes-cortex`, `~/.hermes-cortex`,
   `~/.local/lib/hermes-cortex`. Run
   `bash ops/scripts/manage/migrate-home-dirs.sh` (idempotent; refuses to run
   from inside a source dir), then verify with
   `bash ops/scripts/manage/verify-rename-local.sh`. Never run it under an
   in-flight `fleet-update.sh` writing the same dirs.
2. **Stale units** — any `cortex-*` user unit. `systemctl --user disable --now
   <unit>` → `daemon-reload` → drop the `.wants` symlink; keep the old unit file
   on disk, disabled, for one release. launchd: unload, then move the plist out
   of `~/Library/LaunchAgents/`.
3. **Stale deployed scripts** — the doctor's deploy checks name them (`⚠ Stale
   deploy …`). They are deployed-only files whose repo source is gone; delete
   them. Fingerprint: a deployed `.py`/`.sh` whose frozen `# SOURCE:` header
   names a path absent from the current repo.
4. **Root-owned copies** — `/usr/local/{bin,sbin}` copies of renamed scripts
   cannot be refreshed without the sudo password (`sudo` NOPASSWD covers only
   `nginx -t/-s reload` and a couple of specific paths). Ask the operator to run
   the deploy script. Never hand-write the root copy, never widen sudoers for it.
5. **`__pycache__` under a renamed path** — a stale `.pyc` embeds the pre-rename
   absolute path, so a traceback still names the old dir while the source greps
   clean. Delete the `__pycache__` dirs (host-wide, not just in the repo); they
   regenerate on next import.
6. **Stale venv console-script shebangs** — a venv re-created at the new path
   still has `bin/*` wrappers whose `#!` line names the RETIRED interpreter
   (measured 2026-11-13: 12 wrappers on this host died with `bad interpreter`
   at first use — including `pytest`). Repair the shebangs in place; never
   re-create the venv for this:

   ```bash
   cd ~/.steadfaste-fleet/venv/bin
   grep -rl 'hermes-cortex/venv/bin/python3' . \
     | xargs sed -i "s|#!.*hermes-cortex/venv/bin/python3|#!\${HOME}/.steadfaste-fleet/venv/bin/python3|"
   grep -rl 'hermes-cortex/venv/bin/python3' .   # → 0
   ```

## 5. The gate that keeps it converged

`tests/test_fleet_cli_brand.sh` fails if the retired spelling reappears in a live
instruction surface (block `B8`) or if the derived harness docs drift from their
generator (block `B9`).

A deliberate keep must carry the token `STALE` on its own line — that is the
repo's documented keep contract, and the lint judges each line independently, so
keep the token on the same physical line.

Deliberate keeps (and why they are not findings):
`consolidate-env.sh` (the converter must name the spelling it converts);
`FLEET-Session:` trailer (the gate-keyed commit trailer — writer and reader
agree on the token, which is itself FLEET-spelled);
`mycortex` in every form (a different product); dated records under
`docs/older/`, `docs/evidence/`, `docs/reviews/`, `tests/artifacts/` and any
dated filename (a record quotes what WAS there — rewriting it falsifies it);
and the residue detectors' own probe strings (the B8-negative fixture, the
doctor's `^CORTEX_` env check, `fleet-update.sh`'s agent.env strip) — a
detector must name the retired spelling to flag it.

## 6. Symptom → cause → fix

| Symptom | Cause | Fix |
|---|---|---|
| A script sees an empty variable it used to read | a host `.env` still carries the retired spelling | run `consolidate-env.sh` (a deploy does it too), then re-check `--check` |
| Doctor warns about `agent.env` | legacy line the deploy never removed | re-run `fleet-update.sh` — it now rewrites `agent.env` to `FLEET_`-only |
| A doc tells you to source `lib/fleet-env-compat.sh` | you are reading a pre-release-2 doc | that file was deleted by design — fix the doc you are reading |
| A `cortex-*` unit is still running | the unit was never migrated | §4 step 2 |
| An MCP server starts but every tool is empty | the harness manifest still names a `cortex`-path script | §3, then exercise one call |
| A traceback names `~/.hermes-cortex/...` with `??` source lines | stale `__pycache__` | §4 step 5 |
| A venv wrapper dies with `bad interpreter: ...hermes-cortex/...: no such file or directory` | the venv's console scripts still name the retired interpreter | §4 step 6 — rewrite the shebang, then re-grep → 0 |
