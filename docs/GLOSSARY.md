# Steadfaste Fleet — Glossary & Naming Conventions

> **Why this exists.** Terminology grew organically across governance code, docs,
> and skills. Several words mean two or three different things, and a few names are
> inconsistent (kebab vs snake, legacy `cortex*` leftovers). This is the single
> authoritative place to look up what a term means and how a name should be written.
> When you add a term or fix a name, update here first. Related: the two landed
> rename sweeps (`docs/rename-cortex-to-fleet.md`, `docs/rename-followups-steadfaste-fleet.md`).

---

## Part 1 — Naming conventions

| Surface | Convention | Example |
|---|---|---|
| **Product / repo identifier** | kebab-case | `steadfaste-fleet` |
| **Brand in prose / headings** | Title Case, two words | `Steadfaste Fleet` |
| **Fleet component names** | `fleet-` prefix, kebab, Title Case in prose | `fleet-bus`, `fleet-gateway`, `fleet-doctor`, `fleet-update`, `fleet-dashboard` |
| **Scripts / tools** | kebab-case | `fleet-update.sh`, `fleet-doctor.py` |
| **Python packages / modules** | snake_case (Python import rule) | `fleet_bus/`, `fleet_doctor/`, `fleet_gateway/` |
| **Skills** | kebab-case, `name:` matches dir where possible | `loop-governance`, `fleet-bus` |
| **Docs** | kebab-case | `docs/review-receipt-gate.md` |
| **Cron job names** | prefix by scope (see below) | `agent-*`, `orch-*`, `local-*` |
| **Env vars** | `SCREAMING_SNAKE` | `AGENT_NAME`, `FLEET_*` |

> **Scope of this table — a convention, not a conformance certificate.** These are
> the forms to follow for any name you **add or edit**. They describe the target
> convention; they do **not** assert every existing name already conforms. Known
> divergent names outside the files this glossary swept (e.g. some `mlops/` skill
> dirs, and uncommitted sibling work in `skills/devops/*`, `skills/software-development/`,
> `docs/evidence/`) are listed in **Part 3** or are tracked there. Treat this as the
> rule for new/edited names, and see Part 3 before assuming the whole tree is clean.

**kebab vs snake — the one rule that resolves every mixed pair.** User-facing
names (scripts, docs, skills, the name you type) are **kebab-case**. A name is
**snake_case** *only* when it is a Python importable module/package (Python cannot
import a hyphen). So `fleet_doctor/` is snake because it is a Python package
imported as `fleet_doctor`, while `fleet-doctor.py` and every doc reference to
"fleet-doctor" are kebab. Both refer to the same tool — this is correct, not drift.

**`Steadfaste Fleet` vs bare `fleet`.** `Steadfaste Fleet` (or `steadfaste-fleet`)
is the **product/repo**. Bare `fleet` in prose means **the collection of agent
hosts** (Moses, Esther, Titus, …). When bare `fleet` could read either way, write
it out: "the fleet (the agent hosts)" vs "the Steadfaste Fleet repo". `fleet-*`
hyphenated is always a **component name**, never the collective.

**Legacy `cortex` / `hc` / `hermes-cortex`.** The product was renamed
`cortex`→`fleet` and `hermes-cortex`→`steadfaste-fleet`. Any live occurrence of
`cortex`/`hc`/`hermes-cortex` **outside** a documented keep is a residual bug (see
Part 3). `mycortex` is a **different, kept product** (the knowledge brain) — never
"sweep" it.

**`STALE` vs `stale`.** `STALE` in ALL-CAPS is a deliberate **KEEP marker**: a
pre-rename literal preserved on purpose for detection/migration — **never rename or
delete it**. Lowercase `stale` is the ordinary word (an expired lock, an old skill).
A cleanup sweep must not touch `STALE`-marked literals.

**Cron name prefixes.** `agent-*` = fleet-wide job. `orch-*` = orchestrator-only.
`local-*` = this host's own job, intentionally **not** in the repo manifest (doctor
silently excludes it). A cron outside these three prefixes is a naming bug.

---

## Part 2 — Overloaded terms (one word, several meanings)

These are the terms most likely to be misread. Each lists its distinct meanings and
the authoritative one for the context.

### cycle
- **Governance cycle** — `begin_change → work → … → end_change`, a row in
  `loop_cycles`; owns one review verdict and one lock. **This is the default meaning.**
- **Review round** — one submit→FINDINGS→CLEAN pass of the adversarial reviewer.
- **Cron tick** — one periodic run of a cron job.

