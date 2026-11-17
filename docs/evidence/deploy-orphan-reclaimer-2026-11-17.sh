#!/usr/bin/env bash
# Evidence: deploy ownership inventory + provenance-based orphan reclaim.
#
# Proves the load-bearing invariant on a REAL sandbox deploy tree, not a mock:
# a file WE deployed and no longer deploy is quarantined (restorable), while a
# host-local file that appears in the previous inventory is still never moved.
#
# Every check below is able to FAIL. The close-gate adversarial review (cycle
# 14933) caught two of them being unable to: the stdlib-only check used
# `grep ... | true`, so the outer condition was always true and it printed PASS
# for a file with no imports at all, and the suite check asserted only that
# pytest RAN, so an all-skipped suite could still go green. An evidence artifact
# whose checks cannot fail is a comment pretending to be a guard.
#
# Usage: bash docs/evidence/deploy-orphan-reclaimer-2026-11-17.sh
# Exit 0 = all assertions held.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
PY="${FLEET_PYTHON:-python3}"
RECLAIM="$REPO/ops/scripts/manage/reclaim-orphaned-deploys.py"
EXPECTED_TESTS=13
FAILED=0

say() { printf '\n=== %s ===\n' "$*"; }
ok()  { printf '  PASS  %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; FAILED=$((FAILED+1)); }

say "revision"
git rev-parse HEAD
git log -1 --format='%h %s'

say "helper is stdlib-only (needs no capability resolver)"
# Checked against sys.stdlib_module_names rather than a hand-written allowlist: a
# hand-written list silently mis-flags legitimate stdlib imports (json, re,
# tempfile, collections...) as third-party. It also fails on an EMPTY import set,
# so it can never print PASS for a file it did not actually inspect.
"$PY" - "$RECLAIM" <<'PYEOF'
import re, sys
from pathlib import Path
src = Path(sys.argv[1])
if not src.exists():
    print(f"  FAIL  {src} does not exist"); sys.exit(1)
mods = set()
for line in src.read_text().splitlines():
    m = re.match(r"\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
    if m:
        mods.add(m.group(1))
print(f"  imports found: {sorted(mods)}")
if not mods:
    print("  FAIL  no import lines parsed — the check cannot verify anything")
    sys.exit(1)
third = sorted(m for m in mods if m not in sys.stdlib_module_names)
if third:
    print(f"  FAIL  third-party import(s): {third} — this helper must route "
          "through python-with-module.sh instead")
    sys.exit(1)
print(f"  PASS  stdlib-only ({len(mods)} module(s), none third-party)")
PYEOF
[[ $? -eq 0 ]] || bad "stdlib-only check failed"

say "unit suite — the COUNT is asserted, not merely that it ran"
SUITE_OUT="$("$PY" -m pytest tests/test_reclaim_orphaned_deploys.py -q 2>&1)"; SUITE_RC=$?
printf '%s\n' "$SUITE_OUT" | tail -3 | sed 's/^/  | /'
[[ $SUITE_RC -eq 0 ]] && ok "pytest exited 0" || bad "pytest exited $SUITE_RC"
# Without a count, a suite that skips everything (or collects nothing) could
# produce a green evidence artifact — exit 0 with zero assertions executed.
if grep -qE "(^|[^0-9])${EXPECTED_TESTS} passed" <<<"$SUITE_OUT"; then
  ok "${EXPECTED_TESTS} tests passed (count asserted)"
else
  bad "expected '${EXPECTED_TESTS} passed' — got: $(tail -1 <<<"$SUITE_OUT")"
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

# PROOF IT ACTUALLY EXECUTED, asserted before any file-state claim: without it, a
# CLI/flag mismatch (argparse exits 2) would let the invariant checks below pass
# vacuously, because the reclaimer would simply never have touched anything.
grep -q "QUARANTINED" <<<"$out" \
  && ok "reclaimer reported a quarantine — it really executed" \
  || bad "no QUARANTINED line — the reclaimer did not do the work (flag/CLI mismatch?)"

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
# Correct behaviour is to WRITE the baseline inventory even when there is nothing
# to reclaim: that first write IS the memory every later run depends on.
[[ -f "$H2/state/deployed-files.txt" ]] \
  && ok "baseline inventory written on the first run (the memory later runs need)" \
  || bad "first run did not establish the ownership baseline"

say "deploy wires the reclaim OUTSIDE the force-all block"
# A bare grep passes on the historical bug too: the old warn-only scan lived
# under --force-all, and that placement is exactly what let orphans accumulate.
# So assert the call exists AND sits BEFORE the force-all branch.
CALL_LINE="$(grep -n 'reclaim-orphaned-deploys\.py' ops/scripts/fleet-update.sh | head -1 | cut -d: -f1)"
FORCE_LINE="$(grep -n 'Auto-clean stale deploy files after force-all' ops/scripts/fleet-update.sh | head -1 | cut -d: -f1)"
echo "  reclaim call at line: ${CALL_LINE:-<none>}; force-all block at line: ${FORCE_LINE:-<none>}"
if [[ -z "$CALL_LINE" ]]; then
  bad "deploy never calls the reclaimer"
elif [[ -n "$FORCE_LINE" && "$CALL_LINE" -gt "$FORCE_LINE" ]]; then
  bad "the reclaim call sits at/after the force-all block — it would only run on --force-all again"
else
  ok "reclaim call is outside the force-all block (line $CALL_LINE)"
fi
bash -n ops/scripts/fleet-update.sh && ok "fleet-update.sh parses" || bad "fleet-update.sh syntax error"

rm -rf "$SB" "$SB2"
say "result"
if [[ $FAILED -eq 0 ]]; then echo "VERIFIER: ALL CHECKS PASSED"; exit 0; fi
echo "VERIFIER: $FAILED CHECK(S) FAILED"; exit 1
