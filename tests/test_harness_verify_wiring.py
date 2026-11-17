"""The wiring half of `fleet harness verify` — the part a static scan cannot reach.

The registry/artifact checks answer "is the paperwork current?". The wiring block
answers the question an operator actually asks: *is this coding agent connected?*
It has four independent verdicts, and each can be wrong in its own way, so each
gets a probe here:

  - agent present / absent / undeclared   (harness_presence_line)
  - adapter declared but missing          (FAIL — a promise the registry broke)
  - context actually registered           (adapter_verify's exit-code mapping)
  - a declared gap                        (DECLARED GAP, never a bare PASS)

These are called directly rather than through `cmd_verify` where possible, so the
test does not have to reach a live store to assert a routing decision. The two
`cmd_verify` paths stub the two expensive side effects (the doc generator and the
store CLI) and assert the WIRING verdict, which is what this change owns.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS_PY = REPO / "ops" / "scripts" / "fleet" / "harness.py"


def _load():
    spec = importlib.util.spec_from_file_location("fleet_harness_under_test", HARNESS_PY)
    assert spec is not None and spec.loader is not None, f"cannot load {HARNESS_PY}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


harness = _load()


# ── 1. Presence: three distinct answers, not a boolean ───────────────────────

def test_presence_is_undeclared_when_the_registry_says_nothing():
    kind, detail = harness.harness_presence_line({})
    assert kind == "undeclared", "no `presence:` key must not read as absent"
    assert "declares no" in detail


def test_presence_is_absent_for_an_empty_declared_list():
    # steadfaste-tui declares `presence: []` on purpose — a declared gap.
    kind, _ = harness.harness_presence_line({"presence": []})
    assert kind == "absent"


def test_presence_path_probe_hits_on_an_existing_path(tmp_path):
    kind, detail = harness.harness_presence_line({"presence": [str(tmp_path)]})
    assert kind == "present"
    assert str(tmp_path) in detail


def test_presence_path_probe_misses_when_no_declared_path_exists(tmp_path):
    kind, detail = harness.harness_presence_line(
        {"presence": [str(tmp_path / "nope"), str(tmp_path / "neither")]}
    )
    assert kind == "absent"
    assert "none of" in detail


def test_presence_cmd_probe_hits_on_a_real_command():
    # `sh` exists everywhere; a cmd probe must be honoured the same way as a path.
    kind, detail = harness.harness_presence_line({"presence": [{"cmd": "sh"}]})
    assert kind == "present"
    assert "cmd:sh" in detail


def test_presence_cmd_probe_misses_for_an_absent_command():
    kind, _ = harness.harness_presence_line(
        {"presence": [{"cmd": "definitely-not-a-real-cli-xyz"}]}
    )
    assert kind == "absent"


# ── 2. adapter_verify maps an exit code to a verdict — never a silent pass ───

class _FakeCompleted:
    def __init__(self, rc, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


@pytest.mark.parametrize(
    "rc,expected_ok,expected_word",
    [
        (0, True, "registered"),
        (1, False, "NOT WIRED"),
        (2, False, "NOT WIRED"),
    ],
)
def test_adapter_verify_maps_exit_codes(monkeypatch, tmp_path, rc, expected_ok, expected_word):
    fake = tmp_path / "install-x-mcp.sh"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(
        harness.subprocess, "run", lambda *a, **k: _FakeCompleted(rc, out=f"tail line rc={rc}\n")
    )
    msg, ok = harness.adapter_verify(fake)
    assert ok is expected_ok
    assert expected_word in msg


def test_adapter_verify_reports_the_last_line_as_evidence(monkeypatch, tmp_path):
    fake = tmp_path / "install-x-mcp.sh"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(
        harness.subprocess, "run", lambda *a, **k: _FakeCompleted(1, out="first\nsecond-line\n")
    )
    msg, _ = harness.adapter_verify(fake)
    assert "second-line" in msg, "the verdict must carry the adapter's own last line"


def test_adapter_verify_never_swallows_an_exception(tmp_path):
    # No monkeypatch: a path that cannot be executed must surface as COULD NOT
    # VERIFY, never as a pass and never as a crash.
    msg, ok = harness.adapter_verify(tmp_path / "does-not-exist.sh")
    assert ok is False
    assert "COULD NOT VERIFY" in msg


# ── 3. cmd_verify's wiring verdicts ─────────────────────────────────────────

def _quiet_generator(monkeypatch):
    """Stub the two expensive side effects: the doc generator and the store CLI.

    One fake serves both calls: the generator check greps its stdout for
    "generate-harnesses: OK", and the store check greps for `"card"`/`"error"`.
    Satisfying both in one stub keeps the assertion on the WIRING verdict, which
    is what this change owns.
    """
    def fake_run(cmd, **kw):
        return _FakeCompleted(0, out='generate-harnesses: OK\n{"card": {}, "error": null}\n')
    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    monkeypatch.setattr(harness, "context_cli", lambda: Path("fleet-context.py"))


def test_declared_gap_reports_declared_gap_not_a_bare_pass(monkeypatch, capsys):
    _quiet_generator(monkeypatch)
    reg = {
        "harnesses": [
            {
                "name": "steadfaste-tui",
                "layer": "none",
                "status": "blocked",
                "presence": [],
                "why": "gateway not complete",
            }
        ]
    }
    rc = harness.cmd_verify(reg, "steadfaste-tui")
    out = capsys.readouterr().out
    assert "DECLARED GAP" in out
    assert "result: PASS (declared gap" in out, "a declared gap must not read as a bare PASS"
    assert rc == 0, "a declared gap is not a fault — it must not fail the command"


def test_missing_adapter_is_a_failure(monkeypatch, capsys):
    _quiet_generator(monkeypatch)
    reg = {
        "harnesses": [
            {
                "name": "ghost-harness",
                "layer": "mcp",
                "status": "shipped",
                "presence": [{"cmd": "sh"}],  # present
                "capabilities": {
                    "context": {"register": "ops/scripts/install/no-such-adapter.sh"},
                    "skills": {},
                    "governance": {},
                },
            }
        ]
    }
    rc = harness.cmd_verify(reg, "ghost-harness")
    out = capsys.readouterr().out
    assert "agent:   present" in out
    assert "adapter MISSING" in out
    assert "no adapter declared" in out, "an undeclared capability must be named, not skipped"
    assert rc == 1, "a broken registry promise must FAIL, not pass"
