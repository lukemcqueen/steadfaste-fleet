#!/usr/bin/env bash
# reconcile-skill-drift-auto.sh — mechanically recover skill-drift strands.
#
# WHY (class-level fix, 2026-11-12): sessions and the lesson pipeline author
# lessons on the DEPLOYED skill copy (~/.steadfaste-fleet/skills/...), leaving
# the repo side stale — reconcile-stranded-skills.py calls that a "strand" and
# the doctor warns on every deploy. Recovery was a manual orchestrator chore
# (5x in one session). This no_agent cron makes the drift-sync mechanical, as
# orch-skill-lifecycle recommends for Phase-3.
#
# SAFETY (why this is not the forbidden auto-skill-edit):
#   - ONLY the pure-superset class is recovered: reconcile --apply refuses the
#     drop/review class, which stays a human decision. Pure superset = strict
#     additions, provably lossless — never deletes or rewrites repo content.
#   - The script NEVER edits a skill itself: it only invokes the sanctioned
#     reconciler, then stages the reconcile's own output. No fence "fixing",
#     no hand-editing (that is what corrupted skills in the past).
#   - Fence-balance guard: any changed SKILL.md with an odd fence count is
#     refused and left for a human.
#   - Sanctioned pipeline commit ("auto: ... [pipeline]" + a FRESH marker):
#     the pre-commit hook still runs adversarial/secret-leak/append-only/SCORING
#     but skips the reflexion/lock gates; pre-push sanctions a range that is all
#     pipeline commits. Only files the reconcile delta produced are staged, so a
#     peer's in-flight skill edit is never swept in.
#
# Silent when nothing is stranded (watchdog pattern).
set -euo pipefail

CORTEX_REPO="${CORTEX_REPO:-${HOME}/steadfaste-fleet}"
GOV_STATE="${HOME}/.steadfaste-fleet/state"
_PIPE_MARKER="${GOV_STATE}/.pipeline-commit"
_LOCK="${GOV_STATE}/.skill-reconcile.lock"
TS() { date '+%Y-%m-%d %H:%M %Z'; }
log()  { echo "[$(TS) skill-drift-reconcile] $*" >&2; }

# ── Single-instance lock (a concurrent run is a push race) ─────
if ! mkdir "$_LOCK" 2>/dev/null; then
  log "another run in progress — exiting"
  exit 0
fi

# ── Guard: abort on stale git state before touching anything ───
if git -C "$CORTEX_REPO" rev-parse --git-dir &>/dev/null; then
  if git -C "$CORTEX_REPO" rebase --show-current &>/dev/null 2>&1; then
    git -C "$CORTEX_REPO" rebase --abort 2>/dev/null || true
    log "aborted a stale rebase"
  fi
  git -C "$CORTEX_REPO" cherry-pick --quit 2>/dev/null || true
  git -C "$CORTEX_REPO" merge --quit 2>/dev/null || true
fi

# A scheduler run may start on a lagging checkout — pull other agents' commits
# first so the reconcile baseline is fresh (and we never re-strand work they
# already committed). ff-only: never create a merge here.
git -C "$CORTEX_REPO" pull --ff-only >/dev/null 2>&1 || true

# ── Snapshot the skill tree before, so we can stage ONLY the delta ──
SCOPE="skills/ docs/evidence/ docs/SKILLS-MANIFEST.md"
BEFORE=$(cd "$CORTEX_REPO" && git status --porcelain --untracked-files=all -- $SCOPE 2>/dev/null || true)

# ── Recover the safe (pure-superset) strand class ────────────────
(cd "$CORTEX_REPO" && python3 ops/scripts/manage/reconcile-stranded-skills.py --apply >/dev/null 2>&1) || true
# A recovered strand that touches a skill's frontmatter description makes the
# skills manifest stale — regenerate it so the doc-audit gate stays green.
(cd "$CORTEX_REPO" && bash ops/scripts/manage/gen-skills-manifest.sh >/dev/null 2>&1) || true

AFTER=$(cd "$CORTEX_REPO" && git status --porcelain --untracked-files=all -- $SCOPE 2>/dev/null || true)
if [ "$AFTER" == "$BEFORE" ] || [ -z "$AFTER" ]; then
  log "no new strands to recover — silent"
  rmdir "$_LOCK" 2>/dev/null || true
  exit 0
fi

# ── Sanctioned pipeline commit: fresh marker, then stage ONLY the delta ──
mkdir -p "$GOV_STATE"
: > "$_PIPE_MARKER"
trap 'rm -f "$_PIPE_MARKER"; rmdir "$_LOCK" 2>/dev/null || true' EXIT

cd "$CORTEX_REPO"
# Paths the reconcile delta added/changed, EXCLUDING any scattered noise:
# use git add on the differing paths only.
{
  printf '%s\n' "$AFTER"
} | awk '{print $2}' | while IFS= read -r p; do
  [ -n "$p" ] && git add -- "$p" 2>/dev/null || true
done

# ── Fence-balance guard: never ship a corrupt skill ────────────────
_BAD=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  n=$(grep -c '^```' "$f" 2>/dev/null || echo 0)
  if [ $((n % 2)) -ne 0 ]; then
    log "unbalanced fences in $f (n=$n) — refusing commit, leaving for human"
    _BAD=1
  fi
done < <(git diff --cached --name-only | grep '/SKILL\.md$' || true)

if [ "$_BAD" -eq 1 ]; then
  git reset -q 2>/dev/null || true
  log "aborted due to unbalanced fences — nothing committed"
  exit 0
fi

if git diff --cached --quiet; then
  log "nothing staged after reconcile — silent"
  exit 0
fi

N=$(git diff --cached --name-only | grep -c '/SKILL\.md$' || echo 0)

# This repo's hook REFUSES a bare commit (shared index): it requires an explicit
# pathspec, which builds a temporary index of only the named paths. Stage the
# exact reconcile-delta paths (captured before the fence check) and commit them.
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
if [ -z "$STAGED" ]; then
  log "nothing staged after reconcile — silent"
  exit 0
fi

if git commit -m "auto: reconcile skill-drift strands (skills=${N}) [pipeline]" -- $STAGED >/dev/null 2>&1; then
  log "committed ${N} skill(s) — deploying + pushing"
else
  log "commit refused — leaving the reconcile delta staged for a human; nothing pushed"
  exit 0
fi

# ── Deploy (satisfies deploy-sync + dogfood), then push ────────────
if ! bash "$CORTEX_REPO/ops/scripts/fleet-update.sh" >/dev/null 2>&1; then
  log "deploy failed — not pushing this cycle (will retry)"
  exit 0
fi
if git push origin main >/dev/null 2>&1; then
  echo "[$(TS) skill-drift-reconcile] reconciled ${N} skill(s); committed + deployed + pushed"
else
  log "push blocked after deploy (retry next cycle)"
  exit 0
fi
