# Agent Fleet Update — the Two Paths That Must Both Work

**Audience:** any fleet agent (moses, esther, joseph, gisu, kustos, titus) and the operator.
**Scope:** updating a host's `steadfaste-fleet` checkout and deployment on operator request.

There are exactly two sanctioned ways an agent updates its fleet. Both must work on every
host, on direct operator request **and** on orchestrator dispatch. The governance lock is a
**normal, satisfiable step** in path A — never a wall that forces punting to an orchestrator.

---

## Path A — Direct operator request (agent-governed)

The operator asks *you* directly (e.g. "pull latest fleet and update"). You are expected to
perform the update yourself, through the governance ceremony. Do **not** defer to an
orchestrator.

1. **Load the always-section skills** (gate 1 of the enforcer) — one batched turn of
   `skill_view` calls. Loads persist for the session; reload only when the enforcer says the
   proof is stale.
2. **Open a governance lock:**
   `mcp__loop_governance__begin_change(task_id="fleet-update", description="operator-requested update")`
3. **Pull + update:**
   `git pull --rebase origin main && bash ops/scripts/fleet-update.sh`
   (`--rebase`, never merge commits — the fleet integration rule.)
4. **Verify:** fleet-update runs the doctor; confirm **0 FAIL** before closing.
5. **Close:**
   `mcp__review__feedback_accept(task_id="fleet-update", note="verified: 0 FAIL")`
   then `mcp__loop_governance__end_change(task_id="fleet-update")`

If the enforcer blocks a step, **read the block message — it IS the procedure** (it spells
out the exact calls). The `GOVERNANCE LOCK REQUIRED` message includes this sequence verbatim.

### When the direct path genuinely cannot proceed

Only two situations justify escalating to an orchestrator instead of doing the update:

- **`begin_change` is not in your tool list at all.** That means the `review` MCP server is
  not registered on this host (host setup problem, not governance). Check lock-free:
  `hermes mcp list`. Fix yourself when possible: `python3 <deploy>/scripts/fleet-doctor.py --fix`
  converges the `loop-governance` + `review` registrations; otherwise report to the
  orchestrator to register them.
- **No session id reaches the enforcer** — the block message says so explicitly ("Report
  this exact message to your orchestrator").

Anything else — skills stale, no lock yet, dirty working tree (`git status` shows changes:
stash or commit them first) — is normal friction you resolve, not an obstacle to report.

---

## Path B — Orchestrator dispatch (UPDATE_REQUEST)

An orchestrator (moses/esther) sends an `UPDATE_REQUEST` over the agent bus. The host's
message handler (`ops/scripts/agent/commands.py` → `handle_update`) runs the update as a
**plain subprocess** — `git pull` + `fleet-update.sh` + doctor — which is **not** the gated
agent-tool path, so **no governance lock is needed** for it. It reports back via
`UPDATE_RESULT`. You never refuse this path on governance grounds.

---

## What fleet-update.sh does for MCP (lock capability)

Since 2026-11-16, `fleet-update.sh` idempotently registers **both** role-distinct
loop-gov servers on **every** host, from the immutable deployed copy
(`$FLEET_DEPLOY_HOME/tools/loop-governance/loop-gov-mcp.py`):

- `loop-governance` (`--role loop-governance`) — lock lifecycle: `begin_change`,
  `end_change`, `check_lock`
- `review` (`--role review`) — scoring/re-review only, no lock authority

If a host shows neither (or only one) in `hermes mcp list`, run fleet-update once (path A
steps 2–4) or `fleet-doctor.py --fix`. The doctor enforces both present on every host.

---

## Rules that never change

- NEVER hand-write a lock file under `~/.steadfaste-fleet/state/` — a fabricated lock is a
  governance violation and is audited.
- NEVER weaken the gate to make the path easier — the ceremony is the point.
- macOS hosts (titus): the same flow applies; no GNU-only commands are required by
  fleet-update's MCP block (`hermes mcp` + `grep -q` only).
