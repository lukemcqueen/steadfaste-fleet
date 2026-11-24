#!/usr/bin/env python3
"""Periodic model / token / cost notice for gateway prompts.

Luke (2026-11-24): "it would be nice to see in the gateway prompt the model / %
tokens / cost (periodically in the prompts - every 5m or 10m)".

The notice is only worth having if every number is REAL. These tests pin the
three properties that make that true:

- a source that cannot be read produces NO notice (never a guessed number),
- the interval is per SESSION, so a chat that goes quiet is not re-measured and
  a busy chat is not re-measured on every turn,
- the notice is OPT-IN: a spec without ``usage_notice`` sends exactly the prompt
  it sent before, byte for byte.

Run: python3 -m pytest tests/test_gateway_usage_notice.py -q
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "ops" / "scripts"))

import gateway_envelope as env                                  # noqa: E402
from fleet_gateway import agents as A                           # noqa: E402
from fleet_gateway import usage as U                            # noqa: E402


def _inbound(body="hello", chat=100001):
    return {"msg_id": env.new_msg_id(), "ts": int(time.time()), "from_agent": "",
            "to_agent": "pi", "channel": "telegram", "channel_user_id": chat,
            "thread_id": 42, "body": body, "media": [], "reply_to_msg_id": None,
            "ack_required": False}


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


# ── parsing a source's output ────────────────────────────────────────

def test_parse_usage_reads_the_key_value_line_a_source_prints():
    snap = U.parse_usage(
        "model=openrouter/kimi-k2.6 tokens=105000 cost=0.45 context=30% "
        "context_tokens=60000 context_window=200000\n")
    assert snap is not None
    assert snap.model == "openrouter/kimi-k2.6"
    assert snap.tokens == 105000
    assert snap.cost_usd == 0.45
    assert snap.context_percent == 30.0
    assert snap.context_tokens == 60000
    assert snap.context_window == 200000


def test_parse_usage_returns_none_when_the_source_printed_nothing_usable():
    assert U.parse_usage("") is None
    assert U.parse_usage("pi-control: could not read the session") is None
    # a partial line with no model and no tokens is not a measurement
    assert U.parse_usage("cost=0.0") is None


def test_format_notice_omits_zero_usage_instead_of_printing_a_broken_meter():
    # pi reports tokens=0 cost=0 for a provider that does not return usage; the
    # context figure is still real, so the line keeps only what stands up.
    snap = U.UsageSnapshot(model="pi/kimi", tokens=0, cost_usd=0.0, context_percent=19.87)
    line = U.format_notice(snap)
    assert "tokens=" not in line and "cost=" not in line
    assert "context=19.9%" in line


def test_format_notice_omits_what_was_not_measured():
    snap = U.UsageSnapshot(model="pi/kimi", tokens=1234)
    line = U.format_notice(snap)
    assert "model=pi/kimi" in line
    assert "tokens=1,234" in line
    assert "cost=" not in line
    assert "context=" not in line


def test_format_notice_is_empty_for_an_empty_snapshot():
    assert U.format_notice(U.UsageSnapshot()) == ""


# ── the interval gate ────────────────────────────────────────────────

def test_the_first_turn_is_due_and_later_turns_wait_for_the_interval():
    clock = _Clock()
    notice = U.UsageNotice(interval_s=600, clock=clock)
    calls = []

    def source():
        calls.append(1)
        return U.UsageSnapshot(model="pi/kimi", tokens=10)

    assert notice.notice("s1", source) != ""
    assert notice.notice("s1", source) == ""          # 0s later: not due
    clock.now += 599
    assert notice.notice("s1", source) == ""
    clock.now += 1
    assert notice.notice("s1", source) != ""          # 600s: due again
    assert len(calls) == 2


def test_the_interval_is_per_session():
    clock = _Clock()
    notice = U.UsageNotice(interval_s=600, clock=clock)
    src = lambda: U.UsageSnapshot(model="pi/kimi", tokens=1)   # noqa: E731
    assert notice.notice("s1", src) != ""
    assert notice.notice("s2", src) != ""             # another chat: its own timer


def test_usage_notice_rejects_an_unusable_interval_at_construction():
    for bad in (-1, "ten", None):
        try:
            U.UsageNotice(bad)
        except ValueError:
            continue
        raise AssertionError(f"interval_s={bad!r} should be refused at construction")


def test_a_source_that_cannot_be_read_produces_no_notice_and_is_not_hammered():
    clock = _Clock()
    notice = U.UsageNotice(interval_s=600, clock=clock)
    calls = []

    def broken():
        calls.append(1)
        return None

    assert notice.notice("s1", broken) == ""
    clock.now += 1
    assert notice.notice("s1", broken) == ""          # waits a full interval
    assert len(calls) == 1


# ── the two real sources ─────────────────────────────────────────────

def test_command_usage_parses_the_declared_command_output():
    snap = U.command_usage([sys.executable, "-c",
                            "print('model=pi/kimi tokens=42 cost=0.001 context=7%')"],
                           timeout_s=10)
    assert snap is not None
    assert snap.tokens == 42
    assert snap.context_percent == 7.0


def test_command_usage_returns_none_when_the_command_fails():
    snap = U.command_usage([sys.executable, "-c", "raise SystemExit(3)"], timeout_s=10)
    assert snap is None


def test_hermes_usage_reads_the_agents_own_session_row(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE sessions (
        chat_id TEXT, started_at REAL, model TEXT, input_tokens INTEGER,
        output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, estimated_cost_usd REAL)""")
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                ("100001", 1000.0, "qwen/qwen3", 900, 100, 50, 0, 0.0123))
    con.commit()
    con.close()

    snap = U.hermes_usage(str(db), "100001")
    assert snap is not None
    assert snap.model == "qwen/qwen3"
    # every token the provider processed: input + output + cache read + cache write
    assert snap.tokens == 1050
    assert snap.cost_usd == 0.0123


