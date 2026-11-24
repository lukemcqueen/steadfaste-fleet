# Rule 33 — All-Repos Discipline Gates & the Session Split

## A discipline gate must fire where the agent it governs actually works

A gate meant to change an agent's behaviour (survey-before-build, plan review,
any ritual discipline) is placed by WHERE that agent commits, not by where an
existing gate happens to live. A reflexion gate wired only into the fleet repo
governs NONE of a dev agent's work in project repos — the exact failure that made
Titus-style agents build without surveying. The fix reused the EXISTING generic
skill-checker (`reflexion-check.py --skill <name>`) in a loop over the ritual
skills (`task-orient execute-right verify-and-ship`)
rather than writing a new verifier: loading = reading the SKILL.md, recorded as a
tool event, so the gate refuses a commit until each ritual skill's content was
loaded that session.

## The session-presence split: govern every repo WITHOUT blocking humans

Host-wide gates must split on whether a governed session exists — the same signal
the reflexion gate already carries:

- a governed agent's commit holds a begin_change lock and has recorded tool events
  → an ACTIVE session exists → the gate applies and refuses on missing evidence;
- a human `git commit` by hand holds no lock → no session resolvable → skip (exit 0),
  via a `--skip-no-session` verifier flag.

Missing that split is both failure modes at once: fleet-only placement misses the
agent the gate was meant to change, and an unscoped host-wide version blocks manual
commits. When a negative-case test cannot manufacture a governed session, stub the
checker (python stub asserting the `--skip-no-session` flag is forwarded) so the
skip path and the refuse path are pinned separately.

## A repeated violation = a gate problem, not a documentation problem

If an agent keeps building without surveying or testing, the rule was prose (skill
body, AGENTS.md bullet) and the agent never loaded it. Enforce the discipline at
commit time via the generic skill-checker loop and the write gate (lock-only is
not enough); put aircraft rules where the agent always reads them (AGENTS.md) AND
make them bind (ritual-gate loop).

## The write/check SESSION KEY must be identical — resolve `effective_session_id`, never the env default

A skill-load event recorded without an explicit `session_key` lands under the
env-derived identity (`resolve_identity` → `FLEET_SESSION_KEY`, else
`harness:repo:branch`), BUT the gate reads `session_loaded_skill` keyed to the
ACTIVE GOVERNANCE LOCK's session id — the value `session_identity` reports as
`effective_session_id`. Those two keys disagree whenever the env var (or the
git-derived fallback) does not equal the lock's id, and the consequence is a
refusal loop with the look of a bug: the agent loaded `task-orient` /
`execute-right` / `verify-and-ship`, `session_loaded_skill` under the env key
returns `loaded:true`, and the commit is STILL refused — because the gate asked
under the lock's key, not the env key the agent recorded under. Re-loading the
skills repeats the refusal; the load was fine, the RECORDING went under the
wrong key.

Rules (bind for every harness that writes the tool-event store):
1. **Resolve the id the gate will check ONCE, then reuse it.** Call
   `mcp__review__session_identity(repo_path="<abs-repo>")` and take its
   `effective_session_id` — it is the covering lock's id when one exists, else
   the per-repo id `begin_change` will stamp, so it is correct whether the skill
   loads before or after the lock. Read the returned value; never guess it in
   the mirror of the env var.
2. **Record every always-skill load under THAT explicit key** — pass
   `session_key="<effective_session_id>"` to `session_tool_event`, never rely on
   the env default. Use the exact string `session_identity` returned (it is one
   value used for BOTH the write and the read).
3. **Verify with `session_loaded_skill` using that same explicit key.** A
   `loaded:true` under a DIFFERENT key is a false positive — the gate still
   refuses, and you loop. Confirm the key matches, then commit.
4. **When the gate still refuses a skill you demonstrably loaded, diagnose the
   KEY mismatch first** — compare `session_identity().effective_session_id`
   against the key the events went under — before re-loading or forcing. The
   fix is RE-RECORDING the events under the correct key (the load itself was
   correct), not loading again.
5. **Ship the resolved `effective_session_id` in your final notes** so a
   handoff to a verifier/reviewer can pass it along instead of re-deriving it.

Hallmark: gate refuses with `❌ Ritual gated: '<skill>' not loaded this session`
while a load plus a same-looking `session_loaded_skill` both appear to have
succeeded — that is the two-keys-disagree signature, not an unloadable skill.

## Block messages must name the LIVE tool prefix

A gate's block message that NAMES a tool must name the prefix the registered MCP
server actually serves. When an MCP server is RENAMED (e.g. `loop_governance` →
`review`), every block message, skill, and doc still quoting the old
(`mcp__loop_governance__*` vs the live `mcp__review__*`) makes a healthy system
read as "governance broken" to every agent that copies the documented name and
finds no tool. The enforcer may accept both prefixes in its session-injection
while the tool surface answers only one — sweep block messages + skills + docs
TOGETHER with the rename, and keep each documented tool name pointing at the
registration that actually serves it.
Related: pi-coding-agent "commit-time ritual gate" section.
