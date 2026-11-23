# Plan — Decouple mycortex from the Hermes plugin, wire every host on update

Cycle 14947 · task `mycortex-decouple` · operator directives: *"decouple safely to
the fleet (from hermes agent)"*, *"finish the migration"*, *"remove the stale items
and properly connect (as any other coding agent should)"*, *"same issue for moses.
PLEASE FIX and MAKE sure on update other agents will fix properly"*.

This is a PLAN material (not a request). It carries the measured state, the code
map, hard constraints and rejected options. Paths use `${HOME}`, never a literal
home path.

## 1. Measured state (this session, this host)

| Fact | Evidence |
|---|---|
| The ONE implementation exists | `ops/services/mycortex-mem/context_tools.py` — `mem_profile`/`mem_search`/`mem_context`/`mem_conclude` + `session_*`, plus `TOOLS`, `HANDLERS`, `dispatch()`, `tool_schema_for_mcp()` |
| The decoupled transports exist | `mcp-servers/fleet-context-mcp.py` (thin wrapper, loads the contract via `importlib` from `_TOOLS_REL = "ops/services/mycortex-mem/context_tools.py"`), `ops/scripts/fleet-context.py` (CLI) |
| The plugin ALREADY delegates | `plugins/mycortex-mem/__init__.py:542-549` `get_tools()` derives schemas from `_load_context_tools()`; `:551-567` `handle_tool_call` routes shared tools through `tools.dispatch()` |
| The residual coupling | `:42` `from agent.memory_provider import ... MemoryProvider`; `:43` `from tools.registry import tool_error`; `:667` `from agent.auxiliary_client import call_llm` (mem_reasoning only) |
| Stale duplicated code | `_tool_profile` (:575), `_tool_search` (:594), `_tool_context` (:620), `_tool_conclude` (:680) are **never called** — `handle_tool_call` dispatches every shared tool to `context_tools.dispatch`. Only `_tool_reasoning` (:624) is reachable (`:555-556`) |
| **Hermes is NOT connected to the shared surface** | `hermes mcp list` → `agent-bus, tasks, executor, review, loop-governance`. **No `fleet-context`** |
| Pi IS connected | `ops/scripts/install/install-pi-mcp.sh:71` `CONTEXT_MCP=.../fleet-context-mcp.py`; `:352` `for name in ("review","tasks","executor","agent-bus","fleet-context")` |
| No Hermes installer exists | survey of `ops/scripts/install/` for `hermes mcp`/`fleet-context` → only `install-pi-mcp.sh` |
| The wiring auditor exists | `ops/scripts/manage/mcp_wiring.py` — audits a registration's command/args still exist on disk (born from a false-pass on Kustos) |

**Root cause of "moses not connected to mycortex":** the memory surface reaches a
host only where something registered `fleet-context-mcp`. Pi gets it from an
installer; Hermes gets memory from the in-process plugin instead. A host where the
plugin is absent and no MCP registration exists has **no** memory path, and nothing
reports it.

## 2. Target design

