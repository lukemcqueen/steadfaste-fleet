#!/usr/bin/env bash
# Evidence: deploy ownership inventory + provenance-based orphan reclaim.
#
# Proves the load-bearing invariant on a REAL sandbox deploy tree, not a mock:
# a file WE deployed and no longer deploy is quarantined (restorable), while a
# host-local file that appears in the previous inventory is still never moved.
#
# Usage: bash docs/evidence/deploy-orphan-reclaimer-2026-11-17.sh
# Exit 0 = all assertions held.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
PY="${FLEET_PYTHON:-python3}"
RECLAIM="$REPO/ops/scripts/manage/reclaim-orphaned-deploys.py"
FAILED=0

say() { printf '\n=== %s ===\n' "$*"; }
ok()  { printf '  PASS  %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; FAILED=$((FAILED+1)); }

say "revision"
git rev-parse HEAD
git log -1 --format='%h %s'

say "helper is stdlib-only (needs no capability resolver)"
if grep -Eq '^(import|from) ' "$RECLAIM" | true; then
  if grep -E '^(import|from) ' "$RECLAIM" | grep -Evq 'argparse|datetime|os|shutil|sys|pathlib|__future__'; then
    bad "third-party import found — this must go through python-with-module.sh"
  else
    ok "stdlib-only"
  fi
fi

say "unit suite (negative cases included)"
if "$PY" -m pytest tests/test_reclaim_orphaned_deploys.py -q 2>&1 | tail -3; then
  ok "suite ran"
else
  bad "suite failed"
fi

say "end-to-end on a real sandbox deploy tree"
SB="$(mktemp -d)"; H="$SB/deploy"
mkdir -p "$H/scripts" "$H/state" "$H/config"
echo old          > "$H/scripts/jev-old-thing.py"     # ours once, renamed away -> orphan
echo hostlocal    > "$H/scripts/local-keep.sh"        # must NEVER be moved
echo '{}'         > "$H/config/router-models.json"    # the current deploy
# Worst case: the previous inventory claims BOTH the orphan and the host-local file.
printf '%s\n%s\n' "$H/scripts/jev-old-thing.py" "$H/scripts/local-keep.sh" > "$H/state/deployed-files.txt"
printf '%s\n'     "$H/config/router-models.json" > "$H/state/new.txt"

out="$("$PY" "$RECLAIM" --inventory "$H/state/deployed-files.txt" \
        --deployed "$H/state/new.txt" --deploy-home "$H" 2>&1)"; rc=$?
printf '%s\n' "$out" | sed 's/^/  | /'
[[ $rc -eq 0 ]] && ok "reclaim exited 0" || bad "reclaim exited $rc"

[[ -f "$H/scripts/local-keep.sh" ]] \
  && ok "host-local local-keep.sh SURVIVED (the invariant that makes this safe unattended)" \
  || bad "host-local file was moved — DATA LOSS CLASS"

[[ ! -f "$H/scripts/jev-old-thing.py" ]] && ok "orphan left the deploy tree" || bad "orphan still present"
q="$(find "$H/state/deploy-quarantine" -name 'jev-old-thing.py' 2>/dev/null | head -1)"
if [[ -n "$q" && "$(cat "$q")" == "old" ]]; then
  ok "orphan quarantined with content intact (restorable): ${q#$SB/}"
else
  bad "orphan not quarantined, or content lost"
fi
grep -q "restore: mv" <<<"$out" && ok "restore command printed" || bad "no restore command printed"
[[ "$(cat "$H/state/deployed-files.txt")" == "$H/config/router-models.json" ]] \
  && ok "inventory advanced to exactly what we now own" || bad "inventory wrong"
[[ -f "$H/state/deployed-files.txt.prev" ]] && ok "previous inventory archived for audit" || bad "no .prev archive"

say "first-run safety: no previous inventory must be a NO-OP"
SB2="$(mktemp -d)"; H2="$SB2/deploy"; mkdir -p "$H2/scripts" "$H2/state"
echo deployed > "$H2/scripts/agent-thing.py"
printf '%s\n' "$H2/config/nothing" > "$H2/state/new.txt"
"$PY" "$RECLAIM" --inventory "$H2/state/deployed-files.txt" --deployed "$H2/state/new.txt" \
  --deploy-home "$H2" >/dev/null 2>&1
[[ -f "$H2/scripts/agent-thing.py" ]] \
  && ok "nothing swept without memory — pre-existing files are the operator's call" \
  || bad "first run swept files it could not prove were ours"
# Correct behaviour is to WRITE the baseline inventory even when there is
# nothing to reclaim: that first write IS the memory every later run depends on.
[[ -f "$H2/state/deployed-files.txt" ]] \
  && ok "baseline inventory written on the first run (the memory later runs need)" \
  || bad "first run did not establish the ownership baseline"

say "deploy wires it on EVERY deploy (not force-all-only)"
if awk '/reclaim-orphaned-deploys\.py/{found=1} END{exit !found}' ops/scripts/fleet-update.sh; then
  ok "deploy calls the reclaimer"
else
  bad "deploy never calls the reclaimer"
fi
bash -n ops/scripts/fleet-update.sh && ok "fleet-update.sh parses" || bad "fleet-update.sh syntax error"

rm -rf "$SB" "$SB2"
say "result"
if [[ $FAILED -eq 0 ]]; then echo "VERIFIER: ALL CHECKS PASSED"; exit 0; fi
echo "VERIFIER: $FAILED CHECK(S) FAILED"; exit 1
