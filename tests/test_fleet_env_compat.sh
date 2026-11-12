#!/usr/bin/env bash
# Hermetic tests for lib/fleet-env-compat.sh (CORTEX_* <-> FLEET_* mirror, env migration P4).
#
# The shell twin of fleet_lib.env's dual-read: sourced AFTER the canonical env loads,
# it must (1) export FLEET_<name> from a CORTEX_<name> the host still sets, so new
# FLEET_ readers keep working on not-yet-repointed hosts; (2) export CORTEX_<name>
# from an FLEET_<name> a repointed host sets, so legacy readers keep working; and
# (3) never clobber either spelling that is explicitly set (FLEET_ is canonical and
# wins only in the sense that it is never overwritten by the legacy value).
# All probe vars are TEST-SPECIFIC (COMPAT_) so no ambient host env is touched.
#
# Run: bash tests/test_fleet_env_compat.sh
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPAT="$REPO/ops/scripts/lib/fleet-env-compat.sh"
pass=0; fails=0
ok()  { pass=$((pass+1)); echo "  ok  $1"; }
bad() { fails=$((fails+1)); echo "  FAIL $1"; }

if [ ! -f "$COMPAT" ]; then bad "compat file missing: $COMPAT"; exit 1; fi

# The compat iterates EVERY CORTEX_/FLEET_ var in the environment (env | sed), so
# ambient leftovers on a host WOULD be mirrored and could confuse assertions.
# Cleanse all test-prefixed vars before each phase.
_cleanse_compat() {
  local name
  while IFS= read -r name; do [ -n "$name" ] && unset "$name"; done \
    < <(env | sed -n 's/^\(CORTEX_COMPAT_[A-Z0-9_]*\|FLEET_COMPAT_[A-Z0-9_]*\)=.*/\1/p')
}
_cleanse_compat

# (1) CORTEX_ -> FLEET_ mirror
export CORTEX_COMPAT_P1="legacy-value-a"
source "$COMPAT"
if [ "${FLEET_COMPAT_P1:-}" = "legacy-value-a" ]; then ok "CORTEX_COMPAT_P1 mirrored to FLEET_COMPAT_P1"; else bad "C->F mirror (FLEET_COMPAT_P1='${FLEET_COMPAT_P1:-}')"; fi
unset CORTEX_COMPAT_P1 FLEET_COMPAT_P1

# (2) FLEET_ -> CORTEX_ mirror (repointed host)
export FLEET_COMPAT_P2="new-value-b"
source "$COMPAT"
if [ "${CORTEX_COMPAT_P2:-}" = "new-value-b" ]; then ok "FLEET_COMPAT_P2 mirrored to CORTEX_COMPAT_P2"; else bad "F->C mirror (CORTEX_COMPAT_P2='${CORTEX_COMPAT_P2:-}')"; fi
unset CORTEX_COMPAT_P2 FLEET_COMPAT_P2

# (3) both set: neither spelling is clobbered; FLEET_ stays canonical
export CORTEX_COMPAT_P3="explicit-c"
export FLEET_COMPAT_P3="canonical-d"
source "$COMPAT"
if [ "${FLEET_COMPAT_P3:-}" = "canonical-d" ]; then ok "FLEET_COMPAT_P3 keeps its explicit value under both-set"; else bad "FLEET clobbered ('${FLEET_COMPAT_P3:-}')"; fi
if [ "${CORTEX_COMPAT_P3:-}" = "explicit-c" ]; then ok "CORTEX_COMPAT_P3 keeps its explicit legacy value under both-set"; else bad "CORTEX clobbered ('${CORTEX_COMPAT_P3:-}')"; fi
unset CORTEX_COMPAT_P3 FLEET_COMPAT_P3

echo
echo "== RESULT: $pass ok, $fails failed =="
[ "$fails" -eq 0 ]
exit $?
