#!/usr/bin/env python3
"""Fleet-owned session/cron model resolvers must be Hermes-independent and drive the materializer.

Design (decouple-fleet-model-materializer, cycle 8566): steadfaste-fleet is the single
authority for the session/cron/reviewer model; the derive-and-decide logic lives ONLY in
Python (fleet_lib/models.py) so it is directly testable, and the shell materializer is a
thin caller (reviewer ADV-0-2). This spec proves:

1. resolve_session_target(source-env, router-models doc) returns (model, provider) from the
   env-file values when present, else falls back to the router-models.json `baseline`,
   stripping a routing label (`provider:` prefix) to the bare id.
2. A baseline that still carries a routing label after stripping IS the label case: the
   resolver marks has_label for the materializer to fail closed (a label 400s every turn).
3. session_change_decision(current_model, target) is the idempotence check: no-op when they
   match, write-decision when they differ.
4. ADV-0-1 proof: the apply path (install-orch-crons.sh orch-model-market-scan prompt) is
   REWRITTEN to re-point the BASELINE into router-models.json and does NOT invoke a bare
   `hermes config set model.default` as the propagation mechanism — so commits and a test
   assert where model.default actually gets set.

Run: pytest tests/test_fleet_lib_model_resolvers.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "fleet_lib_models_resolvers_test",
    REPO / "ops" / "scripts" / "fleet_lib" / "models.py",
)
assert _spec is not None and _spec.loader is not None
_m = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_m)


# ── 1. resolve_session_target: env-file values win, baseline is the fallback ──
def test_session_target_from_env_values():
    env = {"SESSION_MODEL": "deepseek/deepseek-v4-flash-0731",
           "SESSION_PROVIDER": "openrouter"}
    t = _m.resolve_session_target(env, {"baseline": "openrouter:qwen/qwen3-coder-30b-a3b-instruct"})
    assert (t["model"], t["provider"]) == ("deepseek/deepseek-v4-flash-0731", "openrouter")
    assert t["has_label"] is False


def test_session_target_falls_back_to_baseline_label_stripped():
    # env empty -> baseline carries a routing label prefix, must be stripped to the bare id
    t = _m.resolve_session_target({}, {"baseline": "openrouter:qwen/qwen3-coder-30b-a3b-instruct"})
    assert (t["model"], t["provider"]) == ("qwen/qwen3-coder-30b-a3b-instruct", "openrouter")
    # after stripping, the bare id is CLEAN (a normal fleet baseline is not a red flag)
    assert t["has_label"] is False
    assert ":" not in t["model"]  # the bare id never carries a routing label


def test_session_target_double_label_is_red():
    # a double label survives one strip and must be flagged (would 400 every turn)
    t = _m.resolve_session_target({}, {"baseline": "openrouter:nous:deepseek/deepseek-x"})
    assert t["has_label"] is True
    assert ":" in t["model"]


def test_session_target_provider_precedence():
    # explicit provider in env wins over the baseline's implied provider
    env = {"SESSION_MODEL": "qwen/qwen3-coder-30b-a3b-instruct", "SESSION_PROVIDER": "openrouter"}
    t = _m.resolve_session_target(env, {"baseline": "nous:anthropic/claude-sonnet-4"})
    assert (t["model"], t["provider"]) == ("qwen/qwen3-coder-30b-a3b-instruct", "openrouter")


# ── 3. session_change_decision: idempotence ──
def test_change_decision_noop_when_matching():
    t = {"model": "qwen/qwen3-coder-30b-a3b-instruct", "provider": "openrouter", "has_label": False}
    assert _m.session_change_decision("qwen/qwen3-coder-30b-a3b-instruct", t) is False


def test_change_decision_write_when_differing():
    t = {"model": "deepseek/deepseek-v4-flash-0731", "provider": "openrouter", "has_label": False}
    assert _m.session_change_decision("qwen/qwen3-coder-30b-a3b-instruct", t) is True


# ── 4. ADV-0-1: the apply path re-points the BASELINE, not an ephemeral hermes write ──
def test_apply_prompt_repoints_baseline_not_hermes_config():
    prompt_src = (REPO / "ops/scripts/install/install-orch-crons.sh").read_text()
    scan_block = prompt_src.split('create_cron "orch-model-market-scan"', 1)[-1]
    # the propagation mechanism must be the fleet baseline, not a per-host hermes write
    assert "baseline" in scan_block, "apply step must re-point the router-models baseline"
    # the ephemeral per-host write must NOT be the propagation path
    assert "hermes config set model.default" not in scan_block


# ── 5. reviewer resolver still resolves (guarded) ──
def test_reviewer_model_still_resolves():
    rev = _m.get_reviewer_model()
    assert rev  # canonical default or env override, never empty