#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  install-codex-mcp.sh — project the fleet MCP surface onto OpenAI Codex.
#
#  STATUS: scaffold. The presence gate and the surface resolution are real; the
#  write half is TODO (see below). Do NOT read this as "codex is wired".
#
#  Layer (from ops/install/harnesses/registry.yaml, `codex`): mcp-native —
#  registration is a config entry in ~/.codex/config.toml, no shim needed.
#
#  PORTABILITY: macOS (Titus) + Linux. Bash 3.2-safe; no GNU-only flags.
#
#  Usage:
#     install-codex-mcp.sh            # register anything missing
#     install-codex-mcp.sh --check    # report state, change nothing
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=mcp-surface.inc.sh
. "${HERE}/mcp-surface.inc.sh"

MODE="add"
case "${1:-}" in
  --check)  MODE="check" ;;
  --help|-h) sed -n '2,16p' "$0"; exit 0 ;;
esac

OS="$(host_os)"
CODEX_BIN="$(resolve_cli codex || true)"
if [[ -z "${CODEX_BIN}" ]]; then
  echo "codex: not found on PATH or in the usual install locations (${OS}) — nothing to wire, not a fault."
  exit 0
fi

PY="$(resolve_python || true)"
if [[ -z "${PY}" ]]; then
  echo "ERROR: no python3 found on this host (${OS}). Set PYTHON=/path/to/python3 and re-run." >&2
  exit 1
fi

REPO_ROOT="$(mcp_surface_repo_root)"
CODEX_CONFIG="${CODEX_CONFIG:-${HOME}/.codex/config.toml}"

echo "codex config: ${CODEX_CONFIG}"
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

# TODO(codex): the WRITE half is not implemented yet. When implementing:
#   1. Read ${CODEX_CONFIG} and parse the existing [mcp_servers.<name>] tables.
#      Use python3 + a real TOML reader (tomllib, stdlib on 3.11+; on older
#      interpreters fall back to a line-anchored text edit that PRESERVES the
#      rest of the file byte-for-byte — never rewrite the whole file).
#   2. For each surface row missing from the file, append:
#        [mcp_servers.<name>]
#        command = "<python>"
#        args = ["<script>"]
#      Idempotent: a server already present is left exactly as it is.
#   3. NEVER print or log any token/credential already in the file.
#   4. Verify by running `codex mcp list` and asserting the names appear.
#   5. Add the row to the harness registry's `verify:` list once it really works.
#   6. Codex does NOT forward its session id to MCP children by default (shell
#      commands only) — see cross-agent-design references/harness-session-identity.md.
#      If the resolver must answer "env, unique", pin [mcp_servers..env] or accept
#      the shared per-repo id. Decide deliberately; do not assume.
#
# TODO(fleet-update): register this script to RUN from fleet-update.sh gated on
#   `codex` being present, exactly like install-hermes-mcp.sh — registering the
#   script into the scripts dir wires nothing on its own.
echo ""
echo "codex mcp surface (${OS}): write half NOT IMPLEMENTED (see TODO above) — codex is NOT wired by this script yet."
exit 0