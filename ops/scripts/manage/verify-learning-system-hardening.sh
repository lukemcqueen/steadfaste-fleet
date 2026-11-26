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
# Exit code 0 = every claim that should apply on this host was verified PASS.
# Non-zero = at least one claim FAILed. SKIP rows are claims not applicable
# on this host (e.g. a tool that is repo-only by design and is not deployed
# anywhere) — they are reported as unverified and are NOT counted as PASS,
# but they do not by themselves mark the run as not-holding. The summary
# says "N claims unverified (SKIP)" when any SKIP is present, so a reader
# cannot mistake "did not check" for "checked and fine".
#
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
SKIP_COUNT=0
declare -a RESULTS=()

report() {
  local name="$1" status="$2" detail="$3"
  RESULTS+=("${status}|${name}|${detail}")
  # FAIL marks the run as not-holding. SKIP is reported as UNVERIFIED (the
  # summary prints a count) but does not mark the run as not-holding on its
  # own — see the header contract. PASS is the only status that counts as
  # verified.
  if [[ "${status}" == "FAIL" ]]; then FAIL=1; fi
  if [[ "${status}" == "SKIP" ]]; then SKIP_COUNT=$((SKIP_COUNT + 1)); fi
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
# Check the exit code as well as the parsed strings: a parity run that
# crashes after printing a stale "Verdict: PASS" line, or that exits
# non-zero while its output happens to contain the right numbers, must not
# be reported as PASS. The header contract ("every check is a real command
# whose output is the proof") includes the command's exit status.
# Capture stdout and stderr SEPARATELY so diagnostic noise (tracebacks,
# warnings) cannot be parsed as the tool's structured output — only stdout
# is parsed; stderr is surfaced in the FAIL detail.
PARITY_ERR="$(mktemp)"
PARITY_OUT="$(cd "${REPO}" && python3 ops/scripts/manage/check-skill-drift-parity.py 2>"${PARITY_ERR}")"
PARITY_RC=$?
PARITY_STDERR="$(cat "${PARITY_ERR}")"
rm -f "${PARITY_ERR}"
STRANDED=$(grep -E "Stranded in place" <<<"${PARITY_OUT}" | grep -oE "[0-9]+" | head -1)
ABSENT=$(grep -E "Absent from the repo" <<<"${PARITY_OUT}" | grep -oE "[0-9]+" | head -1)
VERDICT=$(grep -E "Verdict:" <<<"${PARITY_OUT}" | tail -1 | sed 's/.*Verdict: *//' | tr -d '*[:space:]')
if [[ ${PARITY_RC} -eq 0 && "${STRANDED:-?}" == "0" && "${ABSENT:-?}" == "0" && "${VERDICT:-}" == "PASS" ]]; then
  report "skill-drift parity (stranded + absent = 0)" "PASS" "rc=0 stranded=${STRANDED} absent=${ABSENT} verdict=${VERDICT}"
elif [[ ${PARITY_RC} -ne 0 ]]; then
  report "skill-drift parity (stranded + absent = 0)" "FAIL" "rc=${PARITY_RC} — the parity command did not exit 0; parsed values cannot be trusted (stranded=${STRANDED:-?} absent=${ABSENT:-?} verdict=${VERDICT:-?}) stderr=[${PARITY_STDERR:0:200}]"
else
  report "skill-drift parity (stranded + absent = 0)" "FAIL" "rc=0 but parsed values disagree (stranded=${STRANDED:-?} absent=${ABSENT:-?} verdict=${VERDICT:-?}) stderr=[${PARITY_STDERR:0:200}]"
fi

# ── Claim 2: --check agrees with the artifact it records ─────────────────────
CHECK_ERR="$(mktemp)"
CHECK_OUT="$(cd "${REPO}" && python3 ops/scripts/manage/check-skill-drift-parity.py --check 2>"${CHECK_ERR}")"
CHECK_RC=$?
CHECK_STDERR="$(cat "${CHECK_ERR}")"
rm -f "${CHECK_ERR}"
if [[ ${CHECK_RC} -eq 0 && "${CHECK_OUT}" == *"artifact matches the live derivation"* ]]; then
  report "parity --check re-derivation" "PASS" "rc=0, artifact matches live derivation"
elif [[ ${CHECK_RC} -ne 0 ]]; then
  report "parity --check re-derivation" "FAIL" "rc=${CHECK_RC}: ${CHECK_OUT:-<no stdout>} stderr=[${CHECK_STDERR:0:200}]"
else
  report "parity --check re-derivation" "FAIL" "rc=0 but output does not say 'artifact matches the live derivation': ${CHECK_OUT}"
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
# Same stdout/stderr split: parse only stdout so diagnostic noise cannot
# masquerade as a test summary.
TEST_ERR="$(mktemp)"
TEST_OUT="$(cd "${REPO}" && bash tests/run-tests.sh tests/test_fleet_doctor_collector_health.py -q 2>"${TEST_ERR}")"
TEST_RC=$?
TEST_STDERR="$(cat "${TEST_ERR}")"
rm -f "${TEST_ERR}"
PASSED_LINE=$(grep -E "passed|failed" <<<"${TEST_OUT}" | tail -1)
if [[ ${TEST_RC} -eq 0 ]] && grep -qE "[0-9]+ passed" <<<"${TEST_OUT}"; then
  report "collector-health tests" "PASS" "${PASSED_LINE}"
elif [[ ${TEST_RC} -ne 0 ]]; then
  report "collector-health tests" "FAIL" "rc=${TEST_RC}: ${PASSED_LINE:-no summary line} stderr=[${TEST_STDERR:0:200}]"
else
  report "collector-health tests" "FAIL" "rc=0 but no '[0-9]+ passed' summary line in stdout: ${TEST_OUT:0:200} stderr=[${TEST_STDERR:0:200}]"
fi

# ── Claim 5: the deployed fleet-doctor surfaces the new row ──────────────────
# The deployed doctor is the LIVE path (the fleet calls `fleet-doctor.py` from
# `~/.steadfaste-fleet/scripts/`, not the repo). A missing deployed doctor is
# exactly the state where the hardening is NOT in place — that is a FAIL, not
# a SKIP.
DOCTOR_BIN="${HOME}/.steadfaste-fleet/scripts/fleet-doctor.py"
if [[ -f "${DOCTOR_BIN}" ]]; then
  DOCTOR_ERR="$(mktemp)"
  DOCTOR_OUT="$(cd "${REPO}" && "${HOME}/.steadfaste-fleet/venv/bin/python3" "${DOCTOR_BIN}" --quiet 2>"${DOCTOR_ERR}")"
  DOCTOR_RC=$?
  DOCTOR_STDERR="$(cat "${DOCTOR_ERR}")"
  rm -f "${DOCTOR_ERR}"
  # The doctor's rc is a warn/fail summary (0 healthy, 1 warnings, 2+ fails),
  # not a "did it run" indicator — so we do NOT require rc=0. But an empty
  # output or an obviously-broken rc (>2) means the run itself did not
  # produce a usable verdict. Parse only stdout; stderr is surfaced separately
  # so diagnostic noise cannot masquerade as a doctor row.
  if [[ -z "${DOCTOR_OUT}" ]]; then
    report "fleet-doctor surfaces Learning collector freshness" "FAIL" "the doctor produced no stdout (rc=${DOCTOR_RC}) stderr=[${DOCTOR_STDERR:0:200}] — cannot verify the row"
  elif [[ ${DOCTOR_RC} -gt 2 ]]; then
    report "fleet-doctor surfaces Learning collector freshness" "FAIL" "the doctor exited rc=${DOCTOR_RC} (above the warn/fail summary range) stderr=[${DOCTOR_STDERR:0:200}] — cannot verify the row"
  elif grep -q "Learning collector freshness" <<<"${DOCTOR_OUT}"; then
    ROW=$(grep "Learning collector freshness" <<<"${DOCTOR_OUT}" | head -1 | sed 's/^ *//')
    report "fleet-doctor surfaces Learning collector freshness" "PASS" "${ROW} (doctor rc=${DOCTOR_RC})"
  else
    report "fleet-doctor surfaces Learning collector freshness" "FAIL" "row not present (doctor rc=${DOCTOR_RC}) stderr=[${DOCTOR_STDERR:0:200}] — is the deployed fleet_doctor/ in sync with the repo?"
  fi
else
  report "fleet-doctor surfaces Learning collector freshness" "FAIL" "no deployed doctor at ${DOCTOR_BIN} — run fleet-update.sh; this claim cannot SKIP because the deployed doctor is the live path"
fi

# ── Claim 6: the deployed fleet_doctor/checks.py is in sync with the repo ────
# Same logic: the deployed copy is what the live doctor imports, so its
# absence is a hardening gap, not a not-applicable case. Like claim 3, the
# deploy stamps a 3-line SOURCE banner on shipped scripts — compare content
# with the banner stripped (content_sha), not raw sha256, or every nightly
# run false-FAILs on a banner-only delta.
REPO_CHECKS_SHA=$(content_sha "${REPO}/ops/scripts/manage/fleet_doctor/checks.py")
DEPLOYED_CHECKS="${HOME}/.steadfaste-fleet/scripts/fleet_doctor/checks.py"
if [[ -f "${DEPLOYED_CHECKS}" ]]; then
  DEPLOYED_CHECKS_SHA=$(content_sha "${DEPLOYED_CHECKS}")
  if [[ "${REPO_CHECKS_SHA}" == "${DEPLOYED_CHECKS_SHA}" ]]; then
    report "fleet_doctor/checks.py deployed = repo (banner-stripped)" "PASS" "content sha256=${REPO_CHECKS_SHA:0:16}"
  else
    report "fleet_doctor/checks.py deployed = repo (banner-stripped)" "FAIL" "repo=${REPO_CHECKS_SHA:0:16} deployed=${DEPLOYED_CHECKS_SHA:0:16} — run fleet-update.sh"
  fi
else
  report "fleet_doctor/checks.py deployed = repo (banner-stripped)" "FAIL" "no deployed copy at ${DEPLOYED_CHECKS} — run fleet-update.sh; this claim cannot SKIP because the deployed copy is what the live doctor imports"
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
  # Emit valid JSON: escape every string through python3 json.dumps so a
  # FAIL detail containing a double quote (e.g. a stderr excerpt with a
  # quoted path or a Python traceback line) cannot produce syntactically
  # invalid JSON. The header advertises "machine-readable" — that is a
  # promise, and unescaped printf is not one.
  # Join the RESULTS array into a newline-delimited env var for the encoder.
  RESULTS_ENV="$(printf '%s\n' "${RESULTS[@]}")"
  export RESULTS_ENV SKIP_COUNT FAIL_FLAG="${FAIL}"
  JSON_PAYLOAD=$(python3 -c '
import json, os, sys
results = []
for r in os.environ.get("RESULTS_ENV", "").split("\n"):
    if not r:
        continue
    st, name, detail = r.split("|", 2)
    results.append({"name": name, "status": st, "detail": detail})
skip = int(os.environ.get("SKIP_COUNT", "0"))
fail = int(os.environ.get("FAIL_FLAG", "0"))
print(json.dumps({
    "claims": results,
    "all_pass": fail == 0 and skip == 0,
    "unverified": skip,
}))
' 2>&1)
  JSON_RC=$?
  if [[ ${JSON_RC} -eq 0 ]]; then
    printf '%s\n' "${JSON_PAYLOAD}"
  else
    # Do not emit a half-built JSON object on failure — emit a valid
    # one-line error object so a machine reader still gets parseable output.
    printf '{"error": "json-encode-failed", "rc": %d, "stderr": %s}\n' \
      "${JSON_RC}" "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${JSON_PAYLOAD:0:200}")"
  fi
else
  if [[ ${FAIL} -ne 0 ]]; then
    echo "  ❌ SOME CLAIMS DO NOT HOLD — see FAIL rows above"
  elif [[ ${SKIP_COUNT} -ne 0 ]]; then
    # Do NOT claim "ALL CLAIMS HOLD" when some claims were not verified —
    # "did not check" is not "checked and fine".
    echo "  ⚠️  NO CLAIM FAILed, but ${SKIP_COUNT} claim(s) UNVERIFIED (SKIP) — see ➖ rows above"
  else
    echo "  ✅ ALL CLAIMS HOLD"
  fi
fi

exit ${FAIL}
