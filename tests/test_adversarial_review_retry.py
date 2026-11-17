"""Retry behaviour of the plan-review client (ops/scripts/orch-bus/adversarial-review.py).

Operator directive 2026-11-17 (Luke): "there needs to be a retry for a failed
review". Before this, a transient reviewer failure either raised a bare urllib
traceback or — when the provider returned HTTP 200 with empty content, which a
reasoning model does when it spends the whole output budget thinking — returned
None and crashed 40 lines later with

    AttributeError: 'NoneType' object has no attribute 'find'

inside _extract_reviewer_json. That named neither the provider nor the cause, and
blocked the plan-review gate with an error that looked like a bug in the worker's
own script.

These tests are hermetic: urlopen is stubbed, so nothing is spent and no network
is touched. They assert the four properties that matter:
  1. a transient failure is retried and a later success is used;
  2. an auth/bad-request failure fails FAST (retrying our own bug wastes time);
  3. an exhausted retry budget raises a loud OUTAGE error naming the cause;
  4. NO review row is written on any failure path — an unanswered review must
     never become a stored verdict (that is the whole point of the gate).
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ops" / "scripts" / "orch-bus" / "adversarial-review.py"
_ENDPOINT = "https://example.com/not-used"


def _load_module():
    spec = importlib.util.spec_from_file_location("adv_review_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def adv(monkeypatch):
    mod = _load_module()
    # Keep the test instant and deterministic.
    monkeypatch.setattr(mod, "_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(mod, "_RETRY_BACKOFF", (0, 0), raising=False)
    monkeypatch.setattr(mod, "time", type("T", (), {"sleep": staticmethod(lambda _s: None)}))
    # Never touch the real reviewer config or the telemetry DB.
    monkeypatch.setattr(mod, "get_reviewer_model", lambda: "test/reviewer-model")
    monkeypatch.setattr(mod, "_record_review_call", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    return mod


class _Resp:
    """Minimal urlopen() return: a context manager with .read()."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def _ok(content):
    return _Resp(json.dumps({"choices": [{"message": {"content": content},
                                          "finish_reason": "stop"}],
                             "usage": {"total_tokens": 1}}).encode())


def _http_error(code, body=b'{"error":"boom"}'):
    return urllib.error.HTTPError(_ENDPOINT, code, "err", {}, io.BytesIO(body))


def _stub(mod, monkeypatch, sequence):
    """urlopen returns/raises each item of `sequence`, recording call count."""
    calls = {"n": 0}

    def fake(req, timeout=None):
        item = sequence[min(calls["n"], len(sequence) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    return calls


def test_transient_503_is_retried_and_the_later_success_is_used(adv, monkeypatch):
    calls = _stub(adv, monkeypatch, [_http_error(503), _http_error(429), _ok("REVIEWED")])
    assert adv._call_model("prompt") == "REVIEWED"
    assert calls["n"] == 3, "both transient failures should have been retried"


def test_connection_error_is_retried(adv, monkeypatch):
    calls = _stub(adv, monkeypatch, [urllib.error.URLError("dns"), _ok("REVIEWED")])
    assert adv._call_model("prompt") == "REVIEWED"
    assert calls["n"] == 2


def test_auth_error_is_not_retried(adv, monkeypatch):
    calls = _stub(adv, monkeypatch, [_http_error(401, b'{"error":"bad key"}')])
    with pytest.raises(SystemExit) as exc:
        adv._call_model("prompt")
    assert calls["n"] == 1, "a 401 is our own misconfiguration — retrying it wastes time"
    assert "not" in str(exc.value) and "401" in str(exc.value)
    assert "bad key" in str(exc.value), "the provider's own message must survive"


def test_empty_content_with_other_finish_reasons_is_retried_then_loud(adv, monkeypatch):
    """Empty content that is NOT a budget ceiling is transient — retry it."""
    calls = _stub(adv, monkeypatch, [_ok(None)])
    with pytest.raises(SystemExit) as exc:
        adv._call_model("prompt")
    assert calls["n"] == 3, "empty content must be retried, not returned as None"
    msg = str(exc.value)
    assert "no usable text" in msg
    assert "No review row was written" in msg, "must state plainly that no verdict exists"


def test_budget_exhausted_on_reasoning_fails_fast_and_names_both_knobs(adv, monkeypatch):
    """The MEASURED production failure (2026-11-17, cycle 14933).

    Real response: HTTP 200, finish_reason='length', completion_tokens=24000,
    reasoning_tokens=23999, content=None — three attempts identical to the token,
    $0.019 each. The model spent the whole output budget thinking. This is
    DETERMINISTIC, so retrying it can never succeed; it must fail fast and name
    the two settings that actually fix it. Calling it an "outage" would send the
    operator to the provider instead of to the cap.
    """
    payload = _Resp(json.dumps({
        "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 24000, "reasoning_tokens": 23999},
    }).encode())
    calls = _stub(adv, monkeypatch, [payload])
    with pytest.raises(SystemExit) as exc:
        adv._call_model("prompt")
    assert calls["n"] == 1, (
        "a deterministic config ceiling must NOT be retried — three identical "
        "attempts cost 3x the money and 3x the wait and can never succeed"
    )
    msg = str(exc.value)
    assert "CONFIG CEILING" in msg and "not an outage" in msg
    assert "REVIEWER_PLAN_REASONING_EFFORT" in msg, "must name the cheap knob"
    assert "REVIEWER_PLAN_MAX_TOKENS" in msg, "must name the other knob"
    assert "OUTAGE" not in msg, "misnaming this points at the wrong fix"


def test_exhausted_retries_never_write_a_verdict_row(adv, monkeypatch, tmp_path):
    db = tmp_path / "governance.db"
    _stub(adv, monkeypatch, [_http_error(502)])
    with pytest.raises(SystemExit):
        adv._call_model("prompt", db_path=str(db))
    assert not db.exists(), (
        "a failed review must leave NO row behind — an unanswered review is not CLEAN"
    )


def test_extract_reviewer_json_refuses_empty_text_instead_of_attributeerror(adv):
    for bad in (None, "", "   "):
        with pytest.raises(SystemExit) as exc:
            adv._extract_reviewer_json(bad)
        assert "refusing to record a verdict" in str(exc.value)


def test_retry_constants_are_bounded_and_sane(adv):
    assert adv._RETRY_ATTEMPTS >= 2, "one attempt is not a retry"
    assert adv._RETRY_ATTEMPTS <= 5, "an unbounded retry burns the review budget"
    assert len(adv._RETRY_BACKOFF) >= adv._RETRY_ATTEMPTS - 1
    assert 429 in adv._RETRY_STATUSES and 503 in adv._RETRY_STATUSES
    assert 401 not in adv._RETRY_STATUSES and 403 not in adv._RETRY_STATUSES