def test_hermes_usage_returns_none_on_a_missing_db_or_chat(tmp_path):
    assert U.hermes_usage(str(tmp_path / "absent.db"), "100001") is None
    db = tmp_path / "state.db"
    sqlite3.connect(db).close()
    assert U.hermes_usage(str(db), "100001") is None      # no sessions table


# ── the command backend ──────────────────────────────────────────────

def _command_spec(**over):
    spec = {"name": "pi", "kind": "command",
            "command": [sys.executable, "-c", "print('REPLY')"],
            "model": "openrouter/kimi-k2.6", "timeout_s": 30}
    spec.update(over)
    return A.AgentSpec.from_dict(spec)


def test_a_command_backend_without_usage_notice_sends_the_bare_prompt():
    backend = A.CommandBackend(_command_spec())
    prompt = backend._prompt(_inbound())
    assert prompt == "hello"


def test_a_command_backend_appends_the_notice_at_most_once_per_interval():
    clock = _Clock()
    spec = _command_spec(
        usage_notice=True, usage_interval_s=600,
        usage_command=[sys.executable, "-c",
                       "print('model=pi/kimi tokens=99 cost=0.002 context=12%')"])
    backend = A.CommandBackend(spec, usage=U.UsageNotice(600, clock=clock))

    first = backend._prompt(_inbound())
    assert "hello" in first
    assert "[gateway-usage]" in first
    assert "tokens=99" in first and "context=12%" in first

    second = backend._prompt(_inbound())
    assert second == "hello"                          # same interval: bare prompt

    clock.now += 600
    third = backend._prompt(_inbound())
    assert "[gateway-usage]" in third


def test_a_command_backend_omits_the_notice_when_the_source_fails():
    spec = _command_spec(
        usage_notice=True,
        usage_command=[sys.executable, "-c", "raise SystemExit(3)"])
    backend = A.CommandBackend(spec)
    assert backend._prompt(_inbound()) == "hello"


def test_usage_notice_on_a_command_backend_requires_a_usage_command():
    spec = _command_spec(usage_notice=True)
    try:
        spec.validate()
    except ValueError as e:
        assert "usage_command" in str(e)
    else:
        raise AssertionError("a usage_notice with no source must fail at build time")


