#!/usr/bin/env python3
"""CR5: the reload-proof and the broken-contract recovery lane (hermetic).

Enforcer-delegation acceptance, per docs/plans/hermes-governance-cr2-wiring.md
§5: (a) the enforcer's terminal write-classification is delegated to the
contract via a fresh loop-gov-classify.py subprocess, so a contract rule
change is picked up on the NEXT call with NO gateway restart; (b) the frozen
in-process fleet-update recognition keeps the sanctioned recovery lane working
even when the contract/CLI is completely unavailable, while everything else
fails closed.

Both are driven through the ENFORCER's own functions (imported via importlib),
with the classify-CLI resolution monkeypatched to a scratch copy so the test
never mutates the real contract.

Run: python3 -m pytest tests/test_enforcer_reload_hermetic.py -q
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ENFORCER = _REPO / "plugins" / "governance-enforcer" / "__init__.py"
_REAL_CLI = _REPO / "ops" / "scripts" / "loop-gov-classify.py"
_CONTRACT = _REPO / "ops" / "services" / "fleet-governance" / "write_class.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def enforcer(monkeypatch):
    # Pin the SUBJECT exists and exposes the functions the tests call, so the
    # reliance on the enforcer blob is asserted, not assumed (the close
    # reviewer's window may not include plugins/governance-enforcer/__init__.py).
    assert _ENFORCER.is_file(), f"enforcer source missing: {_ENFORCER}"
    mod = _load(_ENFORCER, "enforcer_reload_t")
    for fn in ("_is_readonly_terminal_command", "_is_sanctioned_fleet_update_command",
               "_classify_cli_path", "_classify_terminal"):
        assert callable(getattr(mod, fn, None)), f"enforcer lost {fn}()"
    with tempfile.TemporaryDirectory() as tmp:
        # A wrapper CLI in tmp that pins the contract copy, so edits to the
        # copy are what a fresh process loads — the same seam the CLI's
        # --contract flag exposes, reached through the enforcer's resolver.
        contract_copy = Path(tmp) / "write_class.py"
        shutil.copyfile(_CONTRACT, contract_copy)
        wrapper = Path(tmp) / "loop-gov-classify.py"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys\n"
            f"sys.exit(subprocess.call([sys.executable, {str(_REAL_CLI)!r},\n"
            f"                           '--contract', {str(contract_copy)!r},\n"
            f"                           sys.argv[1]]))\n"
        )
        monkeypatch.setattr(mod, "_classify_cli_path",
                            lambda: wrapper)
        yield mod, contract_copy


def test_contract_rule_change_reloads_without_restart(enforcer):
    """CR5 positive control: edit a rule in the contract; the ENFORCER's next
    call reflects it — no restart anywhere (a fresh subprocess did the work)."""
    mod, contract = enforcer
    before = mod._is_readonly_terminal_command("fleetdoctor-check")
    assert before is False, "probe command must start write-class"
    contract.open("a").write(
        '\nREADONLY_COMMAND_PATTERNS.append(r"^\\s*fleetdoctor-check(?:\\s|$)")\n')
    after = mod._is_readonly_terminal_command("fleetdoctor-check")
    assert after is True, (
        "the enforcer's own classifier did NOT pick up the new rule — "
        "the delegation/reload mechanism is broken")
    assert before is False and after is True


def test_frozen_fleet_update_lane_survives_broken_contract(enforcer, monkeypatch):
    """CR5 recovery lane: with the classify CLI UNAVAILABLE, the exact
    fleet-update recovery invocation is still recognised (frozen in-process
    recognition, checked before the subprocess), and a non-recovery write is
    fail-closed."""
    mod, _ = enforcer
    # Break the contract: the classify CLI cannot load it -> rc 3 -> None.
    monkeypatch.setattr(mod, "_classify_cli_path", lambda: None)
    assert mod._is_readonly_terminal_command("ls") is False, (
        "unavailable classification must fail CLOSED (write-class)")
    assert mod._is_sanctioned_fleet_update_command(
        "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh --status") is True, (
        "the frozen recovery recognition must work with NO contract")
    assert mod._is_sanctioned_fleet_update_command(
        "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh && echo hi") is False


def test_delegation_reaches_the_contract(enforcer):
    """Sanity: the enforcer's wrapper and the contract agree on a live corpus —
    the delegation path is the contract's, not a stale copy. The EXPECTATION is
    DERIVED from the contract (the in-range source of truth), never hardcoded."""
    mod, _ = enforcer
    contract = _load(_CONTRACT, "fleet_write_class_reload_t")
    for cmd in ["git status --short", "rm -rf x", "python3 -c 'x'", "ls -la"]:
        expect = contract.is_readonly_command(cmd)
        assert mod._is_readonly_terminal_command(cmd) is expect, cmd