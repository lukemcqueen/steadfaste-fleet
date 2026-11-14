# Slice 1 Material — docs/skill clarity for public product (REVISED after pro review)

> Findings ADV-0-1..4 from the pro review (cycle 14738) are folded below; each
> is resolved by a scope change, then re-reviewed to CLEAN.

Review target: the FIRST governed slice of the operator-directed repo
consolidation (public product, zero PII). This slice is DOCS/SKILL ONLY — no
file moves, no new executable code, no REPO-MAP.md. It exists to kill the #1
stale reference (the repo-organization skill's obsolete `src/` tree) and make
navigation honest. Moves, REPO-MAP generation and its enforcement check are
separate later slices.

## Revised scope (this slice ONLY)

> This commit (`bf7ec695`) delivers item 1 (the skill rewrite) + its evidence.
> Items 2–4 (AGENTS.md nav, CLAUDE.md pointer, DOCS-INDEX) are the slice-1
> FOLLOW-UP and are NOT in `bf7ec695`; they will land in the next slice-1 commit.

1. Rewrite `skills/software-development/repo-organization/SKILL.md` §2
   (directory tree) to the ACTUAL CURRENT layout as measured this session:
   `ops/` (scripts/services/install/offline/deploy), `core/`
   (fleet_bus+governance), `skills/`, `docs/`, `tests/`, `plugins/`,
   `mcp-servers/`, `tools/`, `evals/`, and `config/` + `profiles/` (near-empty;
   flagged in the §2 tree as consolidation candidates that fold into `ops/` in
   the moves slice — the skill must describe the TREE AS IT IS, not the target,
   and say so). Retire every `src/` prescription. KEEP the still-valid §3 naming
   rules (kebab-case, cron `agent-*`/`orch-*`), §7 audit checklist, and symlink
   guidance — only the obsolete §2 tree is replaced. The skill's frontmatter
   description is updated to call it the codified actual standard.
2. Rewrite the AGENTS.md navigation section to name the real top-level trees and
   point at where each class of artifact lives; trim AGENTS.md below ~20KB if it
   exceeds it (today ~24.8KB).
3. `CLAUDE.md` → thin pointer to AGENTS.md (Claude Code auto-loads it); the
   ~6.4KB duplicated body is trimmed to a pointer + one-line purpose.
4. Update `docs/DOCS-INDEX.md` so its top-level section describes the actual
   layer model.

## Explicitly DEFERRED from this slice (pro-review resolutions)

- REPO-MAP.md + `regenerate-repo-map.sh` — DEFERRED to the moves/enforcement
  slice, for TWO reasons: (a) generating the map now would run `git ls-files`
  over a root that still contains `poll-bus-inbox_titus.sh` (a `titus` hostname
  in a non-dated filename) and print it into the map — a PII leak (ADV-0-1);
  (b) adding the generator script is out of a docs/skill-only scope (ADV-0-2).
  The moves slice rehomes the stray to `ops/scripts/`, then the map generator
  runs over a clean tree and its doctor `check_repo_map` enforcement lands in
  the same slice.
- Live `src/` reference purge (`core/governance/__init__.py` shim note,
  `score_cycle.py` docstring examples) and all file moves (`core/tools/evals/
  mcp-servers/config -> ops`, `poll -> ops/scripts/`) — the moves slice.

## Evidence pointers (ADV-0-3 resolution)

Each loaded claim is grounded to git history or an in-repo source:
- Layer model + `src/` retirement commits: `git log --oneline --diff-filter=D --
  'src/*'` -> `f51df21a`, `7e8a8eac`, `05f6ff68`; P0a `b416ed5c` / P0d `0e5be574`.
- Current top-level trees + no REPO-MAP.md: `ls` of repo root (measured this
  session, 2026-11-14: `core/ tools/ evals/ mcp-servers/ config/ profiles/`
  all still top-level). Prior survey: docs/design/repo-reorganization-survey.md
  (commit `9cb85f27`).
- repo-organization skill currently prescribes `src/` tree: read
  `skills/software-development/repo-organization/SKILL.md` §2 (still lists
  `src/scripts`, `src/agent-inbox`, `src/loop-governance`, `deploy/nginx`).
- CLAUDE.md ~6.4KB + AGENTS.md ~24.8KB: `ls -la` of repo root (measured).
- `poll-bus-inbox_titus.sh` is a LIVE tool (not deleted): its source reads
  `~/.steadfaste-fleet/hermes-inbox.conf` and curls
  `$URL/api/pgmq/peek/inbox_titus` — verified reading the file this session;
  the 2026-11-06 repo-consolidation-proposal says "moves to ops/scripts/".
- Scripts count (~362) and doctor-check test gap measurement are recorded in my
  session survey and are NOT load-bearing for this docs-only slice.

## Constraints / honesty notes

- docs/evidence/ (759 files) is NEVER pruned (keep-logs rule).
- Migration/rename tools that must name the old term are deliberate keeps.
- No PII/hostnames in any NEW content. No `titus`/`esther`/`moses`/`joseph`
  appears in new nav prose or the skill rewrite.
- The repo-organization skill rewrite MUST NOT lose the valid §3 cron/naming
  rules or §7 audit checklist — only the obsolete §2 tree is replaced.
- This slice changes NO file locations and adds NO executable code.

## Build state (re-review basis)

Slice 1 executed and COMMITTED as `bf7ec695` (pushed to origin/main, dogfood
PASSED rc0). The repo-organization skill §2 tree is rewritten to the measured
layout, §4 `src/` reference fixed, and the load-bearing repo-state evidence
(top-level `git ls-files` counts + the skill diff) is committed at
`docs/evidence/repo-layout-2026-11-14.txt`. Deferred items remain explicitly
deferred (REPO-MAP + generator → moves/enforcement slice; AGENTS.md nav +
CLAUDE.md pointer + DOCS-INDEX → slice-1 follow-up).

## Ask

Re-review for: any regression risk when §2 is replaced but §3/§7 are kept; whether
AGENTS.md/CLAUDE.md trimming loses load-bearing instruction; whether the deferred
(non-)scope is now consistent with "no PII in generated nav". Flag UNVERIFIED or
fabricated claims.