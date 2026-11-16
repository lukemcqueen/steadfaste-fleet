"""Tests for the doctor's Jev config-model resolver (fleet_doctor.jev).

check_jev_layer previously resolved "the model this host runs" by shelling
`hermes config get model` — ~8s of Hermes plugin+tool startup (measured:
8.09s standalone on moses). _config_model now reads config.yaml's `model:`
block directly, which is bit-identical in steady state because `hermes config
get model` echoes that section verbatim and does NOT resolve
fallback_providers (verified against its live output 2026-11-16).

These tests pin the resolver in both directions: the yaml path (what runs on a
normal host) AND the no-yaml line-scan fallback (what runs when the doctor's
interpreter lacks PyYAML), plus the scoping rule that the fallback_providers
`model:`/`provider:` keys are never mistaken for the running model.

Run: bash tests/run-tests.sh tests/test_doctor_model_config.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops" / "scripts" / "manage"))

from fleet_doctor import jev  # noqa: E402


CONFIG = """\
model:
  default: qwen/qwen3-coder-30b-a3b-instruct
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
providers:
  deepseek:
    request_timeout_seconds: 300
fallback_providers:
  - provider: openrouter
    model: deepseek/deepseek-v4-flash-0731
"""


def _home(tmp_path: Path, text: str) -> Path:
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_yaml_path_reads_the_model_block(tmp_path):
    home = _home(tmp_path, CONFIG)
    assert jev._config_model(home) == ("openrouter", "qwen/qwen3-coder-30b-a3b-instruct")


def test_missing_config_returns_empty(tmp_path):
    assert jev._config_model(tmp_path) == ("", "")


def test_current_model_combines_provider_and_default(tmp_path, monkeypatch):
    home = _home(tmp_path, CONFIG)
    monkeypatch.setattr(jev, "_hermes_home", lambda: home)
    assert jev._current_model() == "openrouter:qwen/qwen3-coder-30b-a3b-instruct"


def test_current_model_empty_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(jev, "_hermes_home", lambda: tmp_path)
    assert jev._current_model() == ""


def test_line_scan_fallback_scopes_to_the_model_block(tmp_path, monkeypatch):
    """The no-yaml fallback must return the _model_ block's values, never the
    top-level fallback_providers' `provider:`/`model:` keys that follow it."""
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "yaml", None)   # force the ImportError path
    home = _home(tmp_path, CONFIG)
    assert jev._config_model(home) == ("openrouter", "qwen/qwen3-coder-30b-a3b-instruct")


def test_line_scan_fallback_missing_file(tmp_path, monkeypatch):
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "yaml", None)
    assert jev._config_model(tmp_path) == ("", "")
