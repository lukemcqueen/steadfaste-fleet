#!/usr/bin/env bash
# Verifier for: the deploy's cron-manifest converge pass, the interpreter it runs
# under, and the doctor's remedy for manifest drift.
#
# EXIT CODE = NUMBER OF FAILED CLAIMS (0 = every claim held). A trailing `echo`
# would always exit 0, so the count is the return value and nothing follows it.
#
# WHY (measured on esther, 2026-11-17): fleet-update.sh ran the manifest converge
# pass as `python3 cron_manifest.py --repair 2>/dev/null || true`, on a host whose
# PATH python3 (a Hermes-managed tools venv, Python 3.14.9) cannot import yaml, so
# cron_manifest.py raised ManifestLoadError, the error was discarded, and the
# script then printed the SUCCESS line "all in-scope crons already match". The
# converge step was therefore dead on every deploy. A repo-side prompt rename then
# never reached the live job, the doctor FAILed 'Cron manifest drift
# (orch-model-market-scan) - prompt differs (2236 -> 2228 chars)', and the
# pre-push gate blocked the host.
#
# Run: bash docs/evidence/cron-manifest-converge-verifier-2026-11-17.sh
set -uo pipefail

REPO="${REPO:-$HOME/steadfaste-fleet}"
DEPLOY="${DEPLOY_HOME:-$HOME/.steadfaste-fleet}"
PY_RESOLVER="$DEPLOY/scripts/lib/python-with-module.sh"
cd "$REPO" || exit 99

fails=0
claim() {  # claim <label> <0|1-ok>
  if [[ "$2" == "1" ]]; then printf '  PASS  %s\n' "$1"
  else printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); fi
}

echo "== 1. the deploy script is syntactically valid =="
bash -n ops/scripts/fleet-update.sh && claim "bash -n ops/scripts/fleet-update.sh" 1 || claim "bash -n fleet-update.sh" 0

echo "== 2. the pins hold (each test is discriminating) =="
for t in test_fleet_update_manifest_repair test_doctor_remedies_use_capable_interpreter test_cron_manifest_matches_installer; do
  out="$("$PY_RESOLVER" yaml -m pytest "tests/$t.py" -q -p no:cacheprovider 2>&1 | tail -1)"
  [[ "$out" == *"passed"* && "$out" != *"failed"* ]] && claim "$t" 1 || claim "$t -> $out" 0
done

echo "== 3. an interpreter that can import yaml is resolvable BY CAPABILITY =="
resolved="$(bash "$PY_RESOLVER" --print yaml 2>/dev/null)"
if [[ -n "$resolved" ]] && env -u PYTHONPATH -u PYTHONHOME "$resolved" -c 'import yaml' 2>/dev/null; then
  claim "python-with-module.sh --print yaml -> $resolved (imports yaml)" 1
else
  claim "python-with-module.sh --print yaml resolved nothing capable" 0
fi

echo "== 4. the pre-fix BASELINE reproduces the defect (the fix is load-bearing) =="
# Pinned to the immutable pre-fix tip (the commit that was on origin/main when
# this defect was live). Unpinning to HEAD/merge-base would silently stop proving
# anything the moment the fix itself is committed.
BASE_SHA="${BASE_SHA:-2764a85f91f044f8a076a82fc310ab59e3b3e886}"
echo "  baseline revision: $BASE_SHA"
if git show "$BASE_SHA:ops/scripts/fleet-update.sh" > /tmp/fu-base-$$.sh 2>/dev/null; then
  grep -q -- '--repair 2>/dev/null || true' /tmp/fu-base-$$.sh \
    && claim "baseline discards the repair's stderr AND exit status" 1 \
    || claim "baseline did not show the swallowed-repair defect" 0
  # Scope the resolver check to the AUTO-REPAIR BLOCK: the file mentions the
  # resolver elsewhere (the deploy map registers it), so a whole-file grep is a
  # false positive and proves nothing about the block under test.
  awk '/Cron manifest auto-repair/,/all in-scope crons already match/' /tmp/fu-base-$$.sh > /tmp/fu-blk-$$.txt
  if [[ -s /tmp/fu-blk-$$.txt ]]; then
    grep -q 'python-with-module.sh' /tmp/fu-blk-$$.txt \
      && claim "baseline block already resolved (unexpected)" 0 \
      || claim "baseline auto-repair block does not use the capability resolver" 1
  else
    claim "could not isolate the baseline auto-repair block" 0
  fi
  rm -f /tmp/fu-base-$$.sh /tmp/fu-blk-$$.txt
else
  claim "could not read the baseline ($BASE_SHA) from git" 0
fi

echo "== 5. the fixed deploy does NOT swallow, and DOES resolve =="
grep -q 'python-with-module.sh' ops/scripts/fleet-update.sh \
  && claim "deploy resolves the interpreter via python-with-module.sh" 1 \
  || claim "deploy still re-derives an interpreter path" 0
if grep -n -- '--repair' ops/scripts/fleet-update.sh | grep -q '2>/dev/null || true'; then
  claim "deploy still swallows the repair failure" 0
else
  claim "deploy no longer discards the repair's diagnostics/status" 1
fi
grep -q 'Cron manifest repair FAILED' ops/scripts/fleet-update.sh \
  && claim "deploy has an explicit failure branch" 1 \
  || claim "deploy has no failure branch" 0

echo "== 6. the doctor's remedy string runs VERBATIM =="
remedy="$(grep -o 'bash [^"]*python-with-module.sh yaml [^"]*cron_manifest.py --check' ops/scripts/manage/fleet_doctor/checks.py | head -1)"
if [[ -n "$remedy" ]]; then
  out="$(eval "$remedy" 2>&1)"; rc=$?
  [[ "$rc" == "0" ]] && claim "doctor remedy executes (rc=0)" 1 \
                     || claim "doctor remedy exits $rc: $out" 0
  printf '  remedy: %s\n' "$remedy"
else
  claim "no runner-form remedy found in the doctor" 0
fi

echo "== 7. the manifest is converged on this host =="
"$PY_RESOLVER" yaml ops/scripts/manage/cron_manifest.py --check >/dev/null 2>&1 \
  && claim "cron_manifest.py --check rc=0 (all in-scope crons match)" 1 \
  || claim "cron_manifest.py --check reports drift" 0

echo
echo "FAILED CLAIMS: $fails"
exit "$fails"
