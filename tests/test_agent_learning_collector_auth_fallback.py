"""Tests for the Bearer fallback in agent-learning-collector.send_report().

Why this exists: the collector picks ONE auth mode (Basic preferred over
Bearer). When FLEET_BASIC_AUTH is stale but the bus is the bare PGMQ API,
every send 401s and — because the failure is swallowed — the collector goes
silent for days (2026-11-23: Basic 401 on the local PGMQ port, Bearer 200,
last staged report 2026-11-19). The fix retries ONCE with the Bearer token
when the first attempt used Basic. These tests pin that behaviour so a
future refactor cannot quietly drop the fallback.
"""

from __future__ import annotations

import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

REPO = Path(__file__).resolve().parents[1]
COLLECTOR = REPO / "ops" / "scripts" / "manage" / "agent-learning-collector.py"

REPORT = {
    "generated": "2026-11-23T00:00:00+00:00",
    "skills": [{"name": "x", "category": "devops", "description": "d", "is_new": False}],
    "lessons": [],
    "learnings": [],
    "sessions": {"total_sessions": 1, "recent_sessions": 0},
}


def _load_collector():
    """Import the hyphenated module by path with AGENT_NAME set."""
    os.environ.setdefault("AGENT_NAME", "esther")
    spec = importlib.util.spec_from_file_location("agent_learning_collector", COLLECTOR)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, msg: str = "nope") -> HTTPError:
    return HTTPError("http://example.com/api/pgmq/send", code, msg, {}, io.BytesIO(b""))


class SendReportAuthFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_collector()

    def _call(self, *, token: str, side_effect):
        """Run send_report with a Basic header forced and _resolve_env stubbed."""
        mod = self.mod
        env = {"FLEET_BUS_URL": "http://127.0.0.1:9", "FLEET_BUS_TOKEN": token}

        def fake_resolve(key, default=""):
            return env.get(key, default)

        with mock.patch.object(mod, "_resolve_env", side_effect=fake_resolve), \
             mock.patch.object(mod, "_build_auth_headers",
                               return_value={"Authorization": "Basic c3RhbGU="}), \
             mock.patch("urllib.request.urlopen", side_effect=side_effect), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = mod.send_report(REPORT)
        return result, out.getvalue(), err.getvalue()

    def test_basic_401_then_bearer_succeeds(self):
        calls = []

        def side_effect(req, timeout=None):
            calls.append(dict(req.headers))
            if len(calls) == 1:
                raise _http_error(401)
            return _FakeResp(b'{"msg_id": "abcd1234"}')

        result, out, err = self._call(token="tok123", side_effect=side_effect)
        self.assertEqual(len(calls), 2, "expected exactly one retry")
        self.assertIn("Bearer tok123", calls[1].get("Authorization", ""))
        self.assertIsNotNone(result, "bearer fallback should have succeeded")
        self.assertIn("bearer fallback", out)
        self.assertNotIn("Send failed", err)

    def test_basic_401_without_token_returns_none(self):
        calls = []

        def side_effect(req, timeout=None):
            calls.append(req)
            raise _http_error(401)

        result, out, err = self._call(token="", side_effect=side_effect)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1, "no token means no retry")
        self.assertIn("401", err)

    def test_bearer_fallback_also_fails_returns_none(self):
        calls = []

        def side_effect(req, timeout=None):
            calls.append(req)
            raise _http_error(401)

        result, out, err = self._call(token="tok123", side_effect=side_effect)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 2, "one retry only")
        self.assertIn("Bearer fallback also failed", err)

    def test_500_does_not_retry(self):
        calls = []

        def side_effect(req, timeout=None):
            calls.append(req)
            raise _http_error(500)

        result, out, err = self._call(token="tok123", side_effect=side_effect)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1, "non-401 must not trigger the fallback")


if __name__ == "__main__":
    unittest.main()
