"""Regression test: mycortex-mem provider must advertise its tools
BEFORE initialize() is called.

The provider advertises the mem_* family (5 tools) plus the session seam the
shared `ops/services/mycortex-mem/context_tools.py` module owns — the memory/
session seam, so one plugin serves both surfaces.

The gateway calls add_provider() (which reads get_tool_schemas() to build
the executor's tool routing table) BEFORE initialize_all() runs
(agent/agent_init.py). If get_tool_schemas() gates on self._pg being set,
the routing table is empty and every mem_* call fails with
"Unknown tool: mem_profile" — even though the schemas appear in the
system prompt (inject_memory_provider_tools runs after initialize, when
_pg exists). This test pins the pre-initialize contract.

Repro log: "Memory provider 'mycortex-mem' registered (0 tools)" on every
session while the prompt advertised the tools.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_INIT = Path(__file__).resolve().parents[1] / "plugins" / "mycortex-mem" / "__init__.py"
CONTEXT_TOOLS = (Path(__file__).resolve().parents[1]
                 / "ops" / "services" / "mycortex-mem" / "context_tools.py")

_spec = importlib.util.spec_from_file_location("mycortex_mem_plugin", PLUGIN_INIT)
assert _spec is not None and _spec.loader is not None, f"cannot load plugin from {PLUGIN_INIT}"
mycortex_mem = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mycortex_mem)


def _context_tools():
    """The shared module that OWNS the session_* handlers the provider advertises."""
    spec = importlib.util.spec_from_file_location("mycortex_context_tools_t", CONTEXT_TOOLS)
    assert spec is not None and spec.loader is not None, f"cannot load {CONTEXT_TOOLS}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXPECTED_MEM_TOOLS = {"mem_profile", "mem_search", "mem_context", "mem_reasoning", "mem_conclude"}


@pytest.fixture()
def provider():
    return mycortex_mem.MycortexMemMemoryProvider()


def test_get_tool_schemas_before_initialize_advertises_both_families(provider):
    """The ABC contract (memory_provider.py) requires schemas to be
    advertised regardless of connection state; the gateway reads them at
    add_provider() time, before initialize() has run.

    The provider advertises the mem_* family PLUS the session seam the shared
    context_tools module owns (memory/session seam). The mem family is pinned
    exactly; the session names must be a non-empty subset of the shared
    surface, so a provider that stops advertising them (or invents names no
    module handles) fails here.
    """
    schemas = provider.get_tool_schemas()
    names = {s.get("name") for s in schemas}

    mem = {n for n in names if n.startswith("mem_")}
    assert mem == EXPECTED_MEM_TOOLS, mem
    ses = {n for n in names if n.startswith("session_")}
    assert ses, "the provider no longer advertises the session seam"
    assert ses <= set(_context_tools().HANDLERS), f"advertised session tools with no handler: {ses}"


def test_handle_tool_call_routes_all_advertised_tools(provider):
    """Every advertised tool must have a handler AT THE LAYER THAT OWNS IT —
    otherwise the executor routes to the provider and dies on a missing
    handler.

    DECOUPLED: `mem_reasoning` is the ONE host-local tool — it calls this host's
    own LLM, which is exactly why the host-neutral surface deliberately does not
    ship it. Every other advertised tool (the mem_* family that any coding agent
    reaches through the same contract, plus the session_* seam) is owned by
    `ops/services/mycortex-mem/context_tools.py` and dispatched there.
    """
    # One implementation, one place: a shared tool redefined as a private method
    # is the duplication this decouple removed, so its return fails here.
    duplicated = [m for m in ("_tool_profile", "_tool_search", "_tool_context", "_tool_conclude")
                  if hasattr(provider, m)]
    assert not duplicated, f"shared tool(s) defined a second time on the provider: {duplicated}"
    assert callable(provider._tool_reasoning), "the host-local extra lost its handler"

    shared = set(_context_tools().HANDLERS)
    for schema in provider.get_tool_schemas():
        name = schema["name"]
        if name == "mem_reasoning":
            assert name not in shared, (
                "mem_reasoning must stay host-local — it needs this host's LLM"
            )
        else:
            assert name in shared, f"no shared handler for {name}"
