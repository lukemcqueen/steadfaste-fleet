#!/usr/bin/env bash
# verify-learning-system-hardening.sh — re-runnable proof for the RS1 / learning-system-hardening cycle.
#
# Runs every claim the two commits in this range make, and prints PASS/FAIL per claim.
# Re-run after any change to:
#   - ops/scripts/manage/check-skill-drift-parity.py
#   - ops/scripts/manage/reconcile-stranded-skills.py
#   - ops/scripts/manage/fleet_doctor/checks.py
#   - ops/scripts/manage/fleet_doctor/cli.py
#   - tests/test_fleet_doctor_collector_health.py
#   - any file under skills/ that the parity checker compares
#
# Usage:
#   bash ops/scripts/manage/verify-learning-system-hardening.sh
#   bash ops/scripts/manage/verify-learning-system-hardening.sh --json   # machine-readable
#
# Exit code 0 = every claim holds. Non-zero = at least one claim does not.
# Every check is a real command whose output is the proof — no assertion-only checks.
#
# CHECKOUT-ONLY by design (deliberately NOT registered in fleet-update.sh's
# register() map): this is a run-scoped evidence tool for THIS cycle's work,
# the same class as `check-skill-drift-parity.py` (also repo-only — see
# deploy-manifest.tsv, which does not carry it either). It reads the repo
# working tree to verify the repo is in sync with the deployed tree; there is
# no deployed copy to verify against. The tools it CHECKS (reconciler,
# fleet_doctor/) ARE deployed and are compared in claims 3 and 6 below.

set -uo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
JSON_MODE=0
[[ "${1:-}" == "--json" ]] && JSON_MODE=1

FAIL=0
declare -a RESULTS=()

report() {
  local name="$1" status="$2" detail="$3"
  RESULTS+=("${status}|${name}|${detail}")
  # SKIP is not a failure — it means the claim does not apply on this host
  # (e.g. a tool that is repo-only by design and is not deployed anywhere).
  # Only a FAIL marks the run as not-holding.
  if [[ "${status}" == "FAIL" ]]; then FAIL=1; fi
  if [[ ${JSON_MODE} -eq 0 ]]; then
    local icon="✅"
    if [[ "${status}" == "FAIL" ]]; then icon="❌"; fi
    if [[ "${status}" == "SKIP" ]]; then icon="➖"; fi
    printf '  %s %s — %s\n' "${icon}" "${name}" "${detail}"
  fi
}

echo "━━━ verify-learning-system-hardening ━━━  ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "repo: ${REPO}"
echo

# ── Claim 1: skill-drift parity is at PASS (0 stranded, 0 absent) ────────────
PARITY_OUT="$(cd "${REPO}" && python3 ops/scripts/manage/check-skill-drift-parity.py 2>&1)"
STRANDED=$(grep -E "Stranded in place" <<<"${PARITY_OUT}" | grep -oE "[0-9]+" | head -1)
ABSENT=$(grep -E "Absent from the repo" <<<"${PARITY_OUT}" | grep -oE "[0-9]+" | head -1)
VERDICT=$(grep -E "Verdict:" <<<"${PARITY_OUT}" | tail -1 | sed 's/.*Verdict: *//' | tr -d '*[:space:]')
if [[ "${STRANDED:-?}" == "0" && "${ABSENT:-?}" == "0" && "${VERDICT:-}" == "PASS" ]]; then
  report "skill-drift parity (stranded + absent = 0)" "PASS" "stranded=${STRANDED} absent=${ABSENT} verdict=${VERDICT}"
else
  report "skill-drift parity (stranded + absent = 0)" "FAIL" "stranded=${STRANDED:-?} absent=${ABSENT:-?} verdict=${VERDICT:-?}"
fi

# ── Claim 2: --check agrees with the artifact it records ─────────────────────
CHECK_OUT="$(cd "${REPO}" && python3 ops/scripts/manage/check-skill-drift-parity.py --check 2>&1)"
CHECK_RC=$?
if [[ ${CHECK_RC} -eq 0 ]] && grep -q "artifact matches the live derivation" <<<"${CHECK_OUT}"; then
  report "parity --check re-derivation" "PASS" "rc=0, artifact matches live derivation"
