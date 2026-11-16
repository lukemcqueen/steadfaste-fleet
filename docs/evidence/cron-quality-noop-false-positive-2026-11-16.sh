#!/usr/bin/env bash
# Evidence: cron-quality watchdog no-op false positives (cycle 14863)
#
# Symptom reported by Luke: every Jev-enabled cron lane raised TWO alerts per run
#   "suspiciously short response ... — possible token garbage"
#   "session guard said ACTIVE but reply was ... — expected the skip token"
# because the healthy no-op reply carries the Jev routing annotation.
#
# Runs against BOTH the repo source and the DEPLOYED script, and against the LIVE
# cron output. Exit code == number of failed claims.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${HOME}/.steadfaste-fleet/venv/bin/python"
[ -x "$PY" ] || PY=python3
REPO_SRC="$REPO/ops/scripts/health/agent-cron-quality-watchdog.py"
DEPLOYED="$HOME/.steadfaste-fleet/scripts/agent-cron-quality-watchdog.py"

fails=0
claim() { # claim <desc> <expected> <actual>
  if [ "$2" = "$3" ]; then
    echo "  PASS  $1"
  else
    echo "  FAIL  $1 (expected=$2 actual=$3)"
    fails=$((fails + 1))
  fi
}

echo "cron-quality watchdog: healthy no-op must not be flagged"
echo "  repo source : $REPO_SRC"
echo "  deployed    : $DEPLOYED"
echo

# ── 1-3: the recognition function, on both copies, with the REAL payloads ────
for label_path in "repo:$REPO_SRC" "deployed:$DEPLOYED"; do
  label="${label_path%%:*}"; path="${label_path#*:}"
  [ -f "$path" ] || { echo "  FAIL  $label copy missing: $path"; fails=$((fails+1)); continue; }
  out=$("$PY" - "$path" "$REPO" <<'PYEOF'
import importlib.util, os, sys, types
p = sys.argv[1]
# the watchdog imports hermes_tz from ops/scripts — the cron's own path
sys.path.insert(0, os.path.join(sys.argv[2], "ops", "scripts"))
sys.path.insert(0, os.path.expanduser("~/.steadfaste-fleet/scripts"))
stub = types.ModuleType("state_tracker")
stub.StateTracker = type("S", (), {"__init__": lambda s, *a, **k: None,
                                   "evaluate": lambda s, *a, **k: "alert"})
sys.modules["state_tracker"] = stub
spec = importlib.util.spec_from_file_location("wd", p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
real1 = "\n\n[SILENT]\n\n[Jev] medium \u00b7 general \u2192 deepseek/deepseek-v4-flash-0731 \u00b7 confidence 0.00 \u00b7 effort low\n"
real2 = "\n\n[SILENT]\n\n[Jev] hard \u00b7 general \u2192 minimax/minimax-m3 \u00b7 confidence 0.69 \u00b7 effort medium\n"
print("trailer", m._is_silent_noop(real1) and m._is_silent_noop(real2))
print("casedrift", m._is_silent_noop("[Silent]"))
print("prose", m._is_silent_noop("[SILENT]\nhere is the summary I was told not to deliver"))
print("unclosed", m._is_silent_noop("[SILENT"))
PYEOF
)
  get() { echo "$out" | awk -v k="$1" '$1==k {print $2}'; }
  claim "$label: no-op + Jev trailer (2 real payloads) is silence" "True" "$(get trailer)"
  claim "$label: case drift '[Silent]' is silence"                 "True" "$(get casedrift)"
  claim "$label: trailing PROSE is NOT silence"                    "False" "$(get prose)"
  claim "$label: malformed '[SILENT' is NOT silence"               "False" "$(get unclosed)"
done
echo

# ── 4: the user-facing symptom, against LIVE cron output ─────────────────────
if [ -f "$DEPLOYED" ]; then
  issues=$("$PY" - "$DEPLOYED" "$REPO" <<'PYEOF'
import contextlib, importlib.util, io, os, sys, types
sys.path.insert(0, os.path.join(sys.argv[2], "ops", "scripts"))
sys.path.insert(0, os.path.expanduser("~/.steadfaste-fleet/scripts"))
stub = types.ModuleType("state_tracker")
stub.StateTracker = type("S", (), {"__init__": lambda s, *a, **k: None,
                                   "evaluate": lambda s, *a, **k: "alert"})  # dedup bypassed
sys.modules["state_tracker"] = stub
spec = importlib.util.spec_from_file_location("wd", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        m.main()
    except SystemExit:
        pass
txt = buf.getvalue()
print(txt.count("- \U0001f7e0") + txt.count("- \U0001f534") + txt.count("- \U0001f7e1"))
PYEOF
)
  claim "deployed script: 0 issues on LIVE cron output (dedup bypassed)" "0" "$issues"
else
  echo "  FAIL  deployed copy missing — cannot check the live path"; fails=$((fails+1))
fi

# ── 5: the operator-facing doc states the widened rule ───────────────────────
skill="$REPO/skills/devops/cron-quality-gate/SKILL.md"
if grep -qi "case drift" "$skill" && grep -qi "Jev routing annotation" "$skill"; then
  claim "skill documents case drift + Jev annotation as the no-op" "yes" "yes"
else
  claim "skill documents case drift + Jev annotation as the no-op" "yes" "no"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "ALL CLAIMS PASS — the healthy no-op is no longer reported as garbage"
else
  echo "$fails CLAIM(S) FAILED"
fi
exit "$fails"
