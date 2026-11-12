# Rename follow-ups: hermes-cortex → steadfaste-fleet (for agents)

The product/repo was renamed `hermes-cortex` → `steadfaste-fleet` (and the brand
`Hermes Cortex` → `Steadfaste Fleet`). A repo-wide sweep renamed 4,648
occurrences across 938 tracked files (docs, scripts, crons, Pi harness, README,
skills bodies, AGENTS/CLAUDE/CONTRIBUTING). A SECOND sweep on 2026-11-12 removed
the remaining active `hc`/`hermes-cortex` brand (env var `HC_AGENT` →
`FLEET_AGENT`, gateway session scheme `hc-<agent>-<chat>` → `fleet-<agent>-<chat>`,
session worktrees `~/hc-session-*` → `~/fleet-session-*`, the four
`hermes-cortex*` skills → `steadfaste-fleet*`, skill aliases `hc-party`/`hc-elicit` (STALE aliases)
→ `fleet-party`/`fleet-elicit`, enforcer sanctioned-update regex now
single-URL, docker-compose `hc-metrics` → `fleet-metrics`).

This file records what was DELIBERATELY left as `hermes-cortex`/`hc` and why, so
a later reader does not "fix" it.

## STALE-path literals that remain (do NOT rename — they are detection/migration)

Each of these keeps the old name ON PURPOSE. They are labeled `STALE` in the
code; the brand lint's `RECORD_FILTER` exempts dated records, not these.

| Literal | Where | Why it stays |
|---|---|---|
| `~/hermes-cortex`, `~/.hermes-cortex`, `~/.local/lib/hermes-cortex` | `ops/scripts/manage/fleet_doctor/stale_premigration_home.py` (+ its test) | the check exists to DETECT these leftovers |
| old→new pairs | `ops/scripts/manage/migrate-home-dirs.sh` | the tool exists to MIGRATE these paths |
| `OLD_REPO` / `OLD_HOME` | `ops/scripts/manage/verify-rename-local.sh` | verifies old paths are symlinks to new |
| legacy repo URL | `ops/scripts/manage/fleet-set-origin.sh` (`LEGACY`) | rewrites a straggler origin carrying it |
| legacy repo URL in the PII gate | `plugins/governance-enforcer/__init__.py` `_PII_REPO_URL_RE` | the old URL redirects forever; the gate must keep recognizing the fleet's own legacy URL or it flags it as a leak |
| `precommit-hc-scope-test` / `precommit-nonhc-test` | `ops/scripts/manage/agent-review-queue-sweep.py` | historical governance-DB cycle names the sweep matches |
| `~/hermes-cortex` in `test_write_class_parity.py` | `tests/` | asserts the pre-migration path is NOT a sanctioned updater (pins the 2026-11-12 removal) |
| `hc-session2` fixture in `test_doctor_dev_repo_detection.py` | `tests/` | historical measurement note for the session-worktree convention |

## Deliberately NOT renamed (do not touch)

1. **Historical/evidence records** — `docs/older/`, `docs/evidence/`, dated
   references (`2026-*-*` lines, session/ledger names, `hc-cli-refactor-session`,
   `gbrain-reference-audit`): dated transcripts and reports quote the old name as
   the thing that WAS at that revision; rewriting falsifies records and their
   checksums. The brand lint's `RECORD_FILTER` excludes exactly these.
2. **Migration tools' functional literals** — see the STALE table above. The
   tools that perform/verify the migration must keep the old name literal.
3. **`hermes-cortex-dashboard.service`** — the dashboard service keeps its name.
4. **`cortex` short-name family** — a SEPARATE naming layer, NOT covered by the
   `hc` sweep: `cortex-bus`, `cortex-dashboard`, `mycortex`, `CORTEX_DEPLOY_HOME`,
   `cortex-deployment-sync` skill, etc. Renaming `cortex` → `fleet` is a SECOND,
   larger sweep with its own runtime-contract risks (service names, env vars, DB
   roles).

## What changed on 2026-11-12 (the final hc sweep)

- Skills renamed: `hermes-cortex` → `steadfaste-fleet`, `hermes-cortex-setup` →
  `steadfaste-fleet-setup`, `hermes-cortex-maintenance` →
  `steadfaste-fleet-maintenance`, `hermes-cortex-repo-deploy` →
  `steadfaste-fleet-repo-deploy`. The old names are no longer load keys; the
  "Stale name — migrated" banners were removed.
- Enforcer `_SANCTIONED_CORTEX_UPDATE_RE` + write_class parity copy: dropped the
  `hermes-cortex` repo-name alternation (single-URL). The PII-URL regex keeps the
  legacy URL (STALE detection literal, see table).
- `HC_AGENT` env var → `FLEET_AGENT` (fleet.py + `.env.example` + tests). Hosts:
  `sed -i 's/^HC_AGENT=/FLEET_AGENT=/' ~/.steadfaste-fleet/.env`.
- Gateway per-chat session ids `hc-<agent>-<chat>` → `fleet-<agent>-<chat>`
  (existing on-disk sessions keep their ids; new sessions use the new prefix).
- Session worktrees `~/hc-session-<tag>` → `~/fleet-session-<tag>`
  (`session-identity.sh` default; existing dirs are STALE leftovers, keep the logs).

## Sequencing requirement (deploy-home default)

The original sweep flipped `CORTEX_DEPLOY_HOME`'s default (`${HOME}/.hermes-cortex`
→ the new local path). A host that pulls this WITHOUT having renamed its local
dirs first would deploy into an empty new home and orphan its real framework.
Order:
1. rename local dirs (migrate-home-dirs.sh, bus EXEC) — symlink keeps old working,
2. THEN pull this swept tree so future deploys land on the renamed home.

## Verify an agent is converged

`git grep -i hermes-cortex` == 0 in tracked in-scope files (the STALE literals
above + historical records excluded), and `git grep -i "hermes cortex"` == 0
outside historical dirs. Run `bash tests/test_fleet_cli_brand.sh` (expect 40+ ok,
0 failed) and the doctor's `Stale pre-migration home folders` check.