Write "governance cycle" / "review round" / "cron run" when the context is not
obviously one of the three.

### window
The single most overloaded word. Distinguish explicitly:
- **Review window** — the commit range **and** the character budget bounding what
  the reviewer sees (`DIFF_CHAR_BUDGET`). "the reviewer's window was cut."
- **Context window** — an LLM's token/context limit (`num_ctx`).
- **Deploy window** — the interval between `fleet-update.sh` unlocking and
  relocking enforcement files (a race an auditor can land inside).
- **Measurement window** — a date range for a cost/usage baseline.

Always qualify: "review window", "context window", "deploy window", "measurement
window". Bare "window" is ambiguous.

### material
The **diff body + evidence** handed to the adversarial reviewer to judge. Not a path.
`--material` takes **content**; `--material-file` takes a path. "the material" = what
the reviewer actually saw.

### blob
One **file's worth of diff** sent to the reviewer in a per-blob pass. The reviewer
judges one blob at a time, so a range of N files costs N rounds. Opaque by
tradition — read it as "one file's diff".

### verdict
- **Review verdict** — `CLEAN` or `FINDINGS` from the adversarial reviewer (stored
  on the governance cycle). **Default meaning.**
- **Jev verdict** — a screening/decision dict from the `jev_*` tools (a different
  schema entirely).

Prefix when unclear: "review verdict (CLEAN/FINDINGS)" vs "Jev screening verdict".

### receipt
- **Review receipt** — a JSON file (`~/.steadfaste-fleet/state/.reviewed-<slug>-<sha>.json`)
  recording a CLEAN review, which authorises a push without a held lock.
  **Security-critical.** Default meaning in a push/gate context.
- **Message receipt** — a `message_id` ack from the gateway reply path.
- **Jev receipt** — a UI confirmation of applied config.

### lock
- **Governance lock** — file-based gate from `begin_change`; write tools refuse
  without it. **Default meaning.**
- **Postgres advisory lock** — a concurrency primitive in the bus/gateway DB layer.
- **lock-free carve-out** — a sanctioned operation allowed without a governance
  lock (scratch cleanup, `fleet-update.sh`).

### gate
A mandatory checkpoint. There are many, at three stages — **commit** (adversarial,
TDD, reflexion, syntax), **push** (doctor, review-receipt), **tool-call** (skills
gate, lock gate, plan-review gate). Each has its own trigger and remedy. If a gate
blocks you, name which one; "the gate" alone is ambiguous.

### worker / agent / orchestrator / subagent
- **agent** — any fleet entity (a host-running assistant). General term.
- **orchestrator** — a top-level agent (Moses/Esther) that dispatches and verifies.
  **Host-derived, never self-claimed.**
- **worker** — *(task model)* an agent that claims a slice, implements, reports,
  and verifies it. Also loosely: a long-running service process (e.g.
  `hermes-agent-worker`). Qualify: "task worker" vs "worker service".
- **subagent** — a `delegate_task` child. **Has its own governance scope/lock** —
  the parent's lock does NOT carry to it. This is a security-relevant distinction.

### slice
- **Task slice** — a unit of work in task model v3 (kind=slice, parent story),
  claimed via `task-db.py claim`.
- **Implementation slice** — informally, a small chunk of work ("ship in slices").

### doctor
The **fleet diagnostic tool** (`fleet-doctor.py`, package `fleet_doctor/`) — checks
deployed state vs repo. Also names a **pre-push gate** ("the doctor gate"). A
separate `codeharness doctor` exists in a different project. Default: the fleet
diagnostic.

### gateway
- **Hermes gateway** — the runtime process that hosts the governance-enforcer
  plugin. "restart the gateway for enforcer changes" = **this one**.
- **fleet-gateway** — the standalone messaging daemon (`fleet_gateway/daemon.py`)
  routing messages between chat platforms and agents.

Prefix both: "the Hermes gateway (runtime)" vs "fleet-gateway (messaging daemon)".

### harness
- **Agent harness** — the execution runtime for an agent (Hermes, Pi, Claude). Also
  "Harness v3 state machine" = the task state machine version.
- **Test/eval harness** — a testing/replay framework.

### bus / inbox / queue
- **Agent Bus** — the whole PGMQ-based inter-agent messaging system.
- **inbox** — the per-agent queue (`inbox_<agent>`) where one agent's messages land.
- **queue** — the underlying PGMQ/Postgres queue implementation.

"check the bus" = the system; "check your inbox" = your queue; "the queue" = the
mechanism. Same infrastructure, three scopes.

