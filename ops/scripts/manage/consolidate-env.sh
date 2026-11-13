#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# consolidate-env.sh — single .env source of truth (cortex layer)
#
# Merges the legacy bus config file (~/.steadfaste-fleet/fleet-bus.conf) into
# the single fleet env file (~/steadfaste-fleet/.env), then creates
# ~/.steadfaste-fleet/.env as a SYMLINK to it so every existing reader keeps
# working (doctor, forwarder, failover watchdog, contact-orchestrator, ...).
# The legacy fleet-bus.conf name is retired: a host that still carries it
# (real file or old symlink) is migrated to the .env name.
#
#   ⚠ ~/.hermes/.env (Hermes Agent runtime) is NEVER touched — it is
#     Hermes-owned (provider API keys, Telegram, browser/terminal
#     settings). This script refuses to operate on it.
#
# Idempotent: already-consolidated hosts exit 0 with no changes.
# Safe: backs up the legacy conf before replacing; aborts on value conflicts.
# Platforms: Linux + macOS (no bash-4-only features).
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ENV="${HOME}/steadfaste-fleet/.env"           # the canonical fleet env
DEPLOY_ENV="${HOME}/.steadfaste-fleet/.env"        # the sanctioned back-compat symlink (deploy dir)
LEGACY="${HOME}/.steadfaste-fleet/fleet-bus.conf" # retired name — migrated to DEPLOY_ENV
BACKUP="${LEGACY}.bak-$(date +%Y%m%d)"

info() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

get_val() { # <file> <key> → value (empty if absent)
  grep -E "^${2}=" "$1" 2>/dev/null | head -1 | cut -d= -f2- || true
}

# ── Dual-spelling mirror (env migration P4): FLEET_X ↔ FLEET_X ──────────────
# The canonical .env is the ONE env boundary read by every runtime entrypoint
# (gateway EnvironmentFile, fleet-bus-bridge-run's child env, fleet-update).
# During the CORTEX_* → FLEET_* migration the .env carries BOTH spellings with
# equal values, so a reader on either side — migrated or not — resolves. FLEET_*
# is canonical: a hand-set FLEET_X that DIFFERS from its FLEET_X is a data
# conflict and aborts (never guess). Idempotent and deterministic: given the
# same input it rewrites to the same output. FLEET-only vars (no CORTEX
# sibling) are preserved.
materialize_dual_spellings() {   # <env-file>
  local f="$1" tmp="${1}.dual.$$"
  local -a cm cv                                       # CORTEX_ names/values
  local -a fm fv                                       # FLEET_   names/values
  cm=(); cv=(); fm=(); fv=()
  local line key val nm i j
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
    if [[ "$key" == CORTEX_* ]]; then
      nm="${key#CORTEX_}"; cm+=("$nm"); cv+=("$val")
    elif [[ "$key" == FLEET_* ]]; then
      nm="${key#FLEET_}"; fm+=("$nm"); fv+=("$val")
    fi
  done < "$f"
  # Conflict check (FLEET canonical — a differing hand-set FLEET is a data conflict)
  local cerr=0
  for i in "${!cm[@]}"; do
    nm="${cm[$i]}"
    for j in "${!fm[@]}"; do
      [[ "${fm[$j]}" == "$nm" ]] || continue
      if [[ "${fv[$j]}" != "${cv[$i]}" ]]; then
        warn "DUAL-SPELLING CONFLICT: CORTEX_${nm}='${cv[$i]}' vs FLEET_${nm}='${fv[$j]}' — reconcile, no changes"
        cerr=1
      fi
    done
  done
  if [[ "$cerr" -ne 0 ]]; then rm -f "$tmp"; return 1; fi
  # Rebuild: keep non-var lines, keep CORTEX_ lines, emit a FLEET_ mirror after
  # each CORTEX_ line, drop stale generated FLEET_ lines, then append any
  # FLEET-only vars (no CORTEX_ sibling) so they are never lost.
  : > "$tmp"
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^(CORTEX_[A-Za-z0-9_]+)=(.*)$ ]]; then
      printf '%s\n' "$line" >> "$tmp"
      printf 'FLEET_%s=%s\n' "${BASH_REMATCH[1]#CORTEX_}" "${BASH_REMATCH[2]}" >> "$tmp"
    elif [[ "$line" =~ ^FLEET_[A-Za-z0-9_]+= ]]; then
      :   # regenerated from the CORTEX_ mirror; original dropped
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$f"
  # FLEET-only vars preserved
  for j in "${!fm[@]}"; do
    nm="${fm[$j]}"; local hasc=0
    for i in "${!cm[@]}"; do [[ "${cm[$i]}" == "$nm" ]] && hasc=1; done
    if [[ "$hasc" -eq 0 ]]; then
      printf 'FLEET_%s=%s\n' "$nm" "${fv[$j]}" >> "$tmp"
    fi
  done
  if [[ -s "$tmp" ]]; then
    mv "$tmp" "$f" 2>/dev/null && chmod 600 "$f"
  else
    rm -f "$tmp"
  fi
}

