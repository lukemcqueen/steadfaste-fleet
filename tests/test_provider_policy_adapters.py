#!/usr/bin/env python3
"""Tests for the provider-exclusion adapter seam (provider-policy.py).

Why this file exists: an adversarial review of the interface refactor found that
the seam — `adapter_for`, `ConfigCliAdapter.read/write`, `DeclaredGap`, and the
fail-closed branch — had NO committed test. The refactor had been verified only by
ad-hoc assertions in a session, which is not re-runnable evidence.

Runs under pytest, and ALSO standalone (`python3 tests/test_provider_policy_adapters.py`)
because pytest is not installed on every host in this fleet.

No live harness binary is touched: `provider_policy._run` is monkeypatched.
"""
from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "ops" / "scripts" / "manage" / "provider-policy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("provider_policy", MODULE_PATH)
    if spec is None or spec.loader is None:      # pragma: no cover - loader always exists
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = load_module()


@contextlib.contextmanager
def fake_run(result):
    """Patch the module's subprocess seam; `result` is a (stdout, err) tuple."""
    original = getattr(pp, "_run")
    setattr(pp, "_run", lambda argv, timeout=30: result)
    try:
        yield
    finally:
        setattr(pp, "_run", original)


# --- the dispatch table is the single seam -----------------------------------

def test_config_cli_maps_to_config_cli_adapter():
    assert pp.ADAPTERS["config-cli"] is pp.ConfigCliAdapter


def test_none_mechanism_maps_to_declared_gap():
    assert pp.ADAPTERS[None] is pp.DeclaredGap


def test_adapter_for_returns_right_classes():
    assert isinstance(pp.adapter_for("h", {"mechanism": "config-cli"}),
                      pp.ConfigCliAdapter)
    assert isinstance(pp.adapter_for("h", {"mechanism": None}), pp.DeclaredGap)


def test_unknown_mechanism_fails_closed_to_a_gap():
    # fail CLOSED: an unenforced harness must never be reported as a pass
    assert isinstance(pp.adapter_for("h", {"mechanism": "no-such-mechanism"}),
                      pp.DeclaredGap)


# --- the binary is per-harness DATA, not a hardcoded literal -----------------

def test_config_cli_uses_spec_bin():
    spec = {"mechanism": "config-cli", "bin": "some-harness",
            "get": ["config", "get", "provider_routing", "--json"],
            "set": ["config", "set", "provider_routing.ignore"]}
    assert pp.ConfigCliAdapter("some-harness", spec)._bin() == "some-harness"


def test_config_cli_bin_falls_back_to_the_harness_name():
    spec = {"mechanism": "config-cli", "get": [], "set": []}
    assert pp.ConfigCliAdapter("named-harness", spec)._bin() == "named-harness"


def test_read_shells_out_to_the_spec_bin():
    seen = {}
    orig = getattr(pp, "_run")

    def capture(argv, timeout=30):
        seen["argv"] = argv
        return '{"ignore": []}', None

    setattr(pp, "_run", capture)
    try:
        spec = {"mechanism": "config-cli", "bin": "myharness",
                "get": ["config", "get", "provider_routing", "--json"]}
        pp.ConfigCliAdapter("myharness", spec).read()
    finally:
        setattr(pp, "_run", orig)
    assert seen["argv"][0] == "myharness"


# --- read(): an error is NEVER agreement -------------------------------------

def test_read_returns_error_on_non_json_output():
    with fake_run((  "not json at all", None)):
        value, err = pp.ConfigCliAdapter("h", {"get": []}).read()
    assert value is None and err is not None


def test_read_returns_error_on_non_object_json():
    with fake_run(("[1, 2, 3]", None)):
        value, err = pp.ConfigCliAdapter("h", {"get": []}).read()
    assert value is None and err is not None


def test_read_returns_sorted_ignore_list():
    with fake_run(('{"ignore": ["zeta", "alpha"]}', None)):
        value, err = pp.ConfigCliAdapter("h", {"get": []}).read()
    assert err is None and value == ["alpha", "zeta"]


def test_read_propagates_a_subprocess_error():
    with fake_run((None, "boom")):
        value, err = pp.ConfigCliAdapter("h", {"get": []}).read()
    assert value is None and err is not None


# --- DeclaredGap is a first-class adapter, not a silent branch ---------------

def test_gap_read_is_an_error_never_agreement():
    value, err = pp.DeclaredGap("pi", {"why": "no knob"}).read()
    assert value is None and err is not None


def test_gap_write_refuses_loudly():
    msg = pp.DeclaredGap("pi", {"why": "no knob"}).write(["provider-x"])
    assert msg and "NOT enforced" in msg


def test_gap_present_uses_its_own_presence_path(tmp_path=None):
    gap = pp.DeclaredGap("h", {"presence": None})
    assert gap.present() is False


def test_gap_describe_falls_back_to_the_reason():
    assert pp.DeclaredGap("h", {"why": "because"}).describe() == "because"


# --- check() routes through the interface ------------------------------------

def test_check_reports_gap_present_for_a_gap_that_is_present():
    original = getattr(pp, "HARNESSES")
    setattr(pp, "HARNESSES", {"fake": {"presence": str(MODULE_PATH), "mechanism": None,
                                       "surface": None, "why": "declared gap"}})
    try:
        findings = pp.check({"provider_exclusions": {"p": {}}}, only_present=True)
    finally:
        setattr(pp, "HARNESSES", original)
    assert [f["kind"] for f in findings] == ["gap-present"]


def test_check_reports_drift_when_live_disagrees_with_the_artifact():
    original = getattr(pp, "HARNESSES")
    setattr(pp, "HARNESSES", {"fake": {"presence": str(MODULE_PATH), "mechanism": "config-cli",
                                       "bin": "myharness", "surface": "surface", "get": []}})
    try:
        with fake_run(('{"ignore": ["other"]}', None)):
            findings = pp.check({"provider_exclusions": {"wanted": {}}}, only_present=True)
    finally:
        setattr(pp, "HARNESSES", original)
    assert [f["kind"] for f in findings] == ["drift"]


def test_selftest_passes_on_the_real_artifact():
    import json
    doc = json.loads((REPO_ROOT / "config" / "router-models.json").read_text(encoding="utf-8"))
    assert pp._selftest(doc) == pp.RC_OK


def _run_all() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok  {name}")
        except Exception as exc:  # noqa: BLE001 - a test runner reports, never hides
            failed.append((name, exc))
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
