#!/usr/bin/env bash
# migrate-victoria-data.sh — move the LIVE VictoriaMetrics data dir off a stale
# pre-migration home path, repoint the container, and only then remove the stale dir.
#
# WHY THIS EXISTS (the trap — see docs/runbooks/live-bindmount-stale-dir.md):
#   The doctor reports `$HOME/.hermes-cortex` as a STALE pre-migration home folder
#   and suggests `rm -rf`. On this host that directory is NOT dead weight —
#   `victoria-data/` inside it is the LIVE, root-owned VictoriaMetrics time-series
#   database, bind-mounted RW into the running `victoria-metrics` container:
#       Source=$HOME/.hermes-cortex/victoria-data -> /victoria-metrics-data (RW)
#   A naive `rm -rf $HOME/.hermes-cortex` DESTROYS THE LIVE METRICS DB.
#
#   `migrate-home-dirs.sh` CANNOT handle this pair: it refuses when the target
#   already exists ("target already exists as a real dir; refusing to nest"), and
#   `$HOME/.steadfaste-fleet` does already exist. Hence this script.
#
# SAFETY MODEL (destructive work, built to be reversible):
#   1. Every container fact (image, restart policy, ports, entrypoint, args) is read
#      from `docker inspect` of the LIVE container at run time. Nothing is hardcoded,
#      and no secret is written to stdout, argv, or a file — the container's args may
#      carry credentials (this one carries -httpAuth.password).
#   2. The old container is STOPPED and RENAMED, never removed first: it is a complete
#      rollback artefact until the new one is verified.
#   3. The data dir is MOVED (atomic on one filesystem), never copy-then-delete.
#   4. Verification runs BEFORE any cleanup. On failure the old container is restored
#      and the script exits non-zero with the old data untouched.
#
# Usage:  sudo bash ops/scripts/manage/migrate-victoria-data.sh           # dry run
#         sudo bash ops/scripts/manage/migrate-victoria-data.sh --apply   # execute
set -euo pipefail

CONTAINER="${VICTORIA_CONTAINER:-victoria-metrics}"
OLD_DIR="${VICTORIA_OLD_DIR:-$HOME/.hermes-cortex/victoria-data}"
NEW_DIR="${VICTORIA_NEW_DIR:-$HOME/.steadfaste-fleet/victoria-data}"
CONTAINER_MNT="/victoria-metrics-data"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

