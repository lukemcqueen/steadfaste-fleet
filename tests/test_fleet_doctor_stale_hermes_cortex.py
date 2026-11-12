#!/usr/bin/env python3
"""The doctor must WARN when an old hermes-cortex home folder remains after the rename.

The hermes-cortex -> steadfaste-fleet migration (docs/runbooks/fleet-local-dir-rename.md)
is only proof-complete when the old home folders are gone. A host that still carries
~/hermes-cortex, ~/.hermes-cortex, or the empty pip leftover ~/.local/lib/hermes-cortex is
not fully migrated, and the check must point at the runbook (not just say "remove it").

Hermetic: loaded as a leaf module like mcp_names.py/jev.py; the module-level
STALE_HERMES_CORTEX_PATHS list is redirected to tmp fixtures — never the host's real home.
"""
import importlib.util
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "ops/scripts/manage/fleet_doctor"
MODULE = BASE / "stale_hermes_cortex.py"
SPEC = importlib.util.spec_from_file_location("fleet_doctor_stale_hc_test", MODULE)
assert SPEC is not None and SPEC.loader is not None, f"cannot load {MODULE}"
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class FakeRes:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", fix=""):
        self.checks.append((name, status, detail, fix))


def test_stale_hermes_cortex_folder_warns(tmp_path):
    """A leftover ~/.hermes-cortex must WARN and point at the runbook."""
    p = tmp_path / ".hermes-cortex"
    p.mkdir()
    old = mod.STALE_HERMES_CORTEX_PATHS
    try:
        mod.STALE_HERMES_CORTEX_PATHS = [p]
        res = FakeRes()
        mod.check_stale_hermes_cortex_home(res)
        assert len(res.checks) == 1
        name, status, detail, fix = res.checks[0]
        assert status == "WARN", (status, detail)
        assert name == "Stale hermes-cortex folders"
        assert str(p) in detail
        assert "fleet-local-dir-rename" in fix, fix
    finally:
        mod.STALE_HERMES_CORTEX_PATHS = old


def test_no_stale_hermes_cortex_passes(tmp_path):
    """No old home folders -> PASS (fully migrated)."""
    old = mod.STALE_HERMES_CORTEX_PATHS
    try:
        mod.STALE_HERMES_CORTEX_PATHS = [
            tmp_path / "hermes-cortex",
            tmp_path / ".hermes-cortex",
            tmp_path / ".local" / "lib" / "hermes-cortex",
        ]
        res = FakeRes()
        mod.check_stale_hermes_cortex_home(res)
        assert len(res.checks) == 1
        assert res.checks[0][1] == "PASS", res.checks[0]
    finally:
        mod.STALE_HERMES_CORTEX_PATHS = old


def test_stale_symlink_warns(tmp_path):
    """A leftover transition SYMLINK is as stale as a real dir — must WARN."""
    target = tmp_path / ".steadfaste-fleet"
    symlink = tmp_path / "hermes-cortex"
    target.mkdir()
    symlink.symlink_to(target, target_is_directory=True)
    old = mod.STALE_HERMES_CORTEX_PATHS
    try:
        mod.STALE_HERMES_CORTEX_PATHS = [symlink]
        res = FakeRes()
        mod.check_stale_hermes_cortex_home(res)
        assert res.checks[0][1] == "WARN", res.checks[0]
    finally:
        mod.STALE_HERMES_CORTEX_PATHS = old