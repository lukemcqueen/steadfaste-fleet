#!/usr/bin/env bash
# install-claude-mcp.sh — project the fleet MCP surface onto Claude Code.
#
#  This is the ADAPTER half of the one-implementation pattern: the semantics live
#  in ops/services/mycortex-mem/context_tools.py and the surface is defined ONCE
#  in mcp-surface.inc.sh (mcp_surface_lines / mcp_surface_resolve). This script
#  only projects that surface through Claude Code's OWN api (`claude mcp add`).
#  It never writes ~/.claude.json directly — that file is security-sensitive and
#  the CLI owns its own lifecycle, so writing behind its back would race it and
#  could clobber a concurrent session's state.
#
#  Requires claude-code >= 2.1.154 (CLAUDE_CODE_SESSION_ID reaches stdio MCP
#  children) — that is what makes the session key resolvable for a non-Hermes
#  harness. Below that the server still loads but falls back to a per-process
#  key, which is visible rather than silent.
#
#  Presence gate: a host without Claude Code is a NO-OP, not a failure.
#
#  Usage:
#    bash ops/scripts/install/install-claude-mcp.sh           # idempotent add
#    bash ops/scripts/install/install-claude-mcp.sh --check   # would-be state
#    bash ops/scripts/install/install-claude-mcp.sh --verify  # exit 1 if not wired
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=mcp-surface.inc.sh
. "${HERE}/mcp-surface.inc.sh"

MODE="add"
case "${1:-}" in
  --check)   MODE="check" ;;
  --verify)  MODE="verify" ;;
  --help|-h) sed -n '2,20p' "$0"; exit 0 ;;
esac

OS="$(host_os)"

# ── Presence gate: a host without the harness is not a fault ──────────────────
CLAUDE_BIN="$(resolve_cli claude || true)"
if [[ -z "${CLAUDE_BIN}" && ! -f "${HOME}/.claude.json" ]]; then
  echo "claude-code: not found on PATH and no ~/.claude.json (${OS}) — nothing to wire, not a fault."
  exit 0
fi

# The FLEET venv is the canonical interpreter for every fleet MCP server — the
# same one pi and Hermes are registered with. A generic python3 here would make
# Claude substitute its own managed venv, coupling a fleet component to one
# harness's install.
PY="${FLEET_PYTHON:-${HOME}/.steadfaste-fleet/venv/bin/python3}"
if [[ ! -x "${PY}" ]]; then
  PY="$(resolve_python || true)"
fi

REPO_ROOT="${REPO_ROOT:-${HOME}/steadfaste-fleet}"

# Names already registered, so re-running is idempotent.
registered_names=""
if [[ -n "${CLAUDE_BIN}" ]]; then
  registered_names="$("${CLAUDE_BIN}" mcp list 2>/dev/null || true)"
fi

has_name() {
  printf '%s\n' "${registered_names}" | grep -q -- "$1"
}

added=0; skipped=0; failed=0; missing=0; unregistered=0

while IFS=$'\t' read -r name deployed src; do
  [[ -z "${name}" ]] && continue
  script="$(mcp_surface_resolve "${deployed}" "${src}" "${REPO_ROOT}")"

  if [[ -z "${script}" ]]; then
    echo "  ✗ ${name}: no script at ${deployed//\$\{HOME\}/${HOME}} and no repo source ${src}"
    missing=$((missing + 1))
    continue
  fi

  if has_name "${name}"; then
    echo "  = ${name}: already registered → ${script}"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ -z "${CLAUDE_BIN}" ]]; then
    echo "  ? ${name}: claude CLI not on PATH — COULD NOT VERIFY (config may exist but cannot be driven)"
    failed=$((failed + 1))
    continue
  fi

  if [[ "${MODE}" == "check" ]]; then
    echo "  - ${name}: NOT registered (would add ${script})"
    unregistered=$((unregistered + 1))
    continue
  fi

  if "${CLAUDE_BIN}" mcp add --scope user "${name}" -- "${PY}" "${script}" >/dev/null 2>&1; then
    echo "  + ${name}: registered → ${script}  (interpreter ${PY})"
    added=$((added + 1))
  else
    echo "  ✗ ${name}: 'claude mcp add' FAILED"
    failed=$((failed + 1))
  fi
done <<EOF
$(mcp_surface_lines)
EOF

echo ""
echo "claude mcp surface (${OS}): ${added} added, ${skipped} already, ${unregistered} to add, ${failed} failed, ${missing} script(s) missing"

if [[ "${MODE}" == "verify" ]] && (( missing + failed + unregistered > 0 )); then
  exit 1
fi
exit 0