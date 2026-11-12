#!/usr/bin/env bash
# fleet-env-compat.sh — CORTEX_* <-> FLEET_* bi-directional mirror (env migration P4).
#
# SAFETY layer for the CORTEX_* -> FLEET_* rename. Source this AFTER the canonical
# env (~/steadfaste-fleet/.env) has been loaded (set -a; source ...; set +a), then:
#   - every CORTEX_<name> that is set is ALSO exported as FLEET_<name>, so code
#     that reads the new canonical FLEET_ name keeps working on hosts that have
#     not repointed their .env yet;
#   - every FLEET_<name> that is set is ALSO exported as CORTEX_<name>, so legacy
#     readers that still reference CORTEX_<name> keep working after a host has
#     repointed to FLEET_.
# FLEET_ is canonical and wins when both spellings carry a value.
#
# This is the shell twin of python fleet_lib.env.get(). When the migration is
# complete (release 2) this file and the python CORTEX_ fallback are removed.
# Behavior is pinned by the committed hermetic test tests/test_fleet_env_compat.sh
# (run evidence: docs/evidence/fleet-env-compat-test-2026-11-13.txt).
#
# This file is deliberately not exec'd on its own; it is meant to be sourced.

_cortex_fleet_compat() {
  local name val twin_name
  # CORTEX_ -> FLEET_ (keep new FLEET_ readers working on not-yet-repointed hosts)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    val="${!name:-}"                       # ${name} is already the full "CORTEX_<suffix>"
    twin_name="FLEET_${name#CORTEX_}"
    if [ -n "$val" ] && [ -z "${!twin_name:-}" ]; then
      export "${twin_name}=${val}"
    fi
  done < <(env | sed -n 's/^\(CORTEX_[A-Z0-9_][A-Z0-9_]*\)=.*/\1/p')
  # FLEET_ -> CORTEX_ (keep legacy CORTEX_ readers working on repointed hosts)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    val="${!name:-}"                       # ${name} is the full "FLEET_<suffix>"
    twin_name="CORTEX_${name#FLEET_}"
    if [ -n "$val" ] && [ -z "${!twin_name:-}" ]; then
      export "${twin_name}=${val}"
    fi
  done < <(env | sed -n 's/^\(FLEET_[A-Z0-9_][A-Z0-9_]*\)=.*/\1/p')
}

_cortex_fleet_compat
