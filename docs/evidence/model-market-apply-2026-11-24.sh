#!/usr/bin/env bash
# model-market-apply-2026-11-24.sh - re-runnable verification of the 2026-11-24
# daily model-market apply (live scan -> config/router-models.json).
# rc = number of FAILED claims (0 = all passed). Read-only.
#
# The scan snapshot this checks against is committed beside this file as
# model-market-apply-2026-11-24.state.json (copied from the live scan state at
# apply time, home prefix normalised to <HOME>), so the comparison is hermetic:
# a later reviewer needs no live state.
#
# Claim 1 pins THIS apply's snapshot: after a LATER apply moves the config on,
# it will report drift by design - the transcript (.txt) records the state at
# THIS apply. Same shape as model-market-apply-2026-11-21.sh.
#
# PROVENANCE (added after a close-gate finding asked where the siblings live):
# both artifacts are committed AT THE TIP in the SAME commit as this script -
# commit e0eb3a2a ("model market: apply 2026-11-24 scan (rc=1 apply_pending,
# prices only)"), which creates all three files together. Checkable:
#   git ls-tree -r --name-only HEAD | grep model-market-apply-2026-11-24
#   git log --oneline -1 -- docs/evidence/model-market-apply-2026-11-24.state.json
# so the claims below are exercisable from the tip with no companion commit, and
# a missing sibling still counts as a FAILED claim (fail-closed), never a pass.
#
# CONCURRENCY: this script is read-only. The apply it verifies was performed
# under the per-repo governance lock (state/.governance-*.json; begin_change ->
# work -> feedback -> end_change), which is the guard against two applies racing
# on the shared config; the live scan state is written only by
# orch-model-market-scan.py runs. On any claim that cannot run, the script
# COUNTS the failure - silence never means "no problem".
#
# PORTABILITY: macOS + Linux, POSIX sh tools only (no GNU-only flags).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 9
PY="${PY:-$HOME/.steadfaste-fleet/venv/bin/python}"
if [ ! -x "$PY" ]; then
  echo "FAIL preflight: venv python absent at $PY - set PY=<python> to re-run" >&2
  exit 9
fi
SNAPSHOT="docs/evidence/model-market-apply-2026-11-24.state.json"
LIST="config/router-models.json"
EVID="docs/evidence/model-market-apply-2026-11-24.txt"
LIVE="${LIVE_STATE:-$HOME/.steadfaste-fleet/state/model-market.json}"
fails=0
note() { printf '  %-5s %s\n' "$1" "$2"; }

# Claim 1 - the committed list carries the committed scan snapshot's prices, tier
# order and recomputed heads.
if "$PY" - "$SNAPSHOT" "$LIST" <<'PYEOF'
import json, sys
snap = json.load(open(sys.argv[1])); cfg = json.load(open(sys.argv[2]))
bad = []
if cfg["tiers"] != snap["tiers"]:
    bad.append("tier order differs from the scan snapshot")
for m, p in snap["prices"].items():
    if m in cfg["prices"]:
        if (cfg["prices"][m]["in"] != round(p["in"] * 1e6, 6)
                or cfg["prices"][m]["out"] != round(p["out"] * 1e6, 6)):
            bad.append("price differs: " + m)
for tier in ("simple", "medium", "hard"):
    for k, pool in cfg["tiers"][tier].items():
        m = pool[0]; q = cfg["prices"][m]
        exp = f"{m} = {q['in']} in + {q['out']} out = ${q['in'] + q['out']:.3f}/M"
        if cfg["derivation"]["heads"].get(f"{tier}/{k}") != exp:
            bad.append("head not recomputed: " + tier + "/" + k)
if cfg.get("captured") != "2026-11-24":
    bad.append("captured date not refreshed")
print("; ".join(bad) if bad else "config == scan snapshot: prices, tier order, recomputed heads; captured=2026-11-24")
sys.exit(len(bad))
PYEOF
then note PASS "config == committed scan snapshot (prices, tier order, recomputed heads)"
else note FAIL "config vs scan snapshot"; fails=$((fails + 1)); fi

# Claim 2 - the rebuilt router pools pass their own selftest (output recorded verbatim).
if selftest_out="$("$PY" ops/scripts/manage/router-pools.py --selftest 2>&1)"; then
  echo "$selftest_out" | sed 's/^/  PASS  selftest: /'
else
  echo "$selftest_out" | sed 's/^/  FAIL  selftest: /'; fails=$((fails + 1))
fi

# Claim 3 - router + scanner + reviewer test files pass (pytest summary recorded verbatim).
if test_out="$("$PY" -m pytest tests/test_router_pools.py tests/test_router_pools_seed.py tests/test_model_market_scan.py tests/test_reviewer_model_default.py tests/test_dry_reviewer_model.py -q 2>&1)"; then
  echo "$test_out" | tail -1 | sed 's/^/  PASS  pytest: /'