### brain / mycortex / corpus
- **mycortex** — the **knowledge/memory product** (its own brand, always kept).
- **brain** — the on-disk knowledge store (`~/brain`, `~/.steadfaste-fleet/brain`).
- **corpus** — the offline code corpus (`ops/offline/code-corpus/`), separate.

`mycortex` is never renamed; `brain` and `corpus` are storage, not the product.

---

## Part 3 — Naming bugs & open collisions (tracked)

These are real inconsistencies found in the 2026-11-23 terminology review. Items
marked **RENAME** change an identifier and are **Rule 9c-gated** — they need a scope
doc + operator review before landing, so they are listed here, not silently applied.

**Dashboard systemd unit name — three-way collision (RESOLVE BEFORE RENAMING).**
The authoritative decision (`docs/rename-cortex-to-fleet.md` **Q2**) is that the
dashboard **joins** the rename → `fleet-dashboard.service`, superseding the older
"keeps its name" note. But live code and docs disagree:
- `ops/scripts/fleet-update.sh:1183` checks `steadfaste-fleet-dashboard.service` and `hermes-fleet-dashboard.service`
- `docs/rename-followups-steadfaste-fleet.md:47` calls the Linux name `hermes-fleet-dashboard.service`
- `docs/runbooks/fleet-local-dir-rename.md:303` and `skills/autonomous-ai-agents/steadfaste-fleet-setup/SKILL.md:728-750` still claim it "deliberately keeps the pre-rename name" — **factually wrong vs Q2**

The installed unit name varies by host, so this cannot be fixed by a doc edit alone.
The false "keeps its name" claims are corrected (see below); the actual unit rename
is **RENAME — gated**.

**Residual `cortex*` / `hermes-cortex*` outside keeps (content bugs, fixed below or
tracked):** `AGENTS.md` "cortex-only gates"; `CONTRIBUTING.md` "Cortex-Update.sh"
heading; `steadfaste-fleet` skills referencing `cortex-dashboard`; `fleet-update.sh`
case pattern matching `cortex-dashboard`; `docs/design/bus-scale/*` `hermes cortex`
CLI examples; `enforcement-change-safety` skill "cortex-only"; code docstrings
"cortex log"/"cortex context store". These are wording, not renames — safe to fix.

**Agent identity env var.** Code reads **`AGENT_NAME`** (`loop-gov-mcp.py`,
`fleet-bus-mcp.py`). The legacy `hc` admin CLI resolves `HC_AGENT` first, then
`AGENT_NAME`. `HC_AGENT`→`FLEET_AGENT` is a *planned* rename (see
`docs/runbooks/fleet-local-dir-rename.md:316`) not yet applied — do not assume
`FLEET_AGENT` exists. Canonical today: `AGENT_NAME`.

**Rule-numbering collisions.** AGENTS.md has two numbering schemes (bold-block
`RULE 1/2/3/7b/7c/8` and the numbered `Agent Execution Contract` items 1–31) that
overlap at 1/2/3/8; `docs/fleet-reference.md` and `docs/git-enforcement.md` have
their own `RULE 1/2`; `R0.7` is a third notation. **Cross-references have been
disambiguated (2026-11-23):** every pointer to a rule now names its source
("AGENTS.md Rule 2", "SOUL.md Principle 13", "cross-agent-design rule 9"), and a
dangling "AGENTS.md Rule 5" was removed. **Doc-audit section identifiers are now
number-free (2026-11-23):** the `agents-doc-audit.py` matcher keys on the section
**name** (`Score Every Change`), not a brittle `Rule #N`, so it survives AGENTS.md
renumbering — match by name, never by number. What remains is the **numbering-scheme
collision itself** in AGENTS.md — renumbering to a single namespace is
**RENAME-adjacent (changes cross-reference identifiers) — gated**; tracked here.

**Cron prefix violations:** `fleet-bus-failover-watchdog` and `fleet-bus-workday`
in `ops/install/cron-manifest.yaml` use `fleet-` instead of the `agent-`/`orch-`/
`local-` convention. **RENAME (cron name = identifier) — gated.**

**Skill `name:` ≠ directory** (mostly `mlops/` short dirs like `slime`, `simpo`,
`qdrant` with longer `name:` fields). Low-risk; renaming skill dirs is
**RENAME — gated**. The convention is documented above (kebab, `name:` matches dir
where possible); the divergence is cosmetic.

---

*Last reviewed: 2026-11-23. Sources: brand, rule-structure, file-system, and
concept terminology audits of `~/steadfaste-fleet`.*
