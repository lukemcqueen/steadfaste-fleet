#!/usr/bin/env bash
# Evidence verifier — config hardening in poll-bus-inbox_titus.sh (cycle 14858).
#
# READ-ONLY: repairs nothing, changes nothing. Its OWN exit code is the NUMBER OF
# FAILED CLAIMS (never a trailing echo, which would always exit 0), so rc=0 is
# information a reader can trust.
#
#   bash docs/evidence/poll-bus-inbox-config-hardening-2026-11-16.sh; echo rc=$?
#
# Every claim is re-runnable: the pre-fix script is read straight out of git at
# the range base, so the RED claim is replayable by anyone.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${FLEET_VENV_PYTHON:-$HOME/.steadfaste-fleet/venv/bin/python}"
[ -x "$PY" ] || PY=python3
SCRIPT_REL="poll-bus-inbox_titus.sh"
TEST_REL="tests/test_poll_bus_inbox_config.py"
BASE="$(cd "$REPO" && git merge-base origin/main HEAD)"
FAILED=0

claim() { # claim <description> <0|1>
  if [ "$2" -eq 0 ]; then printf 'PASS  %s\n' "$1"
  else printf 'FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); fi
}

echo "== range base: $BASE"
printf '== interpreter: %s\n' "${PY/$HOME/<HOME>}"   # normalised: the repo is public

# ── 1. the strict reader is what parses both credential fields ───────────────
grep -q 'conf_value FLEET_BUS_URL' "$REPO/$SCRIPT_REL" &&
  grep -q 'conf_value FLEET_BASIC_AUTH' "$REPO/$SCRIPT_REL"
claim "1. both credential fields are read through conf_value() (no bare awk substitution)" $?

# ── 2. the script is syntactically valid ─────────────────────────────────────
bash -n "$REPO/$SCRIPT_REL"
claim "2. bash -n clean" $?

# ── 3. GREEN: the battery passes against the current script ──────────────────
# NB: pytest -q's summary line is `==== 16 passed in 7.35s ====`, so an anchored
# `^[0-9]+ passed` never matches — that anchor made this claim FAIL while the
# battery was green (a probe bug, not a code bug). Match the counts unanchored.
out="$(cd "$REPO" && "$PY" -m pytest "$TEST_REL" -q 2>&1 || true)"
printf '%s\n' "$out" | tail -1
printf '%s' "$out" | grep -qE '[0-9]+ passed' && ! printf '%s' "$out" | grep -qE '[0-9]+ (failed|error)'
claim "3. battery passes on the CURRENT script" $?

# ── 4. RED: the SAME battery fails at the pre-fix revision ───────────────────
# A before/after claim is worth nothing unless the baseline reproduces the
# defect; read it from git so the comparison cannot be faked by a local edit.
PREFIX="$(mktemp -d)/$SCRIPT_REL"   # real basename: a suffix-less temp path loads as empty
(cd "$REPO" && git show "$BASE:$SCRIPT_REL") > "$PREFIX" 2>/dev/null
if [ -s "$PREFIX" ]; then
  red="$(cd "$REPO" && POLL_BUS_SCRIPT="$PREFIX" "$PY" -m pytest "$TEST_REL" -q 2>&1 || true)"
  printf '%s\n' "$red" | tail -1
  printf '%s' "$red" | grep -q ' failed'
  claim "4. the same battery FAILS at the pre-fix revision (baseline reproduces the defect)" $?
else
  claim "4. the same battery FAILS at the pre-fix revision (baseline reproduces the defect)" 1
fi

# ── 5. a duplicated key is refused, naming the field ─────────────────────────
tmpd="$(mktemp -d)"
printf 'FLEET_BUS_URL=http://127.0.0.1:9\nFLEET_BUS_URL=http://127.0.0.1:9\nFLEET_BASIC_AUTH=u:p\n' > "$tmpd/dup.conf"
dup="$(FLEET_INBOX_CONF="$tmpd/dup.conf" bash "$REPO/$SCRIPT_REL" 2>&1 || true)"
printf '%s\n' "$dup" | grep -q 'FLEET_BUS_URL'
claim "5. duplicated key refused with the FIELD named (was: curl (3) URL rejected, no field)" $?

# ── 6. a CRLF-edited conf is tolerated (no stray CR) ────────────────────────
printf 'FLEET_BUS_URL=http://127.0.0.1:9\r\nFLEET_BASIC_AUTH=u:p\r\n' > "$tmpd/crlf.conf"
crlf="$(FLEET_INBOX_CONF="$tmpd/crlf.conf" bash "$REPO/$SCRIPT_REL" 2>&1 || true)"
! printf '%s' "$crlf" | grep -q $'\r'
claim "6. a CRLF conf yields no stray CR in the value (was: CR echoed into the URL)" $?

# ── 7. no credential literal was introduced ─────────────────────────────────
! grep -qE '(ghp_|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})' "$REPO/$SCRIPT_REL" "$REPO/$TEST_REL"
claim "7. no credential-shaped literal in either changed file" $?

# ── 8. static gate ──────────────────────────────────────────────────────────
if [ -f "$REPO/ops/scripts/quality/adversarial-verify.py" ]; then
  a4="$(cd "$REPO" && "$PY" ops/scripts/quality/adversarial-verify.py --level A4 --gate --file "$SCRIPT_REL" 2>&1 || true)"
  printf '%s' "$a4" | grep -q 'GATE_PASSED'
  claim "8. A4 adversarial gate passes" $?
else
  claim "8. A4 adversarial gate passes" 1
fi

rm -rf "$tmpd"
printf -- '--\nfailed claims: %d\n' "$FAILED"
exit "$FAILED"
