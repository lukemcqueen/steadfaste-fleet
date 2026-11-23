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

# ── Parse the live `hermes mcp list` into registered server names ─────────────
# Used by BOTH the stale-cleanup detection and the add-loop membership check, so
# the two can never disagree about what "registered" means. Names are indented two
# spaces by the CLI, so leading whitespace is stripped here (a bare `grep -q
# "^name "` can never match). FAILS LOUD: an unreadable `hermes mcp list` (nonzero
# exit or timeout) exits 2 and, under `set -euo pipefail`, aborts the installer with
# a named error instead of reading "nothing is registered" — the old
# `except Exception: sys.exit(0)` turned a broken list into "nothing stale to clean"
# AND "everything missing, re-add all", which is exactly the false-green this
# installer was producing on ailing hosts.
mcp_registered_names() {
  "${PY}" - "${HERMES_BIN}" <<'PY'
import re, subprocess, sys
cli = sys.argv[1]
try:
    res = subprocess.run([cli, "mcp", "list"], capture_output=True, text=True, timeout=90)
except Exception as exc:
    sys.stderr.write(f"  ✗ ERROR: 'hermes mcp list' failed to run: {exc}\n")
    sys.exit(2)
if res.returncode != 0:
    sys.stderr.write(f"  ✗ ERROR: 'hermes mcp list' exited {res.returncode}: {res.stderr.strip()}\n")
    sys.exit(2)
for line in res.stdout.splitlines():
    m = re.match(r"\s*([A-Za-z0-9_.:-]+)\s+\S", line)
    if m and m.group(1) not in ("Name", "MCP"):
        print(m.group(1))
PY
}

# ── Cleanup orchestrator-only services on non-orchestrator hosts ──────────────
# Remove any dangling orchestrator-only MCP servers (agent-bus, executor) that were
# registered by an older installer but are no longer intended for this host. The
# subcommand is `hermes mcp remove` ("mcp delete" does not exist — it failed and was
# swallowed, so the cleanup ran but never removed a thing). A failed removal is a
# REAL state error that will re-trigger the watchdog's no-script/args alert on the
# next deploy, so it is counted and surfaced, never `|| echo`-swallowed.
cleanup_removed=0; cleanup_failed=0
cleanup_stale_orchestrator_entries() {
  if is_orchestrator; then
    return 0
  fi
  local service current
  # First read is OUTSIDE any if so `set -e` aborts loudly on an unreadable list.
  current="$(mcp_registered_names)"
  for service in agent-bus executor; do
    if printf '%s\n' "${current}" | grep -qx -- "${service}"; then
      echo "  ~ ${service}: removing stale orchestrator-only server from non-orchestrator host"
      printf 'y\n' | "${HERMES_BIN}" mcp remove "${service}" >/dev/null 2>&1 || {
        # `mcp remove` can exit nonzero; that alone is not the verdict. Re-parse
        # after the attempt (below) and judge by whether the name is gone — the
        # swallow here only keeps tailing on to the authoritative re-check.
        :
      }
      if mcp_registered_names | grep -qx -- "${service}"; then
        # Re-parse is fresh and authoritative: still present = removal failed.
        echo "  ✗ ${service}: FAILED to remove stale orchestrator-only server (still registered)" >&2
        cleanup_failed=$((cleanup_failed + 1))
      else
        echo "  → ${service}: removed"
        cleanup_removed=$((cleanup_removed + 1))
      fi
    fi
  done
  return 0
}

# Run the cleanup on non-orchestrator hosts
cleanup_stale_orchestrator_entries

# ── What is already registered? (one parse, reused per row) ──────────────────
# After the cleanup, so `has_name` reflects the post-cleanup state. Second read of
# the list is fine — the cleanup already established the list is readable.
registered_names="$(mcp_registered_names)"

has_name() {
  printf '%s\n' "${registered_names}" | grep -qx -- "$1"
}

added=0; skipped=0; failed=0; missing=0; unregistered=0

while IFS=$'\t' read -r name deployed src; do
  [[ -z "${name}" ]] && continue
  script="$(mcp_surface_resolve "${deployed}" "${src}" "${REPO_ROOT}")"

  if [[ -z "${script}" ]]; then
    echo "  ✗ ${name}: no script at ${deployed//\$\\{HOME\\}/${HOME}} and no repo source ${src}"
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

  # Under a closed stdin (a headless deploy), `hermes mcp add` SILENTLY CANCELS at
  # its "Save config anyway? [y/N]:" prompt while still exiting 0. Feed it a `y` so
  # it actually registers (verified: `printf 'y\n' | hermes mcp add` writes the entry
  # with both command and args). Exit 0 is therefore NOT proof of registration — a
  # fresh list re-parse after the call is the authoritative check.
  if printf 'y\n' | "${HERMES_BIN}" mcp add "${name}" --command "${PY}" --args "${script}" >/dev/null 2>&1; then
    if ! mcp_registered_names | grep -qx -- "${name}"; then
      # Failed to actually land despite exit 0 (the silent-cancel case). Surface it;
      # do NOT fabricate a "registered" report or run the re-pin below (that is what
      # created the malformed command-only/no-args entry reported as executor).
      echo "  ✗ ${name}: add reported success but it is NOT in 'mcp list' — not registered" >&2
      failed=$((failed + 1))
    else
      # `hermes mcp add` may substitute its OWN managed venv for --command. Pin the
      # interpreter back to the fleet one through the harness's own config api, so a
      # fleet component never depends on the harness's install surviving. A re-pin
      # failure is surfaced, not swallowed.
      if "${HERMES_BIN}" config set "mcp_servers.${name}.command" "${PY}" >/dev/null 2>&1; then
        echo "  + ${name}: registered → ${script}  (interpreter ${PY})"
        added=$((added + 1))
      else
        echo "  ✗ ${name}: registered but FAILED to re-pin interpreter to ${PY} — check 'hermes config set mcp_servers.${name}.command'" >&2
        failed=$((failed + 1))
      fi
    fi
  else
    echo "  ✗ ${name}: 'hermes mcp add' FAILED" >&2
    failed=$((failed + 1))
  fi
done <<EOF
$(mcp_surface_lines)
EOF

echo ""
if (( cleanup_removed > 0 || cleanup_failed > 0 )); then
  echo "cleanup (non-orchestrator): ${cleanup_removed} stale orchestrator-only server(s) removed, ${cleanup_failed} removal(s) FAILED"
fi
echo "hermes mcp surface (${OS}): ${added} added, ${skipped} already, ${unregistered} to add, ${failed} failed, ${missing} script(s) missing"

if [[ "${MODE}" == "verify" ]] && (( missing + failed + unregistered + cleanup_failed > 0 )); then
  exit 1
fi
exit 0