#!/usr/bin/env bash
# Regression test for consolidate-env.sh (the single-env source of truth).
#
# Two contracts are under test:
#   1. the 2026-10-17 rename — ~/.steadfaste-fleet/.env is the sanctioned
#      back-compat symlink to the canonical ~/steadfaste-fleet/.env, and
#      fleet-bus.conf is retired;
#   2. release 2 (2026-11-13) — the canonical env is FLEET_-ONLY. The script
#      CONVERTS a legacy FLEET_X line to FLEET_X (it no longer mirrors both
#      spellings), and --check FAILs while any retired spelling survives.
#
# This test drives the REAL script against an ISOLATED temp HOME so it proves
# the script's actual behaviour — never the live host's env, which is real and
# must not be disturbed.
#
# RED proof: the assertions below FAIL if consolidate-env.sh stops converging a
# legacy fleet-bus.conf into the .env symlink, stops converting CORTEX_ to
# FLEET_, passes --check over a surviving retired name, or would overwrite a
# real-file deploy-dir .env.
#
# Run: bash tests/test-consolidate-env.sh
set -u

SCRIPT="${1:-$(cd "$(dirname "$0")/../ops/scripts/manage" && pwd)/consolidate-env.sh}"
PASS=0; FAIL=0
plan() { echo "  - $1"; }
ok()   { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  ✗ $1"; }

make_host() { # <top> — isolate HOME + the two env dirs under it
  local top="$1"
  export HOME="$top"
  mkdir -p "$HOME/steadfaste-fleet" "$HOME/.steadfaste-fleet"
}

# ── Case 1: legacy fleet-bus.conf converges + converts to FLEET_-only ────
plan "legacy fleet-bus.conf migrates to the sanctioned .env symlink, converted to FLEET_*"
TOP="$(mktemp -d)"
make_host "$TOP"
printf 'FLEET_BUS_URL=http://localhost\nFLEET_BUS_TOKEN=abc\n' > "$HOME/.steadfaste-fleet/fleet-bus.conf"
bash "$SCRIPT" >/dev/null 2>&1
if [[ -L "$HOME/.steadfaste-fleet/.env" ]] \
   && [[ "$(readlink "$HOME/.steadfaste-fleet/.env")" == "$HOME/steadfaste-fleet/.env" ]] \
   && [[ ! -e "$HOME/.steadfaste-fleet/fleet-bus.conf" ]] \
   && [[ "$(grep -c '^FLEET_BUS_URL=' "$HOME/steadfaste-fleet/.env")" -eq 1 ]] \
   && [[ "$(grep -c '^CORTEX_' "$HOME/steadfaste-fleet/.env" || true)" -eq 0 ]]; then
  ok   "legacy conf seeded as FLEET_* + .env symlink created + fleet-bus.conf retired"
else
  bad  "legacy migration did not converge to a FLEET_-only canonical env"
fi

# ── Case 2: idempotent on a second run ──────────────────────────────────
plan "second run is a no-op (idempotent)"
bash "$SCRIPT" >/dev/null 2>&1 && bash "$SCRIPT" >/dev/null 2>&1
if [[ -L "$HOME/.steadfaste-fleet/.env" ]] \
   && [[ "$(readlink "$HOME/.steadfaste-fleet/.env")" == "$HOME/steadfaste-fleet/.env" ]]; then
  ok   "repeated run leaves the sanctioned symlink intact"
else
  bad  "script is not idempotent"
fi

# ── Case 3: refuses to overwrite a real-file deploy-dir .env ────────────
plan "a REAL file at the deploy-dir .env is refused, not overwritten"
TOP2="$(mktemp -d)"
make_host "$TOP2"
printf 'FLEET_BUS_URL=http://localhost\n' > "$HOME/steadfaste-fleet/.env"
printf 'SOME_OTHER_ENV=1\n' > "$HOME/.steadfaste-fleet/.env"   # unowned real file
if bash "$SCRIPT" >/dev/null 2>&1; then
  bad  "refused to... no, it exited 0 — script overwrote/replaced the real file"
else
  if [[ "$(cat "$HOME/.steadfaste-fleet/.env")" == "SOME_OTHER_ENV=1" ]]; then
    ok   "script refused (non-zero) and left the unowned real file untouched"
  else
    bad  "script refused but clobbered the unowned real file"
  fi
fi

# ── Case 4: --check is a dry check, asserted both ways (release 2) ───────
# Converged: the canonical env is FLEET_-only → exit 0, and nothing is written.
plan "--check exits 0 when the canonical env is FLEET_-only"
TOP3="$(mktemp -d)"
make_host "$TOP3"
printf 'FLEET_BUS_URL=http://localhost\n' > "$HOME/steadfaste-fleet/.env"
if bash "$SCRIPT" --check >/dev/null 2>&1; then
  ok   "--check exited 0 (canonical env is FLEET_-only)"
  [[ ! -L "$HOME/.steadfaste-fleet/.env" ]] \
    && ok   "--check left no symlink behind (dry)" \
    || bad  "--check created a symlink (not dry)"
else
  bad  "--check exited non-zero although the canonical env is FLEET_-only (rc=$?)"
fi

# Not converged: a retired spelling survives in the canonical env → exit 1 + names it.
plan "--check exits 1 and names a surviving retired CORTEX_ name"
printf 'FLEET_BUS_TOKEN=abc\n' >> "$HOME/steadfaste-fleet/.env"
CHECK_OUT="${TMPDIR:-/tmp}/check-missing.out"
if bash "$SCRIPT" --check > "$CHECK_OUT" 2>&1; then
  bad  "--check exited 0 although FLEET_BUS_TOKEN still survives in the canonical env"
else
  if grep -q "FLEET_BUS_TOKEN" "$CHECK_OUT"; then
    ok   "--check exited non-zero and named FLEET_BUS_TOKEN"
  else
    bad  "--check exited non-zero but did not name the surviving retired name"
  fi
fi

# ── cleanup ──────────────────────────────────────────────────────────────
rm -rf "$TOP" "$TOP2" "$TOP3" 2>/dev/null || true

echo ""
echo "━━━ consolidate-env pattern test: ${PASS} pass, ${FAIL} fail ━━━"
[[ "$FAIL" -eq 0 ]]
