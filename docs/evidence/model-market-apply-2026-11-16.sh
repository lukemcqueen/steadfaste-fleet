#!/usr/bin/env bash
# model-market-apply-2026-11-16.sh - re-runnable verification of the 2026-11-16
# daily model-market apply (scan -> config/jev-router-models.json).
# rc = number of FAILED claims (0 = all passed). Read-only.
#
# The scan snapshot this checks against is committed beside this file as
# model-market-apply-2026-11-16.state.json (copied from the live scan state at
# apply time, home prefix normalised to <HOME>), so the comparison is hermetic:
# a later reviewer needs no live state.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 9
PY="${PY:-$HOME/.steadfaste-fleet/venv/bin/python}"
SNAPSHOT="docs/evidence/model-market-apply-2026-11-16.state.json"
LIST="config/jev-router-models.json"
EVID="docs/evidence/model-market-apply-2026-11-16.txt"
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
if cfg.get("captured") != "2026-11-16":
    bad.append("captured date not refreshed")
print("; ".join(bad) if bad else "config == scan snapshot: prices, tier order, recomputed heads; captured=2026-11-16")
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
if test_out="$("$PY" -m pytest tests/test_jev_router_pools.py tests/test_model_market_scan.py tests/test_reviewer_model_default.py tests/test_dry_reviewer_model.py -q 2>&1)"; then
  echo "$test_out" | tail -1 | sed 's/^/  PASS  pytest: /'
else
  echo "$test_out" | tail -20 | sed 's/^/  FAIL  pytest: /'; fails=$((fails + 1))
fi

# Claim 4 - the captured transcript re-verifies its own recorded sha256 (a hash
# chain: each capture verifies the transcript present when it ran).
if [ -f "$EVID" ]; then
  if ev_out="$(bash ops/scripts/make-evidence.sh --verify "$EVID" 2>&1)"; then
    echo "$ev_out" | sed 's/^/  PASS  /'
  else
    echo "$ev_out" | sed 's/^/  FAIL  /'; fails=$((fails + 1))
  fi
else
  note WARN "transcript absent (first capture) - transcript hash not re-verified"
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
sha="$( { command -v sha256sum >/dev/null 2>&1 && sha256sum "$LIST" || shasum -a 256 "$LIST"; } | cut -d' ' -f1 )"
note INFO "sha256 $LIST = $sha"

echo "failed claims: $fails"
exit "$fails"