log() { echo "migrate-victoria-data: $*"; }
die() { echo "migrate-victoria-data: ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
[ "$(id -u)" = "0" ] || die "must run as root (the data dir is root-owned); use sudo"
docker inspect "$CONTAINER" >/dev/null 2>&1 || die "container '$CONTAINER' not found"

inspect() { docker inspect "$CONTAINER" --format "$1"; }

IMAGE="$(inspect '{{.Config.Image}}')"
RESTART="$(inspect '{{.HostConfig.RestartPolicy.Name}}')"
[ -n "$RESTART" ] || RESTART="unless-stopped"

PORTS="$(docker inspect "$CONTAINER" --format \
  '{{range $p, $bs := .HostConfig.PortBindings}}{{range $bs}}{{.HostPort}}:{{(index (split $p "/") 0)}}/{{index (split $p "/") 1}}{{"\n"}}{{end}}{{end}}')"

ENV_JSON="$(inspect '{{json .Config.Env}}')"
CMD_JSON="$(inspect '{{json .Config.Cmd}}')"
ENTRY_JSON="$(inspect '{{json .Config.Entrypoint}}')"

SRC_MOUNT="$(docker inspect "$CONTAINER" --format \
  '{{range .Mounts}}{{if eq .Destination "'"${CONTAINER_MNT}"'"}}{{.Source}}{{end}}{{end}}')"
[ "$SRC_MOUNT" = "$OLD_DIR" ] \
  || die "container source mount is '$SRC_MOUNT', expected '$OLD_DIR' — refusing to guess"

# Emit a JSON string array as NUL-separated bytes on stdout. Never prints.
json_args_to_nul() {
  python3 -c '
import json, sys
for a in (json.loads(sys.argv[1]) or []):
    sys.stdout.buffer.write(a.encode("utf-8") + b"\x00")
' "$1"
}

log "plan:"
log "  container : $CONTAINER  (image $IMAGE, restart $RESTART)"
log "  ports     : $(printf '%s' "$PORTS" | tr '\n' ' ')"
log "  data move : $OLD_DIR  ->  $NEW_DIR"
log "  then      : recreate container on $NEW_DIR, verify, then remove the stale dir"

if [ "$APPLY" -ne 1 ]; then
  log "DRY RUN — nothing changed. Re-run with --apply to execute."
  exit 0
fi

[ -d "$OLD_DIR" ] || die "old data dir missing: $OLD_DIR"
[ ! -e "$NEW_DIR" ] || die "target $NEW_DIR already exists — resolve manually (never merge two TSDB trees)"

log "stopping $CONTAINER and parking it as ${CONTAINER}-rollback"
docker stop "$CONTAINER" >/dev/null
docker rename "$CONTAINER" "${CONTAINER}-rollback"

rollback() {
  log "ROLLING BACK: $1"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  # return the DATA too — the move must be reversible, not just the container
  if [ -d "$NEW_DIR" ] && [ ! -e "$OLD_DIR" ]; then
    mkdir -p "$(dirname "$OLD_DIR")"
    mv "$NEW_DIR" "$OLD_DIR" 2>/dev/null \
      || log "WARNING: could not move the data back to $OLD_DIR — it is at $NEW_DIR"
  fi
  docker rename "${CONTAINER}-rollback" "$CONTAINER" >/dev/null 2>&1 || true
  docker start "$CONTAINER" >/dev/null 2>&1 || true
  die "$1 — old container restored and data returned to $OLD_DIR"
}

log "moving data"
mkdir -p "$(dirname "$NEW_DIR")"
mv "$OLD_DIR" "$NEW_DIR" || rollback "mv of the data dir failed"

log "recreating $CONTAINER bound to $NEW_DIR"
RUN=(docker run -d --name "$CONTAINER" --restart "$RESTART")
while IFS= read -r line; do
  [ -n "$line" ] && RUN+=(-p "$line")
done <<EOF
$PORTS
EOF
while IFS= read -r e; do
  [ -n "$e" ] && RUN+=(-e "$e")
done < <(json_args_to_nul "$ENV_JSON" | tr '\0' '\n')
RUN+=(-v "$NEW_DIR:$CONTAINER_MNT")

if [ "$ENTRY_JSON" != "null" ]; then
  entry_tmp="$(mktemp)"
  json_args_to_nul "$ENTRY_JSON" > "$entry_tmp"
  if [ -s "$entry_tmp" ]; then
    while IFS= read -r -d '' a; do RUN+=(--entrypoint "$a"); break; done < "$entry_tmp"
  fi
  rm -f "$entry_tmp"
fi

RUN+=("$IMAGE")

cmd_tmp="$(mktemp)"
json_args_to_nul "$CMD_JSON" > "$cmd_tmp"
while IFS= read -r -d '' a; do RUN+=("$a"); done < "$cmd_tmp"
rm -f "$cmd_tmp"

NEW_ID=""
# Explicit if-form on purpose: it does not depend on how errexit interacts with
# an assignment, so the rollback cannot be skipped by a quoting or shell-mode
# change. (Measured 2026-11-24: the previous `A="$(cmd)" || rollback` DID fire —
# an adversarial finding claimed it could not. Kept the explicit form because it
# is unconditionally clear, NOT because the old one was broken.)
if ! NEW_ID="$("${RUN[@]}")"; then
  rollback "docker run failed"
fi
log "started $CONTAINER (${NEW_ID:0:12})"

# ---- 4. VERIFY before any cleanup -------------------------------------------
# A running process is NOT proof the TSDB opened: a container can report Running
# and exit seconds later on a store it could not open. So the gate polls for the
# REAL startup markers this image emits — measured from live logs, never guessed:
#   "successfully opened storage"  (the TSDB opened)
#   "started VictoriaMetrics in"   (the http server is up)
# and it RE-INSPECTS the new container's mount source instead of trusting the
# run command it just built.
verify() {
  local got_src
  got_src="$(docker inspect "$CONTAINER" --format \
    "{{range .Mounts}}{{if eq .Destination \"${CONTAINER_MNT}\"}}{{.Source}}{{end}}{{end}}" 2>/dev/null || true)"
  [ "$got_src" = "$NEW_DIR" ] || { echo "mount source '$got_src' != '$NEW_DIR'"; return 1; }
  docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q '^true$' \
    || { echo "container is not running"; return 1; }
  docker logs "$CONTAINER" 2>&1 | grep -q 'successfully opened storage' \
    || { echo "TSDB never reported 'successfully opened storage'"; return 1; }
  docker logs "$CONTAINER" 2>&1 | grep -q 'started VictoriaMetrics in' \
    || { echo "http server never started"; return 1; }
  return 0
}

WHY="verification never ran"
VERIFIED=0
for _ in $(seq 1 30); do
  if WHY="$(verify)"; then VERIFIED=1; break; fi
  sleep 2
done
[ "$VERIFIED" = 1 ] || rollback "verification failed after 60s: $WHY"

NEW_COUNT="$(find "$NEW_DIR" -type f 2>/dev/null | wc -l)"
[ "$NEW_COUNT" -gt 0 ] || rollback "new data dir is empty — refusing to proceed"

# the container must SURVIVE a settle window, not merely pass one instant
sleep 5
docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q '^true$' \
  || rollback "new container died inside the settle window"
log "verified: TSDB opened, http server started, $NEW_COUNT file(s), survived settle"

# ---- 5. cleanup (only reached when every gate above passed) -----------------
docker rm "${CONTAINER}-rollback" >/dev/null
rm -rf "$OLD_DIR"
STALE="$(dirname "$OLD_DIR")"
if [ -d "$STALE" ]; then
  if find "$STALE" -mindepth 1 -print -quit | grep -q .; then
    log "NOTE: $STALE not empty — left in place"
  else
    rmdir "$STALE"
  fi
fi
log "done — stale dir removed, container live on $NEW_DIR"
