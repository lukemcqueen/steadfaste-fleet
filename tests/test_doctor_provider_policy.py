"""The doctor's "Provider exclusions" row.

Drives the REAL check (fleet_doctor/router_layer.py, loaded standalone like the
package's other leaf modules) against redirected seams — never this host's live
config. The row is the host-side gate for cross-agent-design rule 9: the deploy
COPIES provider-policy.py, but a host is only wired when the projection has RUN,
so a present harness whose live config lacks the exclusion must FAIL.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
MODULE_PATH = BASE / "ops" / "scripts" / "manage" / "fleet_doctor" / "router_layer.py"


class FakeRes:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", fix=""):
        self.checks.append({"name": name, "status": status, "detail": detail, "fix": fix})


def _load():
    spec = importlib.util.spec_from_file_location("doc_router_layer_pp", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["doc_router_layer_pp"] = mod
    spec.loader.exec_module(mod)
    return mod


def _artifact(tmp_path, providers):
    art = tmp_path / "router-models.json"
    art.write_text(
        json.dumps({"provider_exclusions": {
            p: {"reason": "r", "evidence": "e", "scope": "all"} for p in providers}}),
        encoding="utf-8")
    return art


def _home(tmp_path, ignore_lines):
    home = tmp_path / "hermes"
    home.mkdir()
    body = "model:\n  default: qwen/qwen3-coder-30b-a3b-instruct\nprovider_routing:\n"
    if ignore_lines:
        body += "  ignore:\n"
        for line in ignore_lines:
            body += f"    - {line}\n"
    else:
        body += "  ignore: []\n"
    (home / "config.yaml").write_text(body, encoding="utf-8")
    return home


def _wire(mod, monkeypatch, art, home, harnesses=None):
    monkeypatch.setattr(mod, "POLICY_ARTIFACT_CANDIDATES", [art])
    monkeypatch.setattr(mod, "_hermes_home", lambda: home)
    stub = type("M", (), {"HARNESSES": harnesses or {}})()
    monkeypatch.setattr(mod, "_load_provider_policy", lambda: stub)


def test_fail_when_live_config_missing_the_exclusion(tmp_path, monkeypatch):
    mod = _load()
    art = _artifact(tmp_path, ["amazon-bedrock"])
    home = _home(tmp_path, [])                     # ignore: []  -> drift
    _wire(mod, monkeypatch, art, home)
    res = FakeRes()
    mod.check_provider_policy(res)
    row = res.checks[-1]
    assert row["name"] == "Provider exclusions"
    assert row["status"] == "FAIL"
    assert "amazon-bedrock" in row["detail"]
    assert "--write" in row["fix"]


def test_pass_when_live_config_matches_the_artifact(tmp_path, monkeypatch):
    mod = _load()
    art = _artifact(tmp_path, ["amazon-bedrock"])
    home = _home(tmp_path, ["amazon-bedrock"])
    _wire(mod, monkeypatch, art, home)
    res = FakeRes()
    mod.check_provider_policy(res)
    assert res.checks[-1]["status"] == "PASS"


def test_present_unwired_harness_is_warn_not_pass(tmp_path, monkeypatch):
    mod = _load()
    art = _artifact(tmp_path, ["amazon-bedrock"])
    home = _home(tmp_path, ["amazon-bedrock"])
    claude = tmp_path / "claude.json"
    claude.write_text("{}", encoding="utf-8")
    harnesses = {
        "claude-code": {"presence": str(claude), "mechanism": None},
        "hermes": {"presence": str(home / "config.yaml"), "mechanism": "config-cli"},
    }
    _wire(mod, monkeypatch, art, home, harnesses)
    res = FakeRes()
    mod.check_provider_policy(res)
    row = res.checks[-1]
    assert row["status"] == "WARN"
    assert "claude-code" in row["detail"]


def test_no_hermes_config_is_pass(tmp_path, monkeypatch):
    mod = _load()
    art = _artifact(tmp_path, ["amazon-bedrock"])
    home = tmp_path / "hermes"
    home.mkdir()                                   # no config.yaml
    _wire(mod, monkeypatch, art, home)
    res = FakeRes()
    mod.check_provider_policy(res)
    assert res.checks[-1]["status"] == "PASS"


def test_unreadable_artifact_warns_could_not_verify(tmp_path, monkeypatch):
    mod = _load()
    art = tmp_path / "router-models.json"
    art.write_text("{not json", encoding="utf-8")
    home = _home(tmp_path, ["amazon-bedrock"])
    _wire(mod, monkeypatch, art, home)
    res = FakeRes()
    mod.check_provider_policy(res)
    assert res.checks[-1]["status"] == "WARN"


def test_live_provider_ignore_scopes_to_its_block(tmp_path):
    """The scan must not read the top-level `model:` / fallback block as provider_routing."""
    mod = _load()
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  default: x\n  provider: openrouter\n"
        "fallback_providers:\n  - provider: nous\n    model: y\n"
        "provider_routing:\n  models: {}\n  ignore:\n    - amazon-bedrock\n    - some-other\n",
        encoding="utf-8")
    assert mod.live_provider_ignore(home) == ["amazon-bedrock", "some-other"]


def test_live_provider_ignore_inline_form_on_forced_linescan(tmp_path, monkeypatch):
    """No-yaml hosts still parse the inline list form (the fallback path)."""
    mod = _load()
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "provider_routing:\n  ignore: [amazon-bedrock, some-other]\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)  # force `import yaml` to raise
    assert mod.live_provider_ignore(home) == ["amazon-bedrock", "some-other"]