Follow the shape already proven for POLICY (one artifact → projected per harness
through the harness's own API → the deploy RUNS it → one doctor row):

1. **One artifact declares the required MCP surface.** Add the server set to the
   harness registry `ops/install/harnesses/registry.yaml` (already the declared
   home for per-host facts) as a `mcp_servers` list: `review, tasks, executor,
   agent-bus, fleet-context` with each one's repo-relative script.
2. **Each harness gets an adapter that projects it through its OWN API.**
   - Pi → `install-pi-mcp.sh` (exists; make it READ the artifact, not a hardcoded tuple)
   - Claude Code → `install-claude-governance.sh` (same)
   - **Hermes → new `ops/scripts/install/install-hermes-mcp.sh`** driving
     `hermes mcp add <name> --command <python> --args <script>` — the harness's own
     API, never a raw edit of `config.yaml` (which is security-sensitive).
3. **The deploy RUNS the projection** (cross-agent-design rule 9) — `fleet-update.sh`
   gains a step gated on each harness's PRESENCE (its agent dir or launcher), reporting
   per harness: wired / absent / failed. A host without the harness is not a fault.
4. **One doctor row** so "harness present but unwired" is a reported state:
   extend `mcp_wiring.py` to check the artifact's list, and surface it in
   `check_services`.
5. **Remove the stale items** — the four unreachable `_tool_*` handlers in
   `plugins/mycortex-mem/__init__.py`. The plugin keeps exactly what only a harness
   can own: the `on_session_end` capture hook (a lifecycle trigger) and `mem_reasoning`
   (the host-local extra that needs the host's own LLM), both already stated in the
   file's own comments as deliberate (`:538-541`, `:552-554`).

## 3. Code map

| File | Change |
|---|---|
| `ops/install/harnesses/registry.yaml` | + `mcp_servers` artifact (one list, names + repo-relative scripts) |
| `ops/scripts/install/install-hermes-mcp.sh` | NEW — projects the artifact via `hermes mcp add` |
| `ops/scripts/install/install-pi-mcp.sh` | read the artifact instead of the hardcoded tuple (`:352`) |
| `ops/scripts/install/install-claude-governance.sh` | same |
| `ops/scripts/fleet-update.sh` | RUN the projection per present harness, report per half |
| `ops/scripts/manage/mcp_wiring.py` | expected set ← the artifact; report `fleet-context` missing on Hermes |
| `plugins/mycortex-mem/__init__.py` | delete unreachable `_tool_profile/_tool_search/_tool_context/_tool_conclude` |
| `tests/test_mcp_wiring_artifact.py` | NEW — pins: artifact is the single source; every declared harness is wired or declared a gap; a missing `fleet-context` is reported |

## 4. Hard constraints

- **One implementation per capability** — the four `_tool_*` handlers are deleted, never
  left as a second definition of `mem_profile`/`mem_search`/`mem_context`/`mem_conclude`.
  A test asserts no duplicate tool NAME survives across plugin + contract.
- **The contract may not import a harness.** `context_tools.py` stays harness-free;
  `mem_reasoning` stays host-local and is *declared* as a host-local extra (reason: it
  needs the host's own LLM).
- **Never write `~/.hermes/config.yaml` directly** — only `hermes mcp add`, the
  harness's own API. Same for Pi/Claude: their own config files, via their own shape.
- **Fail closed, never silent-empty.** A harness present + unwired is a WARN naming the
  exact remedy command; a harness absent is PASS/information.
- **Deploy must RUN the adapter** — a registration file written but not executed is the
  defect this whole cycle exists to fix (cross-agent-design rule 9).
- **No data risk:** the Postgres store (`mycortex_mem`) is never migrated or altered by
  this change. Only the *access layer* moves.
- **`mem_reasoning` must keep working** on Hermes after the change (it is the one tool
  that cannot live in the host-neutral surface).

## 5. Rejected options

- **Delete `plugins/mycortex-mem` entirely.** Rejected: its `on_session_end` hook is the
  only thing that captures a session the harness killed — a tool call can never run
  after the process dies. The design doc (`docs/design/fleet-memory-session-mcp.md`)
  assigns that trigger to the harness explicitly. Deleting the plugin loses auto-capture.
- **Move `mem_reasoning` into `context_tools.py`.** Rejected: it calls the host's own LLM
  (`agent.auxiliary_client.call_llm`); putting it in the shared contract re-imports a
  harness into the contract and breaks cross-agent-design rule 2.
- **Hand-edit `config.yaml` to add the server.** Rejected: security-sensitive file; and it
  bypasses the harness's own API, which is what the per-harness adapter pattern forbids.
- **A new daemon for memory.** Rejected: cross-agent-design rule 13 — a process that starts, does one thing
  and exits needs no port or health check. The CLI + MCP stdio surfaces already cover it.
- **Hardcode the Hermes server list in a new script.** Rejected: that is a second
  definition of the surface and it drifts from `install-pi-mcp.sh`'s tuple (it already has).

## 6. Acceptance

1. `hermes mcp list` shows `fleet-context` on this host after the change.
2. `python3 -m pytest tests/test_mcp_wiring_artifact.py` green, including a test that
   fails if any declared harness lacks a wiring path.
3. `fleet-update.sh` prints a per-harness projection line and the doctor shows
   `fleet-context` wired; on a host with a harness absent, it reports absent, not fault.
4. `grep -n "_tool_profile\|_tool_search\|_tool_context\|_tool_conclude"` in
   `plugins/mycortex-mem/__init__.py` returns nothing.
5. `mem_profile`/`mem_search` still answer on Hermes (live probe).

## Status — 2026-11-17

| # | State | Evidence |
|---|---|---|
| 1 | met | `hermes mcp list` shows `fleet-context` on this host |
| 2 | met | `tests/test_mcp_wiring_artifact.py` 4/4 — pins the artifact against the registry's `defaults.mcp_server` AND the doctor's map, and every announced harness against its install path |
| 3 | partial | the doctor now reports `MCP server (fleet-context) — configured in config.yaml`; the per-harness projection LINE that `fleet-update.sh` prints was not re-verified in this pass |
| 4 | met | `grep -n "_tool_profile\|_tool_search\|_tool_context\|_tool_conclude"` in `plugins/mycortex-mem/__init__.py` returns nothing |
| 5 | met | `mem_profile` answered on this host after the deletion (shared dispatch path) |

Acceptance #2 existed because the surface had no audit; it was UNMET and the gap
was recorded as a governance issue when the plugin edit was committed. The
doctor's map did not carry `fleet-context` at all, so the audit did not cover the
very surface this plan is about — nothing ever reported it unwired. Both maps
carry it as of 2026-11-17, pinned by the new test, which was proven to FAIL on the
pre-fix map before being accepted.