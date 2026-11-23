---
name: cross-agent-design
description: >-
  Before designing any cross-agent feature, protocol, or workflow: trace the
  receiving agent's end-to-end connectivity, auth, consumption, and permission
  path. This skill prevents the most expensive mistake in multi-agent systems
  — assuming that because Message A reaches Queue B, Agent C sees it and
  processes it.
version: 1.0.0
category: devops
author: Steadfaste Fleet
created_from_session: fleet-bus-architecture-survey
related_skills:
  - survey-before-action
  - fleet-preflight
  - fleet-bus
  - agent-contract
  - session-start-discipline
---

# Cross-Agent Design — Fleet-Aware Protocol Design

## When to Load

Load this skill BEFORE:
- Proposing any feature that involves another agent
- Creating a new message type, topic, or protocol
- Designing a workflow that delegates to another agent
- Sending commands or requests to another agent's inbox
- Writing any code that assumes another agent will see the message

## The Core Principle

**Messages don't "arrive" — they wait.**

When you send a message to `inbox_esther`, it sits in a Postgres queue on your
machine. Whether Esther sees it depends on a chain of infrastructure that is
NOT under your control:

```
Your send → Bus auth → ACL check → Queue write
                                         │
              ┌──────────────────────────┘
              ▼
    Does Esther connect to this Postgres?
         │                    │
        YES                   NO
         │                    │
    Does she have a        Message is orphaned.
    consumer running?      Only the forwarder
         │                 would move it, and
        YES                it's paused.
         │
    Does the ACL allow
    her to read this
    queue?
```

## Pre-Flight Checklist

### 0. Prove it on your own system first

Before ANY cross-agent design, protocol, or command: prove the identical flow works on your own machine first. A test that skips the local step is not a test.

**The trap:** "I sent the EXEC to Esther, the message landed in her queue with msg_id returned, so it's working." This proves SQL works, not orchestration. You must verify the full send→process→respond→read cycle on yourself before involving another machine.

**Local proof checklist:**
1. Send the message to your own inbox (`inbox_moses`) with a unique `correlation_id`
2. Run the consumer manually to process it
3. Verify the response landed back in your inbox
4. Read the response — extract structured fields (exit_code, stdout, success)
5. Archive the test message
6. Only then send to a fleet agent

### 1. Trace the receiving agent's connectivity

```bash
# How does the remote agent connect?
#   - Same Postgres (orchestrators only)? → localhost:15432, Bearer token
#   - External URL (all workers)?         → FLEET_BUS_URL, Basic Auth
#
# Check the receiving agent's .env
#   FLEET_BUS_URL=https://example.com:13004  (or :14004 for Esther)
#   FLEET_BASIC_AUTH=agentname:password
#   AGENT_NAME=esther
```

**Never assume remote agents have localhost Postgres access.** Only Moses and
Esther (the orchestrators) do. Worker agents connect through nginx with
Basic Auth — a completely different auth path.

### 2. Trace the auth flow

Every agent has exactly ONE of these auth modes:

| Mode | Token type | When used | Who has it |
|------|-----------|-----------|------------|
| Basic Auth | `user:password` (htpasswd) | Through nginx | ALL agents |
| Bearer | `hbus_...` | Direct localhost | Orchestrators only |

**Before sending a command to another agent, verify their auth mode.**
A Bearer-token-based MCP tool won't work on an agent that only has Basic Auth
unless the MCP server is configured to fall back to Basic.

### 3. Verify the consumption pipeline

A message in an agent's inbox is only processed if the agent has a consumer:

| Consumer type | Reads every | Handles | Configured on |
|--------------|-------------|---------|---------------|
| LLM-driven cron | 10-60 min | All message types | Moses (fleet-bus-workday/evening/overnight) |
| agent-worker service | ~30 sec | Only `workflow_step` type | Some agents (may cause competing consumer problem) |
| Manual MCP tools | On-demand | All message types | All agents (but only when they read their inbox) |

**If the target agent has no bus-processor cron, your message sits unprocessed
until it times out and goes to DLQ.** The `fleet-bus-*` cron jobs are only
configured on Moses by default.

### 4. Verify the ACL allows it