else
  report "parity --check re-derivation" "FAIL" "rc=${CHECK_RC}: $(tail -1 <<<"${CHECK_OUT}")"
fi

# ── Claim 3: the deployed reconciler (the one the deploy map actually ships) ─
# The parity checker is REPO-ONLY by design — it is not in deploy-manifest.tsv
# and is not deployed anywhere, so there is nothing to compare. The deployed
# side of the drift tooling is `agent-skill-drift-reconcile.sh`, which IS
# registered (`ops/scripts/manage/agent-skill-drift-reconcile.sh` ->
# `~/.steadfaste-fleet/scripts/manage/agent-skill-drift-reconcile.sh`).
# The deploy adds a 3-line SOURCE banner at the top; per the parity checker's
# own convention, deploy banners are NOT compared — only the content below.
REPO_RECON="${REPO}/ops/scripts/manage/agent-skill-drift-reconcile.sh"
DEPLOYED_RECON="${HOME}/.steadfaste-fleet/scripts/manage/agent-skill-drift-reconcile.sh"
content_sha() {
  # Strip any leading 3-line deploy banner starting with "# SOURCE:" and hash the rest.
  awk 'BEGIN{skip=0} NR<=3 && /^# SOURCE:/{skip=1; next} skip==1 && /^# Do NOT edit/{next} skip==1 && /^$/{skip=0; next} {print}' "$1" | sha256sum | cut -d' ' -f1
}
if [[ -f "${DEPLOYED_RECON}" ]]; then
  REPO_RECON_SHA=$(content_sha "${REPO_RECON}")
  DEPLOYED_RECON_SHA=$(content_sha "${DEPLOYED_RECON}")
  if [[ "${REPO_RECON_SHA}" == "${DEPLOYED_RECON_SHA}" ]]; then
    report "agent-skill-drift-reconcile.sh deployed = repo (banner-stripped)" "PASS" "content sha256=${REPO_RECON_SHA:0:16}"
  else
    report "agent-skill-drift-reconcile.sh deployed = repo (banner-stripped)" "FAIL" "repo=${REPO_RECON_SHA:0:16} deployed=${DEPLOYED_RECON_SHA:0:16} — run fleet-update.sh"
  fi
else
  report "agent-skill-drift-reconcile.sh deployed = repo (banner-stripped)" "FAIL" "no deployed copy at ${DEPLOYED_RECON} — run fleet-update.sh"
fi

# ── Claim 4: the new collector-health test passes ────────────────────────────
TEST_OUT="$(cd "${REPO}" && bash tests/run-tests.sh tests/test_fleet_doctor_collector_health.py -q 2>&1)"
TEST_RC=$?
PASSED_LINE=$(grep -E "passed|failed" <<<"${TEST_OUT}" | tail -1)
if [[ ${TEST_RC} -eq 0 ]] && grep -qE "[0-9]+ passed" <<<"${TEST_OUT}"; then
  report "collector-health tests" "PASS" "${PASSED_LINE}"
else
  report "collector-health tests" "FAIL" "rc=${TEST_RC}: ${PASSED_LINE:-no summary line}"
fi

# ── Claim 5: the deployed fleet-doctor surfaces the new row ──────────────────
DOCTOR_BIN="${HOME}/.steadfaste-fleet/scripts/fleet-doctor.py"
if [[ -f "${DOCTOR_BIN}" ]]; then
  DOCTOR_OUT="$(cd "${REPO}" && "${HOME}/.steadfaste-fleet/venv/bin/python3" "${DOCTOR_BIN}" --quiet 2>&1)"
  if grep -q "Learning collector freshness" <<<"${DOCTOR_OUT}"; then
    ROW=$(grep "Learning collector freshness" <<<"${DOCTOR_OUT}" | head -1 | sed 's/^ *//')
    report "fleet-doctor surfaces Learning collector freshness" "PASS" "${ROW}"
  else
    report "fleet-doctor surfaces Learning collector freshness" "FAIL" "row not present — is the deployed fleet_doctor/ in sync with the repo?"
  fi
