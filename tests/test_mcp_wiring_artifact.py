#!/usr/bin/env python3
"""tests/test_mcp_wiring_artifact.py — the fleet MCP surface has ONE definition, and this pins it.

`ops/scripts/install/mcp-surface.inc.sh` says of itself: "the ONE definition of the
fleet MCP surface", sourced by every per-harness adapter so the surface is not
"N lists that drift the moment one changes". It says of `registry.yaml`: "A test
pins the two together so neither can drift alone."

That test did not exist. What its absence cost: `fleet-context` — the memory and
session surface itself — was declared in the artifact and registered on this host,
but absent from the map the doctor audits against, so no host was ever TOLD it was
unwired. The audit simply did not cover it. A comment claiming a guard is not a
guard; this file is the guard.

Four claims, each read from files (hermetic — no host state, no network):
  A. the artifact and the registry's `defaults.mcp_server` name the same server;
  B. every server the artifact declares is audited by the doctor (the assertion that
     would have caught the gap above — it FAILS the moment the artifact grows a
     server the doctor does not know about);
  C. every surface row names a source file the repo actually carries (a dangling row
     is a route to a script that does not exist);
  D. a harness the registry announces as shipped carries a registration path, and a
     `layer: none` harness states its reason — so a harness cannot be declared
     without a way to wire it.

Run:  python3 tests/test_mcp_wiring_artifact.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SURFACE = REPO / "ops" / "scripts" / "install" / "mcp-surface.inc.sh"
REGISTRY = REPO / "ops" / "install" / "harnesses" / "registry.yaml"
DOCTOR_CONFIG = REPO / "ops" / "scripts" / "manage" / "fleet_doctor" / "config.py"


def _surface_rows():
    """[(name, repo_relative_source)] parsed from the mcp_surface_lines heredoc.

    Only TAB-separated lines are rows: the contract is `name <TAB> deployed <TAB>
    repo-relative source`, so a comment or the `cat <<'SURFACE'` line cannot
    contribute a phantom server.
    """
    text = SURFACE.read_text()
    m = re.search(r"mcp_surface_lines\(\)\s*\{(.*?)\nSURFACE\n", text, re.DOTALL)
    assert m, f"mcp_surface_lines() heredoc not found in {SURFACE} — update this test"
    rows = []
    for line in m.group(1).splitlines():
        if "\t" not in line:
            continue
        parts = line.strip().split("\t")
        assert len(parts) == 3, (
            f"a surface row is not name<TAB>deployed<TAB>source: {line!r}"
        )
        rows.append((parts[0], parts[2]))
    assert rows, "the surface artifact declares no servers — empty or unparsed"
    return rows


def _doctor_map():
    """The doctor's EXPECTED_MCP_SERVERS, parsed the same way test_mcp_wiring.py does.

    Read as TEXT because the doctor is a package this test cannot import without
    pulling its whole environment in; test_mcp_wiring.py already owns the
    "the two maps must match" check, so this only needs the doctor's own view.
    """
    cfg = DOCTOR_CONFIG.read_text()
    block = re.search(r"EXPECTED_MCP_SERVERS\s*=\s*\{(.*?)\}", cfg, re.DOTALL)
    assert block, "the doctor's EXPECTED_MCP_SERVERS was not found — update this test"
    return dict(re.findall(r'"([a-z][a-z0-9-]*)":\s*"([^"]*)"', block.group(1)))


def _registry_default_mcp_server():
    """The registry's `defaults.mcp_server` — the ONE server every harness gets."""
    text = REGISTRY.read_text()
    block = re.search(r"^defaults:[ \t]*$(.*?)^harnesses:", text, re.DOTALL | re.MULTILINE)
    assert block, f"the registry defaults block was not found in {REGISTRY} — update this test"
    found = re.search(r"^[ \t]+mcp_server:[ \t]*(\S+)[ \t]*$", block.group(1), re.MULTILINE)
    assert found, "the registry declares no defaults.mcp_server — update this test"
    return found.group(1)


def test_the_artifact_agrees_with_the_registry_default():
    """The artifact is the EXECUTABLE projection of the registry's default MCP server.

    Compared by basename: the artifact stores a repo-relative source
    (`mcp-servers/fleet-context-mcp.py`) and the registry an expanded path
    (`~/steadfaste-fleet/mcp-servers/fleet-context-mcp.py`), so the FILE each one
    names is what must agree — not the spelling of the prefix, which differs by
    design (one is portable, one is host-expanded).
    """
    want = Path(_registry_default_mcp_server()).name
    sources = {Path(src).name for _, src in _surface_rows()}
    assert want in sources, (
        f"the registry's default MCP server ({want}) is not in the surface artifact "
        f"({sorted(sources)}) — the registry and the artifact have drifted apart"
    )
    print(f"  the artifact declares the registry's default server ({want}) ✓")


def test_every_declared_server_is_audited_by_the_doctor():
    """THE assertion that would have caught the fleet-context gap.

    A server the artifact declares but the doctor's expected map omits is a server
    no host is ever told is unwired. Coverage must therefore run artifact -> doctor.

    Not the reverse: the doctor legitimately audits more than the artifact carries —
    `loop-governance` and `review` are two role-distinct registrations of the one
    loop-gov binary, registered per-role rather than as part of this surface.
    """
    audited = set(_doctor_map().values())
    missing = [f"{name} ({Path(src).name})" for name, src in _surface_rows()
               if Path(src).name not in audited]
    assert not missing, (
        f"declared in {SURFACE.name} but not audited by the doctor: {missing} — add "
        "them to EXPECTED_MCP_SERVERS in fleet_doctor/config.py AND to DEFAULT_EXPECTED "
        "in mcp_wiring.py (a test pins those two to match by key AND value), or the "
        "fleet is never told when the server is missing."
    )
    print(f"  every one of the {len(_surface_rows())} declared servers is audited ✓")


def test_every_surface_row_names_a_source_the_repo_carries():
    """A row pointing at a file the repo does not hold is a route to nothing.

    `mcp_surface_resolve` falls back to the repo source when the deployed copy is
    absent, so a missing source turns "host not deployed yet" into a silent fault in
    whichever adapter asked.
    """
    dangling = [src for _, src in _surface_rows() if not (REPO / src).is_file()]
    assert not dangling, f"surface rows naming files the repo does not carry: {dangling}"
    print("  every surface row resolves to a real repo file ✓")


def test_every_announced_harness_carries_a_registration_path():
    """The registry is the ONE source of harness knowledge, so a harness announced as
    shipped with no `install` is a capability that silently does not ship — the fleet
    has nothing to run. `layer: none` is the declared-unsupported case, and declaring
    it is only honest if it states WHY, so that is required instead.
    """
    harnesses = (yaml.safe_load(REGISTRY.read_text()) or {}).get("harnesses") or []
    assert harnesses, "the registry declares no harnesses — update this test"
    gaps = []
    for h in harnesses:
        name, layer, status = h.get("name"), h.get("layer"), h.get("status")
        if layer == "none":
            if not str(h.get("why") or "").strip():
                gaps.append(f"{name}: layer 'none' with no stated reason")
        elif status == "shipped" and not str(h.get("install") or "").strip():
            gaps.append(f"{name}: announced as shipped with no install path")
    assert not gaps, gaps
    print(f"  all {len(harnesses)} declared harnesses carry a path or a reason ✓")


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}\n        {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
