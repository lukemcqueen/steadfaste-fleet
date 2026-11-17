#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  install-hermes-mcp.sh — project the fleet MCP surface onto Hermes.
#
#  WHY. The harness registry declares hermes as `layer: mcp`, but it carried no
#  register script, so Hermes never got `fleet-context`: memory reached the host
#  only through the in-process plugin, and any host where the plugin was absent
#  had NO memory path while nothing reported it. Pi was wired (install-pi-mcp.sh)
#  and Hermes was not — "harness present but unwired" was a silence nobody read.
#
#  This is the ADAPTER half of the one-implementation pattern: the semantics live
#  in ops/services/mycortex-mem/context_tools.py, and the surface is defined once
#  in mcp-surface.inc.sh. This script only projects it through the harness's OWN
#  api (`hermes mcp add`). It never writes ~/.hermes/config.yaml directly — that
#  file is security-sensitive and the harness owns its own lifecycle.
#
#  PORTABILITY: macOS (Titus) + Linux. Bash 3.2-safe; no GNU-only flags.
#
#  Usage:
#     install-hermes-mcp.sh            # register anything missing (idempotent)
#     install-hermes-mcp.sh --check    # report state, change nothing
#     install-hermes-mcp.sh --verify   # exit 1 if anything required is missing
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
HERMES_BIN="$(resolve_cli hermes || true)"
if [[ -z "${HERMES_BIN}" ]]; then
  echo "hermes: not found on PATH or in the usual install locations (${OS}) — nothing to wire, not a fault."
  exit 0
fi

# The FLEET venv is the canonical interpreter for every fleet MCP server — it is
# what agent-bus/tasks/executor/review/loop-governance are all registered with.
# Passing a generic python3 to `hermes mcp add` made it substitute ITS OWN managed
# venv (~/.hermes/installs/<hash>/.../venv), coupling a fleet component to the
# harness's install. Prefer the fleet venv; fall back to a host python3.
PY="${FLEET_PYTHON:-${HOME}/.steadfaste-fleet/venv/bin/python3}"
if [[ ! -x "${PY}" ]]; then
  PY="$(resolve_python || true)"
fi
if [[ -z "${PY}" ]]; then
  echo "ERROR: no python3 found on this host (${OS}). Set PYTHON=/path/to/python3 and re-run." >&2
  exit 1
fi

REPO_ROOT="$(mcp_surface_repo_root)"

# ── What is already registered? (one parse, reused per row) ──────────────────
registered_names="$("${PY}" - "${HERMES_BIN}" <<'PY'
import re, subprocess, sys
cli = sys.argv[1]
try:
    out = subprocess.run([cli, "mcp", "list"], capture_output=True, text=True, timeout=90).stdout
except Exception:
    sys.exit(0)
for line in out.splitlines():
    m = re.match(r"\s*([A-Za-z0-9_.:-]+)\s+\S", line)
    if m and m.group(1) not in ("Name", "MCP"):
        print(m.group(1))
PY
)"

has_name() {
  printf '%s\n' "${registered_names}" | grep -qx -- "$1"
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

  if [[ "${MODE}" == "check" ]]; then
    echo "  - ${name}: NOT registered (would add ${script})"
    unregistered=$((unregistered + 1))
    continue
  fi

  if "${HERMES_BIN}" mcp add "${name}" --command "${PY}" --args "${script}" >/dev/null 2>&1; then
    # `hermes mcp add` may substitute its OWN managed venv for --command. Pin the
    # interpreter back to the fleet one through the harness's own config api, so a
    # fleet component never depends on the harness's install surviving.
    "${HERMES_BIN}" config set "mcp_servers.${name}.command" "${PY}" >/dev/null 2>&1 || true
    echo "  + ${name}: registered → ${script}  (interpreter ${PY})"
    added=$((added + 1))
  else
    echo "  ✗ ${name}: 'hermes mcp add' FAILED"
    failed=$((failed + 1))
  fi
done <<EOF
$(mcp_surface_lines)
EOF

echo ""
echo "hermes mcp surface (${OS}): ${added} added, ${skipped} already, ${unregistered} to add, ${failed} failed, ${missing} script(s) missing"

if [[ "${MODE}" == "verify" ]] && (( missing + failed + unregistered > 0 )); then
  exit 1
fi
exit 0