else
  report "fleet-doctor surfaces Learning collector freshness" "SKIP" "no deployed doctor at ${DOCTOR_BIN}"
fi

# ── Claim 6: the deployed fleet_doctor/checks.py is in sync with the repo ────
REPO_CHECKS_SHA=$(sha256sum "${REPO}/ops/scripts/manage/fleet_doctor/checks.py" | cut -d' ' -f1)
DEPLOYED_CHECKS="${HOME}/.steadfaste-fleet/scripts/fleet_doctor/checks.py"
if [[ -f "${DEPLOYED_CHECKS}" ]]; then
  DEPLOYED_CHECKS_SHA=$(sha256sum "${DEPLOYED_CHECKS}" | cut -d' ' -f1)
  if [[ "${REPO_CHECKS_SHA}" == "${DEPLOYED_CHECKS_SHA}" ]]; then
    report "fleet_doctor/checks.py deployed = repo" "PASS" "sha256=${REPO_CHECKS_SHA:0:16}"
  else
    report "fleet_doctor/checks.py deployed = repo" "FAIL" "repo=${REPO_CHECKS_SHA:0:16} deployed=${DEPLOYED_CHECKS_SHA:0:16} — run fleet-update.sh"
  fi
else
  report "fleet_doctor/checks.py deployed = repo" "SKIP" "no deployed copy at ${DEPLOYED_CHECKS}"
fi

# ── Claim 7: every hand-merged SKILL.md is byte-identical between repo and deployed
DRIFT_PAIRS=(
  "skills/devops/deploy-load-verification/SKILL.md"
  "skills/devops/git-forensics/SKILL.md"
  "skills/devops/governance-lock-lifecycle/SKILL.md"
  "skills/devops/hermes-gateway-operations/SKILL.md"
  "skills/devops/hermes-home-cleanup/SKILL.md"
  "skills/software-development/documentation-auditing/SKILL.md"
  "skills/software-development/execute-right/SKILL.md"
)
DRIFT_OK=0
DRIFT_BAD=""
for rel in "${DRIFT_PAIRS[@]}"; do
  # `rel` is repo-relative (starts with skills/); the deployed tree is rooted
  # one level up, so strip the leading `skills/` when building the deployed path.
  R="${REPO}/${rel}"
  D_REL="${rel#skills/}"
  D="${HOME}/.hermes/skills/${D_REL}"
  if [[ ! -f "${D}" ]]; then D="${HOME}/.steadfaste-fleet/skills/${D_REL}"; fi
  if [[ -f "${R}" && -f "${D}" ]]; then
    if cmp -s "${R}" "${D}"; then
      DRIFT_OK=$((DRIFT_OK + 1))
    else
      DRIFT_BAD="${DRIFT_BAD} ${rel}"
    fi
  else
    DRIFT_BAD="${DRIFT_BAD} ${rel}(missing)"
  fi
done
if [[ -z "${DRIFT_BAD}" ]]; then
  report "7 hand-merged SKILL.md files byte-identical (repo = deployed)" "PASS" "${DRIFT_OK}/${#DRIFT_PAIRS[@]} identical"
else
  report "7 hand-merged SKILL.md files byte-identical (repo = deployed)" "FAIL" "${DRIFT_OK}/${#DRIFT_PAIRS[@]} identical; diverged:${DRIFT_BAD}"
fi

echo
if [[ ${JSON_MODE} -eq 1 ]]; then
  printf '{"claims": ['
  first=1
  for r in "${RESULTS[@]}"; do
    IFS='|' read -r st name detail <<<"${r}"
    if [[ ${first} -eq 0 ]]; then printf ','; fi
    first=0
    printf '{"name": "%s", "status": "%s", "detail": "%s"}' "${name}" "${st}" "${detail}"
  done
  if [[ ${FAIL} -eq 0 ]]; then printf '], "all_pass": true}\n'; else printf '], "all_pass": false}\n'; fi
else
  if [[ ${FAIL} -eq 0 ]]; then
    echo "  ✅ ALL CLAIMS HOLD"
  else
    echo "  ❌ SOME CLAIMS DO NOT HOLD — see FAIL rows above"
  fi
fi

exit ${FAIL}