else
  echo "$test_out" | tail -20 | sed 's/^/  FAIL  pytest: /'; fails=$((fails + 1))
fi

# Claim 4 - the captured transcript re-verifies its own recorded sha256 (a hash
# chain: each capture verifies the transcript present when it ran). make-evidence.sh
# --verify recomputes the hash over the file EXCEPT its final "sha256:" line and
# compares it to the recorded value, so a capture in progress is unaffected - the
# checker always verifies the file that exists BEFORE make-evidence rewrites it.
# A transcript that is absent or fails verification is a COUNTED failure: an
# unverifiable hash chain must not read as "failed claims: 0".
if [ -f "$EVID" ]; then
  if ev_out="$(bash ops/scripts/make-evidence.sh --verify "$EVID" 2>&1)"; then
    echo "$ev_out" | sed 's/^/  PASS  /'
  else
    echo "$ev_out" | sed 's/^/  FAIL  /'; fails=$((fails + 1))
  fi
else
  note FAIL "transcript $EVID absent - hash chain not re-verified"; fails=$((fails + 1))
fi

# Claim 5 - the model SURFACES match the list, and carry no routing label
# (hermes-model-config: a 'label:org/model' value in model.default 400s every
# turn and silently falls through to a fallback). Expected session default is
# DERIVED from the list's own baseline field; the reviewer must resolve to an
# unlabelled id that is priced in the list.
if surf_out="$("$PY" - "$LIST" <<'PYEOF'
import json, subprocess, sys
sys.path.insert(0, "ops/scripts")
cfg = json.load(open(sys.argv[1]))
def has_label(v):
    # a ROUTING LABEL is 'label:org/model' - colon AND a slash AFTER it. An
    # Ollama tag ('qwen2.5:3b') also has a colon but no slash after it: never
    # fire on a bare colon (hermes-model-config pitfall).
    return ":" in v and "/" in v.split(":", 1)[1]
bad = []
base = cfg["baseline"].split(":", 1)[1] if has_label(cfg["baseline"]) else cfg["baseline"]
try:
    got = subprocess.run(["hermes", "config", "get", "model.default"],
                         capture_output=True, text=True, timeout=60)
    default = (got.stdout or "").strip()
    if got.returncode != 0:
        bad.append("hermes config get model.default failed rc=%d" % got.returncode)
    elif default != base:
        bad.append("model.default %r != list baseline %r" % (default, base))
    if has_label(default):
        bad.append("model.default carries a routing label: %r" % default)
except Exception as exc:
    bad.append("model.default could not be read: %s" % exc)
from fleet_lib.models import get_reviewer_model
rev = get_reviewer_model()
if has_label(rev):
    bad.append("reviewer carries a routing label: %r" % rev)
# list ids carry the 'openrouter:' ROUTING LABEL (hermes-model-config); surfaces
# hold BARE ids, so compare label-stripped - the bare form is the correct one.
bare_prices = {k.split(":", 1)[-1] for k in cfg["prices"]}
if rev not in bare_prices:
    bad.append("reviewer %r is not priced in the list" % rev)
print("; ".join(bad) if bad else
      "session default == list baseline (bare id); reviewer bare + priced in list")
sys.exit(len(bad))
PYEOF
)"; then
  echo "$surf_out" | sed 's/^/  PASS  surfaces: /'
else
  echo "$surf_out" | sed 's/^/  FAIL  surfaces: /'; fails=$((fails + 1))
fi

# Info - does live scan state still agree with the snapshot? A comparison that
# cannot run must be VISIBLE (WARN), never silently absorbed.
if [ -f "$LIVE" ]; then
  if live_out="$("$PY" - "$SNAPSHOT" "$LIVE" <<'PYEOF'
import json, sys
snap = json.load(open(sys.argv[1])); live = json.load(open(sys.argv[2]))
keys = ("prices", "tiers", "signature", "missing_models", "violations")
same = all(snap.get(k) == live.get(k) for k in keys)
print("  INFO  live scan state " + ("still matches" if same else "has MOVED from") +
      " the snapshot (fields: " + ", ".join(keys) + ")")
PYEOF
)"; then
    printf '%s\n' "$live_out"
  else
    note WARN "live-state comparison could not run/read - snapshot freshness UNVERIFIED"
  fi
else
  note WARN "live scan state absent - snapshot freshness UNVERIFIED"
fi

# Info - sha256 of the checked list, regenerated on every run.
sha="$({ command -v sha256sum >/dev/null 2>&1 && sha256sum "$LIST" || shasum -a 256 "$LIST"; } | cut -d' ' -f1 )"
note INFO "sha256 $LIST = $sha"

echo "failed claims: $fails"
exit "$fails"
