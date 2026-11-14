# Slice 1 Material — docs/skill clarity for public product

Review target: the FIRST governed slice of the operator-directed repo
consolidation (public product, zero PII). This slice is DOCS/SKILL ONLY — no file
moves. It exists to kill the #1 stale reference that confuses agents and requesters.
Subsequent slices (moves, enforcement) are separate reviewed cycles.

## Context (verified, not assumed)

- Repo: `~/steadfaste-fleet` (~360 scripts, 331 docs, 385 skills, 379 tests).
- The operative layout is a LAYER model established by commits P0a `b416ed5c` /
  P0d `0e5be574` and the physical removal of `src/` (`f51df21a`, `7e8a8eac`,
  `05f6ff68`). Today's top-level trees: `ops/ core/ skills/ docs/ tests/ tools/
  plugins/ mcp-servers/ evals/ config/ profiles/`.
- `core/` holds fleet_bus + governance server code; `ops/` holds all scripts
  (358), install (64), services (42); this is the real, working separation.
- The `repo-organization` SKILL (`skills/software-development/repo-organization/SKILL.md`)
  still prescribes an OBSOLETE `src/`-based tree (`src/scripts`, `src/agent-inbox`,
  `src/loop-governance`, `deploy/nginx`). That layout predates P0a/P0d and was never
  updated. It is the single largest source of "agents get confused" and must be
  rewritten to codify the ACTUAL model (its naming/cron/audit sections remain valid).
- Root strays already confirmed: `poll-bus-inbox_titus.sh` (LIVE tool, verified
  reads `~/.steadfaste-fleet/hermes-inbox.conf`, polls `inbox_titus`) — to be REHOMED
  (not deleted) in the moves slice; `evidence-*-20261009.md` ×2 already moved to
  docs/evidence/ (done, commit `cec52ee8`); `CLAUDE.md` (a 6.4KB redundancy of
  AGENTS.md — to become a thin pointer).

## Decisions locked this session (do not relitigate)

| Decision | Resolution | Why |
|---|---|---|
| Rename `core/` → `src/` | REJECTED | `src/` was deliberately retired (P0a/P0d); the SKILL is the stale artifact. Cosmetic churn, no public-product benefit, breaks every reference. |
| Delete `poll-bus-inbox_titus.sh` | REJECTED | Verified live tool. Test-gated: its source reads `hermes-inbox.conf` and curls `$URL/api/pgmq/peek/inbox_titus`. |
| Delete simple `project-map/__init__.py` | REJECTED | Live package marker for a documented tool. |
| Delete repo-root `.env` / `.env.bak*` | REJECTED for `.env` | Repo-root `.env` is the LIVE used env (gateway `EnvironmentFile`, fleet FLEET_* reads; `~/.steadfaste-fleet/.env` symlinks to it). Gitignored, so it never enters the public repo. |
| Target audience | Public product, zero PII | Hostnames / endpoints / `titus` filenames must not ship. |

## Concrete changes in THIS slice

1. Rewrite `skills/software-development/repo-organization/SKILL.md` §2 (directory
   tree) to the real layer model: `ops/` (scripts/services/install/offline/deploy),
   `core/` (fleet_bus + governance), `skills/`, `docs/`, `tests/`, `plugins/`,
   `mcp-servers/`, `tools/`, `evals/`. Retire every `src/` prescription. Keep §3
   naming, cron `agent-*`/`orch-*`, §7 audit checklist, symlink guidance. Change the
   description/frontmatter to reflect it is the actual standard.
2. Add `REPO-MAP.md` at repo root — the single 30-second navigation artifact,
   listing each top-level tree, its purpose, and the anchor files, as at
   2026-11-14. HUMAN-authored purpose + STRUCTURE derived from `git ls-files`.
3. Add `ops/scripts/manage/regenerate-repo-map.sh` — regenerates the structural
   skeleton of REPO-MAP.md from `git ls-files` top-level trees (name + first-segment
   list + counts), so the map cannot silently drift. Read-only / idempotent.
4. Rewrite the AGENTS.md nav section to name the real trees and point at REPO-MAP.md;
   trim AGENTS.md if it exceeds the 20KB budget.
5. `CLAUDE.md` → thin pointer to AGENTS.md (Claude Code auto-loads it; the duplicated
   6.4KB content is trimmed).
6. Update `docs/DOCS-INDEX.md` to describe the layer model + REPO-MAP.md.

## What is NOT in this slice (deferred by design)

- Any file MOVE (core/tools/evals/mcp-servers→ops, poll→ops/scripts, jev-router→
  ops/config, laptop artifact) — that is the moves slice with its own full reference
  sweep + MCP re-registration.
- The doctor `check_repo_map` enforcement check — that is the enforcement slice
  (it must come WITH the moves slice, after the map exists, and FAIL only on real drift).
- The live `src/` reference purge (`core/governance/__init__.py` shim note,
  `score_cycle.py` docstring examples) — part of the moves slice's sweep.

## Constraints / honesty notes

- Evidence store (`docs/evidence/`, 759 files) is NEVER pruned (keep-logs rule).
- Migration/rename tools that must name the old term are deliberate keeps.
- No PII/hostnames in any NEW content; `titus` appears only in already-existing
  dated filenames/records, not in new nav content.
- The repo-organization skill rewrite must NOT lose its valid §3 cron/naming
  rules — only the obsolete §2 tree is replaced.

## Ask

Review this slice for: gaps in the concise navigation story, anything that would
regress the existing valid skill content when §2 is replaced, whether REPO-MAP's
structure-vs-purpose split will actually prevent drift, and whether AGENTS.md/CLAUDE.md
trimming loses load-bearing instructions. Flag UNVERIFIED or fabricated claims.