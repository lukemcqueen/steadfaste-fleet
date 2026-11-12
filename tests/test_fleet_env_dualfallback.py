"""Hermetic tests for fleet_lib.env dual-read resolution (CORTEX_* -> FLEET_*).

The safety foundation of the env migration: a host that still sets the legacy
CORTEX_<name> must still resolve when code reads FLEET_<name> (via fleet_env.get),
LOUDLY (one-time deprecation warning), never silently - a silent fallback is how
a half-migrated host hides. FLEET_ wins when both are present (it is canonical).

These derive their expectations from the module under test (fleet_lib.env), with
controlled os.environ (save/restored), so the tests stay hermetic and re-runnable.
"""
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "ops" / "scripts"

_FAKE_URL = "https://bus.example.org"
_FAKE_URL2 = "https://bus.legacy.example.org"
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
    fe.set_deprecation_logging(True)
    saved = {}
    keys = [k for k in os.environ if k.startswith(("FLEET_", "CORTEX_"))]
    for k in keys:
        saved[k] = os.environ.pop(k)
    fe._warned.clear()
    yield fe
    for k in saved:
        os.environ[k] = saved[k]
    for k in keys:
        if k not in saved:
            os.environ.pop(k, None)


def test_fleet_wins_when_both_set(env_clean):
    fe = env_clean
    os.environ["FLEET_BUS_URL"] = _FAKE_URL
    os.environ["CORTEX_BUS_URL"] = _FAKE_URL2
    assert fe.get("BUS_URL") == _FAKE_URL


def test_cortex_fallback_used_and_loud(env_clean, capsys):
    fe = env_clean
    os.environ["CORTEX_DEPLOY_HOME"] = _FAKE_PATH
    assert fe.get("DEPLOY_HOME") == _FAKE_PATH
    err = capsys.readouterr().err
    assert "DEPRECATED: CORTEX_DEPLOY_HOME set; rename to FLEET_DEPLOY_HOME" in err


def test_deprecation_warned_once_per_name(env_clean, capsys):
    fe = env_clean
    os.environ["CORTEX_PROFILE"] = "laptop"
    fe.get("PROFILE")
    fe.get("PROFILE")
    err = capsys.readouterr().err
    assert err.count("DEPRECATED: CORTEX_PROFILE") == 1


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
