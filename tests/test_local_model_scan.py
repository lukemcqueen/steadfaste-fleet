#!/usr/bin/env python3
"""Tests for the no_agent model-scan scripts.

- local-model-scan-watchdog.py: state-transition watchdog — silent when the
  material model state is unchanged, reports a diff when it changes, and its
  fingerprint excludes volatile content (timestamps, flags).
- local-model-scan.py: compliance scan — silent when clean, prints findings
  when a check fails (label in config, baseline mismatch, bad ids, cron pins,
  log markers, unvetted routing head).

Both are driven via subprocess with env-var path overrides (LSM_*), so the
tests run against tmp_path fixtures, never the live home directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WATCHDOG = REPO / "ops" / "scripts" / "manage" / "local-model-scan-watchdog.py"
COMPLIANCE = REPO / "ops" / "scripts" / "manage" / "local-model-scan.py"

BASE = "qwen/qwen3-coder-30b-a3b-instruct"
CHEAP = "openrouter:openai/gpt-oss-20b"

CONFIG_TPL = """model:
  default: {default}
  provider: openrouter
fallback_providers:
  - provider: openrouter
    model: deepseek/deepseek-v4-flash-0731
cron:
  model: deepseek/deepseek-v4-flash-0731
"""

MARKET = {
    "captured": "live daily scan",
    "catalog_source": "live",
    "list": "/some/path",
    "prices": {f"openrouter:{BASE}": {"in": 7e-08, "out": 2.8e-07}},
    "tiers": {"simple": {"general": [f"openrouter:{BASE}"]},
              "medium": {"coding": [f"openrouter:{BASE}"]}},
    "order_changed": True,
    "price_changed": True,
    "apply_pending": True,
    "violations": [],
    "unvetted_cheaper_candidates": {"simple/general": [
        {"model": "google/gemma-4-26b-a4b-it:free", "blended": 0.0}]},
    "free_tier_junk_dropped": [],
}

JOBS = {
    "jobs": [{
        "name": "agent-memory-pruning",
        "model": None,
        "provider": None,
        "model_snapshot": "deepseek-v4-flash",
        "provider_snapshot": "opencode-zen",
        "no_agent": False,
    }],
    "updated_at": "2026-11-26T00:00:00+09:00",
}

LIST_DOC = {
    "baseline": f"openrouter:{BASE}",
    "policy": {"simple": {"max_multiple_of_baseline": 0.6},
               "medium": {"max_multiple_of_baseline": 1.0}},
    "tiers": {"simple": {"general": [f"openrouter:{BASE}", CHEAP]},
              "medium": {"coding": [f"openrouter:{BASE}", CHEAP]}},
}

ROUTING = {
    "mode": "redacted-text",
    "tiers": {"simple": {"general": [f"openrouter:{BASE}"]},
              "medium": {"coding": [f"openrouter:{BASE}"]}},
}


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2))
    return path


def _env(tmp_path, **extra):
    env = dict(os.environ)
    env.update({
        "LSM_CONFIG_PATH": str(tmp_path / "config.yaml"),
        "LSM_MARKET_PATH": str(tmp_path / "model-market.json"),
        "LSM_JOBS_PATH": str(tmp_path / "jobs.json"),
        "LSM_ROUTER_LIST_PATH": str(tmp_path / "router-models.json"),
        "LSM_ROUTING_PATH": str(tmp_path / "routing.json"),
        "LSM_LOG_DIR": str(tmp_path / "logs"),
        "LSM_FINGERPRINT_PATH": str(tmp_path / "watchdog.fingerprint"),
    })
    env.update(extra)
    return env


def _run(script, tmp_path, env_extra=None):
    return subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=60,
                          env=_env(tmp_path, **(env_extra or {})))


# ── watchdog: state-transition behavior ────────────────────────────────

def _watchdog_fixture(tmp_path):
    _write(tmp_path, "config.yaml", CONFIG_TPL.format(default=BASE))
    _write(tmp_path, "model-market.json", MARKET)
    _write(tmp_path, "jobs.json", JOBS)


def test_watchdog_first_run_establishes_baseline(tmp_path):
    _watchdog_fixture(tmp_path)
    r = _run(WATCHDOG, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "baseline" in r.stdout
    fp = tmp_path / "watchdog.fingerprint"
    assert fp.exists()
    assert "|" in fp.read_text()  # fingerprint|state


def test_watchdog_silent_when_state_unchanged(tmp_path):
    _watchdog_fixture(tmp_path)
    _run(WATCHDOG, tmp_path)
    r = _run(WATCHDOG, tmp_path)  # second run, identical state
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # nothing delivered


def test_watchdog_reports_candidate_change(tmp_path):
    _watchdog_fixture(tmp_path)
    _run(WATCHDOG, tmp_path)
    market = json.loads((tmp_path / "model-market.json").read_text())
    market["unvetted_cheaper_candidates"]["simple/general"].append(
        {"model": "new/free-model:free", "blended": 0.0})
    (tmp_path / "model-market.json").write_text(json.dumps(market, indent=2))
    r = _run(WATCHDOG, tmp_path)
    assert r.returncode == 0
    assert "Model state changed" in r.stdout
    assert "new/free-model:free" in r.stdout


def test_watchdog_ignores_volatile_fields(tmp_path):
    """order_changed/price_changed/apply_pending + timestamps must NOT fire."""
    _watchdog_fixture(tmp_path)
    _run(WATCHDOG, tmp_path)
    market = json.loads((tmp_path / "model-market.json").read_text())
    market["order_changed"] = False          # volatile flag flip
    market["price_changed"] = False
    market["apply_pending"] = False
    (tmp_path / "model-market.json").write_text(json.dumps(market, indent=2))
    jobs = json.loads((tmp_path / "jobs.json").read_text())
    jobs["updated_at"] = "2099-01-01T00:00:00+09:00"   # volatile timestamp
    (tmp_path / "jobs.json").write_text(json.dumps(jobs, indent=2))
    r = _run(WATCHDOG, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # still silent


def test_watchdog_reports_config_default_change(tmp_path):
    _watchdog_fixture(tmp_path)
    _run(WATCHDOG, tmp_path)
    (tmp_path / "config.yaml").write_text(CONFIG_TPL.format(default="some/other-model"))
    r = _run(WATCHDOG, tmp_path)
    assert r.returncode == 0
    assert "config.yaml model" in r.stdout
    assert "some/other-model" in r.stdout


# ── compliance scan: findings only ─────────────────────────────────────

def _compliance_fixture(tmp_path):
    _write(tmp_path, "config.yaml", CONFIG_TPL.format(default=BASE))
    _write(tmp_path, "router-models.json", LIST_DOC)
    _write(tmp_path, "routing.json", ROUTING)
    _write(tmp_path, "jobs.json", JOBS)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "agent.log").write_text("everything fine\n")


def test_compliance_silent_when_clean(tmp_path):
    _compliance_fixture(tmp_path)
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # no findings -> nothing delivered


def test_compliance_flags_routing_label_in_config(tmp_path):
    _compliance_fixture(tmp_path)
    (tmp_path / "config.yaml").write_text(
        CONFIG_TPL.format(default=f"openrouter:{BASE}"))
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert "LABEL" in r.stdout or "label" in r.stdout
    assert "openrouter:" in r.stdout


def test_compliance_flags_baseline_mismatch(tmp_path):
    _compliance_fixture(tmp_path)
    (tmp_path / "config.yaml").write_text(CONFIG_TPL.format(default="other/model"))
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert "baseline" in r.stdout


def test_compliance_flags_known_bad_id(tmp_path):
    _compliance_fixture(tmp_path)
    (tmp_path / "config.yaml").write_text(CONFIG_TPL.format(default="deepseek-chat"))
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert "deepseek-chat" in r.stdout


def test_compliance_flags_cron_model_pin(tmp_path):
    _compliance_fixture(tmp_path)
    jobs = json.loads((tmp_path / "jobs.json").read_text())
    jobs["jobs"][0]["model"] = "some/pinned-model"
    (tmp_path / "jobs.json").write_text(json.dumps(jobs, indent=2))
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert "per-job model pin" in r.stdout


def test_compliance_flags_log_markers(tmp_path):
    _compliance_fixture(tmp_path)
    (tmp_path / "logs" / "agent.log").write_text(
        "turn failed: is not a valid model ID\n")
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert "is not a valid model ID" in r.stdout


def test_compliance_flags_unvetted_routing_head(tmp_path):
    _compliance_fixture(tmp_path)
    routing = json.loads((tmp_path / "routing.json").read_text())
    routing["tiers"]["medium"]["coding"] = ["openrouter:unvetted/model"]
    (tmp_path / "routing.json").write_text(json.dumps(routing, indent=2))
    r = _run(COMPLIANCE, tmp_path)
    assert r.returncode == 0
    assert "not in vetted list" in r.stdout
