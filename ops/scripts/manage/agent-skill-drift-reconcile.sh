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

FLEET_REPO="${FLEET_REPO:-${HOME}/steadfaste-fleet}"
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
if git -C "$FLEET_REPO" rev-parse --git-dir &>/dev/null; then
  if git -C "$FLEET_REPO" rebase --show-current &>/dev/null 2>&1; then
    _ABORT_RC=0
    git -C "$FLEET_REPO" rebase --abort 2>/dev/null || _ABORT_RC=$?
    if [ "$_ABORT_RC" -ne 0 ]; then
      log "could not abort a stale rebase (rc=$_ABORT_RC) — lifting the lock and stopping; the repo needs a human"
      rm -f "$_PIPE_MARKER" 2>/dev/null || true
      rmdir "$_LOCK" 2>/dev/null || true
      exit 1
    fi
    log "aborted a stale rebase"
  fi
  _CP_QUIT_RC=0
  git -C "$FLEET_REPO" cherry-pick --quit 2>/dev/null || _CP_QUIT_RC=$?
  [ "$_CP_QUIT_RC" -eq 0 ] || log "cherry-pick --quit failed (rc=$_CP_QUIT_RC)"
  _MG_QUIT_RC=0
  git -C "$FLEET_REPO" merge --quit 2>/dev/null || _MG_QUIT_RC=$?
  [ "$_MG_QUIT_RC" -eq 0 ] || log "merge --quit failed (rc=$_MG_QUIT_RC)"
fi

# A scheduler run may start on a lagging checkout — pull other agents' commits
# first so the reconcile baseline is fresh (and we never re-strand work they
# already committed). ff-only: never create a merge here. A failed pull is
# surfaced with the real rc (a stale baseline could re-strand a peer's committed
# work); the cycle still proceeds so a transient network blip retries next run.
_PULL_RC=0
git -C "$FLEET_REPO" pull --ff-only >/dev/null 2>&1 || _PULL_RC=$?
if [ "$_PULL_RC" -ne 0 ]; then
  log "git pull --ff-only failed (rc=$_PULL_RC) — proceeding on the local baseline"
fi

# ── Snapshot the skill tree before, so we can stage ONLY the delta ──
SCOPE="skills/ docs/evidence/ docs/SKILLS-MANIFEST.md"
BEFORE=$(cd "$FLEET_REPO" && git status --porcelain --untracked-files=all -- $SCOPE 2>/dev/null || true)

# ── Recover the safe (pure-superset) strand class ────────────────
# A failed reconcile is SURFACED (not swallowed) and STOPS the cycle with a
# clear log line — proceeding would print a misleading "no new strands" and
# mask a broken reconciler. A transient failure retries next cycle with no
# side effects. Capture rc BEFORE logging (a bare `$?` after `if !` is 0).
_RECONCILE_RC=0
(cd "$FLEET_REPO" && python3 ops/scripts/manage/reconcile-stranded-skills.py --apply >/dev/null 2>&1) || _RECONCILE_RC=$?
if [ "$_RECONCILE_RC" -ne 0 ]; then
  log "reconcile-stranded-skills.py --apply failed (rc=$_RECONCILE_RC) — leaving recovery to the next cycle"
  rmdir "$_LOCK" 2>/dev/null || true
  exit 0
fi
# A recovered strand that touches a skill's frontmatter description makes the
# skills manifest stale — regenerate it so the doc-audit gate stays green. A
# failed regen is logged with its real rc; the pre-commit doc-audit gate is the
# backstop that refuses a stale-manifest commit.
_MANIFEST_RC=0
(cd "$FLEET_REPO" && bash ops/scripts/manage/gen-skills-manifest.sh >/dev/null 2>&1) || _MANIFEST_RC=$?
if [ "$_MANIFEST_RC" -ne 0 ]; then
  log "gen-skills-manifest.sh failed (rc=$_MANIFEST_RC) — manifest may be stale for this commit"
fi

AFTER=$(cd "$FLEET_REPO" && git status --porcelain --untracked-files=all -- $SCOPE 2>/dev/null || true)
if [ "$AFTER" == "$BEFORE" ] || [ -z "$AFTER" ]; then
  log "no new strands to recover — silent"
  rmdir "$_LOCK" 2>/dev/null || true
  exit 0
fi

# ── Sanctioned pipeline commit: fresh marker, then stage ONLY the delta ──
mkdir -p "$GOV_STATE"
: > "$_PIPE_MARKER"
trap 'rm -f "$_PIPE_MARKER"; rmdir "$_LOCK" 2>/dev/null || true' EXIT

cd "$FLEET_REPO"
# Paths the reconcile delta added/changed, EXCLUDING any scattered noise:
# use git add on the differing paths only.
{
  printf '%s\n' "$AFTER"
} | awk '{print $2}' | while IFS= read -r p; do
  [ -n "$p" ] || continue
  _ADD_RC=0
  git add -- "$p" 2>/dev/null || _ADD_RC=$?
  if [ "$_ADD_RC" -ne 0 ]; then
    log "could not stage $p (rc=$_ADD_RC) — path may be missing or unreadable"
  fi
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

_COMMIT_RC=0
_COMMIT_LOG=$(git commit -m "auto: reconcile skill-drift strands (skills=${N}) [pipeline]" -- $STAGED 2>&1) || _COMMIT_RC=$?
if [ "$_COMMIT_RC" -ne 0 ]; then
  _reason=$(printf '%s\n' "$_COMMIT_LOG" | grep -viE '^$|^\\s*$' | tail -1)
  log "commit refused (rc=$_COMMIT_RC): ${_reason:-see output} — delta left staged for a human; nothing pushed"
  exit 0
fi
log "committed ${N} skill(s) — deploying + pushing"

# ── Deploy (satisfies deploy-sync + dogfood), then push ────────────
_DEPLOY_RC=0
_DEPLOY_LOG=$(bash "$FLEET_REPO/ops/scripts/fleet-update.sh" 2>&1) || _DEPLOY_RC=$?
if [ "$_DEPLOY_RC" -ne 0 ]; then
  _reason=$(printf '%s\n' "$_DEPLOY_LOG" | grep -E '❌|FAIL|error|Error' | tail -1)
  log "deploy failed (rc=$_DEPLOY_RC): ${_reason:-see output} — not pushing this cycle"
  exit 0
fi
_PUSH_RC=0
_PUSH_LOG=$(git push origin main 2>&1) || _PUSH_RC=$?
if [ "$_PUSH_RC" -eq 0 ]; then
  echo "[$(TS) skill-drift-reconcile] reconciled ${N} skill(s); committed + deployed + pushed"
else
  _reason=$(printf '%s\n' "$_PUSH_LOG" | grep -E '❌|error|rejected|blocked' | tail -1)
  log "push blocked after deploy (rc=$_PUSH_RC): ${_reason:-see output} — retry next cycle"
  exit 0
fi