# ── pi-control stats: the pi reader ──────────────────────────────────

def test_pi_control_stats_prints_the_line_the_gateway_parses(monkeypatch, capsys):
    from types import SimpleNamespace
    from fleet_gateway import pi_control as P

    monkeypatch.setattr(P, "_session_argv", lambda args: ["pi"])
    monkeypatch.setattr(P, "_rpc", lambda argv, cmd, deadline_s=0: {
        "success": True,
        "data": {"tokens": {"total": 105, "input": 100, "output": 5},
                 "cost": 0.45,
                 "contextUsage": {"tokens": 60000, "contextWindow": 200000,
                                  "percent": 30}},
    })
    args = SimpleNamespace(session_id="fleet-pi-1", model="openrouter/kimi",
                           timeout_s=30)
    rc = P.cmd_stats(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert "tokens=105" in out and "cost=0.45" in out and "context=30%" in out
    snap = U.parse_usage(out)
    assert snap is not None and snap.context_percent == 30


def test_pi_control_stats_returns_could_not_verify_when_pi_refuses(monkeypatch, capsys):
    from types import SimpleNamespace
    from fleet_gateway import pi_control as P

    monkeypatch.setattr(P, "_session_argv", lambda args: ["pi"])
    monkeypatch.setattr(P, "_rpc", lambda argv, cmd, deadline_s=0: {
        "success": False, "error": "no session"})
    rc = P.cmd_stats(SimpleNamespace(session_id="s", model="", timeout_s=30))
    assert rc == P.COULD_NOT_VERIFY
    assert "no session" in capsys.readouterr().err


# ── the hermes backend ───────────────────────────────────────────────

def _hermes(tmp_path, **over):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE sessions (
        chat_id TEXT, started_at REAL, model TEXT, input_tokens INTEGER,
        output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, estimated_cost_usd REAL)""")
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                ("100001", 1000.0, "qwen/qwen3", 900, 100, 0, 0, 0.02))
    con.commit()
    con.close()
    from fleet_gateway.hermes_backend import HermesBackend
    kw = dict(agent="esther", bus_url="", bus_headers={}, secret="s" * 32,
              usage_notice=True, usage_db=str(db))
    kw.update(over)
    return HermesBackend(**kw)


def test_hermes_backend_appends_the_notice_to_the_envelope_body(tmp_path):
    backend = _hermes(tmp_path)
    body = backend._body_with_usage(_inbound())
    assert "hello" in body
    assert "[gateway-usage]" in body
    assert "tokens=1,000" in body


def test_hermes_dispatch_sends_the_notice_and_leaves_the_original_alone(tmp_path, monkeypatch):
    """END-TO-END through dispatch(): the bus message the agent receives carries it.

    The unit test above proves the body builder; this drives the real dispatch
    path with only the bus stubbed, so the seam that actually ships the message
    is covered.
    """
    from fleet_gateway import hermes_backend as HB
    sent = []
    monkeypatch.setattr(HB._bus, "bus_send",
                        lambda url, headers, queue, msg: sent.append((queue, msg)) or True)
    backend = _hermes(tmp_path)
    envelope = _inbound()
    assert backend.dispatch(envelope) is None          # async backend
    assert len(sent) == 1
    queue, message = sent[0]
    assert queue == "inbox_esther"
    body = json.loads(message["body"])["body"]
    assert "[gateway-usage]" in body and "tokens=1,000" in body
    assert envelope["body"] == "hello"                 # caller's envelope untouched


def test_hermes_backend_does_not_mutate_the_callers_envelope(tmp_path):
    backend = _hermes(tmp_path)
    envelope = _inbound()
    body = backend._body_with_usage(envelope)
    assert "[gateway-usage]" in body
    assert envelope["body"] == "hello"


def test_hermes_backend_without_the_flag_leaves_the_body_alone(tmp_path):
    backend = _hermes(tmp_path, usage_notice=False)
    assert backend._body_with_usage(_inbound()) == "hello"
