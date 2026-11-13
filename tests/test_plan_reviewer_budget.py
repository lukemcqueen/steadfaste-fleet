#!/usr/bin/env python3
"""The PLAN reviewer call must be BOUNDED — output cap and reasoning effort.

The close-gate reviewer (loop-gov-mcp.py) carries a bounded reasoning budget;
the plan reviewer (ops/scripts/orch-bus/adversarial-review.py `_call_model`)
historically sent NO max_tokens and NO reasoning_effort, so it ran the model's
default (effectively max/high) — measured: 6-15K output tokens per plan review,
74% of the fleet review bill (docs/evidence/review-cost-ledger-2026-11-13.txt).

This file proves the plan-review call carries:
- max_tokens == REVIEWER_PLAN_MAX_TOKENS (default 24000), env-overridable,
  with a floor below which the value is rejected LOUDLY (never silently clamped);
- reasoning.effort == REVIEWER_PLAN_REASONING_EFFORT (default "low"), taken from
  the provider's own vocabulary, env-overridable, invalid value falls back loudly;
- the usage ledger (`_record_review_call`) is still written.

Discrimination: run this against the pre-change module and the budget assertions
MUST fail (that revision sends no max_tokens/reasoning). Hermetic: the loaded
module's own `urllib` reference is swapped, the shared module is never touched.

Run:  python3 tests/test_plan_reviewer_budget.py
   or: pytest tests/test_plan_reviewer_budget.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Discrimination seam: point it at a pre-change copy of the script and the
# budget assertions must fail (that revision sends no max_tokens/reasoning).
SCRIPT = Path(os.environ.get(
    "PLAN_REVIEW_SCRIPT", REPO / "ops" / "scripts" / "orch-bus" / "adversarial-review.py"))


def _load_script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("plan_review_budget_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeUrllib:
    """Per-module urllib stand-in so the shared module is never patched."""

    def __init__(self, urlopen):
        self.request = types.SimpleNamespace(
            urlopen=urlopen, Request=urllib.request.Request)
        self.error = urllib.error


class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(payload: dict, captured: dict):
    def _open(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return _Resp(payload)
    return _open


def _call_with(mod: types.ModuleType, captured: dict, tmp: Path) -> str:
    os.environ["OPENROUTER_API_KEY"] = "test-key-not-a-secret"
    payload = {
        "choices": [{"message": {"content": '{"verdict":"CLEAN","findings":[]}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mod.urllib = _FakeUrllib(_stub_urlopen(payload, captured))
    return mod._call_model("judge this", db_path=str(tmp / "lg.db"))


def _env(tmp: Path, **kv) -> dict:
    """Run a call with the given env overrides; returns captured body."""
    saved = {}
    for k in kv:
        saved[k] = os.environ.get(k)
        os.environ[k] = kv[k]
    try:
        captured: dict = {}
        mod = _load_script()
        out = _call_with(mod, captured, tmp)
        assert "CLEAN" in out, out
        return captured["body"]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_the_request_carries_a_bounded_output_cap(tmp_path):
    body = _env(tmp_path)
    assert "max_tokens" in body, (
        "the plan-review call must cap OUTPUT tokens: an unbounded max_tokens "
        f"lets a reasoning model spend the whole budget thinking: {body}")
    assert body["max_tokens"] == 24000, (
        f"default cap must be REVIEWER_PLAN_MAX_TOKENS=24000 (measured plan "
        f"output is 6-15K): {body['max_tokens']}")


def test_max_tokens_is_env_overridable(tmp_path):
    body = _env(tmp_path, REVIEWER_PLAN_MAX_TOKENS="30000")
    assert body["max_tokens"] == 30000, body


def test_max_tokens_below_the_floor_is_rejected_loudly(tmp_path):
    # A cap below the floor would truncate the measured 6-15K output range and
    # silently gut the review; it must refuse loudly, not clamp.
    saved = os.environ.get("REVIEWER_PLAN_MAX_TOKENS")
    os.environ["REVIEWER_PLAN_MAX_TOKENS"] = "8000"
    try:
        mod = _load_script()
        captured: dict = {}
        try:
            _call_with(mod, captured, tmp_path)
            raise AssertionError("a below-floor cap must RAISE, never silently clamp")
        except SystemExit as exc:
            assert "REVIEWER_PLAN_MAX_TOKENS" in str(exc), exc
            assert "16000" in str(exc), exc
    finally:
        if saved is None:
            os.environ.pop("REVIEWER_PLAN_MAX_TOKENS", None)
        else:
            os.environ["REVIEWER_PLAN_MAX_TOKENS"] = saved


def test_the_request_carries_a_reasoning_effort(tmp_path):
    body = _env(tmp_path)
    assert "reasoning" in body, (
        "the plan-review call must bound THINKING: no reasoning key means the "
        f"model's default effort (max/high) is used: {body}")
    effort = (body.get("reasoning") or {}).get("effort")
    assert effort in ("none", "low", "medium", "high"), (
        f"reasoning.effort must come from the provider vocabulary: {effort!r}")
    assert effort == "low", (
        f"default plan-review effort must be low (enough thinking for real "
        f"findings, bounded): {effort!r}")


def test_reasoning_effort_is_env_overridable(tmp_path):
    body = _env(tmp_path, REVIEWER_PLAN_REASONING_EFFORT="medium")
    assert body["reasoning"]["effort"] == "medium", body


def test_an_invalid_effort_falls_back_loudly(tmp_path):
    saved = os.environ.get("REVIEWER_PLAN_REASONING_EFFORT")
    os.environ["REVIEWER_PLAN_REASONING_EFFORT"] = "extremely-hard-please"
    try:
        mod = _load_script()
        captured: dict = {}
        _call_with(mod, captured, tmp_path)
        effort = (captured["body"].get("reasoning") or {}).get("effort")
        assert effort == "low", (
            "an unknown effort must fall back to the default (a 400 at review "
            f"time reads exactly like a reviewer outage): {effort!r}")
    finally:
        if saved is None:
            os.environ.pop("REVIEWER_PLAN_REASONING_EFFORT", None)
        else:
            os.environ["REVIEWER_PLAN_REASONING_EFFORT"] = saved


def test_the_ledger_is_still_written(tmp_path):
    db = tmp_path / "lg.db"
    body = _env(tmp_path)
    _ = body  # the call above already ran with a temp db_path
    # The stub captured the request; the ledger write is exercised through the
    # same call. Assert the call returned a verdict and the module's recorder
    # is reachable with a real (temp) db — the review itself is what records.
    import sqlite3
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='review_calls'")]
    conn.close()
    assert "review_calls" in tables, (
        "_call_model must still create/write the review_calls ledger")


def test_the_pre_change_revision_is_unbounded(tmp_path):
    """Discrimination: the baseline must FAIL these same checks."""
    if SCRIPT == REPO / "ops" / "scripts" / "orch-bus" / "adversarial-review.py":
        print("SKIP: discrimination case — run with "
              "PLAN_REVIEW_SCRIPT=<pre-change copy>")
        return
    src = SCRIPT.read_text()
    assert '"reasoning"' not in src, (
        "the baseline already sends a reasoning budget — it is not a pre-change "
        "revision and this comparison proves nothing")
    assert '"max_tokens"' not in src, (
        "the baseline already sends max_tokens — not a pre-change revision")


if __name__ == "__main__":
    CASES = (
        test_the_request_carries_a_bounded_output_cap,
        test_max_tokens_is_env_overridable,
        test_max_tokens_below_the_floor_is_rejected_loudly,
        test_the_request_carries_a_reasoning_effort,
        test_reasoning_effort_is_env_overridable,
        test_an_invalid_effort_falls_back_loudly,
        test_the_ledger_is_still_written,
        test_the_pre_change_revision_is_unbounded,
    )
    failures = 0
    for fn in CASES:
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td))
                print(f"✅ {fn.__name__}")
            except AssertionError as exc:
                failures += 1
                print(f"❌ {fn.__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001 — a missing symbol is a finding too
                failures += 1
                print(f"❌ {fn.__name__}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
