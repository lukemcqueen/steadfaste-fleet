"""Hermetic tests for the fleet-owned DeepSeek review MCP server.

The module filename uses hyphens (fleet convention, matching the other
mcp-servers/*.py), so it cannot be imported by name — load it via
importlib.util.spec_from_file_location. OPENROUTER_API_KEY is set in the
environment BEFORE loading so the module's startup key check does not
sys.exit(1); the tests use a throwaway key and never make a real network call
(the error path is driven with a monkeypatched transport).
"""

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

_SERVER = Path(__file__).parent.parent / "mcp-servers" / "deepseek-review-mcp.py"
assert _SERVER.is_file(), f"server not found: {_SERVER}"


@pytest.fixture(scope="module")
def server():
    """Load the hyphen-named server module with a throwaway key in env."""
    saved = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "sk-test-key-1234567890"
    try:
        spec = importlib.util.spec_from_file_location("_dsrm_under_test", str(_SERVER))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if saved is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = saved


def _tool_names(mod):
    return [t.name for t in asyncio.run(mod.list_tools(None)).tools]


def _tool(mod, name):
    return next(t for t in asyncio.run(mod.list_tools(None)).tools if t.name == name)


class _Params:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


def test_default_model_is_the_cost_pinned_reviewer(server):
    assert server.DEFAULT_MODEL == "deepseek/deepseek-v4-pro-0813"


def test_exposes_exactly_the_three_tools(server):
    assert _tool_names(server) == ["review", "ask_deepseek", "list_available_models"]


def test_review_defaults_to_the_pinned_model(server):
    rev = _tool(server, "review")
    assert rev.input_schema["properties"]["model"]["default"] == server.DEFAULT_MODEL


@pytest.mark.parametrize("content,expected", [
    ("OPENROUTER_API_KEY=abc123\n", "abc123"),
    ("  OPENROUTER_API_KEY=abc123\n", "abc123"),          # leading whitespace
    ("export OPENROUTER_API_KEY=abc123\n", "abc123"),     # export prefix
    ('OPENROUTER_API_KEY="abc123"\n', "abc123"),          # double quotes
    ("OPENROUTER_API_KEY='abc123'\n", "abc123"),          # single quotes
    ("OPENROUTER_API_KEY=abc123\r\n", "abc123"),          # CRLF line ending
    ("OTHER=x\n", ""),                                    # absent -> ""
])
def test_read_key_from_tolerates_real_env_syntax(server, tmp_path, content, expected):
    p = tmp_path / ".env"
    p.write_text(content)
    assert server._read_key_from(str(p)) == expected


def test_read_key_from_unreadable_file_returns_empty(server, tmp_path):
    assert server._read_key_from(str(tmp_path / "does-not-exist")) == ""


def test_call_deepseek_error_is_scrubbed_and_never_a_clean_review(server, monkeypatch):
    """A 401 must raise (never return a fake review) and must scrub the key."""
    class _Fake401:
        status_code = 401
        text = '{"error":{"message":"Bad key sk-real-secret-abcdefghijkl"}}'

    monkeypatch.setattr(server.requests, "post", lambda *a, **k: _Fake401())
    with pytest.raises(RuntimeError) as exc:
        server._call_deepseek("sys", "user")
    assert "sk-" not in str(exc.value)
    assert "REDACTED" in str(exc.value)


def test_review_tool_errors_on_empty_plan(server):
    result = asyncio.run(server.call_tool(None, _Params("review", {"plan_text": "  "})))
    assert result.is_error is True
    assert "plan_text is required" in result.content[0].text