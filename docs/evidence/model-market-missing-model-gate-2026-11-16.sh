#!/usr/bin/env bash
# READ-ONLY verifier for the model-market missing-model gate.
#
#   docs/evidence/model-market-missing-model-gate-2026-11-16.txt  (capture)
#
# Measures, never repairs. Exit code = NUMBER OF FAILED CLAIMS, so rc=0 is a
# claim a reader can trust (never a trailing `echo`, which always exits 0).
#
# Usage: bash docs/evidence/model-market-missing-model-gate-2026-11-16.sh [repo-root]
set -u

ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="${FLEET_PY:-${HOME}/.steadfaste-fleet/venv/bin/python}"
[ -x "${PY}" ] || PY=python3
SCAN="${ROOT}/ops/scripts/manage/orch-model-market-scan.py"
TEST="${ROOT}/tests/test_model_market_scan.py"
LOG="$(mktemp)"

fails=0
ok()  { printf '  PASS  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

echo "repo   : ${ROOT}"
echo "python : ${PY}"
echo

# --- claim 1: the regression test exists and the suite is green -------------
if grep -q 'def test_scan_missing_model_fails_closed' "${TEST}"; then
  ok "regression test test_scan_missing_model_fails_closed present"
else
  bad "regression test test_scan_missing_model_fails_closed MISSING from ${TEST}"
fi
if "${PY}" -m pytest "${TEST}" -q >"${LOG}" 2>&1; then
  ok "suite green: $(grep -oE '[0-9]+ passed[^,]*' "${LOG}" | head -1)"
else
  bad "suite RED: $(tail -3 "${LOG}" | tr '\n' ' ')"
fi
echo

# --- claims 2 and 3: BEHAVIOUR, both directions -----------------------------
# A controlled probe drives the real entrypoint with a synthetic catalog, so the
# verdict does not depend on the live network or on ambient host state.
probe() { # $1 = include the unknown model? $2 = scanner to drive (default: the repo copy)
  "${PY}" - "${2:-${SCAN}}" "$1" <<'PY'
import importlib.util, json, os, sys, tempfile
from pathlib import Path

scan_path, include_ghost = sys.argv[1], sys.argv[2] == "yes"
spec = importlib.util.spec_from_file_location("scan_under_test", scan_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

catalog = {"fakeprov": {"models": {
    "good":  {"cost": {"input": 0.1, "output": 0.2}},   # blended 0.30 — pool head
    "gamma": {"cost": {"input": 0.4, "output": 0.5}},   # blended 0.90 — the baseline
}}}
doc = {
    "captured": "probe",
    # baseline must be DEARER than the pool head, or the pre-existing
    # 'head not below baseline' rule fires and the probe measures the wrong thing.
    "baseline": "openrouter:fakeprov/gamma",
    "policy": {"hard": {"max_price_usd_per_m": 3.0, "whitelist_only": False}},
    "prices": {"openrouter:fakeprov/good": {"in": 0.1, "out": 0.2},
               "openrouter:fakeprov/gamma": {"in": 0.4, "out": 0.5}},
    "tiers": {"medium": {"coding": ["openrouter:fakeprov/good"]}},
}
if include_ghost:
    doc["prices"]["openrouter:fakeprov/ghost"] = {"in": 0.01, "out": 0.01}
    doc["tiers"]["medium"]["coding"].append("openrouter:fakeprov/ghost")

tmp = Path(tempfile.mkdtemp())
list_path, state_path = tmp / "list.json", tmp / "state.json"
list_path.write_text(json.dumps(doc), encoding="utf-8")
mod.default_list = lambda: list_path
mod.get_catalog = lambda: (catalog, "synthetic")
mod.STATE_PATH = str(state_path)

rc = mod.main(["--json"])
state = json.loads(state_path.read_text(encoding="utf-8"))
print(f"rc={rc} missing={state['missing_models']} violations={state['violations']}")
PY
}

ghost_out="$(probe yes)"
case "${ghost_out}" in
  *"rc=3"*"ghost"*"not in the live catalog"*)
    ok "a model absent from the live catalog fails CLOSED and is named: ${ghost_out}" ;;
  *)
    bad "missing model did NOT fail closed: ${ghost_out}" ;;
esac

clean_out="$(probe no)"
case "${clean_out}" in
  *"rc=0"*"missing=[]"*"violations=[]"*)
    ok "no missing model -> unchanged, no over-firing: ${clean_out}" ;;
  *)
    bad "the gate over-fires on a clean list: ${clean_out}" ;;
esac

# --- claim 4: the BEFORE side, pinned to an immutable revision --------------
# A fix is only a fix against a measured baseline: drive the SAME probe at the
# pre-fix revision of the scanner (the range base) and show the defect is there.
# Info finding ADV-14828-1: the RED transcript lived only in a terminal and could
# not be replayed; pinning it to a sha makes the transition reproducible.
base="${MM_BASE:-$(git -C "${ROOT}" merge-base origin/main HEAD 2>/dev/null || true)}"
if [ -z "${base}" ]; then
  printf '  SKIP  no git range base resolvable (set MM_BASE=<sha> to run the before-and-after claim)\n'
else
  before="$(mktemp -d)/orch-model-market-scan.py"
  if git -C "${ROOT}" show "${base}:ops/scripts/manage/orch-model-market-scan.py" >"${before}" 2>/dev/null; then
    before_out="$(probe yes "${before}" 2>&1)"
    case "${before_out}" in
      *"rc=1"*"ghost"*)
        ok "pre-fix ${base} reproduces the defect (rc 1, dead id on the apply path): ${before_out}" ;;
      *)
        bad "pre-fix ${base} did NOT reproduce the defect — the after-side proves nothing: ${before_out}" ;;
    esac
  else
    bad "cannot read ${base}:ops/scripts/manage/orch-model-market-scan.py from git"
  fi
  rm -f "${before}"
fi

rm -f "${LOG}"
echo
echo "failed claims: ${fails}"
exit "${fails}"
