#!/usr/bin/env bash
# test_consolidate_env_dualspell.sh — CORTEX_X → FLEET_X converter (release 2)
#
# Drives materialize_dual_spellings() from consolidate-env.sh against temp
# fixtures (never the live .env): conversion, idempotence, conflict-abort,
# FLEET-only preservation, and a value containing '='.
set -uo pipefail

SRC="${1:-ops/scripts/manage/consolidate-env.sh}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dualspell.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# Extract materialize_dual_spellings() from the source (function's own close: a
# column-0 '}').
sed -n '/^materialize_dual_spellings()/,/^}/p' "$SRC" > "$TMP/fn.sh"
if ! grep -q '^materialize_dual_spellings() {' "$TMP/fn.sh"; then
  echo "FAIL: could not extract materialize_dual_spellings from $SRC"; exit 1
fi
{ printf 'warn(){ printf "WARN: %%s\\n" "$*" >&2; }\n'; cat "$TMP/fn.sh"; } > "$TMP/harness.sh"

pass=0; fail=0
chk() { # <name> <cond>
  if eval "$2"; then pass=$((pass+1)); echo "  ok: $1"; else fail=$((fail+1)); echo "  FAIL: $1"; fi
}

# T1 convert CORTEX -> FLEET (release 2) + idempotence
# One canonical CORTEX_DEPLOY_HOME only: the converter emits one FLEET_X per
# CORTEX_X (converted lines are NOT deduped), so a duplicate input would break
# the single-FLEET_DEPLOY_HOME idempotence assertion below.
printf 'CORTEX_DEPLOY_HOME=/srv/deploy\nCORTEX_BUS_ENDPOINT=VAL_A\n# c\n' > "$TMP/t1.env"
B=". '$TMP/harness.sh'; materialize_dual_spellings '$TMP/t1.env'"
bash -c "$B"
grep -q '^FLEET_DEPLOY_HOME=/srv/deploy$' "$TMP/t1.env"; chk "T1 FLEET_DEPLOY_HOME" "[ $? -eq 0 ]"
grep -q '^FLEET_BUS_ENDPOINT=VAL_A$' "$TMP/t1.env"; chk "T1 FLEET_BUS_ENDPOINT" "[ $? -eq 0 ]"
if grep -q '^CORTEX_DEPLOY_HOME=' "$TMP/t1.env"; then chk "T1 CORTEX dropped" "false"; else chk "T1 CORTEX dropped" "true"; fi
grep -q '# c' "$TMP/t1.env"; chk "T1 comment preserved" "[ $? -eq 0 ]"
bash -c "$B"  # idempotent
c1=$(grep -c '^FLEET_DEPLOY_HOME=' "$TMP/t1.env"); chk "T1 idempotent (one FLEET_DEPLOY_HOME, got $c1)" "[ \"$c1\" -eq 1 ]"

# T2 conflict aborts, file unchanged
printf 'CORTEX_BUS_TRUE=alpha\nFLEET_BUS_TRUE=beta\n' > "$TMP/t2.env"
before=$(cat "$TMP/t2.env")
bash -c ". '$TMP/harness.sh'; materialize_dual_spellings '$TMP/t2.env'"; rc=$?
chk "T2 conflict rc=1" "[ $rc -eq 1 ]"
chk "T2 conflict file unchanged" "[ \"$(cat "$TMP/t2.env")\" = \"$before\" ]"

# T3 FLEET-only preserved
printf 'FLEET_ONLY=x\nCORTEX_K=v\n' > "$TMP/t3.env"
bash -c ". '$TMP/harness.sh'; materialize_dual_spellings '$TMP/t3.env'"
grep -q '^FLEET_ONLY=x$' "$TMP/t3.env"; chk "T3 FLEET-only preserved" "[ $? -eq 0 ]"
grep -q '^FLEET_K=v$' "$TMP/t3.env"; chk "T3 CORTEX_K -> FLEET_K" "[ $? -eq 0 ]"

# T4 value containing '='
printf 'CORTEX_URL=a=b=c\n' > "$TMP/t4.env"
bash -c ". '$TMP/harness.sh'; materialize_dual_spellings '$TMP/t4.env'"
grep -q '^FLEET_URL=a=b=c$' "$TMP/t4.env"; chk "T4 '=' in value" "[ $? -eq 0 ]"

echo
echo "dualspell test: $pass pass, $fail fail"
[ "$fail" -eq 0 ]
