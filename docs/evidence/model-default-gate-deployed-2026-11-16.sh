#!/usr/bin/env bash
# model-default-gate-deployed-2026-11-16.sh — READ-ONLY verifier for the
# DEPLOYED tree (not the repo copy). Exit code = number of FAILED claims.
#
# The repo copy passing proves nothing about what ships: a check can be written
# against the repo layout and be structurally unable to run where it lands (a
# module the deploy never copies, a path that resolves differently). So the gate
# is exercised on the deployed tree, in both directions.
#
#   1. The deployed fleet_doctor/checks.py carries the new check.
#   2. The deployed entry point runs at all (imports resolve — not a crash).
#   3. A LABELLED `HERMES_HOME` fixture makes the DEPLOYED doctor report a FAIL
#      that names the label AND carries the executable remedy.
#   4. The REAL home produces no `Model default` line at all — healthy is silent.
set -uo pipefail

DEPLOY="${FLEET_DEPLOY_HOME:-$HOME/.steadfaste-fleet}"
DOCTOR="$DEPLOY/scripts/fleet-doctor.py"
CHECKS="$DEPLOY/scripts/fleet_doctor/checks.py"
FAILED=0
LOG="$(mktemp)"
fx="$(mktemp -d)"
trap 'rm -f "$LOG"; rm -rf "$fx"' EXIT
ok()  { printf '  PASS  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); }

echo "== 1. deployed checks.py carries the gate =="
if grep -q 'def check_model_default' "$CHECKS" 2>/dev/null; then
  ok "deployed $(basename "$CHECKS") defines check_model_default"
else
  bad "check_model_default is NOT on the deployed tree ($CHECKS)"
fi

echo "== 2. deployed doctor runs (imports resolve) =="
HERMES_HOME="$HOME/.hermes" python3 "$DOCTOR" >"$LOG" 2>&1
rc=$?
if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || [ "$rc" -eq 2 ]; then
  ok "deployed doctor executed (rc=$rc — defined verdict code, not a crash)"
else
  bad "deployed doctor exited rc=$rc (unexpected; $(tail -1 "$LOG"))"
fi
if grep -qi 'Traceback' "$LOG"; then
  bad "deployed doctor raised a traceback: $(grep -m1 -A2 'Traceback' "$LOG" | tr '\n' ' ')"
else
  ok "no traceback in the deployed doctor run"
fi

echo "== 3. the deployed gate FIRES on a labelled fixture =="
printf 'model:\n  default: openrouter:qwen/qwen3-coder-30b-a3b-instruct\n  provider: openrouter\n' > "$fx/config.yaml"
HERMES_HOME="$fx" python3 "$DOCTOR" >"$LOG" 2>&1
line="$(grep -m1 'Model default' "$LOG")"
case "$line" in
  *"routing LABEL"*) ok "deployed doctor reports the label: $(printf '%s' "$line" | cut -c1-120)..." ;;
  *) bad "deployed doctor did not report the label (got: ${line:-nothing})" ;;
esac
if grep -q 'hermes config set model.default qwen/qwen3-coder-30b-a3b-instruct' "$LOG"; then
  ok "the reported remedy is the executable bare-id fix"
else
  bad "no executable remedy in the deployed verdict"
fi

echo "== 4. the real home is silent (nothing to report) =="
HERMES_HOME="$HOME/.hermes" python3 "$DOCTOR" >"$LOG" 2>&1
if grep -q 'Model default' "$LOG"; then
  bad "the real home reports 'Model default' — expected silence on a bare id"
else
  ok "real home: no 'Model default' line (silent, as designed)"
fi

echo
echo "FAILED CLAIMS: $FAILED"
exit "$FAILED"
