#!/usr/bin/env bash
# READ-ONLY verifier for the cron_manifest --repair correctness fix (issue #293)
# and the load_manifest fail-closed fix (issue #291).
#
#   docs/evidence/cron-manifest-repair-correctness-2026-11-16.txt   (capture)
#
# Exit code = NUMBER OF FAILED CLAIMS (0 = all held).
#
# Usage: bash docs/evidence/cron-manifest-repair-correctness-2026-11-16.sh [repo-root]
set -u

ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="${FLEET_PY:-${HOME}/.steadfaste-fleet/venv/bin/python}"
[ -x "${PY}" ] || PY=python3

fails=0
ok()  { printf '  PASS  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

echo "repo   : ${ROOT}"
echo "python : ${PY}"
echo

# --- claim 1: the invariant tests exist and the suite is green --------------
TEST="${ROOT}/tests/test_cron_manifest_repair.py"
if [ -f "${TEST}" ] && grep -q 'test_repair_never_creates_what_check_refuses_to_report' "${TEST}"; then
  ok "invariant test present: test_repair_never_creates_what_check_refuses_to_report"
else
  bad "invariant test MISSING from ${TEST}"
fi
LOG="$(mktemp)"
if "${PY}" -m pytest "${TEST}" "${ROOT}/tests/test_cron_manifest_matches_installer.py" \
        "${ROOT}/tests/test_retired_orch_crons.py" -q >"${LOG}" 2>&1; then
  ok "cron suites green: $(grep -oE '[0-9]+ passed[^,]*' "${LOG}" | head -1)"
else
  bad "suite RED: $(tail -3 "${LOG}" | tr '\n' ' ')"
fi
rm -f "${LOG}"
echo

# --- claim 2: the LIVE dry-run creates nothing and skips the bridge set ----
OUT="$("${PY}" "${ROOT}/ops/scripts/manage/cron_manifest.py" --repair --dry-run 2>&1)"
creates="$(printf '%s\n' "${OUT}" | grep -cE '^(WOULD-CREATE|CREATED) ' || true)"
skips="$(printf '%s\n'   "${OUT}" | grep -cE '^SKIPPED-BRIDGE ' || true)"
errors="$(printf '%s\n'  "${OUT}" | grep -cE '^ERROR ' || true)"
if [ "${creates}" = "0" ]; then
  ok "live --repair --dry-run creates 0 crons (was 48) — creates=${creates} skips=${skips} errors=${errors}"
else
  bad "live --repair --dry-run still wants to create ${creates} crons"
fi
if [ "${skips}" -gt 0 ]; then
  ok "bridge-owned crons are SKIPPED and VISIBLE, not silently dropped (${skips} named)"
else
  bad "no SKIPPED-BRIDGE lines — the bridge guard is not reporting what it skips"
fi
echo

# --- claim 3: load_manifest fails closed (issue #291) -----------------------
CLOSED="$("${PY}" - "${ROOT}" <<'PY'
import importlib.util, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("cm", root / "ops/scripts/manage/cron_manifest.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
bad = Path(tempfile.mkdtemp()) / "bad.yaml"
bad.write_text("crons:\n  - name: 'unterminated\n   scope: [unclosed\n")
try:
    m.load_manifest(bad)
    print("READ_AS_EMPTY")          # the silent no-op class
except Exception as e:
    print("RAISED" if "manifest" in str(e).lower() else f"RAISED_UNHELPFUL:{e}")
PY
)"
case "${CLOSED}" in
  RAISED) ok "load_manifest fails closed on an unparsable manifest (issue #291 fixed)" ;;
  READ_AS_EMPTY) bad "load_manifest still reads an unparsable manifest as EMPTY — the silent no-op class survives" ;;
  *) bad "load_manifest raised an unhelpful error: ${CLOSED}" ;;
esac

echo
echo "failed claims: ${fails}"
exit "${fails}"
