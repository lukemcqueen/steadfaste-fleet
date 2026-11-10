"""review_calls ledger — every reviewer model call records the provider's usage.

The fleet review-cost figure was modeled (~20K in / 4K out per review) because
blob_reviews stores no token counts. This pins the fix: a per-call ledger
table, written at BOTH reviewer call sites from the `usage` the API response
already carries. Ledger-accurate cost = sum(review_calls) x live prices.

The ledger is telemetry, not enforcement: a write failure must never fail the
review (enforcement-change-safety Rule 1 — the review refusal/skip logic is
untouched). A retry is a NEW spend and gets a NEW row; review_id is uuid4,
collision protection only.

Both modules are loaded by spec (dash-free alias) like the sibling
test_reviewer_tier_wiring.py; the HTTP layer is stubbed at urlopen, the
module's LOOP_DB is redirected to a throwaway temp DB.
"""

import io
import importlib.util
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MCP = REPO / "mcp-servers" / "loop-gov-mcp.py"
ADV = REPO / "ops" / "scripts" / "orch-bus" / "adversarial-review.py"

USAGE = {
    "prompt_tokens": 3456,
    "completion_tokens": 789,
    "completion_tokens_details": {"reasoning_tokens": 200},
}


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeUrlopen:
    def __init__(self, payload: dict):
        self._payload = payload

    def __call__(self, req, timeout=None):
        return _FakeResp(self._payload)


def _load(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gov(tmp_path, monkeypatch):
    mod = _load(MCP, "ledger_gov")
    monkeypatch.setattr(mod, "LOOP_DB", tmp_path / "loop-governance.db")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ADVERSARIAL_REVIEW_BACKEND", "llm")
    return mod


@pytest.fixture()
def adv(tmp_path, monkeypatch):
    mod = _load(ADV, "ledger_adv")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return mod, tmp_path / "loop-governance.db"


def _ok_payload(content='{"verdict": "CLEAN", "findings": [], "summary": "stub"}', usage=USAGE):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": usage}


def _empty_payload(usage=USAGE):
    return {"choices": [{"message": {"content": None}, "finish_reason": "length"}], "usage": usage}


def _rows(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT review_id, source, reviewer_model, prompt_tokens,"
                           " completion_tokens, reasoning_tokens FROM review_calls"
                           " ORDER BY ts").fetchall()
    finally:
        con.close()


# ── 1. loop-gov writes a close row with the usage it received ──

def test_loop_gov_records_usage_row(gov, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _FakeUrlopen(_ok_payload()))
    text = gov._call_reviewer_llm("prompt")
    assert "CLEAN" in text
    rows = _rows(gov.LOOP_DB)
    assert len(rows) == 1
    rid, source, model, pt, ct, rt = rows[0]
    assert source == "close"
    assert pt == 3456 and ct == 789 and rt == 200


# ── 2. adversarial-review writes a plan row ──

def test_adversarial_review_records_plan_row(adv, monkeypatch):
    mod, db = adv
    monkeypatch.setattr(urllib.request, "urlopen", _FakeUrlopen(_ok_payload()))
    text = mod._call_model("prompt", db_path=str(db))
    assert "CLEAN" in text
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0][1] == "plan"
    assert rows[0][3] == 3456 and rows[0][4] == 789


# ── 3. ledger write failure never fails the review ──

def test_ledger_failure_does_not_fail_the_review(gov, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _FakeUrlopen(_ok_payload()))

    def _boom_db():
        raise sqlite3.Error("ledger full")

    # The REAL _record_review_call must swallow a failing store: stub the
    # connection factory (the seam the function's own try/except guards), not
    # the whole function (which would bypass the protection being tested).
    monkeypatch.setattr(gov, "_db", _boom_db)
    text = gov._call_reviewer_llm("prompt")
    assert "CLEAN" in text  # the review still succeeded


# ── 4. two calls, same material → two rows (a retry is a new spend) ──

def test_two_calls_two_rows(gov, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _FakeUrlopen(_ok_payload()))
    gov._call_reviewer_llm("same material")
    gov._call_reviewer_llm("same material")
    rows = _rows(gov.LOOP_DB)
    assert len(rows) == 2
    assert rows[0][0] != rows[1][0]  # distinct review_id
    assert rows[0][3] == rows[1][3] == 3456  # same tokens, two spends


# ── 5. missing usage → row still written, NULL tokens ──

def test_missing_usage_writes_row_with_nulls(gov, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _FakeUrlopen({"choices": [{"message": {"content": "ok"},
                                                   "finish_reason": "stop"}]}))
    text = gov._call_reviewer_llm("prompt")
    assert text == "ok"
    rows = _rows(gov.LOOP_DB)
    assert len(rows) == 1
    assert rows[0][3] is None and rows[0][4] is None


# ── 6. empty-content/refused path writes the row, then still raises ──

def test_refused_path_records_then_raises(gov, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _FakeUrlopen(_empty_payload()))
    with pytest.raises(RuntimeError, match="NO usable content"):
        gov._call_reviewer_llm("prompt")
    rows = _rows(gov.LOOP_DB)
    assert len(rows) == 1          # the expensive no-verdict spend IS measured
    assert rows[0][1] == "close"
    assert rows[0][3] == 3456
