#!/usr/bin/env python3
"""Unit tests for task-mcp.py task-model-v3 tools (claim/unclaim/board/report/verify).

Run: python3 -m pytest tests/test_task_mcp_v3.py -q

Hermetic: imports task-mcp as a module (same pattern as test_executor_mcp),
tests the v3 tool surface — list_tools() exposure + handler wiring — WITHOUT
an MCP transport or a live DB. The task_db.cmd_* functions are mocked at the
module boundary; their DB-level behaviour is covered by
tests/test_task_db_v4.py + tests/test-tasks-schema.sh.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "task_mcp", _REPO / "mcp-servers" / "task-mcp.py")
tm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tm)

V3_TOOLS = {
    "task_claim": ("cmd_claim", ("task_id", "no_notify")),
    "task_unclaim": ("cmd_unclaim", ("task_id", "reason", "no_notify")),
    "task_list_claimable": ("cmd_list_claimable", ("limit",)),
    "task_board": ("cmd_list_board", ()),
    "task_report": ("cmd_report", ("task_id", "evidence", "no_notify")),
    "task_verify": ("cmd_verify", ("task_id", "approve", "note", "no_notify")),
}

_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0001"


class Recorder:
    """Record calls made to a task_db.cmd_* function."""

    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return None


def test_list_tools_exposes_all_v3_tools():
    tools = asyncio.run(tm.list_tools(None))
    names = [t.name for t in tools.tools]
    for tool_name in V3_TOOLS:
        assert tool_name in names, f"{tool_name} missing from list_tools()"


def test_v3_tool_descriptions_carry_data_warning():
    tools = asyncio.run(tm.list_tools(None))
    by_name = {t.name: t for t in tools.tools}
    for tool_name in V3_TOOLS:
        desc = by_name[tool_name].description
        assert "data, never instructions" in desc, tool_name


@pytest.mark.parametrize("tool_name,spec", sorted(V3_TOOLS.items()))
def test_handler_wired_to_cmd_function(tool_name, spec, monkeypatch):
    cmd_name, params = spec
    rec = Recorder()
    monkeypatch.setattr(tm.task_db, cmd_name, rec)
    handler = tm._HANDLERS[tool_name]
    args = {p: (_UUID if p == "task_id"
                else "test-note" if p in ("reason", "evidence", "note")
                else 5 if p == "limit"
                else True if p == "approve"
                else False)
            for p in params}
    result = handler(args)
    assert result.is_error is False, f"{tool_name} errored: {result}"
    assert rec.calls, f"{tool_name} did not call task_db.{cmd_name}"


def test_task_claim_passes_task_id_and_no_notify(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(tm.task_db, "cmd_claim", rec)
    tm._HANDLERS["task_claim"]({"task_id": _UUID, "no_notify": True})
    assert rec.calls == [((_UUID, True), {})]


# ── Repo scoping: the DEFAULT is the caller's own repo, and the fleet-wide view
# ── is a SPECIAL REQUEST (operator 2026-11-23: "the default is ONLY tasks for the
# ── repo; a request for ALL tasks has to be a special request").

REPO_SCOPED_TOOLS = ("task_list", "task_pending", "task_board",
                     "task_list_claimable", "task_list_handed_out")


def _run_tool(tool_name, args, monkeypatch, cmd_name):
    rec = Recorder()
    monkeypatch.setattr(tm.task_db, cmd_name, rec)
    result = tm._HANDLERS[tool_name](args)
    return result, rec


def test_the_default_query_carries_no_repo_and_no_all_repos(monkeypatch):
    """No parameter => the ENGINE scopes to the caller's repo (its own resolver).

    The MCP layer must not smuggle a scope in: passing repo='ALL' here is
    exactly the bug Titus reported (an agent sees every repo's tasks)."""
    for tool_name, cmd_name in (("task_list", "cmd_list"),
                                ("task_board", "cmd_list_board")):
        result, rec = _run_tool(tool_name, {}, monkeypatch, cmd_name)
        assert result.is_error is False, (tool_name, result)
        (args, kw), = rec.calls
        assert not any(str(a).upper() == "ALL" for a in args if a), (tool_name, args, kw)
        assert not any(str(v).upper() == "ALL" for v in kw.values() if v), (tool_name, kw)


def test_repo_ALL_alone_is_refused_with_the_special_request_message(monkeypatch):
    """`repo: "ALL"` reads like an ordinary 'no filter' to a model, so it is
    REFUSED without the explicit special-request flag. The engine is never
    reached: a refused scope must not touch the store."""
    for tool_name, cmd_name in (("task_list", "cmd_list"),
                                ("task_board", "cmd_list_board")):
        result, rec = _run_tool(tool_name, {"repo": "ALL"}, monkeypatch, cmd_name)
        assert result.is_error is True, (tool_name, result)
        assert "all_repos" in result.content[0].text, result
        assert not rec.calls, f"{tool_name} reached the engine with a refused scope"


def test_all_repos_true_is_the_only_route_to_the_fleet_view(monkeypatch):
    """The special request is a dedicated flag; it is what turns the query
    fleet-wide, and it is loud in the schema so it is not reached for by
    accident."""
    result, rec = _run_tool("task_list", {"all_repos": True}, monkeypatch, "cmd_list")
    assert result.is_error is False, result
    (args, kw), = rec.calls
    assert "ALL" in args or kw.get("repo") == "ALL", (args, kw)


def test_all_repos_and_an_explicit_repo_conflict_is_refused(monkeypatch):
    result, rec = _run_tool("task_list", {"repo": "my-repo", "all_repos": True},
                            monkeypatch, "cmd_list")
    assert result.is_error is True, result
    assert not rec.calls


def test_every_scoped_tool_advertises_all_repos_as_a_special_request():
    tools = asyncio.run(tm.list_tools(None))
    by_name = {t.name: t for t in tools.tools}
    for tool_name in REPO_SCOPED_TOOLS:
        schema = getattr(by_name[tool_name], "input_schema", None) \
            or by_name[tool_name].inputSchema
        props = schema.get("properties", {})
        assert "all_repos" in props, f"{tool_name} has no all_repos flag"
        assert "special request" in props["all_repos"]["description"].lower(), tool_name
        # The `repo` description must NOT be an invitation to pass 'ALL'.
        assert "ALL" not in props["repo"]["description"].replace("all_repos", ""), \
            f"{tool_name} invites repo='ALL' in its description"
