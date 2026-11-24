#!/usr/bin/env python3
"""The doctor must WARN when .env backups sprawl past the single recovery point.

Every ad-hoc edit of the deploy-root .env leaves a `.env.*` backup behind, and each
one is an independent plaintext copy of the FULL secret set. Measured on this host:
SIX had accumulated, and four carried no key the live file lacked while the other two
carried only a retired CORTEX_* generation with no live reader — so the sprawl bought
nothing and multiplied the exposure of every credential in the file.

This check is the GATE that was missing: retention cannot depend on an agent
remembering, so the doctor reports the count and dictates the proof required before
deleting. It fires without anyone's cooperation.

Hermetic: loaded as a leaf module like stale_premigration_home.py, and the
module-level ENV_ROOTS list is redirected at tmp fixtures — never the host's real
deploy root, whose .env holds live secrets.
"""
import importlib.util
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "ops/scripts/manage/fleet_doctor"
MODULE = BASE / "env_backup_sprawl.py"
SPEC = importlib.util.spec_from_file_location("fleet_doctor_env_backup_sprawl_test", MODULE)
assert SPEC is not None and SPEC.loader is not None, f"cannot load {MODULE}"
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class FakeRes:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", fix=""):
        self.checks.append((name, status, detail, fix))


def _run_with(tmp_path, filenames):
    """Create `filenames` inside a tmp deploy root and run the check against it."""
    root = tmp_path / "deploy-root"
    root.mkdir(exist_ok=True)
    for fn in filenames:
        (root / fn).write_text("PLACEHOLDER=1\n")
    old = mod.ENV_ROOTS
    try:
        mod.ENV_ROOTS = [root]
        res = FakeRes()
        mod.check_env_backup_sprawl(res)
        return res
    finally:
        mod.ENV_ROOTS = old


def test_no_backups_passes(tmp_path):
    """A clean deploy root (live .env + the tracked template only) -> PASS."""
    res = _run_with(tmp_path, [".env", ".env.example"])
    assert len(res.checks) == 1
    assert res.checks[0][1] == "PASS", res.checks[0]


def test_single_backup_passes(tmp_path):
    """ONE backup is the sanctioned recovery point -> PASS. Boundary derived from
    the module's own KEEP_MAX so the test tracks the rule, not a copied constant."""
    assert mod.KEEP_MAX == 1, "this test asserts the documented single recovery point"
    res = _run_with(tmp_path, [".env", ".env.bak-20260101T0000Z"])
    assert res.checks[0][1] == "PASS", res.checks[0]


def test_sprawl_warns_and_names_them(tmp_path):
    """More than KEEP_MAX backups -> WARN, naming every extra file."""
    names = [".env", ".env.bak-a", ".env.bak-b", ".env.dual-bak-c"]
    res = _run_with(tmp_path, names)
    name, status, detail, fix = res.checks[0]
    assert status == "WARN", (status, detail)
    assert name == mod.CHECK_NAME
    for n in (".env.bak-a", ".env.bak-b", ".env.dual-bak-c"):
        assert n in detail, f"{n} missing from detail: {detail}"
    assert ".env.example" not in detail


def test_boundary_is_discriminating(tmp_path):
    """KEEP_MAX passes; KEEP_MAX+1 warns. Proves the check is not vacuously green."""
    at_limit = [".env"] + [f".env.bak-{i}" for i in range(mod.KEEP_MAX)]
    over_limit = [".env"] + [f".env.bak-{i}" for i in range(mod.KEEP_MAX + 1)]
    assert _run_with(tmp_path, at_limit).checks[0][1] == "PASS"
    assert _run_with(tmp_path, over_limit).checks[0][1] == "WARN"


def test_template_and_lock_are_not_counted(tmp_path):
    """`.env.example` (tracked template) and `.env.lock` (0-byte lock) are NOT
    backups — counting them would warn on a pristine host forever."""
    res = _run_with(tmp_path, [".env", ".env.example", ".env.lock"])
    assert res.checks[0][1] == "PASS", res.checks[0]


def test_remediation_demands_proof_before_deleting(tmp_path):
    """The remedy must require proving no unique KEY is lost BEFORE deleting —
    a bare `rm` would be the same careless destruction this check prevents."""
    res = _run_with(tmp_path, [".env", ".env.bak-a", ".env.bak-b"])
    fix = res.checks[0][3]
    assert "key" in fix.lower(), fix
    assert "newest" in fix.lower(), fix


def test_check_is_inert_on_import(tmp_path):
    """Importing must have no side effects and must not have run the check."""
    assert callable(mod.check_env_backup_sprawl)
    assert isinstance(mod.ENV_ROOTS, list)
    # A pristine tmp root with nothing in it still runs (no crash on an absent dir).
    old = mod.ENV_ROOTS
    try:
        mod.ENV_ROOTS = [tmp_path / "does-not-exist"]
        res = FakeRes()
        mod.check_env_backup_sprawl(res)
        assert res.checks[0][1] == "PASS", res.checks[0]
    finally:
        mod.ENV_ROOTS = old