check_dual_spellings() {   # <env-file> — verify every FLEET_X has a matching FLEET_X; no mutation
  local f="$1" ck cvv fn fv2 rc=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^(CORTEX_[A-Za-z0-9_]+)=(.*)$ ]] || continue
    ck="${BASH_REMATCH[1]}"; cvv="${BASH_REMATCH[2]}"; fn="FLEET_${ck#CORTEX_}"
    fv2="$(get_val "$f" "$fn")"
    if [[ -z "$fv2" ]]; then
      warn "check: ${ck} lacks ${fn} mirror (run consolidate-env.sh without --check to materialize)"; rc=1
    elif [[ "$fv2" != "$cvv" ]]; then
      warn "check: ${ck}='${cvv}' differs from ${fn}='${fv2}'"; rc=1
    fi
  done < "$f"
  return "$rc"
}



# ── Guard 1: never touch the Hermes runtime env ───────────────
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1 || CHECK_ONLY=0

# ── Already consolidated? ────────────────────────────────────
if [[ -L "${DEPLOY_ENV}" ]]; then
  target="$(readlink "${DEPLOY_ENV}" 2>/dev/null || true)"
  if [[ "${target}" == "${REPO_ENV}" ]]; then
    # A leftover retired-name symlink is inert (same inode) — drop it.
    if [[ -e "${LEGACY}" ]] || [[ -L "${LEGACY}" ]]; then rm -f "${LEGACY}" 2>/dev/null || true; fi
    # Already consolidated — still ensure dual spellings (the .env may have
    # gained CORTEX_* vars since the last run; FLEET_* must track them for
    # every runtime boundary). Check mode verifies; write mode materializes.
    if [[ -f "${REPO_ENV}" ]]; then
      if [[ "${CHECK_ONLY}" == "1" ]]; then
        if ! check_dual_spellings "${REPO_ENV}"; then
          info "check: dual-spelling mirrors NOT fully present"; exit 1
        fi
      else
        materialize_dual_spellings "${REPO_ENV}" || warn "dual-spelling conflict; FLEET_* not guaranteed on this host"
      fi
    fi
    info "already consolidated: ${DEPLOY_ENV} → ${REPO_ENV}"
    exit 0
  fi
  fail "${DEPLOY_ENV} is a symlink to ${target}, not ${REPO_ENV} — refusing to touch"
fi

# Seed the canonical env from the legacy conf when it does not exist yet —
# hosts installed before the single-env refactor otherwise break, since every
# script now reads ${REPO_ENV}. (Never overwrite an existing canonical env.)
if [[ ! -f "${REPO_ENV}" ]]; then
  if [[ -f "${LEGACY}" && ! -L "${LEGACY}" ]]; then
    cp "${LEGACY}" "${REPO_ENV}"
    chmod 600 "${REPO_ENV}"
    info "seeded ${REPO_ENV} from ${LEGACY} (migration)"
  else
    fail "neither ${REPO_ENV} nor ${LEGACY} exists — run install.sh first"
  fi
fi