From `bus.permissions` (Postgres table):

```sql
-- Only Moses can send to arbitrary queues.
-- All other agents can only READ their own inbox and SEND to `inbox_orchestrator` (the shared orchestrator inbox — default target) or reply to a specific orchestrator via `inbox_moses`.
SELECT agent_name, can_read, can_write, is_admin FROM bus.permissions;
```

| Agent | Can send to |
|-------|-------------|
| **Moses** | **Every queue** — all inboxes, workflow queues, health |
| **Everyone else** | `inbox_orchestrator` (default), `inbox_moses` (point-to-point replies), own inbox, `workflow_step_result`, `inbox_health_check` |

**If you need an agent to reply**, they send to `inbox_orchestrator` (or the originating orchestrator's queue, e.g. `inbox_moses`).
They cannot send to any other agent's inbox.

### 5. Trace the reply path

When the receiving agent replies, verify the path:

```bash
# The agent sends to inbox_orchestrator (or inbox_moses for point-to-point replies)
# What URL do they use?
#   - FLEET_BUS_URL (through nginx, port 13004)?
#   - Direct localhost (port 8903)?
# What auth?
#   - Basic Auth for nginx?
#   - Bearer token for localhost?
```

### 6. Account for the bus forwarder (if applicable)

The forwarder (`orch-bus-forwarder.py`, cron `orch-bus-forwarder-sync` every
2m) mirrors messages between Moses' and Esther's Postgres instances —
role-aware PEER resolution since 2026-08-03 (Esther's peer = Moses :13004,
Moses's peer = Esther :14004; `PEER_AUTH` = nginx Basic creds).

- Without the forwarder, all messages live only in the local Postgres
- Agents connecting to Esther's bus see only Esther-local messages
- Cross-server message sync requires the forwarder to be running

## Designing for N HETEROGENEOUS CODING AGENTS (the second axis)

The rest of this skill is about agents on the **bus**. This section is about
agents consuming a **capability** — Pi, Hermes, Claude Code, Codex, whatever
appears next. Standing constraint (Luke 2026-10-02): *"fix things for
interoperability with different coding agents. always be thinking this."*

**The rule: ONE implementation, per-host ADAPTERS.** A capability is implemented
once in a host-neutral contract; each host gets a thin adapter that converts its
calling convention and adds **no semantics**.

The failure this prevents is real and was shipped: `mem_context` existed in BOTH
the Hermes plugin and the MCP server, so whichever the runtime resolved, the
other **silently did not exist**. An agent concluded "memory is broken", fell
back to raw SQL, and reported the backend as the working path.

Rules, each of which is a bug that actually happened:

1. **One implementation per capability.** Two implementations of one tool NAME
   is worse than none — the runtime picks one silently and the other becomes a
   phantom. Enforce with a test, and MOVE that test WITH the deletion: a handler
   map still binding the removed private methods (`provider._tool_profile`, …)
   fails on `AttributeError` the moment the duplicate goes, so the assertion must
   become routing AT THE LAYER THAT OWNS each tool — the shared contract for every
   shared name, the host-local method for the one extra. Assert the ABSENCE too:
   a test that fails when a shared tool is redefined as a private method is what
   stops the duplicate from creeping back.
2. **The contract may not import a harness.** If it needs one, either extract it
   or declare it a **host-local extra** that names the reason (e.g. a tool that
   needs the host's own LLM). A test OF the host-bound module inherits that
   coupling: it imports the harness (`agent.*`), so the fleet venv alone cannot
   even COLLECT it — `ModuleNotFoundError: No module named 'agent'` means the
   interpreter is wrong, not that the test is broken. Run it with the harness
   runtime on the path (`PYTHONPATH=~/.hermes/hermes-agent <fleet venv>/bin/python3
   -m pytest …`) and state which interpreter you exercised.
3. **Schemas are DERIVED, never hand-written** — a hand-written schema list *is*
   a second definition and drifts immediately.
4. **Never substitute a fallback implementation on failure.** Fail-open is for
   *availability* ("carry on without memory"), never for *semantics*. A fallback
   is how the duplicate starts.
5. **Fail loudly, never silently-empty.** An empty result that *looks* like "no
   data" when the backend is down is worse than an error.
6. **Verify the host's OWN API before writing its adapter** — never infer it from
   a skill or a doc. Pi's first adapter failed TWICE, silently: the handler's
   first parameter is the event (not a ctx), and prompt injection is the RETURN
   VALUE (there is no ctx method). Both were masked by optional chaining.

   Same rule when an adapter's session-uniqueness depends on env propagation:
   a variable's presence on the host is NOT proof it reaches the consumer that
   matters — inspect the propagation seam for the EXACT consumer, never a source
   map or a documented merge. Two variants dead-end a resolver's env read: an
   MCP `env` block that REPLACES the child environment (strips the harness's own
   session id), and the subtler one — the merge is fine but the harness never
   exports the id to its OWN process.env (Pi injects `PI_SESSION_ID` only into
   bash-tool commands, so the governor's MCP child inherits nothing); verify on
   the host which one you are facing. A static MCP `env` block cannot hold a
   per-session value, so set the identity in the harness's lifecycle hook from
   its own session manager (`ctx.sessionManager.getSessionId()`), which runs
   BEFORE the consumer spawns and writes the env var the resolver reads
   (measured: resolver answers "per-repo, shared" with no key and "env,
   unique" once the key is set).

   **The env-var NAME differs per harness — survey the exact name, never guess.**
   Verified (2026-11-11) from each harness's own source: Claude Code injects
   `CLAUDE_CODE_SESSION_ID` (note `CODE` in the middle — `CLAUDE_SESSION_ID` is
   NOT set) into stdio MCP children since v2.1.154; Codex sets `CODEX_SESSION_ID`
   but does NOT forward it to MCP children by default (shell commands only), so it
   needs a `[mcp_servers..env]` pin or an upstream change. The resolver honours all
   of them (`_ENV_SESSION_ID_SOURCES` in loop-gov-mcp.py), so a harness that reaches
   the MCP child with its own id gets a unique session with no adapter — and a
   a harness that does not silently shares the per-repo id (fail-closed since
   2026-11-10). Per-harness session-id table (which env var, and whether it
   reaches the MCP child): `references/harness-session-identity.md`.
7. **Never let the model own a lifecycle guarantee.** Triggers (`turn_end`, stop
   hooks, crons) belong to the host — "remember to save" is not a mechanism.
8. **A registry, not per-host prose.** At N hosts the failure mode is N docs that
   disagree; declare hosts in one file and GENERATE their docs from it.
9. **Registering is not executing.** A deploy that COPIES a per-host adapter into the
   scripts directory has wired NOTHING — wiring is a step that RUNS. Gate it on the
   harness's PRESENCE (its agent dir or launcher), run every half of the adapter, and
   report the outcome per half: a host without the harness is not a fault, while a host
   that HAS it and comes out of a deploy unwired is. Pair the run with one line in that
   host's doctor, so "harness present but unwired" is a reported state rather than a
   silence nobody reads. An installer reaching a host is not evidence that it ever ran.

**A per-capability wiring check must complete EVERY present harness's registry
entry in the same change.** `fleet harness verify <name>` judges each declared
capability (`context`/`skills`/`governance`) by whether the registry names a
real `register` adapter — and a present, shipped harness with an undeclared one
FAILs. Adding or tightening that check but completing only SOME entries (e.g.
hermes/codex) leaves a genuinely-wired harness (claude-code) reporting FAIL
purely from a registry omission that reads like broken wiring. When the doctor
starts surfacing a harness verify FAIL after a check ships, distinguish the two:
`fleet harness verify <name>` printing `no adapter declared in the registry`
while the harness's own surface probe (`install-<h>-mcp.sh --verify`,
`<h> mcp list`) shows servers registered means the REGISTRY is under-declared,
not the wiring — add the missing `skills`/`governance` capability entries
pointing at the real adapter scripts, regenerate the derived docs, re-verify.

**Declaring a harness the operator ASKED FOR — `planned` is the honest status.**
When the operator names a set of coding agents the fleet must support, the
deliverable is one registry entry per agent, not prose in a reply: an agent
mentioned only in chat is invisible to `fleet harness list`, to the doctor's
per-harness rows, and to every later session. Procedure, in order:

1. **Map the operator's vocabulary onto the DECLARED names first** — an agent may
   already be declared under its own name (`claude` is `claude-code`), and adding
   a second entry for a host that exists is the duplicate-surface defect again.
2. **One entry per missing agent carrying `layer` + `surface` + `why` + `install` +
   `verify`.** The generator REJECTS an entry missing `why`, `surface`, `install` or
   `verify` ("an unverifiable instruction is how a wiring ships broken"), so a
   declaration with no install/verify step cannot be committed at all — which is
   the point: a requirement you cannot state a verification for is not yet known.
3. **Use `status: planned` when the requirement is recorded but no wiring has been
   exercised.** `shipped` and `documented` are claims about a host; never set them
   from vendor docs alone. `planned` is the status that reads "declared, install
   steps named, verification owed".
4. **State what is UNVERIFIED rather than inventing a knob** — the per-harness
   session-identity env var and an undocumented config key are exactly the debts a
   planned entry should name. Check the vendor's OWN docs for the configured
   surface; where only the TYPE is documented (a toolset kind, not its ref
   spelling), say so and name the command that will settle it on a real host.
5. **`presence:` must be the harness's OWN artifact** — its config file or its
   launcher. A sibling that merely ships alongside it (declaring the `docker` CLI
   as presence for a `cagent`-based agent) makes the doctor judge a host that does
   not have the harness, and it reports a wiring fault that is really a wrong
   declaration. A harness the doctor can never find reports SKIP forever.
6. **Add the name to `provider-policy.py`'s `HARNESSES` in the SAME change** as a
   DECLARED GAP (`mechanism: None`, `surface: None`, `why: ...`) —
   `tests/test_provider_policy.py::test_registry_harnesses_are_covered_or_declared`
   parses the registry and fails on any harness with no policy entry. That test is
   the drift guard for exactly this: a new harness must never silently inherit no
   policy.
7. **Regenerate and check with the FLEET venv interpreter:**
   `<fleet venv>/bin/python3 ops/install/harnesses/generate-harnesses.py && … --check && … --report`.
   A bare `python3` exits 1 on the missing PyYAML — that is the interpreter, not a
   broken registry, so do not go debugging the YAML. The `--report` line
   (`shipped/documented/blocked/planned` counts) is the coverage statement to quote.
8. **Never hand-edit a derived README** — it is generated from the registry, its
   banner says so, and the CLI-brand suite re-flags the pair. Change the registry
   entry and regenerate. A `planned` harness whose entry is absent from the daemon
   view (`fleet harness list`) was not declared at all.

**Standing rule — a diagnosed gap is not a deliverable.** Finding that a fleet
improvement is not wired everywhere is NOT the work: describing the gap and
offering to fix it later ("say the word") hands the work back to the operator and
leaves the fleet unwired. *ALWAYS provide a way for agents to implement fleet
improvements* — ship the runnable mechanism in the SAME pass: the deploy RUNS the
projection where the harness is present, the host doctor reports drift/gaps with a
runnable remedy, and a test pins it. Catching yourself closing a reply with a
proposed fix is the signal to open the cycle instead.

### The GATE half of a capability

A capability a host must satisfy is not only code the host RUNS — it is often also a gate
the host must PASS (a pre-commit check, an evidence record, a policy its commits are judged
by). Design that gate for the host's REAL mechanism, or the host is refused forever while
nothing anywhere reports a fault:

- **A gate keyed on another host's tool NAME is unsatisfiable here.** Establish what the
  host actually does by driving the host's own package — its exported tool factories and
  the instruction its own system prompt emits. A name that is ordinary vocabulary in one
  harness may appear NOWHERE in another's bundle; grep the bundle and treat zero matches as
  the measurement it is.
- **Record what the host actually did, under its true name**, and give the checker one
  predicate per real mechanism. Never normalise a host's action into another host's verb:
  the store is evidence, so a row asserting an action that never happened is worse than a
  missing row.
- **Ask the shared implementation for the identity the gate queries** rather than deriving a
  second key from an env var or a state file — and resolve it for the timing case where the
  host acts BEFORE the gate's context exists.
- **A host refused for a reason it cannot observe looks broken to its operator.** When a
  host reports "I cannot do X", check FIRST whether the gate demands a mechanism that host
  does not have: the agent's behaviour is often correct and the gate is the defect.

Procedure, predicates and the probe recipe:
`enforcement-gate-migration > references/multi-harness-evidence-writer.md`.

### The POLICY half of a capability — one artifact, projected per harness

A standing rule the operator states (exclude this upstream, cap this spend, prefer
this route) is a capability with N consumers like any other, and its FIRST fix is
usually wrong in a specific way: it gets written straight into one harness's config
file. That serves exactly ONE agent, teaches the fleet nothing, and the next agent to
hit the same failure rediscovers it from scratch — the same "each host grows its own
version" defect as a duplicated tool. Policy follows the same contract/adapter split
as code:

- **Policy is DATA in the ONE reviewable artifact**, beside the policy the fleet
already keeps there — one entry per subject, each carrying a REASON, an EVIDENCE
pointer and a SCOPE, enforced by that artifact's own `--selftest` so an unexplained
exclusion cannot be committed. Read the artifact's existing blocks FIRST and extend
the established shape (an `excluded` block that already demands reason+evidence sets
the pattern); never open a second policy store.
- **Each harness gets an adapter that projects the artifact through the harness's OWN
api** — never by editing its config file behind its back, so the harness keeps owning
its own lifecycle. Shell out to the host's own config command for the write half, and
use a documented machine-readable form for the read half (`<host> config get <key>
--json`) so the drift check needs no YAML dependency.
- **The harness CLI's own warning is not the reader.** A `config set` printing "not a
recognized config key — saved anyway" is its RECOGNITION TABLE, not the runtime. Grep
the harness source for the key→behaviour mapping and verify what actually consumes it
before believing either side; a CLI can disclaim a key its own client reads.
- **Declare the gaps, and make them LOUD where it matters.** A harness with no
verified knob gets an entry whose reason says the knob is UNVERIFIED — never an
invented flag. Report it as a coverage FINDING when that harness is actually present
on the host (it can still reach the excluded thing), and as information when absent.
An unreadable harness is `could not verify`, never "matches".
- **Test the COVERAGE, not just your entry:** parse the harness registry
(`ops/install/harnesses/registry.yaml`) and assert every declared harness is either
wired or explicitly declared as a gap — that test is what stops a new harness from
silently inheriting no policy. Pin the artifact's schema, the projected key's NAME,
and the fail-closed path in the same file.
- **A structure assertion beats a wording assertion.** A test grepping a reason for
the literal word "not" fails on a correct reason that says "no knob verified". Pin the
STRUCTURAL invariant (a declared gap must not name a surface it cannot write) and let
the reporting path carry the loud half.

### Choosing the implementation of a SHARED capability

A capability both systems consume is a design decision before it is a build. Decide in this order,
and let MEASUREMENT make the call:

10. **Budget the LANGUAGE by measuring, not by preference.** Check the toolchains that actually
    exist (`cargo --version`, `go version`, the interpreter) and what the systems you are serving
    are written in. A capability that adds a THIRD language to a two-language fleet costs a
    toolchain, a build path and a dependency surface on every host — and the "lowest memory" option
    is worthless if its toolchain is not installed. Prefer the language of the system the capability
    serves: a binary in that language is also the one its consumers can link in-process.
11. **Keep the runtime dependency set inside what a constrained host already has.** A build that
    needs a registry fetch, or a cache that shells out to a tool that may be absent, is unavailable
    exactly when that host cannot repair it. Prefer dependencies resolvable from the local cache,
    and where a component can be compiled IN rather than called OUT to (a bundled engine), compile
    it in: one build-time cost beats an unavailability that renders as "no data".
12. **Every external provider is OPTIONAL, and its absence is LOUD and NAMED.** An unconfigured
    provider exits with its OWN status (never a generic failure) and names the variable that unlocks
    it, so "could not look" cannot render as "found nothing". Say what still works without it — the
    offline half of a capability is usually the durable half, and it must not depend on the
    provider being reachable.
13. **One dispatch, two surfaces.** Expose a CLI and an MCP stdio server over the SAME functions,
    and generate both surfaces' metadata from ONE table, so no surface can describe an operation
    differently or drift from the other. A daemon is a third surface nobody asked for: a process
    that starts, does one thing and exits needs no port, no health check and no listener to secure.
14. **Measure the envelope on a real host before quoting one.** A sizing table describes someone
    else's machine; report the binary size and the STEADY-STATE footprint you measured, and when the
    decision turns on a number the operator named, go and test THAT number instead of arguing from
    the table.

**Standing rule — a change to the harness/API/adapter carries its docs in the
SAME change.** Anything that alters the surface a coding agent sees — a tool, a
tool NAME, an MCP server, a registration script, the harness registry, an env
var an adapter reads — changes what the docs describe, and a doc left describing
the old surface is a trap: the next reader trusts it and wires to something that
is no longer there. Update in the same pass, never "later":
`ops/install/harnesses/registry.yaml` (then run its generator so the derived
docs follow), the design doc that owns the area (`docs/design/agent-interop.md`
and siblings), and any SKILL.md that names the surface. `generate-harnesses.py
--check` gates the GENERATED docs only — prose you wrote by hand is gated only by
you. Search for the old name/value repo-wide first (see below): the fix is
almost never one file.

Checklist before shipping anything agent-facing:
- [ ] works from **at least two different harnesses** (prove it, don't claim it)
- [ ] any gate the host must PASS keys on something that host **has** — proven by driving
      the host's own package, never inferred from a doc or from another harness's vocabulary
- [ ] semantics in **one** place; adapters add none
- [ ] metadata **derived**, not duplicated
- [ ] unreachable dependency → **visible message**, never a silent empty
- [ ] Linux **and macOS**
- [ ] a **drift guard test**, not just a doc
- [ ] **docs carried in the SAME change** — registry + its generator, the owning design
      doc, and any skill naming the surface you touched (grep for the OLD name repo-wide
      first; the fix is rarely one file)
- [ ] the deploy **RUNS** the adapter where the harness is present, and the host doctor reports it
- [ ] a standing POLICY is data in **one** artifact, projected per harness through that
      harness's own api, with every unwired harness **declared** — never written into a
      single harness's config file

Reference: `docs/design/agent-interop.md`.

### Migrating a host-bound capability so ALL hosts reach it — and rule changes load without a host restart

A capability that lives only inside one harness's plugin (the enforcement gate, memory, context)
has **no access layer for other agents** — only that harness reaches it, and its code is frozen at
that host's startup, so a rule change forces a host restart (the "deploy ≠ load" trap). To expose
it to N heterogeneous coding agents AND make a rule change live on the next call instead of a
restart, follow the order that was proven on the governance write-gate:

1. **Extract the semantics into a host-neutral contract** under `ops/services/<name>/<module>.py`
   — stdlib-only, no harness import, loadable via `importlib.util.spec_from_file_location`.
   Register it in the deploy map (`fleet-update.sh`) beside the established
   `ops/services/mycortex-mem/context_tools.py` contract. This is the ONE implementation both the
   current host plugin and every adapter call.
2. **Build the PARITY gate first, before integrating.** The migration arbiter is a test that loads
   the ORIGINAL implementation from its real file (importlib) and asserts the contract returns the
   identical verdict on a corpus spanning the whole surface — the allowlists, the write-class
   rules, every sanctioned lane, and the evasions. "Works as well as X" is then a test, not a
   promise. When a corpus expectation diverges from parity, the ORIGINAL is the spec: correct the
   TEST, not the extraction — the divergence is normally a latent quirk of the original (below),
   not a loss.
3. **Reach the contract by subprocess on the decision path** — a fresh CLI invocation per call
   (a `loop-gov.py`-style CLI over the one dispatch). A new process always loads the current file,
   so a rule change takes effect on the next call with no host restart. This is the mechanism that
   actually removes "restart the gateway to load new enforcement rules"; it is the same shape
   pi's adapter already uses, and it is why no long-lived daemon is needed (rule 13).
4. **Make every host a thin adapter** over the contract; a host with no matching interception hook
   is declared in the harness registry (`ops/install/harnesses/registry.yaml`) WITH a reason, never
   left silently ungated.

Pitfalls that cost a cycle here:
- **An allowlist that anchors the head command couples the subcommand's position.** A rule
  `systemctl (status|show|...)` matches only when the subcommand comes immediately after
  `systemctl`; `systemctl --user show x` is therefore write-class in BOTH the original and the
  contract. Preserve the limitation in the migration — parity is the goal, not improved
  classification — and do not "fix" it as part of a faithful extraction.
- **Corpus fixtures must not carry a `/home/`-prefixed literal path, even a generic one.** The PII
  guard refuses the `write_file` on it. Use `~` verbatim or a `/var`/`/srv`/`/tmp` path when a
  fixture needs a filesystem shape.
- **An adapter that classifies locally still carries governance semantics.** "The adapter delegates
  the X decision" and "the adapter has no semantics of its own" are different claims: a local
  classifier (an allowlist, a decision rule) IS a governance semantic even when it forwards the
  lock check to the shared impl. State precisely which decision is delegated and which is still
  implemented locally, or a plan review (and the migration) will contradict itself.

## Common Pitfalls

### ❌ "I sent a message to inbox_esther, why didn't she respond?"

Check, in order:
1. Does Esther connect to the same Postgres instance? (She uses `FLEET_BUS_URL`)
2. Does she have a bus-processor cron? (`fleet-bus-*` jobs in her cron list)
3. Does her ACL allow reading `inbox_esther`? (Yes — every agent reads their own)
4. Was the message consumed by `agent-worker` without being archived? (Competing consumer)

### ❌ "The message was consumed but no response came back — and the queue is empty"

This is the trickiest failure: no evidence left behind. The handler consumed the message, archived it, and exited cleanly — but `send_bus_result` never delivered the reply.

**Diagnostic chain:**

1. **Check `archived_by`** — `bus.archive(queue, msg_id, agent_name)` stores the archiver. Query recent archives to confirm the expected agent consumed it, not a forwarder or admin cleanup:
   ```bash
   sg docker -c "docker exec mycortex-postgres psql -U mycortex -d mycortex -t" << 'EOF'
   SELECT queue_name, body->>'subject' as subject,
          archived_at::timestamptz(0), archived_by
   FROM bus.archives
   WHERE archived_at > now() - interval '30 minutes'
   ORDER BY archived_at DESC;
   EOF
   ```
   If `archived_by` matches the target agent → handler ran, reply path is broken.
   If `archived_by` is `moses` or `pre-cleanup` → something else ate it.

2. **Check handler state file** — The handler logs processed correlation_ids:
   ```bash
   cat ~/.steadfaste-fleet/state/agent-message-state.json
   ```
   If your correlation_id is in `processed_ids` → handler ran. The gap is in `send_bus_result`.

3. **Test `bus_send` independently** — If the handler ran but no reply came, test whether the agent's `bus_send` works:
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '$HOME/steadfaste-fleet/ops/scripts')
   from lib.fleet_bus import bus_send
   r = bus_send('inbox_orchestrator', {'from':'test','subject':'TEST','body':{},
                                'correlation_id':'diag'})
   print(f'Send: {r}')
   "
   ```
   Returns `{'msg_id': '...'}` → send works interactively. The cron environment differs.
   Returns `None` → bus_send is broken. Check config, auth, and URL.

4. **AGENT_NAME misconfiguration** — The handler polls `inbox_{AGENT_NAME}`. If AGENT_NAME falls back to hostname (older code) instead of being set in `.env`, the handler polled `inbox_cisnet03` while you sent to `inbox_gisu`. Check the agent's `.env` has `AGENT_NAME=<name>`.

5. **Known silent-failure gap** — `send_bus_result()` catches all exceptions internally and returns `False` without propagating. The crash guard's except block is never triggered. The handler logs "Failed to send" to stdout (which the cron runner captures) but returns `True` (all done). The message is archived with no result sent. Being actively fixed.

### ❌ "The message vanished — consumed but I never asked for it"

### ❌ "The MCP tool works on my machine but not on the remote agent"

Your machine (Moses) has a Bearer token for direct localhost access. Remote
agents only have Basic Auth through nginx. The MCP tool resolves auth from
`.env` — verify the remote agent's config matches their actual
auth mode.

### ❌ "I designed a protocol where Agent X sends to Agent Y directly"

Only Moses can send to arbitrary inboxes. All other agents can only send to
`inbox_moses`. If your protocol requires Esther → Gisu direct messaging,
it won't work at the ACL level. Route through Moses as an intermediary.

### ❌ "I created a new fleet-wide cron without checking what already runs"

Before creating any `agent-*` or `local-*` cron, run `cronjob(action='list')`
and `search_files()` with 3+ terms for the script purpose. The
`scoring-activity-watchdog` already existed when `local-cron-cost-report` and
`local-trace-quality-watchdog` were created — extending the watchdog was the
correct call. Creating new parallel crons fragments the system and creates
duplicate maintenance burden. See `session-start-discipline > references/survey-before-creating.md`.

### ❌ "bus_mode defines how agents communicate"

The field was renamed to `bus_access` (values `host`/`client`) because
`bus_mode` sounded like a configuration instruction to install a bus daemon.
`bus_access` describes capability: does this agent run the bus server (`host`)
or connect to a shared one (`client`)? All agents use `client` except
orchestrators (Moses, Esther) who use `host`.

## Reference

- `fleet-bus` skill — bus operations, DLQ management, recovery
- `ops/install/harnesses/registry.yaml` — the single source for coding-agent
  harnesses; `generate-harnesses.py --report` for coverage, `--check` for parity
- `fleet-preflight` skill — general pre-flight checks (manually authored)
- `docs/reference/fleet-bus-config.md` — detailed config and auth resolution
- `docs/orch-bus-setup.md` — full setup guide with architecture overview
- `agent-message-handler.py` — fleet-wide no_agent consumer (every 5 min)

## Deployment Pattern: Fleet-Wide Features

When a new cross-agent feature needs every agent to support it:

1. **Add handler to `agent-message-handler.py`** — add a new `subject` case
   (e.g. `subject == "EXEC"`) to the existing dispatch chain. This is the
   only consumer that exists on every agent in the fleet.
2. **Register** new or updated files in `fleet-update.sh`
3. **Deploy** via `fleet-update.sh` on each agent
4. **~5 min response time** (handler polls every 5 min)
5. **No LLM cost** (no_agent script — subprocess only)

### Example: EXEC Command (added 2026-07-21)

```
Moses: fleet exec esther fleet-doctor.py --json
  → sends EXEC to inbox_esther
  → handler picks up within 5 min
  → runs ~/.steadfaste-fleet/scripts/fleet-doctor.py --json
  → sends EXEC_RESULT back to inbox_moses
  → fleet exec polls and displays result
```

Message schema:
- `subject: EXEC` — body: `{ command: str, params: list, timeout: int }`
- `reply: EXEC_RESULT` — body: `{ success: bool, stdout, stderr, exit_code }`

### Repo-Wide Search for Config/Variable Changes

When fixing a port number, URL, env var name, or file path — **do not fix
just the file you're editing**. Search the entire repo first:

```bash
grep -rn "<old-value>" ~/steadfaste-fleet \
  --include="*.py" --include="*.md" --include="*.sh" \
  --include="*.yaml" --include="*.conf" \
  2>/dev/null | grep -v ".git/" | grep -v "__pycache__"
```

**Why:** The 8905→8903 bus port fix found 4 stale files across 3 directories
(workflow YAML, server.py default, MCP docstring, architecture doc) that a
single-file fix would have missed. Workflow YAMLs, code docstrings, and
historical plan documents are the most common hiding places.

### Test Like an Agent Would

After making changes, verify through the correct path:

| Path | What it proves | When to use |
|------|---------------|-------------|
| `fleet send` (docker exec SQL) | SQL function works | Local dev only — not representative |
| `curl 127.0.0.1:8903 + Bearer` | MCP tools path works | Testing Moses/Esther MCP |
| `curl domain:13004 + Basic` | **External agent path works** | Testing remote agent connectivity |
| `agent-message-handler.py --once` | Consumer picks it up | Testing handler dispatch |

The external path (nginx :13004 + Basic auth) is the only one that proves
a remote agent can send/receive. Always test through the path the
receiving agent would use.
