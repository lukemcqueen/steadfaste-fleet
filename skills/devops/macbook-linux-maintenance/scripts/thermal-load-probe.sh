#!/usr/bin/env bash
# thermal-load-probe.sh — bounded CPU-load thermal probe for Apple laptops (applesmc).
#
# Emits a transcript: idle baseline -> N `yes` workers -> recovery, sampling
# fans / package / heatsink / load / core freq / CPU PSI every SAMPLE_SECS.
# Aborts the load phase if the package reaches ABORT_AT (micro-C, default
# derived from temp1_crit minus a margin) so an unattended run cannot walk the
# host into a thermal shutdown.
#
#   WORKERS=8 LOAD_SECS=120 bash thermal-load-probe.sh
#
# Start it as a BACKGROUND terminal job — the harness refuses a foreground '&' —
# then wait for the end marker and read the artifact, rather than hand-polling:
#
#   terminal(command="bash scripts/thermal-load-probe.sh", background=true, notify=true)
#   terminal(command="timeout 250 bash -c 'F=$(ls -t $OUT/thermal-*.txt | head -1);
#       until grep -q "thermal-probe end" "$F"; do sleep 5; done; cat "$F"'")
#
# Report the BASELINE beside the peak: if the baseline is already hot the run
# measures the thermal CEILING, not a cold-start ramp — say which one it is.
set -u

OUT="${OUT:-$HOME/.hermes/cache/scratch/thermal}"
SMC="${SMC:-/sys/devices/platform/applesmc.768}"
PKG="${PKG:-/sys/class/hwmon/hwmon1/temp1_input}"   # coretemp package
FREQ="${FREQ:-/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq}"
WORKERS="${WORKERS:-8}"
LOAD_SECS="${LOAD_SECS:-120}"
SAMPLE_SECS="${SAMPLE_SECS:-5}"
MARGIN="${MARGIN:-6000}"                             # micro-C below temp1_crit

mkdir -p "$OUT"
F="$OUT/thermal-$(date +%Y%m%d-%H%M%S).txt"

crit=$(cat "${PKG%/*}/temp1_crit" 2>/dev/null || echo 100000)
ABORT_AT="${ABORT_AT:-$(( crit - MARGIN ))}"

pids=""
cleanup() { [ -n "$pids" ] && kill $pids 2>/dev/null; return 0; }
trap cleanup EXIT

sample() {
  printf '%s %-8s f1=%s f2=%s pkg=%s hs=%s load=%s mhz=%s psi=%s\n' \
    "$(date +%H:%M:%S)" "$1" \
    "$(cat "$SMC/fan1_input")" "$(cat "$SMC/fan2_input")" \
    "$(cat "$PKG")" "$(cat "$SMC/temp5_input")" \
    "$(cut -d' ' -f1 /proc/loadavg)" \
    "$(cut -d' ' -f1 "$FREQ" 2>/dev/null || echo n/a)" \
    "$(cut -d' ' -f2 /proc/pressure/cpu 2>/dev/null || echo n/a)"
}

{
  echo "thermal-probe start $(date -Is)"
  echo "workers=$WORKERS load_secs=$LOAD_SECS sample_secs=$SAMPLE_SECS abort_at_pkg=$ABORT_AT crit=$crit"
  echo "powerclamp_loaded=$(lsmod | grep -c intel_powerclamp) idle_inject_threads=$(ps -eo comm= | grep -c idle_inject)"
  echo "--- baseline (no load) ---"
  for _ in 1 2 3; do sample baseline; sleep "$SAMPLE_SECS"; done

  echo "--- load phase ---"
  for _ in $(seq 1 "$WORKERS"); do yes > /dev/null & pids="$pids $!"; done
  for i in $(seq 1 $(( LOAD_SECS / SAMPLE_SECS ))); do
    sample load
    if [ "$(cat "$PKG")" -ge "$ABORT_AT" ]; then
      echo "ABORT package reached $ABORT_AT uC at load sample $i"; break
    fi
    sleep "$SAMPLE_SECS"
  done
  kill $pids 2>/dev/null; pids=""

  echo "--- recovery (load removed) ---"
  for _ in $(seq 1 9); do sample recovery; sleep "$SAMPLE_SECS"; done
  echo "thermal-probe end $(date -Is)"
} 2>&1 | tee "$F"

echo "ARTIFACT=$F"
