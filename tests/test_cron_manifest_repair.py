"""The repairer must never act on something the checker refuses to report.

Measured defect (issue #293): `cron_manifest.py --repair` created crons that
`--check` correctly does NOT report as missing. `check_drift()` skips
bridge-owned crons ("Owned by the fleet cron bridge (systemd timer) and removed
from Hermes by design — not expected in live jobs.json"), but `repair_all()` had
no such guard, so a blanket `--repair` would have brought up 48 dormant
watchdogs/scorers/reporters on esther — all bridge-owned, several delivering to
Telegram. 48/48 of the WOULD-CREATE set was bridge-owned (measured 2026-11-16).

These tests pin the INVARIANT, not the symptom: whatever the checker considers
missing is the only set the repairer may create.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CM = REPO / "ops" / "scripts" / "manage" / "cron_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("cm_under_test", CM)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_env(mod, manifest, live, bridged, in_scope=True):
    """Point the module at a synthetic manifest/live set — hermetic, no systemd."""
    mod.load_manifest = lambda path=None: manifest
    mod.load_live_jobs = lambda: live
    mod._bridge_owned = lambda: set(bridged)
    mod.is_in_scope = lambda cron, is_orch=None: in_scope


def test_repair_never_creates_what_check_refuses_to_report():
    """THE gate: would-create ⊆ check_drift's missing.

    The two used to compute "missing" differently — the checker excluded
    bridge-owned crons, the repairer did not — so a blanket --repair created
    crons that were deliberately absent. One definition, two consumers.
    """
    mod = _load_module()
    manifest = [
        {"name": "agent-bridge-owned", "scope": "universal", "schedule": "0 8 * * *"},
        {"name": "agent-genuinely-missing", "scope": "universal", "schedule": "0 9 * * *"},
        {"name": "agent-already-live", "scope": "universal", "schedule": "0 10 * * *"},
    ]
    live = {"agent-already-live": {"name": "agent-already-live"}}
    bridged = {"agent-bridge-owned"}
    _fake_env(mod, manifest, live, bridged)

    _drifted, missing, _orphans = mod.check_drift()
    missing_names = {m["name"] for m in missing}
    results = mod.repair_all(dry_run=True)
    would_create = {r["name"] for r in results if r["action"] in ("would-create", "created")}

    assert missing_names == {"agent-genuinely-missing"}, (
        f"checker's missing set changed: {sorted(missing_names)}"
    )
    assert would_create <= missing_names, (
        f"repair would create {sorted(would_create - missing_names)} that the "
        f"checker does not report as missing"
    )
    assert would_create == {"agent-genuinely-missing"}, (
        f"repair should create exactly the missing set, got {sorted(would_create)}"
    )


def test_repair_reports_the_bridge_skips_instead_of_swallowing_them():
    """A skip must be VISIBLE. Silently dropping a cron and printing nothing is
    how a reader concludes the repair was a clean no-op."""
    mod = _load_module()
    manifest = [{"name": "agent-bridge-owned", "scope": "universal", "schedule": "0 8 * * *"}]
    _fake_env(mod, manifest, {}, {"agent-bridge-owned"})
    results = mod.repair_all(dry_run=True)
    actions = {r["name"]: r["action"] for r in results}
    assert actions == {"agent-bridge-owned": "skipped-bridge"}, f"actions={actions}"
    assert any("bridge" in r["detail"].lower() for r in results), (
        f"a skipped entry must name the reason: {[r['detail'] for r in results]}"
    )


def test_repair_cron_refuses_a_bridge_owned_name_when_called_directly():
    """repair_all is the CLI path, but repair_cron is importable — a direct call
    must not bypass the guard the loop has."""
    mod = _load_module()
    _fake_env(mod, [], {}, {"agent-bridge-owned"})
    action, detail = mod.repair_cron({"name": "agent-bridge-owned", "scope": "universal"}, dry_run=True)
    assert action in ("skipped-bridge", "error"), f"direct call returned {action!r}"
    assert "bridge" in detail.lower(), detail


def test_load_manifest_fails_closed_instead_of_reading_an_empty_manifest(tmp_path):
    """Issue #291: load_manifest() wrapped `import yaml` AND the parse in
    `except Exception: return []`. Under an interpreter without PyYAML the tool
    read an EMPTY manifest, printed `0 declared, 0 drifted`, exited 0 and repaired
    nothing — every line looked like a clean no-op. An unreadable manifest is a
    FAILURE, not an empty one."""
    mod = _load_module()
    bad = tmp_path / "manifest_unparsable.yaml"
    bad.write_text("crons:\n  - name: 'unterminated\n   scope: [unclosed\n")
    with pytest.raises(Exception) as exc:
        mod.load_manifest(bad)
    assert "manifest" in str(exc.value).lower(), f"unhelpful error: {exc.value}"


def test_load_manifest_still_returns_empty_for_a_genuinely_absent_file(tmp_path):
    """The honest absence must stay empty — the fix must not turn "no manifest on
    this host" into a hard failure that breaks every caller."""
    mod = _load_module()
    assert mod.load_manifest(tmp_path / "does-not-exist.yaml") == []
