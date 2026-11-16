#!/usr/bin/env python3
"""check_harness_registry must parse the registry view when the CLI's stderr
notice is merged onto the same stream.

`_run_hc` returns stdout + stderr deliberately — the caller judges what RUNS,
and the harness `verify` path reads the tail of that merged text for its error
line. So `fleet harness list --json` arrives as the JSON object FOLLOWED by the
interpreter resolver's notice, e.g.

    python-with-module: ignoring inherited PYTHONPATH/PYTHONHOME while choosing
    an interpreter — an inherited path can shadow the target's own packages
    { "count": 5, ... }

(a block-buffered stdout is flushed at exit, so the unbuffered stderr line can
lead; either order reaches the parse). A plain `json.loads` over that merged
text raised `Extra data: line 68 column 1` and the doctor reported the registry
as COULD NOT VERIFY on a host whose registry was perfectly readable.

The contract asserted here: the JSON OBJECT anywhere in the merged view is the
registry; trailing or leading diagnostics are noise and must not turn a
readable registry into an unverifiable one. A view with NO parseable object
must still WARN — the check must not be silenced into a pass.

Hermetic: `_run_harness_list` and `_run_harness_verify` are monkeypatched here;
the real CLI is never run and no host path is read.

`_VIEW` below is MINIMAL BY DESIGN for the parse contract: it carries the keys
the parse and the presence/skip path read (name, layer, status, surface,
presence, why, install). It is a fixture for "is the object found and read", not
a copy of the registry's full schema — so it stays stable when the registry
gains keys the parse does not need.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "ops" / "scripts" / "manage"))

from fleet_doctor import checks as C  # noqa: E402

# A registry view with one declared harness whose presence resolves to nothing,
# so the check reports SKIP ("not installed on this host") without needing the
# verify path. `why`/`install` are present because the registry declares them.
_VIEW = """{
  "count": 1,
  "layers": ["mcp"],
  "harnesses": [
    {
      "name": "claude-code",
      "layer": "mcp",
      "status": "shipped",
      "surface": ".mcp.json",
      "presence": ["~/.no-such-harness-presence-file-zzz"],
      "why": "MCP-native",
      "install": "bash install.sh"
    }
  ]
}
"""

# The exact notice the interpreter resolver writes to stderr.
_NOTICE = ("python-with-module: ignoring inherited PYTHONPATH/PYTHONHOME while "
           "choosing an interpreter — an inherited path can shadow the target's "
           "own packages\n")


class FakeRes:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", fix=""):
        self.checks.append((name, status, detail, fix))


def _run_with_view(view, monkeypatch):
    """Drive check_harness_registry against a fixed merged view."""
    monkeypatch.setattr(C, "_run_harness_list", lambda: (0, view))
    monkeypatch.setattr(C, "_run_harness_verify", lambda name: (0, "ok"))
    res = FakeRes()
    C.check_harness_registry(res)
    return res.checks


def _status(checks, name):
    return next((s for n, s, _d, _f in checks if n == name), None)


def test_stderr_notice_after_the_json_is_not_a_parse_failure(monkeypatch):
    """The reported defect: notice trailing the object must not break the view."""
    checks = _run_with_view(_VIEW + _NOTICE, monkeypatch)
    assert _status(checks, "Harness registry") is None, (
        "a readable registry was reported as unverifiable: %r" % (checks,))
    assert _status(checks, "Harness (claude-code)") == "SKIP", checks


def test_stderr_notice_before_the_json_is_not_a_parse_failure(monkeypatch):
    """Buffering can put the unbuffered stderr line FIRST; both orders must work."""
    checks = _run_with_view(_NOTICE + _VIEW, monkeypatch)
    assert _status(checks, "Harness registry") is None, checks
    assert _status(checks, "Harness (claude-code)") == "SKIP", checks


def test_clean_json_still_parses(monkeypatch):
    """The happy path must not regress."""
    checks = _run_with_view(_VIEW, monkeypatch)
    assert _status(checks, "Harness registry") is None, checks
    assert _status(checks, "Harness (claude-code)") == "SKIP", checks


def test_a_view_with_no_json_still_warns(monkeypatch):
    """Guard the guard: noise-only output must stay COULD NOT VERIFY, not a pass."""
    checks = _run_with_view(_NOTICE + "bash: fleet: command not found\n", monkeypatch)
    assert _status(checks, "Harness registry") == "WARN", checks
    detail = next(d for n, _s, d, _f in checks if n == "Harness registry")
    assert "COULD NOT VERIFY" in detail, detail


def test_a_view_with_broken_json_still_warns(monkeypatch):
    """Truncated/garbled JSON is a real unverifiable view, not noise."""
    checks = _run_with_view('{"count": 5, "harnesses": [', monkeypatch)
    assert _status(checks, "Harness registry") == "WARN", checks


def test_resolver_notice_is_not_read_as_the_verify_verdict(monkeypatch):
    """The `verify` path reads the TAIL of the merged stream as the verdict.

    The resolver's notice trails every invocation, so it was being reported as
    the verdict ("...nothing to check; python-with-module: ignoring inherited
    PYTHONPATH/PYTHONHOME ..."). The command's own last line is the verdict.
    """
    view = _VIEW.replace('"~/.no-such-harness-presence-file-zzz"', '"cmd:python3"')
    monkeypatch.setattr(C, "_run_harness_list", lambda: (0, view + _NOTICE))
    monkeypatch.setattr(
        C, "_run_harness_verify",
        lambda name: (0, "artifact: fleet-governance.ts present\nharness pi OK\n" + _NOTICE))
    res = FakeRes()
    C.check_harness_registry(res)
    name, status, detail, _fix = next(c for c in res.checks if c[0] == "Harness (claude-code)")
    assert status == "PASS", (status, detail)
    assert detail.endswith("harness pi OK"), detail
    assert "python-with-module:" not in detail, detail
