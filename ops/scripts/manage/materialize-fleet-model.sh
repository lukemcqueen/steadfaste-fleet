#!/usr/bin/env bash
# materialize-fleet-model.sh — make fleet-owned model authority reach Hermes config.yaml.
#
# steadfaste-fleet (fleet_lib/models.py) is the SINGLE authority for the session/cron
# model; ~/.hermes/config.yaml model.default is a MATERIALIZED artifact of it. This
# script runs on EVERY host during fleet-update.sh (each host's own sync cron), so a
# baseline change committed by orch-model-market-scan propagates automatically to all
# agents — no orchestrator hand-stamping per host.
#
# It is a THIN caller: the derive-and-decide logic (target resolution, idempotence,
# bare-id validation) lives ONLY in fleet_lib/models.py so it is directly testable
# (cycle decouple-fleet-model-materializer, reviewer ADV-0-2). This shell only:
#   1. resolves the venv python,  2. runs the Python decision,  3. writes via the
#   4. public `hermes config set` CLI when a non-idempotent valid target is computed.
#
# Idempotent: no-op when current model.default already equals the target.
# Fail-closed: a routing-LABEL target (label:org/model in baseline) is NEVER written —
# a label in model.default 400s every turn — the script FAILS loudly instead.
#
# Cross-platform (Linux + macOS), no Hermes-internal deps beyond the public CLI.

set -uo pipefail

HOME_DIR="${HOME:-$(eval echo ~)}"
REPO_DIR="${STEADFASTE_REPO:-$HOME_DIR/steadfaste-fleet}"
FLEET_DEPLOY="${FLEET_DEPLOY_HOME:-$HOME_DIR/.steadfaste-fleet}"

# Resolve a python that can import fleet_lib.models (PyYAML not required by models.py,
# but prefer the fleet venv the resolver uses for consistency).
PY=""
for _cand in "$FLEET_DEPLOY/venv/bin/python" "$FLEET_DEPLOY/venv/bin/python3" \
             "$REPO_DIR/.venv/bin/python3" python3; do
  if command -v "$_cand" >/dev/null 2>&1 && "$_cand" -c \
     'import sys; sys.path.insert(0,"'$HOME_DIR'/steadfaste-fleet/ops/scripts"); import fleet_lib.models' \
     >/dev/null 2>&1; then
    PY="$_cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "materialize-fleet-model: COULD NOT VERIFY — no interpreter that imports fleet_lib.models" >&2
  exit 3
fi

if ! command -v hermes >/dev/null 2>&1; then
  echo "materialize-fleet-model: hermes CLI absent — cannot write config.yaml (Hermes not on this host?)" >&2
  exit 3
fi

# Fetch current model.default via the public CLI.
CURRENT="$(hermes config get model.default 2>/dev/null || true)"
CURRENT="${CURRENT:-}"
# Run the Python decision: print a JSON line: {"model":..,"provider":..,"write":bool,"label":bool}
DECISION="$(CURRENT_MODEL="$CURRENT" PYTHONPATH="$REPO_DIR/ops/scripts" "$PY" - <<'PYEOF'
import json, os, sys
import fleet_lib.models as m
target = m.resolve_session_target()
write = m.session_change_decision(os.environ.get("CURRENT_MODEL", ""), target)
json.dump({"model": target["model"], "provider": target["provider"],
           "write": bool(write), "label": bool(target["has_label"]),
           "source": target.get("source","")}, sys.stdout)
PYEOF
)"

MODEL="$(printf '%s' "$DECISION" | sed -n 's/.*"model": *"\([^"]*\)".*/\1/p')"
PROVIDER="$(printf '%s' "$DECISION" | sed -n 's/.*"provider": *"\([^"]*\)".*/\1/p')"
WRITE="$(printf '%s' "$DECISION" | sed -n 's/.*"write": *\(true\|false\).*/\1/p')"
LABEL="$(printf '%s' "$DECISION" | sed -n 's/.*"label": *\(true\|false\).*/\1/p')"

# Fail closed on a routing label — never write a label into model.default.
if [ "$LABEL" = "true" ]; then
  echo "materialize-fleet-model: FAIL — resolved session target '$MODEL' carries a routing label; model.default must hold the bare 'provider/model' id. Refusing to write." >&2
  exit 2
fi

if [ "$WRITE" != "true" ]; then
  echo "materialize-fleet-model: no-op — model.default already at fleet target ('$MODEL')."
  exit 0
fi

if [ -z "$MODEL" ]; then
  echo "materialize-fleet-model: FAIL — no session model target resolved (env unset and router-models.json baseline absent)." >&2
  exit 2
fi

# Apply via the public Hermes CLI (the guard requires it; never write config.yaml raw).
hermes config set model.default "$MODEL" || {
  echo "materialize-fleet-model: FAIL — 'hermes config set model.default $MODEL' returned $?" >&2
  exit 1
}
if [ -n "$PROVIDER" ]; then
  hermes config set model.provider "$PROVIDER" || {
    echo "materialize-fleet-model: FAIL — 'hermes config set model.provider $PROVIDER' failed; model.default was set but provider is now stale relative to the fleet canonical. Resolve before next sync." >&2
    exit 1
  }
fi

echo "materialize-fleet-model: wrote model.default=$MODEL provider=$PROVIDER (source ${DECISION})"
exit 0