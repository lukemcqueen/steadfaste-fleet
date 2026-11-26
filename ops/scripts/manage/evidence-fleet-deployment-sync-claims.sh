#!/usr/bin/env bash
# evidence-fleet-deployment-sync-claims.sh — re-runnable proof for the
# behavioral claims in skills/devops/fleet-deployment-sync/SKILL.md
# (findings ADV-15212-3/4/5 from the 2026-11-26 soul-refine close review).
#
# Claims proven here:
#  C3  A rebase-based pull runs post-rewrite, NOT post-merge (SKILL.md ~L110).
#  C4  An ABANDONED unmerged index (scheduled pull whose process ended) has UU
#      index entries with NO .git/MERGE_HEAD, NO rebase-merge/, NO rebase-apply/
#      — nothing for `rebase --continue` to continue (SKILL.md ~L1072).
#  C5  skills.yaml `always:` lists THREE always-section skills, not "7"
#      (SKILL.md ~L1029; the "7" wording was stale and has been fixed in
#      ops/scripts/manage/fix-cron-skills-toolset.py).
#
# Exit 0 = all claims hold. Run from anywhere; uses throwaway repos under
# ${TMPDIR:-/tmp}, cleaned up on exit.

set -u
FAIL=0
ok()  { printf '  ✅ %s\n' "$1"; }
bad() { printf '  ❌ %s\n' "$1"; FAIL=1; }
cleanup() { [ -n "${D1:-}" ] && rm -rf "$D1" 2>/dev/null; [ -n "${D2:-}" ] && rm -rf "$D2" 2>/dev/null; }
trap cleanup EXIT

SKILLS_YAML="${HOME}/.steadfaste-fleet/skills.yaml"
REPO="${HOME}/steadfaste-fleet"

echo "━━━ evidence-fleet-deployment-sync-claims ━━━ ($(date '+%Y-%m-%d %H:%M:%S %Z'))"

# ── C5: skills.yaml always: count ─────────────────────────────────────────
ALWAYS_COUNT=$(awk '/^always:/{f=1;next} f && /^  - name:/{c++} f && /^[a-z]/ && !/^  /{f=0} END{print c+0}' "$SKILLS_YAML")
echo "  skills.yaml always: section lists ${ALWAYS_COUNT} skills"
if [ "$ALWAYS_COUNT" = "3" ]; then
  ok "C5: skills.yaml always: = 3 (task-orient, execute-right, verify-and-ship)"
else
  bad "C5: skills.yaml always: = ${ALWAYS_COUNT}, expected 3"
fi
CODE_7=$(grep -c "7 always-section skills" "$REPO/ops/scripts/manage/fix-cron-skills-toolset.py" 2>/dev/null || true)
if [ "$CODE_7" = "0" ]; then
  ok "C5: no stale '7 always-section skills' wording remains in fix-cron-skills-toolset.py"
else
  bad "C5: fix-cron-skills-toolset.py still says '7 always-section skills' (${CODE_7}x)"
fi

# ── C3: rebase fires post-rewrite, not post-merge ─────────────────────────
D1=$(mktemp -d "${TMPDIR:-/tmp}/c3-demo-XXXX")
cd "$D1"
git init -q -b main .
git config user.email demo@local; git config user.name demo
mkdir -p .githooks
echo base > f.txt && git add f.txt && git commit -q -m base -- f.txt
cat > .githooks/post-merge <<'EOF'
#!/bin/sh
echo "HOOK post-merge FIRED"
EOF
cat > .githooks/post-rewrite <<'EOF'
#!/bin/sh
echo "HOOK post-rewrite FIRED"
EOF
chmod +x .githooks/post-merge .githooks/post-rewrite
git config core.hooksPath .githooks
git checkout -qb topic
echo topic > g.txt && git add g.txt && git commit -q -m topic -- g.txt
git checkout -q main
echo main2 >> f.txt && git add f.txt && git commit -q -m main2 -- f.txt
HOOK_OUT=$(git checkout -q topic && git rebase main 2>&1 | grep -oE "HOOK (post-merge|post-rewrite) FIRED" | sort -u)
echo "  hooks fired during rebase: [${HOOK_OUT}]"
if [ "$HOOK_OUT" = "HOOK post-rewrite FIRED" ]; then
  ok "C3: rebase runs post-rewrite only; post-merge does NOT fire"
else
  bad "C3: expected 'HOOK post-rewrite FIRED' only, got [${HOOK_OUT}]"
fi

# ── C4: abandoned unmerged index shape ────────────────────────────────────
D2=$(mktemp -d "${TMPDIR:-/tmp}/c4-demo-XXXX")
cd "$D2"
git init -q -b main .
git config user.email demo@local; git config user.name demo
echo a > shared.txt && git add shared.txt && git commit -q -m base -- shared.txt
git checkout -qb cronbranch
echo cron > shared.txt && git add shared.txt && git commit -q -m cron-side -- shared.txt
git checkout -q main
echo agent > shared.txt && git add shared.txt && git commit -q -m agent-side -- shared.txt
# A scheduled `git pull` that conflicts then has its process END leaves the
# conflicted index behind. `git merge --quit` reproduces that exact state:
# it forgets the in-flight merge but leaves the index (UU) and worktree as-is.
git merge cronbranch >/dev/null 2>&1 || true   # conflicts -> UU + MERGE_HEAD
git merge --quit >/dev/null 2>&1               # process ends: state gone, index stays
UU=$(git status --short | grep -c '^UU' || true)
STATE_FILES=$(for d in .git/MERGE_HEAD .git/rebase-merge .git/rebase-apply .git/CHERRY_PICK_HEAD; do [ -e "$d" ] && echo "$d"; done | tr '\n' ' ')
echo "  abandoned shape: UU=${UU} state_files=[${STATE_FILES:-<none>}]"
if [ "$UU" -ge 1 ] && [ -z "$STATE_FILES" ]; then
  ok "C4: abandoned shape reproduced — UU present, no MERGE_HEAD/rebase state"
  # detection recipe from SKILL.md must work
  DETECT=$(for d in .git/MERGE_HEAD .git/rebase-merge .git/rebase-apply .git/CHERRY_PICK_HEAD; do [ -e "$d" ] && echo "PRESENT: $d" || echo "absent : $d"; done | tr '\n' '; ')
  echo "  detection recipe: ${DETECT}"
else
  bad "C4: expected UU>=1 with no state files, got UU=${UU} state=[${STATE_FILES}]"
fi

echo "━━━ result: $([ $FAIL -eq 0 ] && echo 'ALL CLAIMS HOLD' || echo 'SOME CLAIMS FAILED') ━━━"
exit $FAIL
