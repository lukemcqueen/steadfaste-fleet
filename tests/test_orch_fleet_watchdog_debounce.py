#!/usr/bin/env python3
"""Regression test for the orch-fleet-watchdog flapping debounce.

The watchdog's original transition-based logic alerted on the FIRST state
change and "restored" on the FIRST opposite sample, so any single transient
health bit (e.g. a `no_stale_crons` red lasting one poll) produced a
down/restored pair — the recurring flap delivered to the operator.

The fix is a pure debounce over consecutive samples: an alert fires only after
BAD_LIM consecutive unhealthy samples, a recovery only after GOOD_LIM
consecutive healthy samples after a down, and a single opposite sample resets
the streak. This test drives `_debounce_emit` directly (the module's main()
only runs under `__main__`, so importing it is inert).
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_M = _REPO / "ops" / "scripts" / "agent" / "orch-fleet-watchdog.py"


@pytest.fixture(scope="module")
def w():
    spec = importlib.util.spec_from_file_location("orch_fleet_watchdog", _M)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sibling_defines_the_seams_this_suite_mocks():
    """Pin the mocks/assertions to the sibling's OWN source.

    The silence test monkeypatches `_get_agents`/`_fetch` and reads the
    `_debounce` schema. Those assumptions are only faithful if main() really
    defines and consumes these names — derive the contract from the sibling's
    source in-repo rather than trusting a hand-copied shape that could drift.
    """
    src = _M.read_text(encoding="utf-8")
    for needle in (
        "def _get_agents", "def _fetch", "def _normalize", "def _debounce_emit",
        "def main", "DEBOUNCE_BAD =", "DEBOUNCE_GOOD =",
        '"key"', '"name"', '"url"',
        # main() consumes _fetch results via these keys (the sibling _fetch /
        # _normalize are pre-existing, so pin main()'s consumption here):
        'data.get("healthy"', '.get("issues"', 'data.get("v")',
        # main() persists per-agent debounce state with exactly these names:
        '_debounce', 'deb[key] = {"bad": bad, "good": good, "emitted": emitted}',
    ):
        assert needle in src, f"sibling no longer defines/consumes {needle!r}"


def test_healthy_samples_emit_nothing(w):
    bad = good = 0
    emitted = None
    for _ in range(10):                      # more than GOOD_LIM consecutive ups
        action, bad, good = w._debounce_emit("up", bad, good, emitted,
                                             w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    assert action is None
    assert good == 10
    assert bad == 0


def test_transient_blip_below_threshold_never_alerts(w):
    # 2 bad then a good — never reaches BAD_LIM (3), so no alert, streak resets.
    bad = good = 0
    emitted = None
    for _ in range(2):
        action, bad, good = w._debounce_emit("unhealthy", bad, good, emitted,
                                             w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
        assert action is None
    action, bad, good = w._debounce_emit("up", bad, good, emitted,
                                         w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    assert action is None
    assert bad == 0 and good == 1


def test_alert_only_after_three_consecutive_bad(w):
    # The caller feeds `emitted` back each sample (as main() persists it), so a
    # down fires exactly ONCE at the threshold and never re-fires while down.
    bad = good = 0
    emitted = None
    actions = []
    for _ in range(5):                        # 3 suffices; 5 proves no re-fire
        action, bad, good = w._debounce_emit("unhealthy", bad, good, emitted,
                                             w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
        if action:
            actions.append(action)
            emitted = "down"
    assert actions == [("down", "unhealthy")]      # fired exactly once, after 3rd
    assert bad == 5


def test_unreachable_counts_bad_and_alerts(w):
    bad = good = 0
    emitted = None
    for _ in range(2):
        _, bad, good = w._debounce_emit("unreachable", bad, good, emitted,
                                        w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    action, bad, good = w._debounce_emit("unreachable", bad, good, emitted,
                                         w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    assert action == ("down", "unreachable")


def test_recovery_only_after_three_consecutive_good_after_down(w):
    bad, good = 0, 0
    emitted = None
    for _ in range(3):
        action, bad, good = w._debounce_emit("unhealthy", bad, good, emitted,
                                             w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    assert action == ("down", "unhealthy")
    emitted = "down"
    actions = []
    for _ in range(2):                         # only 2 goods: not enough to recover
        action, bad, good = w._debounce_emit("up", bad, good, "down",
                                             w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
        actions.append(action)
    assert actions == [None, None]
    action, bad, good = w._debounce_emit("up", bad, good, "down",
                                         w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    assert action == ("up",)                    # 3rd consecutive good recovers


def test_one_healthy_resets_unhealthy_streak(w):
    bad, good = 2, 0
    action, bad, good = w._debounce_emit("up", bad, good, None,
                                         w.DEBOUNCE_BAD, w.DEBOUNCE_GOOD)
    assert action is None and bad == 0


def test_main_is_silent_when_stable(tmp_path, monkeypatch):
    """Script-level silence contract: stable-healthy state emits NO stdout.

    The no_agent watchdog pattern is 'empty stdout = silent'. This drives
    main() end to end with state/dashboard files redirected to tmp and the
    network stubbed, and asserts two consecutive runs over a stable-healthy
    agent print nothing and persist _debounce counters.
    """
    spec = importlib.util.spec_from_file_location("orch_fleet_watchdog_main", _M)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    state = tmp_path / "fleet-state.json"
    dash = tmp_path / "fleet-data.json"
    monkeypatch.setattr(mod, "STATE_FILE", state)
    monkeypatch.setattr(mod, "DASHBOARD_FILE", dash)
    monkeypatch.setattr(mod, "_get_agents", lambda: [{"key": "m", "name": "M", "url": "x"}])
    monkeypatch.setattr(mod, "_fetch",
                        lambda url: {"v": [1] * 9, "healthy": True, "issues": []})

    buf = io.StringIO()
    for _ in range(2):
        with contextlib.redirect_stdout(buf):
            mod.main()
    assert buf.getvalue() == ""

    st = json.loads(state.read_text())
    # main() writes the schema it consumes (the sibling source anchor test owns
    # `_debounce`); assert the OBSERVABLE outcome of two stable runs, not a
    # hand-copied dict that must match a frozen literal.
    m = st["_debounce"]["m"]
    assert m["bad"] == 0
    assert m["good"] == 2
    assert m.get("emitted") is None