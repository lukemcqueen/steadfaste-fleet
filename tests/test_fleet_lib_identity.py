#!/usr/bin/env python3
"""fleet_lib.identity — the ONE orchestrator-identity rule.

These tests pin the property every consumer depends on: orchestrator status is
host-derived and the env vars AGENT_TYPE / IS_ORCHESTRATOR can NEVER flip it.
The two divergent env-first copies (fleet_doctor.config AGENT_ROLE and
fleet_doctor.task_integrity._is_orchestrator) used to widen the doctor's task
probe to fleet scope whenever those vars were set on a worker host; this module
kills that class of bug by refusing env entirely.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fleet_lib import identity

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "ops" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _on(hostname: str, home: str) -> bool:
    """Compute is_orchestrator as if running on `hostname` with passwd home `home`."""
    old_short = identity._short_hostname
    old_home = identity._real_home
    try:
        identity._short_hostname = lambda: hostname
        identity._real_home = lambda: home
        return identity.is_orchestrator()
    finally:
        identity._short_hostname = old_short
        identity._real_home = old_home


def test_orch_host_member_with_match_home():
    assert _on("moses", "/home/moses") is True
    assert _on("esther", "/home/esther") is True


def test_member_host_with_mismatched_home_is_not_orch():
    # Same hostname but a non-matching home — NOT an orchestrator.
    assert _on("moses", "/home/other") is False


def test_non_member_host_short_circuits_even_with_orch_home():
    # titus has /home/titus; neither name nor home grants orch status.
    assert _on("titus", "/home/titus") is False
    assert _on("titus", "/home/moses") is False


def test_env_vars_cannot_flip_a_worker_to_orchestrator(monkeypatch):
    """The authority-widening defect: env vars used to elevate a worker.

    Locks the fix — AGENT_TYPE/IS_ORCHESTRATOR must be ignored entirely.
    """
    monkeypatch.setenv("AGENT_TYPE", "orchestrator")
    monkeypatch.setenv("IS_ORCHESTRATOR", "true")
    assert _on("titus", "/home/titus") is False


def test_env_vars_cannot_flip_an_orch_to_worker():
    """The reverse direction: spoofed env on a real orchestrator is also ignored."""
    old_env = {k: os.environ.get(k) for k in ("AGENT_TYPE", "IS_ORCHESTRATOR")}
    try:
        os.environ["AGENT_TYPE"] = "server"
        os.environ["IS_ORCHESTRATOR"] = "0"
        assert _on("moses", "/home/moses") is True
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_agent_role_is_binary():
    assert identity.agent_role() in ("orchestrator", "server")


def test_short_hostname_falls_back_on_hostname_failure(monkeypatch):
    """Robustness: hostname -s failure still resolves from uname."""
    import subprocess as _sp

    def boom(*a, **k):
        raise OSError("no hostname")

    monkeypatch.setattr(_sp, "run", boom)
    short = identity._short_hostname()
    # Whatever uname gives, it must be a non-empty string and never raise.
    assert isinstance(short, str) and short != ""
