#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  mcp-surface.inc.sh — the ONE definition of the fleet MCP surface.
#
#  Sourced by every per-harness adapter (install-hermes-mcp.sh, install-codex-mcp.sh,
#  install-opencode-mcp.sh, install-fleet-mcp.sh). A server list copied into N
#  adapters is N lists that drift the moment one changes — the adapters convert a
#  CALLING CONVENTION and add no semantics, so the surface itself has one home.
#
#  PORTABILITY. Titus is macOS; every other host is Linux. Bash here is the
#  macOS stock 3.2, so: no associative arrays, no `mapfile`, no GNU `readlink -f`,
#  no `sed -i`, no `${var,,}`. Anything needing those goes through python3.
# ─────────────────────────────────────────────────────────────────────────────

MCP_SURFACE_VERSION="1"

# ── OS / interpreter resolution (macOS + Linux) ──────────────────────────────
host_os() {
  case "$(uname -s 2>/dev/null)" in
    Darwin) echo "macos" ;;
    Linux)  echo "linux" ;;
    *)      echo "other" ;;
  esac
}

# python3 first on PATH, else the Homebrew/Apple locations, else /usr/bin.
# On macOS the interpreter is frequently NOT on a non-interactive PATH even
# though it is installed (Homebrew lives at /opt/homebrew or /usr/local).
resolve_python() {
  if [[ -n "${PYTHON:-}" && -x "${PYTHON}" ]]; then
    printf '%s' "${PYTHON}"
    return 0
  fi
  local cand
  for cand in \
      "$(command -v python3 2>/dev/null || true)" \
      /opt/homebrew/bin/python3 \
      /usr/local/bin/python3 \
      /usr/bin/python3; do
    if [[ -n "$cand" && -x "$cand" ]]; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

# The harness CLI may be absent from PATH on a non-interactive macOS shell.
resolve_cli() {
  # $1 = bare command name
  local name="$1" cand
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi
  for cand in \
      "${HOME}/.local/bin/${name}" \
      "${HOME}/bin/${name}" \
      /opt/homebrew/bin/"${name}" \
      /usr/local/bin/"${name}"; do
    if [[ -x "$cand" ]]; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

# ── The surface ──────────────────────────────────────────────────────────────
# TAB-separated: name <TAB> deployed script <TAB> repo-relative source.
# The deployed path is what the harness must execute; the repo source is the
# fallback for a host running from a clone that has not been deployed yet.
#
# NOTE: this is the executable projection of the same surface declared in
# ops/install/harnesses/registry.yaml (`defaults.mcp_server`). A test pins the
# two together so neither can drift alone.
mcp_surface_lines() {
  cat <<'SURFACE'
fleet-context	${HOME}/.steadfaste-fleet/scripts/fleet-context-mcp.py	mcp-servers/fleet-context-mcp.py
agent-bus	${HOME}/.steadfaste-fleet/scripts/fleet-bus-mcp.py	mcp-servers/fleet-bus-mcp.py
tasks	${HOME}/.steadfaste-fleet/scripts/task-mcp.py	mcp-servers/task-mcp.py
executor	${HOME}/.steadfaste-fleet/scripts/executor-mcp.py	mcp-servers/executor-mcp.py
SURFACE
}

# Resolve a surface row to a real file path (deployed first, then repo source).
# Prints nothing when neither exists — the caller reports that as a fault.
mcp_surface_resolve() {
  # $1 = deployed path (unexpanded), $2 = repo-relative source, $3 = repo root
  local deployed="$1" src="$2" repo="$3"
  deployed="${deployed//\$\{HOME\}/${HOME}}"
  if [[ -f "$deployed" ]]; then
    printf '%s' "$deployed"
  elif [[ -n "$repo" && -f "${repo}/$src" ]]; then
    printf '%s' "${repo}/$src"
  fi
}

# The repo root, resolved WITHOUT readlink -f (absent on macOS).
mcp_surface_repo_root() {
  if [[ -n "${REPO:-}" ]]; then
    printf '%s' "${REPO}"
    return 0
  fi
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # .../ops/scripts/install -> repo root is three levels up
  printf '%s' "$(cd "${here}/../../.." && pwd)"
}