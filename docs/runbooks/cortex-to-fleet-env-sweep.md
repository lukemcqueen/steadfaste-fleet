# Runbook — `CORTEX_*` → `FLEET_*` env-name sweep (P4)

> **Audience:** any agent migrating a consumer of the `CORTEX_*` environment
> variables to the canonical `FLEET_*` names.
> **Status:** in progress since 2026-11-13. Foundation landed (dual-read layer
> `b13ae4ae`, dual-export bridge `6ed58ade`); consumers migrate in small batches.
> **Companion:** `docs/runbooks/fleet-env-migration.md` (the ONE canonical
> `.env` — where values live) · `docs/plans/cortex-to-fleet-rename-plan.md` §5 P4.

## Why this is NOT a find-and-replace

`fleet-update.sh` **provides** `CORTEX_DEPLOY_HOME` (and the whole `CORTEX_*`
family from `.env`) to every script it runs. Renaming only the provider, or
only some consumers, leaves the other side reading an **empty** variable —
measured 2026-11-13: renaming `fleet-update.sh` alone produced **660 doctor
warnings** in one deploy. The migration therefore runs on a **dual-export
bridge**: both spellings resolve at all times while consumers migrate one
batch at a time.

## The safety layer (already deployed — do not rebuild it)

1. **`ops/scripts/fleet_lib/env.py`** — Python dual-read resolver:
   `fleet_env.get("X")` reads `FLEET_X` first, falls back to `CORTEX_X`
   (loud one-time deprecation warning), then the default. Use this for Python
   readers; see `tests/test_fleet_env_dualfallback.py`.
2. **`ops/scripts/lib/fleet-env-compat.sh`** — shell twin: bi-directional
   `CORTEX_* ↔ FLEET_*` mirror at env-load time (`FLEET_` canonical, never
   clobbered). Sourced by `fleet-update.sh` right after the canonical env
   loads; see `tests/test_fleet_env_compat.sh`.
3. **The dual-export bridge** in `fleet-update.sh` (right after the
   `CORTEX_DEPLOY_HOME` default): `FLEET_DEPLOY_HOME` is set from
   `CORTEX_DEPLOY_HOME` and BOTH are exported, so a child script may read
   either side during the migration.

## Which names are safe to rename in a given batch

Classify every `CORTEX_*` in the file **before** editing:

| Class | Examples | Rule |
|---|---|---|
| **Local/derived** | `CORTEX_REPO` (git-derived), `CORTEX_HOME`/`CORTEX_DEPLOY_HOME` when self-defaulted, `CORTEX_OS`, `CORTEX_ENV_FILE`, `CORTEX_UPDATE`, `CORTEX_UPDATE_INNER`, `CORTEX_FORCE_DEPLOY`, `CORTEX_SKIP_NGINX`, `CORTEX_SSL_*`, `CORTEX_NGINX_PORT_PREFIX`, `CORTEX_SCRIPTS` | **Safe now** if the file derives the value in-file (`${CORTEX_X:-default}` or direct assignment) and does not export it |
| **`.env`-sourced secrets** | `CORTEX_BUS_URL`, `CORTEX_BUS_AUTH`, `CORTEX_BASIC_AUTH`, `CORTEX_INBOX_AUTH`, `CORTEX_BUS_TOKEN`, `CORTEX_PYTHON` | **Phase 2 only** — dual-read (`fleet_env.get` / the compat mirror) + `.env` repoint + a round-trip bus test. These are live messaging/interpreter config |
| **External contracts** | `CORTEX_SESSION_KEY/REPO/HARNESS/BRANCH` (set by the Hermes harness when spawning), `HERMES_CORTEX_HOME` | **Do not rename from this repo alone** — the setter is not ours; renaming the reader breaks the handshake |
| **Coupled children** | `fleet-update-mutex.py` (reads `CORTEX_UPDATE_INNER` set by `fleet-update.sh`), `gen-skills-manifest.py` (reads `CORTEX_REPO` with NO default) | Migrate **together with the setter/caller**, in the same batch |

**Coupling map (verified 2026-11-13):** only three production files EXPORT
`CORTEX_*` across a process boundary — `fleet-update.sh` (`CORTEX_DEPLOY_HOME`,
bridged), `ops/scripts/bus/bus-inbox-survey.sh` (`CORTEX_BUS_TOKEN`),
`ops/scripts/install/os-config.sh` (`CORTEX_AGENT_TYPE`, `CORTEX_OS`). Nothing
else crosses; everything else is a local read.

## The batch loop (how to land one batch)

One governance cycle per batch, **small** (3–5 files) so each close is one
`end_change` pass:

1. **Inspect** every `CORTEX_*` line in the candidate files. Confirm each is
   local/self-defaulted (safe class) and NOT in the deferred classes above.
2. **Rename** `CORTEX_X` → `FLEET_X` for the safe names only — including the
   file's own local variable names (`_CORTEX_ENV` → `_FLEET_ENV`). Keep
   `mycortex`, `HERMES_CORTEX_HOME`, and lowercase prose untouched.
3. **Verify** `py_compile` / `bash -n`; then re-grep the file for `\bCORTEX_`
   and report the count (expect only deliberate keeps).
4. **Deploy + doctor**: `bash ops/scripts/fleet-update.sh` → doctor must be
   **0 fail** before push. A new FAIL names the real problem — e.g. a
   root-owned copy (`/usr/local/sbin`) going stale (see below).
5. **Close + push**: `feedback_accept` → `end_change` → `git push origin main`
   (lone push command; doctor FAIL blocks push).

## Sudo boundary (files deployed to root-owned paths)

Files the deploy copies to root-owned paths — e.g.
`ops/install/deploy/nginx/fix-blocked-ips.py` → `/usr/local/sbin/` — cannot be
refreshed without the sudo password; `sudo` NOPASSWD covers only `nginx -t/-s
reload` and a couple of specific paths. Renaming such a file makes the doctor's
root-copy check FAIL (stale deploy). Sequence for these:

1. Rename the file + its deploy script, commit.
2. Ask the operator to run the deploy script (it needs sudo), e.g.
   `sudo bash ops/install/deploy/nginx/deploy-fix-blocked-ips.sh`.
3. Only after the root copy matches again: doctor is green → push.

Never hand-write the root copy and never widen sudoers for this.

## Phase 2 and release 2 (not yet)

- **Phase 2** — `.env`-sourced names: give each reader the dual-read
  (`fleet_env.get` in Python; source `fleet-env-compat.sh` in shell), repoint
  the per-host `.env` to `FLEET_*` values, verify a real bus round-trip, then
  drop the `CORTEX_*` spelling from that reader.
- **Release 2** — once every consumer reads `FLEET_*` and every host's `.env`
  carries `FLEET_*`: remove the compat mirror, the dual-export bridge, and the
  `CORTEX_*` fallback in `fleet_lib/env.py`. Until then, **do not delete the
  `CORTEX_*` side** — a reverted or not-yet-updated host keeps working on it.

## Do not

- Do **not** run a repo-wide `CORTEX_` → `FLEET_` replace. Classify per file.
- Do **not** rename `.env`-sourced secrets, `CORTEX_SESSION_*`, or
  `HERMES_CORTEX_HOME` outside phase 2 / the external contract rule.
- Do **not** delete the `CORTEX_*` fallbacks before release 2.
- Do **not** trust a green repo check alone — the doctor's deployed-tree checks
  are the gate (deploy, then doctor, then push).
