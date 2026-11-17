#!/usr/bin/env bash
# Verify the decision-layer name sweep, as claims. Exit code = FAILED claim count
# (never a trailing echo, which would always exit 0 and carry no information).
#
# Repo-side only: this change touches nothing outside ~/steadfaste-fleet. Anything
# under ~/.hermes is the Hermes agent's own and is out of scope for this sweep.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${FLEET_VENV_PYTHON:-$HOME/.steadfaste-fleet/venv/bin/python3}"
[ -x "$PY" ] || PY=python3
# Run from the repo root so every echoed path is relative — an absolute path in
# the captured output would carry a home prefix into a committed evidence file.
cd "$REPO" || exit 9
failed=0
claim() {  # claim <description> <expected-rc> <command...>
  local desc="$1" want="$2"; shift 2
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ "$rc" -eq "$want" ]; then
    echo "  PASS  $desc"
  else
    echo "  FAIL  $desc  (rc=$rc want=$want)"
    echo "$out" | tail -5 | sed 's/^/          /'
    failed=$((failed + 1))
  fi
}

echo "== renamed artifacts are the live paths =="
claim "config/router-models.json is tracked" 0 \
  git -C "$REPO" ls-files --error-unmatch config/router-models.json
claim "config/jev-router-models.json is gone" 1 \
  git -C "$REPO" ls-files --error-unmatch config/jev-router-models.json
claim "fleet_doctor/router_layer.py is tracked" 0 \
  git -C "$REPO" ls-files --error-unmatch ops/scripts/manage/fleet_doctor/router_layer.py
claim "fleet_doctor/jev.py is gone" 1 \
  git -C "$REPO" ls-files --error-unmatch ops/scripts/manage/fleet_doctor/jev.py

echo "== the doctor still imports the module it checks =="
claim "checks.py imports .router_layer" 0 \
  grep -q "from .router_layer import" "$REPO/ops/scripts/manage/fleet_doctor/checks.py"
claim "cli.py imports check_jev_layer from .router_layer" 0 \
  grep -q "from .router_layer import check_jev_layer" "$REPO/ops/scripts/manage/fleet_doctor/cli.py"

echo "== register map sources all still exist =="
# The count floor matters: a grep that matches nothing makes the loop read
# nothing and exit 0, so a vacuous sweep over a missing tree would read as PASS.
claim "every register() source path exists" 0 \
  bash -c 'cd "$0" && n=0; while IFS= read -r f; do [ -e "$f" ] || { echo "missing: $f"; exit 1; }; n=$((n+1)); done < <(grep -E "^register(_orch)?[[:space:]]" ops/scripts/fleet-update.sh | sed -E "s/^register(_orch)?[[:space:]]+\"([^\"]+)\".*/\2/"); [ "$n" -ge 300 ] || { echo "only $n register paths parsed — a sweep over nothing would pass"; exit 1; }' "$REPO"

echo "== the naming guard (the regression test for this class) =="
claim "guard test passes: no product token in tracked file names" 0 \
  "$PY" -m pytest "tests/test_repo_structure.py::test_no_product_token_in_tracked_file_names" -q -p no:cacheprovider

echo "== the affected suites =="
claim "renamed-module + register-map suites pass" 0 \
  "$PY" -m pytest -q -p no:cacheprovider \
    tests/test_router_pools.py \
    tests/test_router_shadow_report.py \
    tests/test_router_notice_contract.py \
    tests/test_router_swap_seam.py \
    tests/test_fleet_doctor_router_layer.py \
    tests/test_doctor_model_config.py \
    tests/test_repo_resource_paths.py \
    tests/test_registered_sources_are_tracked.py

echo
echo "failed claims: $failed"
exit "$failed"
