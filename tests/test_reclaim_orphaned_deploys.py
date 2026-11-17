#!/usr/bin/env python3
"""Tests for the provenance-based deploy orphan reclaimer.

The whole point of this tool is what it does NOT touch, so most of these tests
assert a file SURVIVED. A suite that only proved "orphans get moved" would pass
for a tool that also ate the host-local scripts, which is the 2026-08-03 loss.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ops" / "scripts" / "manage" / "reclaim-orphaned-deploys.py"
FLEET_UPDATE = REPO / "ops" / "scripts" / "fleet-update.sh"
CHECKS = REPO / "ops" / "scripts" / "manage" / "fleet_doctor" / "checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("reclaim_orphaned_deploys", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reclaim_orphaned_deploys"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    assert SCRIPT.exists(), f"reclaimer missing: {SCRIPT}"
    return _load()


def _mkdeploy(tmp_path: Path) -> Path:
    home = tmp_path / "deploy"
    (home / "scripts" / "nested").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "state").mkdir(parents=True, exist_ok=True)
    return home


def _write_inventory(path: Path, paths) -> None:
    path.write_text("".join(f"{p}\n" for p in paths))


def _inv(mod, home, inventory, deployed_paths, dry_run=False):
    inv = home / "state" / "deployed-files.txt"
    dep = home / "state" / "deployed-files.new"
    _write_inventory(dep, [str(Path(p).resolve()) for p in deployed_paths])
    return mod.reclaim(
        inv, dep, home, home / "state" / "deploy-quarantine", dry_run=dry_run
    )


def test_missing_old_inventory_is_noop(mod, tmp_path):
    """First run after this ships: we have no memory, so we reclaim NOTHING.

    This is the test that keeps the rollout safe - a tool that treated a missing
    inventory as 'everything is an orphan' would quarantine the whole tree.
    """
    home = _mkdeploy(tmp_path)
    (home / "scripts" / "agent-thing.py").write_text("# deployed once\n")
    inv = home / "state" / "deployed-files.txt"
    assert not inv.exists()
    dep = home / "state" / "deployed-files.new"
    _write_inventory(dep, [])
    code, messages, reclaimed, errors = mod.reclaim(
        inv, dep, home, home / "state" / "deploy-quarantine"
    )
    assert code == 0, errors
    assert reclaimed == []
    assert (home / "scripts" / "agent-thing.py").exists(), "no-memory run must not sweep"
    assert any("NOTHING is touched" in m for m in messages)


def test_host_local_file_is_never_touched(mod, tmp_path):
    """A file in NEITHER inventory was never ours. Structural, not heuristic."""
    home = _mkdeploy(tmp_path)
    ours = home / "scripts" / "agent-ours.py"
    ours.write_text("# ours\n")
    local = home / "scripts" / "local-prod-db-backup.sh"
    local.write_text("#!/bin/bash\n# host-local, created per-host\n")
    operator = home / "scripts" / "agent-thing-operator-edited.py"
    operator.write_text("# added by hand\n")

    _write_inventory(home / "state" / "deployed-files.txt", [ours])
    code, _, reclaimed, errors = _inv(mod, home, home, [ours])

    assert code == 0, errors
    assert reclaimed == []
    assert local.exists(), "host-local script must survive"
    assert operator.exists(), "a file absent from the old inventory must survive"
    assert ours.exists()


def test_orphan_is_quarantined_and_restorable(mod, tmp_path):
    """A renamed destination: old path ours, new path replaces it. Move, verify
    content survives, and prove the printed restore command actually works."""
    home = _mkdeploy(tmp_path)
    old = home / "scripts" / "jev-thing.py"
    new = home / "scripts" / "thing.py"
    old.write_text("# the old name, deployed by an earlier deploy\n")
    new.write_text("# the new name\n")

    _write_inventory(home / "state" / "deployed-files.txt", [old.resolve()])
    code, messages, reclaimed, errors = _inv(mod, home, home, [new])

    assert code == 0, errors
    assert reclaimed == ["scripts/jev-thing.py"], reclaimed
    assert not old.exists(), "orphan must be moved out of the deploy tree"
    quarantined = list((home / "state" / "deploy-quarantine").rglob("jev-thing.py"))
    assert len(quarantined) == 1, quarantined
    assert quarantined[0].read_text() == "# the old name, deployed by an earlier deploy\n"
    assert any("restore: mv" in m for m in messages), "must print a restore command"
    assert new.exists(), "the current deploy is untouched"


def test_nested_relative_path_is_preserved(mod, tmp_path):
    """Quarantine keeps the tree shape so a restore cannot collide by basename."""
    home = _mkdeploy(tmp_path)
    orphan = home / "scripts" / "nested" / "deep" / "old-thing.py"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("x\n")
    _write_inventory(home / "state" / "deployed-files.txt", [orphan.resolve()])
    code, _, reclaimed, errors = _inv(mod, home, home, [])
    assert code == 0, errors
    assert reclaimed == ["scripts/nested/deep/old-thing.py"]
    assert list((home / "state" / "deploy-quarantine").rglob("deep/old-thing.py"))


def test_path_outside_deploy_home_is_refused(mod, tmp_path):
    """Invariant 2: authority stops at the deploy tree.

    Simulates a corrupt or hostile inventory - it must not be able to make us
    move something outside the tree we own.
    """
    home = _mkdeploy(tmp_path)
    outside = tmp_path / "not-ours"
    outside.mkdir()
    victim = outside / "important.conf"
    victim.write_text("keep me\n")

    _write_inventory(home / "state" / "deployed-files.txt", [victim, home / "scripts" / "gone.py"])
    code, _, reclaimed, errors = _inv(mod, home, home, [])
    assert code == 0, errors
    assert victim.exists(), "a path outside deploy home must never be touched"
    assert reclaimed == [], reclaimed


def test_symlink_orphan_is_skipped_target_intact(mod, tmp_path):
    """Hooks are intentional symlinks into registered scripts. A symlink's
    target is not ours to move, so we skip it and say so."""
    home = _mkdeploy(tmp_path)
    real = home / "scripts" / "agent-real.py"
    real.write_text("# real\n")
    link = home / "scripts" / "hook-link"
    link.symlink_to(real)

    _write_inventory(home / "state" / "deployed-files.txt", [link.resolve()])
    # Make the link itself the inventory entry (not its target) by using a
    # non-resolving inventory for this case.
    (home / "state" / "deployed-files.txt").write_text(f"{link}\n")
    dep = home / "state" / "deployed-files.new"
    dep.write_text("")
    code, messages, reclaimed, errors = mod.reclaim(
        home / "state" / "deployed-files.txt", dep, home,
        home / "state" / "deploy-quarantine",
    )
    assert code == 0, errors
    assert reclaimed == []
    assert real.exists(), "symlink target must survive"


def test_local_prefix_is_preserved_even_if_inventory_claims_it(mod, tmp_path):
    """Belt-and-braces: local-* can't legitimately be in an inventory, but if a
    future bug puts one there we still do not move it."""
    home = _mkdeploy(tmp_path)
    localish = home / "scripts" / "local-thing.sh"
    localish.write_text("#!/bin/bash\n")
    (home / "state" / "deployed-files.txt").write_text(f"{localish}\n")
    code, messages, reclaimed, errors = _inv(mod, home, home, [])
    assert code == 0, errors
    assert localish.exists()
    assert reclaimed == []
    assert any("preserved (local-*)" in m for m in messages)


def test_dry_run_moves_nothing(mod, tmp_path):
    home = _mkdeploy(tmp_path)
    orphan = home / "scripts" / "old-thing.py"
    orphan.write_text("x\n")
    _write_inventory(home / "state" / "deployed-files.txt", [orphan.resolve()])
    code, messages, reclaimed, errors = _inv(mod, home, home, [], dry_run=True)
    assert code == 0, errors
    assert reclaimed == []
    assert orphan.exists()
    assert any("WOULD QUARANTINE" in m for m in messages)


def test_inventory_is_rewritten_and_previous_is_archived(mod, tmp_path):
    """The tool is the single writer: it must advance what we own, and keep the
    previous list for audit - a partial failure must NOT advance it."""
    home = _mkdeploy(tmp_path)
    new = home / "config" / "router-models.json"
    new.write_text("{}\n")
    inv = home / "state" / "deployed-files.txt"
    _write_inventory(inv, [home / "config" / "jev-router-models.json"])
    code, _, _, errors = _inv(mod, home, home, [new])
    assert code == 0, errors
    assert inv.read_text().strip() == str(new.resolve())
    assert inv.with_suffix(inv.suffix + ".prev").exists()


def test_failed_reclaim_does_not_advance_inventory(mod, tmp_path):
    """Fail closed: if we could not reclaim, our notion of what we own must not
    move on, or the orphan becomes invisible forever."""
    home = _mkdeploy(tmp_path)
    orphan = home / "scripts" / "old-thing.py"
    orphan.write_text("x\n")
    inv = home / "state" / "deployed-files.txt"
    _write_inventory(inv, [orphan.resolve()])
    blocked = tmp_path / "blocked-quarantine"
    blocked.mkdir()
    dep = home / "state" / "deployed-files.new"
    # Written BEFORE the call: this file is an INPUT, not an output. (The first
    # version of this test wrote it after, so the tool correctly returned 4 for a
    # missing new-deploy list and never exercised the failure path it intends.)
    _write_inventory(dep, [])
    blocked.chmod(0o500)  # not writable -> mkdir of the timestamp dir fails
    try:
        code, _, _, errors = mod.reclaim(inv, dep, home, blocked)
        if code == 0:
            pytest.skip("running as a user that can write to mode-0500 dirs")
        assert code == 3, (code, errors)
        assert inv.read_text().strip() == str(orphan.resolve()), "inventory must not advance"
    finally:
        blocked.chmod(0o700)


# ── the deploy must actually call it, on EVERY deploy ──────────────────────
# SOUL 12: a fix that can silently no-op is not a fix. These are grep-level
# guards against the fix being wired in somewhere that never runs - which is
# precisely the defect that let orphans accumulate (clean_stale_deploys is
# invoked only under --force-all).

def test_deployer_wires_the_reclaimer_on_every_deploy():
    src = FLEET_UPDATE.read_text()
    assert "reclaim-orphaned-deploys.py" in src, "deploy never calls the reclaimer"
    idx = src.index("reclaim-orphaned-deploys.py")
    # The call must NOT live inside the force-all-only block that owns
    # clean_stale_deploys (guarded by a surrounding 'after force-all' comment).
    window = src[max(0, idx - 400):idx]
    assert "after force-all" not in window, (
        "the reclaimer is wired into the force-all-only path - an ordinary "
        "deploy would never reclaim anything"
    )


def test_deployer_emits_an_inventory_and_delegates_writing_it():
    src = FLEET_UPDATE.read_text()
    assert "deployed-files.txt" in src, "deploy does not point the reclaimer at the inventory"


def test_doctor_remedy_offers_a_restore_path_not_a_bare_rm():
    """A diagnosis that says `rm` without recovery is how data is lost. The
    stale-deploy remedy must not prescribe destroying the file outright."""
    src = CHECKS.read_text()
    start = src.index("def check_stale_deploys")
    # Bound by the NEXT top-level def, not a fixed character window: a fixed
    # window silently stops testing the moment someone adds a comment above the
    # remedy — which is exactly what happened when this test was written.
    nxt = src.find("\ndef ", start + 10)
    body = src[start:nxt if nxt != -1 else len(src)]
    assert "deploy-quarantine" in body or "reclaim-orphaned-deploys" in body, (
        "stale-deploy remedy still tells the operator to destroy data with no "
        "recovery path"
    )


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
