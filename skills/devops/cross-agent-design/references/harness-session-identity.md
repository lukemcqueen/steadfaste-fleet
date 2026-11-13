## Harness session-identity: which env var reaches the MCP child

Per-harness session-identity facts for governance lock uniqueness. The resolver
(`loop-gov-mcp.py` `_ENV_SESSION_ID_SOURCES`) reads the caller's environment in
this order: `FLEET_SESSION_KEY` → `PI_SESSION_ID` → `CLAUDE_CODE_SESSION_ID` →
`CODEX_SESSION_ID`, namespaced as `sess_<source>_<value>`. The premise: one MCP
child serves one session. When NO env value reaches the child, the resolver falls
back to a per-REPO id (`state/.session-<slug>.id`) — shared by every session in
the repo, which is the "a new session picks up an existing lock" bug (fail-closed
since the lock-ownership hardening: a session cannot CLAIM the shared id).

| Harness | Env var | Reaches MCP child? | Adapter needed |
|---|---|---|---|
| Hermes | (enforcer injects `session_id` per-call) | yes | none |
| Pi | `PI_SESSION_ID` | **NO** — injected only into bash-tool commands, never onto its own `process.env` | `fleet-context.ts` extension sets `FLEET_SESSION_KEY` from `ctx.sessionManager.getSessionId()` at `before_agent_start` |
| Claude Code | `CLAUDE_CODE_SESSION_ID` (note CODE; `CLAUDE_SESSION_ID` is NOT set) | yes, since v2.1.154 | none |
| Codex | `CODEX_SESSION_ID` | **NO by default** — shell commands only; rmcp-client forwards only a fixed set | operator must pin it via `[mcp_servers..env]` (or an upstream change) |
| Any CLI via gateway | `FLEET_SESSION_KEY={session_id}` | yes | gateway `command` backend pins it per chat |

### Verify on the host, never assume

A variable's presence on the host is NOT proof it reaches the MCP child.
Diagnose which of the two dead-ends you face: (a) the MCP `env` block REPLACES
the child environment (strips the harness's own id), or (b) the merge is fine
but the harness never exports the id to its own `process.env` (Pi's case).
A static MCP `env` block cannot hold a per-session value, so the fix for (b) is
the harness's lifecycle hook writing the env var the resolver reads, using the
harness's own session manager, running BEFORE the child spawns.

Check which source actually resolved (the host truth):
```bash
loop-gov.py session_identity '{}'   # effective_session_id + source
# "per-repo, shared" ⇒ the env never reached the child — fix propagation
# "env:FLEET_SESSION_KEY / PI_SESSION_ID / CLAUDE_CODE_SESSION_ID / CODEX_SESSION_ID" ⇒ unique
```

Verify the resolver's behaviour end-to-end by driving the module with the
candidate env var set and none of the others, asserting it resolves to the
namespaced id (`sess_<label>_<value>`) and that two different values give two
ids — never by reading a doc about the harness.

### Pi extension specifics (the seam that works)

`ExtensionContext.sessionManager` is `ReadonlySessionManager`, which has
`getSessionId()` (same source the bash tool uses). The handler signature is
`(event, ctx)` — ctx is the SECOND param. Set the env var inside
`before_agent_start`, before the loop-governance MCP child spawns (it connects
lazily on the first tool call, so it inherits the value via the env merge). Set
only-if-unset so an operator/gateway pin wins.
```typescript
pi.on("before_agent_start", async (event: any, ctx: any) => {
  const sessionId = ctx?.sessionManager?.getSessionId?.();
  if (sessionId && !process.env.FLEET_SESSION_KEY)
    process.env.FLEET_SESSION_KEY = String(sessionId);
  // ... existing restore/inject logic
});
```

### Renumbering-gate lesson

A variable's documented presence is a STARTING HYPOTHESIS, never verified fact.
The plan that shipped the Pi env fix relied on "Pi merges its env into the MCP
child" as an UNVERIFIED inference from a doc — wrong, and it cost a session.
Prove it against the harness's installed source or a live host before building.
