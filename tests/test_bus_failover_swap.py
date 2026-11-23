#!/usr/bin/env python3
"""swap_bus_config must be IDEMPOTENT: a repeat swap must never overwrite the
peer slot with the LOCAL bus.

Incident (2026-11-23, backup orchestrator): both FLEET_BUS_URL and
FLEET_BUS_FALLBACK_URL held the loopback bus URL. swap_bus_config derives the
peer from the very values it is about to overwrite, so a SECOND activation
(failover flap, drill, or a restore run while already swapped) stored the local
bus as the peer. Consequences, all measured on the host:

  * orch-bus-forwarder-sync alerted `LOCAL→PEER: N failed` with
    401 "Missing or invalid Authorization header" every tick — Basic auth sent
    to the raw local bus, which accepts only Bearer or nginx's X-Forwarded-User;
  * the forwarder's own peer probe pointed at loopback, so it could never
    observe recovery (258 consecutive failures);
  * the real peer URL survived ONLY in a .env backup — recovery from .env alone
    was impossible, which is the part that makes this a class bug, not a blip.

The contract these tests pin: capture the steady-state pair ONCE (while .env
still holds real URLs), reuse it for every later swap, and FAIL CLOSED — never
write a local value into the peer slot, and never guess a peer that is not
there.

Hermetic: the watchdog module is loaded from the repo, CONF_FILE is redirected
to a temp file, and the ambient FLEET_BUS_* env vars are cleared (the module's
_env_value gives the process env priority over the file, so an inherited value
would silently replace the fixture).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "ops" / "scripts" / "agent" / "fleet-bus-failover-watchdog.py"

PEER = "https://peer.example.org:13004"   # the other orchestrator's bus
SELF = "https://self.example.org:14004"   # this host's own external bus
LOCAL = "http://127.0.0.1:8903"


def _read(conf: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in conf.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


@pytest.fixture()
def wd(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("wd_swap", _SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wd_swap"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.delenv("FLEET_BUS_URL", raising=False)
    monkeypatch.delenv("FLEET_BUS_FALLBACK_URL", raising=False)
    conf = tmp_path / ".env"
    conf.write_text(f"FLEET_BUS_URL={PEER}\nFLEET_BUS_FALLBACK_URL={SELF}\nUNRELATED=keep-me\n")
    monkeypatch.setattr(mod, "CONF_FILE", conf)
    monkeypatch.setattr(mod, "LOG_FILE", tmp_path / "log")
    mod._conf = conf
    return mod


def test_a_repeat_activation_keeps_the_peer_url(wd):
    """THE incident: activating twice must not destroy the peer URL."""
    assert wd.swap_bus_config(active=True) is True
    assert _read(wd._conf)["FLEET_BUS_URL"] == LOCAL
    assert _read(wd._conf)["FLEET_BUS_FALLBACK_URL"] == PEER

    assert wd.swap_bus_config(active=True) is True
    after = _read(wd._conf)
    assert after["FLEET_BUS_FALLBACK_URL"] == PEER, (
        "a repeat activation overwrote the peer slot with the local bus")
    assert after["FLEET_BUS_FALLBACK_URL"] != LOCAL


def test_restore_returns_the_steady_state_not_the_local_bus(wd):
    """Recovery must bring back the real peer, not leave loopback in the
    primary slot (which silently re-collapses the fleet config)."""
    assert wd.swap_bus_config(active=True) is True
    assert wd.swap_bus_config(active=False) is True
    after = _read(wd._conf)
    assert after["FLEET_BUS_URL"] == PEER, "restore left the local bus as primary"
    assert after["FLEET_BUS_FALLBACK_URL"] == SELF


def test_restore_is_idempotent(wd):
    wd.swap_bus_config(active=True)
    wd.swap_bus_config(active=False)
    wd.swap_bus_config(active=False)
    after = _read(wd._conf)
    assert after["FLEET_BUS_URL"] == PEER
    assert after["FLEET_BUS_FALLBACK_URL"] == SELF


def test_the_anchor_makes_restore_work_after_a_collapsed_file(wd):
    """The anchor is the mechanism that survives: captured once, then reused."""
    steady = wd.capture_steady_urls()
    assert steady == {"peer": PEER, "self": SELF}

    wd.swap_bus_config(active=True, steady=steady)
    wd.swap_bus_config(active=True, steady=steady)     # repeat — the flap case
    assert wd.swap_bus_config(active=False, steady=steady) is True
    after = _read(wd._conf)
    assert after["FLEET_BUS_URL"] == PEER
    assert after["FLEET_BUS_FALLBACK_URL"] == SELF


def test_capture_returns_nothing_when_both_values_are_local(wd):
    """An anchor must never be captured FROM a collapsed file — that would
    enshrine the loopback URL as the peer."""
    wd._conf.write_text(f"FLEET_BUS_URL={LOCAL}\nFLEET_BUS_FALLBACK_URL={LOCAL}\n")
    assert wd.capture_steady_urls() == {}


def test_activation_fails_closed_when_no_peer_url_exists(wd):
    """Collapsed file, no anchor: refuse. Writing the local bus into the peer
    slot is exactly the corruption this fix exists to prevent."""
    wd._conf.write_text(f"FLEET_BUS_URL={LOCAL}\nFLEET_BUS_FALLBACK_URL={LOCAL}\n")
    before = wd._conf.read_text()
    assert wd.swap_bus_config(active=True) is False
    assert wd._conf.read_text() == before, "refused swap still rewrote the file"


def test_restore_fails_closed_when_the_peer_is_unrecoverable(wd):
    wd._conf.write_text(f"FLEET_BUS_URL={LOCAL}\nFLEET_BUS_FALLBACK_URL={LOCAL}\n")
    before = wd._conf.read_text()
    assert wd.swap_bus_config(active=False) is False
    assert wd._conf.read_text() == before


def test_the_marker_survives_a_collapsed_pair(wd):
    """The anchor must outlive the corruption it exists to repair: after a swap
    the pair is recorded IN .env, so even if both keys are later collapsed (the
    2026-11-23 state) the peer is still recoverable without a backup."""
    wd.swap_bus_config(active=True)
    assert wd.capture_steady_urls() == {"peer": PEER, "self": SELF}

    # Simulate the corruption: both keys rewritten to loopback by the old code.
    text = wd._conf.read_text()
    collapsed = "\n".join(
        f"{k}={LOCAL}" if k.startswith("FLEET_BUS_") else ln
        for ln in text.splitlines()
        for k in [ln.split("=", 1)[0] if "=" in ln else ""])
    wd._conf.write_text(collapsed + "\n")

    assert wd.capture_steady_urls() == {"peer": PEER, "self": SELF}
    assert wd.swap_bus_config(active=False) is True
    after = _read(wd._conf)
    assert after["FLEET_BUS_URL"] == PEER
    assert after["FLEET_BUS_FALLBACK_URL"] == SELF


def test_unrelated_lines_are_preserved(wd):
    wd.swap_bus_config(active=True)
    wd.swap_bus_config(active=False)
    assert _read(wd._conf)["UNRELATED"] == "keep-me"


def test_is_local_url_discriminates_loopback_spellings(wd):
    for local in (LOCAL, "http://localhost:8903", "https://127.0.0.1:14004"):
        assert wd._is_local_url(local) is True, local
    for remote in (PEER, SELF, "https://bus.example.org:13004"):
        assert wd._is_local_url(remote) is False, remote
