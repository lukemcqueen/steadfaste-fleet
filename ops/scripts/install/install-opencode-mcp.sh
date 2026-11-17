#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  install-opencode-mcp.sh — project the fleet MCP surface onto OpenCode.
#
#  STATUS: scaffold. The presence gate and the surface resolution are real; the
#  write half is TODO (see below). Do NOT read this as "opencode is wired".
#
#  Layer: TODO — verify OpenCode's ACTUAL registration surface on a live host
#  before writing any adapter (cross-agent-design rule 6: never infer a host's
#  API from a doc or from another harness's vocabulary; Pi's first adapter failed
#  twice, silently, for exactly that).
#
#  PORTABILITY: macOS (Titus) + Linux. Bash 3.2-safe; no GNU-only flags.
#
#  Usage:
#     install-opencode-mcp.sh            # register anything missing
#     install-opencode-mcp.sh --check    # report state, change nothing
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=mcp-surface.inc.sh
. "${HERE}/mcp-surface.inc.sh"

MODE="add"
case "${1:-}" in
  --check)   MODE="check" ;;
  --help|-h) sed -n '2,17p' "$0"; exit 0 ;;
esac

OS="$(host_os)"
OPENCODE_BIN="$(resolve_cli opencode || true)"
if [[ -z "${OPENCODE_BIN}" ]]; then
  echo "opencode: not found on PATH or in the usual install locations (${OS}) — nothing to wire, not a fault."
  exit 0
fi

PY="$(resolve_python || true)"
if [[ -z "${PY}" ]]; then
  echo "ERROR: no python3 found on this host (${OS}). Set PYTHON=/path/to/python3 and re-run." >&2
  exit 1
fi

REPO_ROOT="$(mcp_surface_repo_root)"
# TODO(opencode): confirm the real config location on a live host. Candidates
#   seen elsewhere (~/.config/opencode/opencode.json, project opencode.jsonc) are
#   NOT verified here — measure it before you trust any of them.
OPENCODE_CONFIG="${OPENCODE_CONFIG:-${HOME}/.config/opencode/opencode.json}"

echo "opencode config (unverified): ${OPENCODE_CONFIG}"
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

# TODO(opencode): the WRITE half is not implemented yet. When implementing:
#   1. MEASURE the host surface first: run `opencode --help`, look for an
#      `mcp`/`plugin` subcommand, and read the config it actually loads. Record
#      what you found in the harness registry's `surface:` field.
#   2. If it reads an MCP server map from JSON, register each surface row there
#      through its own CLI if one exists (preferred), else a byte-preserving edit.
#   3. Idempotent: never clobber an existing entry with different args.
#   4. Decide and record the session-identity story (does it forward a session id
#      to MCP children?) before relying on "env, unique".
#   5. Add `register: ops/scripts/install/install-opencode-mcp.sh` to the
#      harness registry entry once it really works — and make the doctor report
#      "opencode present but unwired" until then.
#
# TODO(fleet-update): run this from fleet-update.sh gated on `opencode` presence.
echo ""
echo "opencode mcp surface (${OS}): write half NOT IMPLEMENTED (see TODO above) — opencode is NOT wired by this script yet."
exit 0