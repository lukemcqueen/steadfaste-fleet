"""Hermetic tests for fleet_lib.env FLEET-only resolution (release 2).

Release 2 removed the CORTEX_* dual-read fallback: `fleet_env.get("<name>")`
resolves FLEET_<name> then the default. A legacy CORTEX_<name> is NOT read, so
a CORTEX-only host resolves to the default (it must have been repointed by
consolidate-env.sh, which converts the .env to FLEET-only).

These derive their expectations from the module under test (fleet_lib.env), with
controlled os.environ (save/restored), so the tests stay hermetic and re-runnable.
"""
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "ops" / "scripts"

_FAKE_URL = "https://bus.example.org"
_FAKE_PATH = "/tmp/fleet-deploy-home"


@pytest.fixture()
def scripts_path():
    added = []
    for p in (SCRIPTS_DIR, SCRIPTS_DIR / "fleet_lib"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
            added.append(str(p))
    yield SCRIPTS_DIR
    for p in added:
        try:
            sys.path.remove(p)
        except ValueError:
            pass


@pytest.fixture()
def env_clean(scripts_path):
    """Isolate the real environment: clear every FLEET_/CORTEX_ var and restore."""
    import fleet_lib.env as fe
    saved = {}
    keys = [k for k in os.environ if k.startswith(("FLEET_", "CORTEX_"))]
    for k in keys:
        saved[k] = os.environ.pop(k)
    yield fe
    for k in saved:
        os.environ[k] = saved[k]
    for k in keys:
        if k not in saved:
            os.environ.pop(k, None)


def test_fleet_wins_when_both_set(env_clean):
    fe = env_clean
    os.environ["FLEET_BUS_URL"] = _FAKE_URL
    os.environ["CORTEX_BUS_URL"] = "https://bus.legacy.example.org"
    assert fe.get("BUS_URL") == _FAKE_URL


def test_cortex_not_used_after_release2(env_clean):
    fe = env_clean
    # A CORTEX-only value is ignored: no fallback, resolves to default.
    os.environ["CORTEX_DEPLOY_HOME"] = _FAKE_PATH
    assert fe.get("DEPLOY_HOME") is None
    assert fe.get("DEPLOY_HOME", "/default") == "/default"


def test_default_when_neither_set(env_clean):
    fe = env_clean
    assert fe.get("NOPE_XYZ", "fallback") == "fallback"


def test_get_bool_truthy_and_falsy(env_clean):
    fe = env_clean
    assert fe.get_bool("FLAG_X", False) is False
    os.environ["FLEET_FLAG_X"] = "1"
    assert fe.get_bool("FLAG_X") is True
    os.environ["FLEET_FLAG_X"] = "no"
    assert fe.get_bool("FLAG_X", True) is False


def test_module_is_hermes_free(scripts_path):
    import fleet_lib.env as fe
    src = Path(fe.__file__).read_text()
    offending = [ln for ln in src.splitlines()
                 if (ln.startswith("import hermes") or ln.startswith("from hermes"))]
    assert not offending, f"fleet_lib.env imports from Hermes runtime: {offending}"
