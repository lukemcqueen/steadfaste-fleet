#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  test_claude_governance_installer.sh — regression test for
#  ops/scripts/install/install-claude-governance.sh
#
#  Proves (2026-09-29, governance-agnostic-claude):
#    1. Registers the 5 governance MCP servers at USER scope (loop-governance,
#       review, tasks, executor, agent-bus).
#    2. Preserves every pre-existing ~/.claude.json key (never clobbers).
#    3. Preserves the user's own non-governance mcpServers.
#    4. Is idempotent (second run = same server set).
#    5. --remove strips only the governance servers.
#    6. No rendered command/arg carries a literal `~` (Claude Code / posix_spawn
#       does not shell-expand it — see docs/design/claude-governance.md §6.2).
#    7. Runs against a temp HOME — never touches the real ~/.claude.json.
#
#  Run:  bash tests/test_claude_governance_installer.sh
# ─────────────────────────────────────────────────────────────
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO/ops/scripts/install/install-claude-governance.sh"
FAILS=0

check() { # check <name> <0|1>
  if [[ "$2" == "1" ]]; then echo "  PASS  $1"; else echo "  FAIL  $1"; FAILS=$((FAILS+1)); fi
}

TD="$(mktemp -d)"
trap 'rm -rf "$TD"' EXIT
HOME_T="$TD/home"; mkdir -p "$HOME_T"
CJ="$HOME_T/.claude.json"

# A pre-existing config with user state + a user-owned server.
cat > "$CJ" <<'EOF'
{
  "firstStartTime": "2026-08-30T07:49:17.307Z",
  "machineID": "abc123",
  "migrationVersion": 13,
  "mcpServers": {"user-own-server": {"command": "uvx", "args": ["x"]}}
}
EOF

export HOME="$HOME_T" PYTHON="$(command -v python3)"
unset AGENT_NAME 2>/dev/null || true

bash "$SCRIPT" >/dev/null 2>&1
python3 - "$CJ" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["machineID"] == "abc123", "existing key lost"
assert d["mcpServers"]["user-own-server"]["command"] == "uvx", "user server clobbered"
for n in ("loop-governance", "review", "tasks", "executor", "agent-bus"):
    assert n in d["mcpServers"], f"{n} missing"
assert d["mcpServers"]["review"]["env"]["AGENT_NAME"] == "titusclaude"
assert d["mcpServers"]["loop-governance"]["env"]["AGENT_NAME"] == "titusclaude"
EOF
check "run1: user keys preserved + 5 governance servers + AGENT_NAME" "$([ $? -eq 0 ] && echo 1 || echo 0)"

BEFORE="$(python3 -c "import json;print(sorted(json.load(open('$CJ'))['mcpServers']))")"
bash "$SCRIPT" >/dev/null 2>&1
AFTER="$(python3 -c "import json;print(sorted(json.load(open('$CJ'))['mcpServers']))")"
check "run2: idempotent (same server set)" "$([ "$BEFORE" = "$AFTER" ] && echo 1 || echo 0)"

bash "$SCRIPT" --check >/dev/null 2>&1; RC=$?
check "--check exits 0" "$([ $RC -eq 0 ] && echo 1 || echo 0)"

bash "$SCRIPT" --remove >/dev/null 2>&1
python3 - "$CJ" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
assert "user-own-server" in d["mcpServers"], "remove took the user's server"
assert "review" not in d["mcpServers"], "governance server survived remove"
EOF
check "--remove strips governance only, keeps user's own" "$([ $? -eq 0 ] && echo 1 || echo 0)"

# 6. A PRE-Slice-B config carries a single "loop-governance" entry pointing at the probe
#    WITHOUT a --role (the old one-name shape). An idempotent reinstall must upsert the
#    current Slice-B shape: BOTH loop-governance (--role loop-governance) AND review
#    (--role review) exist — no missing role, no duplicated probe under a third name.
python3 - "$CJ" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
# Rebuild a bare/legacy entry (no role) to overwrite after the earlier --remove.
d["mcpServers"]["loop-governance"] = {
    "command": "python3",
    "args": ["loop-gov-mcp.py"],
}
json.dump(d, open(sys.argv[1], "w"))
EOF
grep -q '"loop-governance"' "$CJ" || { echo "  FAIL  6a: legacy seed itself did not land"; FAILS=$((FAILS+1)); }
bash "$SCRIPT" >/dev/null 2>&1
python3 - "$CJ" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
# Both role-distinct loop-gov registrations present, each role-matched.
assert "--role" in d["mcpServers"]["loop-governance"]["args"], "lifecycle role missing"
assert "--role" in d["mcpServers"]["review"]["args"], "review role missing"
i = d["mcpServers"]["loop-governance"]["args"].index("--role")
assert d["mcpServers"]["loop-governance"]["args"][i+1] == "loop-governance"
i = d["mcpServers"]["review"]["args"].index("--role")
assert d["mcpServers"]["review"]["args"][i+1] == "review"
print("single probe under both role-matched names")
EOF
check "6a: legacy entry replaces with loop-governance + review, role-matched" "$([ $? -eq 0 ] && echo 1 || echo 0)"

# 7. Claude Code / posix_spawn does NOT shell-expand ~ — a rendered command or arg
#    that still carries a literal `~` would ENOENT at startup (see claude-governance.md §6.2).
python3 - "$CJ" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["mcpServers"]["review"]["command"].startswith("/"), (
    "review command is not absolute: " + d["mcpServers"]["review"]["command"])
for name, s in d["mcpServers"].items():
    for key in ("command",) + tuple(s.get("args") or []):
        if "~" in str(key):
            raise AssertionError(f"{name} carries an unexpanded '~': {key!r}")
print("no ~ in any governance server command/arg")
EOF
check "7: no literal ~ left in any governance server" "$([ $? -eq 0 ] && echo 1 || echo 0)"

echo
if [[ "$FAILS" -gt 0 ]]; then echo "$FAILS FAILED"; exit 1; fi
echo "ALL PASS"