# ── Guard 2: a stray real-file .env in the deploy dir ────────
# Historically ~/.steadfaste-fleet/.env was a stray symlink to ~/langfuse/.env.
# The .env name is now OURS (canonical + sanctioned symlink), so a REAL file
# at the deploy path is not something consolidate-env created — refuse to own it.
if [[ -e "${DEPLOY_ENV}" && ! -L "${DEPLOY_ENV}" ]]; then
  fail "${DEPLOY_ENV} is a real file (not a symlink) — refuse to replace unowned data"
fi

# ── Collect legacy vars missing from the target (conflict-safe) ─
missing_keys=()
missing_vals=()
if [[ -f "${LEGACY}" && ! -L "${LEGACY}" ]]; then
  while IFS= read -r line; do
    [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    existing="$(get_val "${REPO_ENV}" "${key}")"
    if [[ -n "${existing}" ]]; then
      if [[ "${existing}" != "${val}" ]]; then
        fail "CONFLICT on ${key}: ${REPO_ENV} has '${existing}', ${LEGACY} has '${val}' — no changes made"
      fi
      continue  # same value — nothing to merge
    fi
    missing_keys+=("${key}")
    missing_vals+=("${val}")
  done < "${LEGACY}"
fi

if [[ "${CHECK_ONLY}" == "1" ]]; then
  if [[ ${#missing_keys[@]} -eq 0 ]]; then
    info "check: ${LEGACY} fully represented in ${REPO_ENV}"
  else
    warn "check: ${#missing_keys[@]} var(s) in ${LEGACY} missing from ${REPO_ENV}: ${missing_keys[*]}"
    exit 1
  fi
  # Mirrors: every FLEET_X should have a FLEET_X of the same value (and vice
  # versa). Report any missing/mismatched without mutating.
  if ! check_dual_spellings "${REPO_ENV}"; then
    info "check: dual-spelling mirrors NOT fully present"
    exit 1
  fi
  exit 0
fi

# ── Merge ────────────────────────────────────────────────────
if [[ ${#missing_keys[@]} -gt 0 ]]; then
  {
    echo ""
    echo "# ── Merged from ${LEGACY} by consolidate-env.sh ($(date +%F)) ──"
    for i in "${!missing_keys[@]}"; do
      echo "${missing_keys[$i]}=${missing_vals[$i]}"
    done
  } >> "${REPO_ENV}"
  info "merged ${#missing_keys[@]} var(s): ${missing_keys[*]}"
else
  info "no new vars in ${LEGACY} — only the symlink step remains"
fi

# ── Materialize dual spellings (FLEET_X ↔ FLEET_X) ──────────────────────
# So every runtime boundary (gateway EnvironmentFile, bridge-run child env,
# fleet-update) resolves FLEET_* even before its readers are migrated. A
# hand-set FLEET that differs from CORTEX is surfaced as a warning (the
# operator reconciles it); until then FLEET_* is not guaranteed on that host.
if ! materialize_dual_spellings "${REPO_ENV}"; then
  warn "dual-spelling materialization skipped — ${REPO_ENV} has a FLEET_*/CORTEX_* value conflict to reconcile"
fi

# ── Create the sanctioned .env symlink ───────────────────────
rm -f "${DEPLOY_ENV}" 2>/dev/null || true
ln -s "${REPO_ENV}" "${DEPLOY_ENV}"

# ── Retire the legacy name (real files get a backup first) ──
if [[ -e "${LEGACY}" || -L "${LEGACY}" ]]; then
  if [[ -f "${LEGACY}" && ! -L "${LEGACY}" ]]; then
    cp "${LEGACY}" "${BACKUP}" 2>/dev/null || warn "could not back up ${LEGACY} to ${BACKUP}"
  fi
  rm -f "${LEGACY}" 2>/dev/null || true
fi

info "${DEPLOY_ENV} → ${REPO_ENV} (fleet-bus.conf retired)"
if [[ -f "${BACKUP}" ]]; then
  info "legacy backup kept at ${BACKUP}"
fi
info "~/.hermes/.env (Hermes runtime) untouched by design — it stays Hermes-owned."