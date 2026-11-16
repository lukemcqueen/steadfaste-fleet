#!/usr/bin/env bash
# model-default-routing-label-2026-11-16.sh — READ-ONLY evidence verifier.
#
# Exit code IS the number of FAILED claims (no trailing `echo`, which would
# always exit 0). Every claim is re-runnable on any host in the fleet.
#
#   1. config.yaml `model.default` is BARE (no `<label>:` prefix) — fix is live.
#   2. That bare id IS in the live catalog, and NO `openrouter:`-prefixed id is:
#      the label is not a model id, it is Jev's routing currency.
#   3. agent.log captures the real HTTP 400 for the label — the defect was LIVE,
#      not theoretical, and it was not retryable.
#   4. sessions: the newest row carries the BARE id, an older row the LABEL —
#      the before/after pair on this host's own history.
#   5. The doctor check exists AND is wired into the CLI that actually runs it
#      (a check nobody calls is a report, not a gate).
#   6. The new test battery is GREEN.
#   7. The live host is now SILENT — a healthy value needs no report.
#   8. ANTI-VACUITY: against a LABELLED fixture the SAME check fires exactly one
#      FAIL, so claim 7's silence is not an inert check.
set -uo pipefail

REPO="${FLEET_REPO:-$HOME/steadfaste-fleet}"
PY="${FLEET_VENV_PYTHON:-$HOME/.steadfaste-fleet/venv/bin/python}"
[ -x "$PY" ] || PY=python3
CLI="$REPO/ops/scripts/manage/fleet_doctor/cli.py"
CONFIG="$HOME/.hermes/config.yaml"
LOG="$HOME/.hermes/logs/agent.log"
FAILED=0
ok()  { printf '  PASS  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); }

echo "== 1. live model.default is a BARE id =="
cur="$(grep -m1 '^  default:' "$CONFIG" 2>/dev/null | sed 's/^  default:[[:space:]]*//')"
case "$cur" in
  "")  bad "could not read model.default from $CONFIG" ;;
  *:*) bad "model.default still carries a label prefix: $cur" ;;
  *)   ok "model.default = $cur (no label prefix)" ;;
esac

echo "== 2. the label is not a model id; the bare one is =="
cat_file="$(mktemp)"; fx="$(mktemp -d)"
trap 'rm -f "$cat_file"; rm -rf "$fx"' EXIT
curl -s --max-time 90 https://openrouter.ai/api/v1/models -o "$cat_file" 2>/dev/null || true
bare_hits="$(grep -o "\"id\":\"$cur\"" "$cat_file" 2>/dev/null | wc -l | tr -d ' ')"
label_hits="$(grep -o '"id":"openrouter:[^"]*"' "$cat_file" 2>/dev/null | wc -l | tr -d ' ')"
[ "${bare_hits:-0}" -ge 1 ] && ok "bare id present in live catalog ($bare_hits match)" \
  || bad "bare id '$cur' NOT found in the live catalog (bare_hits=$bare_hits)"
[ "${label_hits:-0}" -eq 0 ] && ok "zero 'openrouter:'-prefixed ids in the live catalog" \
  || bad "the live catalog DOES expose openrouter:-prefixed ids ($label_hits) — re-check the premise"

echo "== 3. the 400 was real and live (captured in this host's log) =="
n400="$(grep -c 'is not a valid model ID' "$LOG" 2>/dev/null)"
n400="${n400:-0}"
[ "$n400" -ge 1 ] && ok "agent.log holds $n400 'is not a valid model ID' line(s)" \
  || bad "no 400 captured in $LOG — claim cannot be substantiated"
grep -q 'not retryable' "$LOG" 2>/dev/null \
  && ok "the 400 is recorded as 'not retryable' (no silent auto-repair)" \
  || bad "'not retryable' marker absent"

echo "== 4. sessions show the before/after pair =="
pair="$("$PY" - "$HOME" <<'PYEOF' 2>/dev/null
import os, sqlite3, sys
home = sys.argv[1]
db = os.path.join(home, ".hermes", "state.db")
try:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = [r[0] or "" for r in con.execute("SELECT model FROM sessions ORDER BY rowid DESC LIMIT 60")]
except Exception:
    print("ERR"); raise SystemExit
newest = rows[0] if rows else ""
print("newest_bare=%s older_labelled=%s newest=%s"
      % ((":" not in newest and "/" in newest),
         any(r.startswith("openrouter:") for r in rows), newest))
PYEOF
)"
case "$pair" in
  *newest_bare=True*older_labelled=True*) ok "newest session is bare, an older one carries the label ($pair)" ;;
  *) bad "session pair not found: $pair" ;;
esac

echo "== 5. the check is wired into the doctor that runs it =="
wired="$(grep -c 'check_model_default' "$CLI" 2>/dev/null)"
wired="${wired:-0}"
[ "$wired" -ge 2 ] && ok "check_model_default appears $wired times in cli.py (import + registry)" \
  || bad "check_model_default appears only $wired time(s) in cli.py — not registered"

echo "== 6. the test battery is GREEN =="
tests="$(cd "$REPO" && "$PY" -m pytest tests/test_doctor_model_default_label.py -q 2>&1 || true)"
if printf '%s' "$tests" | grep -qE '[0-9]+ passed' && ! printf '%s' "$tests" | grep -qE '[0-9]+ (failed|error)'; then
  ok "$(printf '%s' "$tests" | grep -oE '[0-9]+ passed' | head -1)"
else
  bad "test battery not green: $(printf '%s' "$tests" | tail -2 | tr '\n' ' ')"
fi

echo "== 7. the live host is now silent (healthy value reports nothing) =="
live="$(cd "$REPO" && "$PY" - <<'PYEOF' 2>/dev/null
import sys
sys.path.insert(0, "ops/scripts/manage")
from fleet_doctor.results import Results
import fleet_doctor.checks as C
r = Results(); C.check_model_default(r)
print(r.checks)
PYEOF
)"
[ "$live" = "[]" ] && ok "live host verdicts: [] (silent)" || bad "live host reported: $live"

echo "== 8. anti-vacuity: the same check FIRES on a labelled fixture =="
mkdir -p "$fx"
printf 'model:\n  default: openrouter:qwen/qwen3-coder-30b-a3b-instruct\n  provider: openrouter\n' > "$fx/config.yaml"
fired="$(cd "$REPO" && HERMES_HOME="$fx" "$PY" - <<'PYEOF' 2>/dev/null
import sys
sys.path.insert(0, "ops/scripts/manage")
from fleet_doctor.results import Results
import fleet_doctor.checks as C
r = Results(); C.check_model_default(r)
print(len(r.checks), r.checks[0]["status"] if r.checks else "-")
PYEOF
)"
case "$fired" in
  "1 FAIL") ok "labelled fixture -> exactly one FAIL (the gate is load-bearing)" ;;
  *) bad "labelled fixture produced '$fired', expected '1 FAIL'" ;;
esac

echo
echo "FAILED CLAIMS: $FAILED"
exit "$FAILED"
