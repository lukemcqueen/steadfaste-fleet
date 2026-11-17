#!/usr/bin/env python3
"""The MCP surface is ONE artifact, projected per harness — and nothing re-implements
a contract tool.

These tests are the drift guard for the mycortex decoupling (cycle 14947): the
memory/session tool surface lives in ONE contract, every harness reaches it through
its own registration API, and the host that has a harness but no wiring is REPORTED.

Expectations are DERIVED from the system's own surface (the contract's HANDLERS, the
registry's own keys) — never hand-copied constants — so the tests cannot go green by
echoing their own literals.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

REGISTRY = REPO / "ops" / "install" / "harnesses" / "registry.yaml"
CONTRACT = REPO / "ops" / "services" / "mycortex-mem" / "context_tools.py"
PLUGIN = REPO / "plugins" / "mycortex-mem" / "__init__.py"
HERMES_INSTALLER = REPO / "ops" / "scripts" / "install" / "install-hermes-mcp.sh"
FLEET_UPDATE = REPO / "ops" / "scripts" / "fleet-update.sh"


def _load_contract():
    """Load the ONE implementation from its real file, exactly as the transports do."""
    spec = importlib.util.spec_from_file_location("cortex_context_tools_under_test", CONTRACT)
    assert spec is not None and spec.loader is not None, f"cannot load {CONTRACT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registry_text() -> str:
    assert REGISTRY.is_file(), f"the harness registry is missing: {REGISTRY}"
    return REGISTRY.read_text(encoding="utf-8")


def _declared_harnesses(text: str) -> list[tuple[str, str]]:
    """[(name, layer)] from the registry's own `harnesses:` list."""
    out: list[tuple[str, str]] = []
    name = None
    for line in text.splitlines():
        m = re.match(r"^  - name:\s*(\S+)", line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"^    layer:\s*(\S+)", line)
        if m and name:
            out.append((name, m.group(1)))
            name = None
    return out


# ── 1. The artifact is the single source of the surface ────────────────────────

def test_registry_exists_and_declares_the_context_server():
    text = _registry_text()
    assert "fleet-context-mcp.py" in text, (
        "the registry must name the shared context server — it is the ONE artifact "
        "that says which surface a harness connects to"
    )


def test_the_contract_is_the_only_definition_of_the_tool_surface():
    mod = _load_contract()
    assert hasattr(mod, "HANDLERS") and hasattr(mod, "TOOLS"), (
        "context_tools.py must expose HANDLERS and TOOLS — the surface both access "
        "layers derive from"
    )
    handler_names = set(mod.HANDLERS)
    tool_names = {t["name"] for t in mod.TOOLS}
    assert handler_names == tool_names, (
        f"HANDLERS and TOOLS disagree: {handler_names ^ tool_names}"
    )


# ── 2. No second implementation of a contract tool (cross-agent-design rule 1) ─

def test_plugin_reimplements_no_contract_tool():
    """A tool NAME defined twice is worse than not at all: the runtime picks one
    silently and the other becomes a phantom."""
    assert PLUGIN.is_file(), f"plugin source missing: {PLUGIN}"
    plugin_text = PLUGIN.read_text(encoding="utf-8")

    # Names the contract owns. Derived from the contract, not from this test.
    mod = _load_contract()
    contract_names = set(mod.HANDLERS)

    # The plugin may hold a handler whose NAME matches a contract tool only if it
    # forwards to the contract. Anything else is a second implementation.
    offenders = []
    for name in contract_names:
        short = name.split("_", 1)[-1]  # mem_profile -> profile
        if re.search(rf"^\s+def _tool_{short}\(", plugin_text, re.M):
            offenders.append(f"_tool_{short}")
    assert not offenders, (
        "the plugin re-implements contract tools and must not: "
        f"{offenders}. Dispatch through context_tools.dispatch() instead."
    )


def test_plugin_keeps_only_the_host_local_extra():
    """mem_reasoning needs the host's own LLM, so it is a legitimate host-local
    extra. It must be the ONLY tool the plugin handles itself."""
    mod = _load_contract()
    plugin_text = PLUGIN.read_text(encoding="utf-8")
    contract_names = set(mod.HANDLERS)

    local = set(re.findall(r'tool_name\s*==\s*"([a-z_]+)"', plugin_text))
    assert local, "expected the plugin to name its host-local extra explicitly"
    assert local.isdisjoint(contract_names), (
        f"the plugin handles {local & contract_names} locally AND via the contract"
    )


# ── 3. Every declared harness has a wiring path that exists ────────────────────

def test_every_mcp_harness_declares_a_register_script_that_exists():
    text = _registry_text()
    harnesses = _declared_harnesses(text)
    assert harnesses, "the registry declares no harnesses at all"

    blocks = re.split(r"^  - name: ", text, flags=re.M)[1:]
    missing = []
    for block in blocks:
        name = block.splitlines()[0].strip()
        # A declared gap (layer: none / status: blocked) has NO wiring path by
        # design — demanding a register script would force a fake one into
        # existence. Its `why` is the answer, and the doctor reports it as such.
        if re.search(r"^\s+layer:\s*none\s*$", block, re.M) or re.search(
            r"^\s+status:\s*blocked\s*$", block, re.M
        ):
            continue
        m = re.search(r"^        register:\s*(\S+)", block, re.M)
        if not m:
            missing.append(f"{name}: no capabilities.context.register")
            continue
        script = REPO / m.group(1)
        if not script.is_file():
            missing.append(f"{name}: register script absent ({m.group(1)})")
    assert not missing, f"harnesses without a usable wiring path: {missing}"


def test_hermes_is_a_declared_harness_with_a_context_adapter():
    """Hermes is the harness that was left unwired while Pi was shipped — the
    silence this whole cycle exists to end."""
    harnesses = dict(_declared_harnesses(_registry_text()))
    assert "hermes" in harnesses, (
        "hermes is not declared in the harness registry, so nothing knows it must "
        "be wired to the shared context surface"
    )
    assert HERMES_INSTALLER.is_file(), (
        f"the Hermes adapter must exist: {HERMES_INSTALLER}"
    )


# ── 4. The deploy RUNS the adapter (cross-agent-design rule 9) ────────────────

def test_the_deploy_runs_the_projection_not_just_copies_it():
    text = FLEET_UPDATE.read_text(encoding="utf-8")
    assert "install-hermes-mcp.sh" in text, (
        "fleet-update.sh must RUN the Hermes registration — copying an installer "
        "into the scripts dir wires nothing"
    )


def test_the_hermes_adapter_uses_the_harness_api_not_a_raw_config_write():
    """~/.hermes/config.yaml is security-sensitive; the harness's own CLI owns it."""
    text = HERMES_INSTALLER.read_text(encoding="utf-8")
    assert "hermes mcp add" in text, (
        "the adapter must register through the harness's own API (hermes mcp add)"
    )
    assert not re.search(r">\s*[^ ]*config\.yaml", text), (
        "the adapter must never redirect into config.yaml directly"
    )