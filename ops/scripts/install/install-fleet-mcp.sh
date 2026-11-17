#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  install-fleet-mcp.sh — project the fleet MCP surface onto our own coding
#  agent (steadfaste-fleet's harness / steadfaste-tui).
#
#  STATUS: scaffold. The surface resolution is real; the registration is TODO
#  because the harness's own surface is NOT COMPLETE yet — see the registry's
#  `steadfaste-tui` entry (layer: none, status: blocked): "Steadfaste's own
#  gateway is not complete, so its harness surface cannot be designed against it
#  yet. Declared here with the reason rather than guessed at: a wrong layer means
#  a wiring that 'exists' and does nothing."
#
#  That declaration is the correct state and this script must NOT paper over it.
#
#  PORTABILITY: macOS (Titus) + Linux. Bash 3.2-safe; no GNU-only flags.
#
#  Usage:
#     install-fleet-mcp.sh            # report what WOULD be registered
#     install-fleet-mcp.sh --check    # same, change nothing
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=mcp-surface.inc.sh
. "${HERE}/mcp-surface.inc.sh"

MODE="add"
case "${1:-}" in
  --check)   MODE="check" ;;
  --help|-h) sed -n '2,18p' "$0"; exit 0 ;;
esac

OS="$(host_os)"
REPO_ROOT="$(mcp_surface_repo_root)"

# TODO(fleet-agent): resolve the harness's own CLI/launcher on a live host before
#   anything else. Until the gateway is complete there is no stable binary name
#   to gate presence on — measure it, then replace this placeholder.
FLEET_BIN="$(resolve_cli steadfaste || resolve_cli steadfaste-tui || true)"
if [[ -z "${FLEET_BIN}" ]]; then
  echo "fleet agent: no launcher found (${OS}) — the harness is not present/complete here. Declared gap, not a fault."
  echo "  See ops/install/harnesses/registry.yaml -> harnesses[steadfaste-tui]."
  exit 0
fi

echo "fleet agent launcher: ${FLEET_BIN}"
while IFS=$'\t' read -r name deployed src; do
  [[ -z "${name}" ]] && continue
  script="$(mcp_surface_resolve "${deployed}" "${src}" "${REPO_ROOT}")"
  if [[ -z "${script}" ]]; then
    echo "  ✗ ${name}: no script at ${deployed//\$\{HOME\}/${HOME}} and no repo source ${src}"
    continue
  fi
  echo "  - ${name}: would register ${script}"
done <<EOF
$(mcp_surface_lines)
EOF

# TODO(fleet-agent): the WRITE half is not implemented and SHOULD NOT be until the
#   harness surface exists. When implementing:
#   1. Decide the layer by measurement: if the agent has an MCP client, register
#      the shared servers through its own config/CLI (`layer: mcp`); if it only
#      shells out, use the ONE shared CLI (fleet-context) via a thin extension
#      (`layer: cli-extension`) — never a bespoke per-agent implementation.
#   2. Register through the harness's OWN api. Never hand-edit its config if it
#      exposes a command for this.
#   3. Because this is OUR agent, it should be the BEST-wired host, not the
#      worst: it gets the write-gate + session triggers the same way Pi does.
#   4. Flip `status: blocked` -> `shipped` and add the `verify:` proofs in the
#      registry ONLY once a session can call mem_context and get the peer card.
#   5. Until then the doctor must keep reporting it as a DECLARED GAP with this
#      reason, never as "not installed".
#
# TODO(fleet-update): once the above exists, run this from fleet-update.sh gated
#   on the launcher being present.
echo ""
echo "fleet mcp surface (${OS}): registration NOT IMPLEMENTED — this is a declared gap (harness incomplete), not a silent pass."
exit 0