#!/usr/bin/env bash
# Runnable verifier for the skills-backport cycle (#14815, 2026-11-16).
#
# Prints, for each claim the cycle makes, the exact command, its output and its
# OWN exit status. READ-ONLY: it runs and prints; it repairs nothing it measures.
# Host home paths are normalised to <HOME> so the capture is committable.
# The script's OWN exit status = the number of claims that FAILED (0 = all hold),
# so the canonical capture's `rc` is meaningful rather than a bare zero.
#
# Usage:  bash docs/evidence/skills-backport-2026-11-16.sh
# Canonical capture:
#   bash ops/scripts/make-evidence.sh -k docs/evidence/skills-backport-2026-11-16.txt \
#     -- bash docs/evidence/skills-backport-2026-11-16.sh
set -u
H="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$H" || exit 2

bad=0
norm() { sed "s|${HOME}|<HOME>|g"; }
section() { printf '\n=== %s ===\n' "$1"; }

# ---- 1. the reconcile detector: PASS + rc 0 (the claim the review disputed) ----
printf '$ python3 ops/scripts/manage/reconcile-stranded-skills.py\n'
out=$(python3 ops/scripts/manage/reconcile-stranded-skills.py 2>&1); rc=$?
printf '%s\n' "$out" | norm
printf 'rc=%s\n' "$rc"
[ "$rc" -eq 0 ] || { echo "CLAIM FAILED: reconcile rc=$rc"; bad=$((bad+1)); }

# ---- 2. the parity checker: PASS + rc 0; its counts are the committed table ----
section "check-skill-drift-parity.py (the counts behind the verdict)"
printf '$ python3 ops/scripts/manage/check-skill-drift-parity.py\n'
out=$(python3 ops/scripts/manage/check-skill-drift-parity.py 2>&1); rc=$?
printf '%s\n' "$out" | norm | grep -E 'Stranded in place|Absent from the repo|dropped from repo history|never committed|Files in sync|Hermes defaults|Verdict|^- '
printf 'rc=%s\n' "$rc"
[ "$rc" -eq 0 ] || { echo "CLAIM FAILED: parity rc=$rc"; bad=$((bad+1)); }

# ---- 3. the committed parity artifact is NOT stale ----
section "check-skill-drift-parity.py --check (freshness of the committed artifact)"
printf '$ python3 ops/scripts/manage/check-skill-drift-parity.py --check\n'
out=$(python3 ops/scripts/manage/check-skill-drift-parity.py --check 2>&1); rc=$?
printf '%s\n' "$out" | norm
printf 'rc=%s\n' "$rc"
[ "$rc" -eq 0 ] || { echo "CLAIM FAILED: committed parity artifact is STALE (rc=$rc)"; bad=$((bad+1)); }

# ---- 4. the doctor: the skill-drift class is gone; the ONE remaining warn is the ----
#         separate harness-registry defect, disclosed and owned by the next cycle.
section "fleet-doctor.py (Skill drift / Harness registry / Overall)"
printf '$ python3 ops/scripts/manage/fleet-doctor.py\n'
out=$(python3 ops/scripts/manage/fleet-doctor.py 2>&1); rc=$?
printf '%s\n' "$out" | norm | grep -E 'Skill drift|Repo clean|Harness registry|Overall'
printf 'rc=%s   # NON-ZERO is expected: the doctor exits 1 while any warn stands\n' "$rc"
printf '%s\n' "$out" | grep -q '0 fail' || { echo "CLAIM FAILED: doctor is not at 0 fail"; bad=$((bad+1)); }
printf '%s\n' "$out" | grep -q 'Skill drift' && printf '%s\n' "$out" | grep -q 'skills in sync' \
  || { echo "CLAIM FAILED: Skill drift is not reported in sync"; bad=$((bad+1)); }

# ---- 5. the tree after the backport: no TRACKED file left uncommitted ----
# The verifier's OWN evidence artifacts are untracked by design (the cycle that
# runs this commits them), so the claim is about tracked files: the porcelain
# view with --untracked-files=no must be empty.
section "git status --short"
printf '$ git status --short\n'
out=$(git status --short 2>&1); rc=$?
printf '%s\n' "${out:-(no output)}"
printf 'rc=%s\n' "$rc"
tracked=$(git status --porcelain --untracked-files=no 2>&1)
printf '$ git status --porcelain --untracked-files=no   # the tracked-file claim\n'
printf '%s\n' "${tracked:-(clean — no tracked file modified)}"
[ -z "$tracked" ] || { echo "CLAIM FAILED: tracked file(s) left uncommitted"; bad=$((bad+1)); }

# ---- 6. what the commit actually carried ----
section "git show --stat (the backport commit)"
printf '$ git show --stat feda5157\n'
out=$(git show --stat feda5157 2>&1); rc=$?
printf '%s\n' "$out" | norm
printf 'rc=%s\n' "$rc"
[ "$rc" -eq 0 ] || { echo "CLAIM FAILED: commit feda5157 not found"; bad=$((bad+1)); }

section "VERDICT"
printf 'claims failed: %s   (0 means every claim above held)\n' "$bad"
exit "$bad"
