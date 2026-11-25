#!/usr/bin/env python3
"""test-bus-failover-orch-debounce.py — regression test for the ORCHESTRATOR
"first-failure" false alarm in fleet-bus-failover-watchdog.py.

Root cause (Nov 2026, Luke report: "This seems wrong since Moses is up"):
the orchestrator path warned on its FIRST failed probe
(`consecutive_failures == 1`), while the WORKER path was already debounced to
WORKER_ALERT_CONSECUTIVE consecutive failures by commit 84794f85. A single
transient probe blip therefore emitted a false

    ⚠️ Moses health check FAILED (1/4) — failover to Esther will trigger
    after >15 min of continuous outage

while Moses was up and answering. The blip is real and reproducible: 25
concurrent GETs to the target's nginx health listener yielded 4x200 and
21x8s-timeouts (the documented conn_limit/burst throttle: queued connections
trip the probe's own 8 s urlopen timeout, which endpoints_reachable() reads
as "down").

Fix: the orchestrator emits its warning only after ORCH_ALERT_CONSECUTIVE
consecutive failed probes (default 2, mirroring the worker path), so a
single-tick blip produces zero output. Activation is DELIBERATELY unchanged:
still 3 consecutive failures AND FAILOVER_MIN_DOWN_MINUTES elapsed — this
change only silences the premature heads-up, never the real failover.

Usage:
  python3 tests/test-bus-failover-orch-debounce.py     # exit 0 = pass

Safe: forces orchestrator mode, injects moses_up/esther_up (no network),
forces DRY_RUN (no .env swap, no marker, no fleet notify), and redirects the
state file to a temp dir — the host's real failover state is never touched.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASS = 0
FAIL = 0

REPO = Path(__file__).resolve().parent.parent
WATCHDOG_SRC = REPO / "ops" / "scripts" / "agent" / "fleet-bus-failover-watchdog.py"


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")


def _load_watchdog():
    """Import the watchdog with state/log redirected to a temp dir, forced
    into ORCHESTRATOR (non-Moses) mode, probes injected (no network)."""
    spec = importlib.util.spec_from_file_location("wd_orch_debounce", WATCHDOG_SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wd_orch_debounce"] = mod
    spec.loader.exec_module(mod)
    tmpdir = Path(tempfile.mkdtemp(prefix="wd-orch-debounce-"))
    mod.STATE_FILE = tmpdir / "bus-failover-state.json"
    mod.LOG_FILE = tmpdir / ".bus-failover-log"
    mod._tmpdir = tmpdir
    # Force orchestrator role (esther-like): orchestrator path, not Moses.
    mod.IS_ORCHESTRATOR = True
    mod.IS_MOSES = False
    return mod


def _load_source(src: str, name: str):
    """Import watchdog source TEXT with state/log redirected to a temp dir and
    orchestrator (non-Moses) role forced — used for the git-pinned baseline."""
    tmpdir = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    path = tmpdir / f"{name}.py"          # real .py suffix or the loader yields nothing
    path.write_text(src)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    mod.STATE_FILE = tmpdir / "state.json"
    mod.LOG_FILE = tmpdir / ".log"
    mod._tmpdir = tmpdir
    mod.IS_ORCHESTRATOR = True
    mod.IS_MOSES = False
    return mod


def _pre_fix_source():
    """The watchdog source immediately BEFORE the orchestrator debounce landed.

    Derived from git — the PARENT of the commit that introduced
    ORCH_ALERT_CONSECUTIVE — so the before/after comparison re-runs forever
    instead of scoring against a copy that silently becomes the fixed code.
    Returns (source_text | None, introducing_sha | None).
    """
    path = "ops/scripts/agent/fleet-bus-failover-watchdog.py"
    r = subprocess.run(["git", "log", "-S", "ORCH_ALERT_CONSECUTIVE",
                        "--format=%H", "--", path],
                       capture_output=True, text=True, cwd=REPO, timeout=60)
    shas = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if not shas:
        return None, None
    intro = shas[-1]                       # oldest commit that introduced the symbol
    r2 = subprocess.run(["git", "show", f"{intro}~1:{path}"],
                        capture_output=True, text=True, cwd=REPO, timeout=60)
    if r2.returncode != 0:
        return None, intro
    return r2.stdout, intro


def _fresh(mod):
    mod._save_state({
        "consecutive_failures": 0, "first_failure_at": None,
        "consecutive_successes": 0, "failover_active": False,
        "last_status": "idle", "last_check_at": None,
    })


def main() -> int:
    global PASS, FAIL
    print("═══ Orchestrator failover watchdog debounce regression ═══")
    mod = _load_watchdog()
    mod.DRY_RUN = True  # no .env swap / marker / fleet notify
    urls_m = ["probe-moses"]
    urls_e = ["probe-esther"]

    # The debounce knob must exist and be at least 2 (a 1 would be "no debounce").
    knob = getattr(mod, "ORCH_ALERT_CONSECUTIVE", None)
    # Coerce for the loop bounds so a missing/absurd knob produces a VERDICT on
    # every later case instead of a TypeError crash (a crashing check cannot be
    # told apart from a passing one by its exit code alone).
    knob_n = knob if isinstance(knob, int) and knob >= 2 else 2
    if isinstance(knob, int) and knob >= 2:
        ok(f"knob: ORCH_ALERT_CONSECUTIVE={knob} (>= 2)")
    else:
        bad(f"knob: ORCH_ALERT_CONSECUTIVE missing or < 2 (got {knob!r})")

    # ── TEST A: single transient blip must be SILENT (the false-alarm fix) ──
    _fresh(mod)
    out_down = mod.run_once(moses_up=False, esther_up=True,
                            moses_urls=urls_m, esther_urls=urls_e)
    out_up = mod.run_once(moses_up=True, esther_up=True,
                          moses_urls=urls_m, esther_urls=urls_e)
    if out_down == [] and out_up == []:
        ok("A: single-tick Moses blip → NO warning and NO recovery (silent)")
    else:
        bad(f"A: blip produced output (false alarm) — down={out_down} up={out_up}")

    # ── TEST B: a sustained outage still warns (once), at the threshold ──
    _fresh(mod)
    ticks = []
    for _ in range(knob_n):
        ticks.append(mod.run_once(moses_up=False, esther_up=True,
                                  moses_urls=urls_m, esther_urls=urls_e))
    warned = [i for i, t in enumerate(ticks, 1) if any("FAILED" in l for l in t)]
    if warned == [knob_n]:
        ok(f"B: warning fires exactly once, on consecutive failure #{knob_n}")
    else:
        bad(f"B: warning timing wrong — fired on ticks {warned} (want [{knob_n}])")

    # ── TEST C: the warning must NOT re-fire while still degraded ──
    _fresh(mod)
    for _ in range(knob_n):
        mod.run_once(moses_up=False, esther_up=True,
                     moses_urls=urls_m, esther_urls=urls_e)
    later = mod.run_once(moses_up=False, esther_up=True,
                         moses_urls=urls_m, esther_urls=urls_e)
    if not any("FAILED" in l for l in later):
        ok("C: no repeat warning on a later tick while still down (once-only)")
    else:
        bad(f"C: warning re-fired while still degraded — {later}")

    # ── TEST D: ACTIVATION is unchanged (3 failures + elapsed >= threshold) ──
    _fresh(mod)
    long_ago = (datetime.now(timezone.utc)
                - timedelta(minutes=mod.MIN_DOWN_MINUTES + 5)).isoformat()
    mod._save_state({
        "consecutive_failures": 2, "first_failure_at": long_ago,
        "consecutive_successes": 0, "failover_active": False,
        "last_status": "degraded", "last_check_at": None,
    })
    act = mod.run_once(moses_up=False, esther_up=True,
                       moses_urls=urls_m, esther_urls=urls_e)
    if any("FAILOVER ACTIVATED" in l for l in act):
        ok("D: real outage still ACTIVATES failover (3 failures + elapsed)")
    else:
        bad(f"D: activation broken by the debounce — {act}")

    # ── TEST E: the test DISCRIMINATES — the pre-fix code must raise the alarm ──
    # A green run proves nothing unless the same input would have failed before
    # the fix. The baseline is materialised from GIT (the parent of the commit
    # that introduced ORCH_ALERT_CONSECUTIVE), never a stale copy, so this
    # comparison re-runs correctly forever; the extraction is asserted genuine
    # by the absence of the symbol the fix introduced.
    pre_src, intro_sha = _pre_fix_source()
    if pre_src is None:
        bad(f"E: could not materialise the pre-fix baseline from git "
            f"(introducing commit: {intro_sha}) — discrimination unproven")
    else:
        if "ORCH_ALERT_CONSECUTIVE" in pre_src:
            bad("E: baseline is NOT pre-fix — it already contains ORCH_ALERT_CONSECUTIVE")
        else:
            pre = _load_source(pre_src, "wd_prefix")
            pre.DRY_RUN = True
            pre.IS_ORCHESTRATOR = True
            pre.IS_MOSES = False
            _fresh(pre)
            pre_out = pre.run_once(moses_up=False, esther_up=True,
                                   moses_urls=urls_m, esther_urls=urls_e)
            if any("FAILED" in l for l in pre_out):
                ok("E: pre-fix baseline DOES emit the false alarm on a single blip "
                   "(the test discriminates RED -> GREEN), baseline="
                   f"{intro_sha[:8]}~1")
            else:
                bad(f"E: pre-fix baseline was silent too — the assertion is vacuous "
                    f"(baseline={intro_sha[:8]}~1, out={pre_out})")
            shutil.rmtree(getattr(pre, "_tmpdir", ""), ignore_errors=True)

    try:
        shutil.rmtree(getattr(mod, "_tmpdir", ""), ignore_errors=True)
    except OSError:
        pass

    print(f"\n═══ Summary: {PASS} passed, {FAIL} failed ═══")
    return 0 if FAIL == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
