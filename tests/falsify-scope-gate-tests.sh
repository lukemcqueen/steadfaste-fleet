#!/bin/bash
# Falsification probe for the repo-scope gate tests (pro finding ADV-E-1/E-2).
#
# Claim under test: the parametrised gate tests FAIL when a handler skips the
# gate, and the spy in them is what makes that possible (a test that only
# asserts "no ALL reached the engine" passes against a gate-less handler).
#
# Method: copy the module + the test file into a throwaway tree, strip the gate
# call from exactly ONE handler (task_list_claimable), and run the SAME test
# file there. Expected: the three parametrised cases for task_list_claimable
# fail, every other case still passes.
#
# Exit code = NUMBER OF FAILED CLAIMS (0 = the probe's own expectations held).
set -u

REPO="${REPO:-$HOME/steadfaste-fleet}"
PY="${FLEET_VENV_PYTHON:-$HOME/.steadfaste-fleet/venv/bin/python}"
[ -x "$PY" ] || PY=python3

WORK="$(mktemp -d)"
mkdir -p "$WORK/tests" "$WORK/mcp-servers"
cp "$REPO/tests/test_task_mcp_v3.py" "$WORK/tests/"
cp "$REPO/pytest.ini" "$WORK/" 2>/dev/null
# The module resolves task-db.py through fleet_lib.paths.resolve_repo_resource,
# so the throwaway tree needs the repo's ops/ tree visible or the module
# sys.exit(1)s at import (that is a probe defect, not a test result).
ln -s "$REPO/ops" "$WORK/ops"

failed=0
check() { # check <label> <expected> <actual>
  if [ "$2" = "$3" ]; then
    echo "  ok   $1 (expected $2, got $3)"
  else
    echo "  FAIL $1 (expected $2, got $3)"
    failed=$((failed + 1))
  fi
}

# Strip the gate call from exactly one handler, asserting the match count.
"$PY" - "$REPO/mcp-servers/task-mcp.py" "$WORK/mcp-servers/task-mcp.py" <<'PYEOF'
import sys
src = open(sys.argv[1]).read()
needle = ("def _task_list_claimable(args: dict) -> CallToolResult:\n"
          "    repo, err = _scope_gate(args)\n"
          "    if err:\n"
          "        return _err(err)\n")
assert src.count(needle) == 1, f"handler shape changed ({src.count(needle)} matches)"
open(sys.argv[2], "w").write(
    src.replace(needle,
                "def _task_list_claimable(args: dict) -> CallToolResult:\n"
                "    repo, err = args.get(\"repo\"), None\n"))
print("mutant written: gate call removed from _task_list_claimable")
PYEOF

echo
echo "== mutant run: the same tests against a gate-less task_list_claimable =="
out="$(cd "$WORK" && "$PY" -m pytest tests/test_task_mcp_v3.py -q 2>&1 || true)"
echo "$out" | tail -8
mutant_fails="$(echo "$out" | grep -c '^FAILED' || true)"
mutant_passed="$(echo "$out" | sed -n 's/.*[0-9] failed, \([0-9]*\) passed.*/\1/p')"
check "mutant: task_list_claimable cases fail" 3 "$mutant_fails"
check "mutant: every other case still passes" 24 "$mutant_passed"

echo
echo "== control run: the unmutated module =="
out="$(cd "$REPO" && "$PY" -m pytest tests/test_task_mcp_v3.py -q 2>&1 || true)"
echo "$out" | tail -3
check "control: no failures" 0 "$(echo "$out" | grep -c '^FAILED' || true)"

rm -rf "$WORK"
echo
echo "failed claims: $failed"
exit "$failed"
