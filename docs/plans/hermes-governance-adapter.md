# Plan — Hermes governance adapter: remove `hermes gateway restart` from the enforcement path

> **Status:** PLAN — awaiting adversarial review + owner go-ahead. No code changed.
> **Cycle:** #14389 · **task_id:** `hermes-governance-adapter`
> **Owner decision (2026-11-09):** Option **A** — thin in-process shim delegating the
> allow/deny decision to a fleet-owned implementation — **plus** the standing constraint
> that it must work for the *other coding agents*, not Hermes alone.
> **Supersedes nothing.** Extends `docs/design/component-hermes-separation.md` slice **S2c**.
> **Audience:** owner, orchestrator, builder.

## 1. The defect, stated measurably

The enforcement rule set is loaded **in-process** by the Hermes gateway.

| Artifact | Size | Where it runs |
|---|---|---|
| `plugins/governance-enforcer/__init__.py` | 3799 lines | in-process, inside `hermes-gateway.service` |
| `mcp-servers/loop-gov-mcp.py` (shared impl) | 9110 lines | MCP stdio subprocess / CLI |
| `ops/scripts/loop-gov.py` (shared CLI) | 375 lines | any subprocess |

Consequence, documented at `docs/troubleshooting.md` §25 ("Deploy ≠ load"): `fleet-update.sh`
copies new enforcer code to disk, the **running** gateway still holds the old module, and the
sanctioned `fleet-update.sh --status` keeps returning `GOVERNANCE LOCK REQUIRED`. The remedy is
`hermes gateway restart`, which agents cannot run (lifecycle guard) — so every enforcement-code
change requires the **host operator** (Luke).

Measured cost this session: the first `terminal` call was refused, and the working remedy in the
docs is an operator-only action. That is the coupling being removed.

## 2. What already exists (do NOT rebuild — extend)

The one-implementation/per-host-adapter pattern is **already built and proven for pi**:

- **Shared implementation** — `loop-gov-mcp.py`, with `loop-gov.py` as a generic CLI over it.
  Predicate `check_repo_lock` already returns rc 0 = live lock covers repo / rc 1 = refused /
  rc 3 = unavailable. `check_lock` and the `--list` tool surface exist.
- **Per-host adapter, pi** — `ops/install/harnesses/pi/extensions/fleet-governance.ts` (573 lines).
  It holds **no lock-coverage decision semantics**: it does not parse lock files, decide staleness,
  or match repos — it asks `loop-gov.py check_repo_lock` and blocks on refusal, and every
  cannot-verify path **fails CLOSED**. It has an executed-gate test:
  `pi/verify-governance-extension.mjs`.
  **But it DOES carry a second implementation of write-classification** — the `READ_ONLY_COMMANDS`
  allowlist, `isReadOnlySingle`, `toolNeedsLock` and the compound/metacharacter rules are a
  governance *decision*, duplicated from the enforcer rather than shared. So "adapter over the
  shared implementation" is currently true for the **lock** predicate only, and false for the
  **write-class** predicate. That duplication is not a footnote: it is in scope for CR1/CR4, and it
  is a live drift risk today (two tables that must agree, with nothing asserting that they do).
- **Registry (single source)** — `ops/install/harnesses/registry.yaml`, declaring per harness
  `capabilities.governance` (how the agent REACHES the tools) and `capabilities.governance-gate`
  (the INTERACTIVE write gate). `generate-harnesses.py --check` is the parity gate.

So Hermes is **the last host whose gate is not an adapter**. S2c is therefore not "build a
governance service" — it is "**make Hermes the next adapter over the implementation pi already
uses**", which is exactly `multi-consumer-integration` Rule 1 and `agent-interop.md`'s stated
failure mode: *"a capability that lives only in the HOST's plugin system has no access layer at
all."*

**Why this also fixes the reload problem structurally.** The pi adapter spawns a fresh
`loop-gov.py` subprocess **per gate decision**. A new process always loads the current file, so a
rule change takes effect on the **next tool call** — no restart of any unit. That property, not
the shim's existence, is what removes `hermes gateway restart`.

## 3. Target design

```
CONTRACT   mcp-servers/loop-gov-mcp.py  (+ loop-gov.py CLI, one dispatch → both surfaces)
           predicates: check_repo_lock · skills_gate · write_class · path_guard · db_mutation
           host-neutral: imports NO harness
              ▲                    ▲                    ▲
   ┌──────────┴───────┐  ┌─────────┴────────┐  ┌────────┴─────────┐
   │ ADAPTER: hermes  │  │ ADAPTER: pi      │  │ ADAPTER: claude/ │
   │ plugin shim      │  │ fleet-governance │  │ codex  (CR4)     │
   │ (thin, frozen)   │  │ .ts  (EXISTS)    │  │ hook | mcp       │
   └──────────────────┘  └──────────────────┘  └──────────────────┘
```

- The **shim** keeps only what is genuinely harness-local (Hermes session id, marker/credit
  plumbing, context injection) and must declare those as host-local extras *with a reason*, per
  `agent-interop.md`.
- The **decision** — write-class, lock coverage, skills gate, path guards — moves into the
  contract and is reached by subprocess.

## 4. Slices

| Slice | What | Acceptance (what the TEST asserts) |
|---|---|---|
| **CR1** | Extract the decision predicates into the contract: `write_class` (bash read-only allowlist, deny-by-default, segment-aware), `skills_gate`, `path_guard`, `db_mutation`. Expose via ONE dispatch table → CLI **and** MCP surfaces. | A hermetic test drives each predicate directly and asserts both directions (a `cp`/`rm`/`tee` command is write-class; `ls \| grep` is not). A drift test asserts the pi extension's local classifier and the contract's classifier agree on a shared corpus — today they are **two copies**. |
| **CR2** | Reduce the Hermes enforcer plugin to a thin adapter: resolve repo → call the contract → allow/block, preserving the existing block-message text and the fail-CLOSED-on-unavailable behaviour. | A test asserts the shim issues the CLI call and blocks on rc 1 **and** on rc 3; and that it holds no governance semantics of its own (no local lock parsing, no local classifier table). |
| **CR3** | **Parity proof (the gate that decides whether this is safe).** A corpus of real tool calls (read-only compounds, writes, metacharacter evasions, sanctioned lanes, orchestrator-only paths, DB-mutation shapes, scratch carve-out) run through BOTH the current enforcer and the shim: identical verdicts, both directions. | The test EXECUTES both sides and fails if any case diverges. Asserting on source text is not acceptable. Cite the range base sha so the baseline is reproducible. |
| **CR4** | Multi-harness completion: replace the pi extension's duplicated classifier with the contract's (or prove equivalence by drift guard); add `governance-gate` capability entries for `claude-code` and `codex` **after verifying each host's own interception API** (never inferred from a doc — `agent-interop.md` rule 7). | `generate-harnesses.py --check` exits 0; each new entry carries a `verify` step; a `-  name: <h>` entry exists for every host that cannot be gated, with the REASON (a declared gap, not a silence). |
| **CR5** | **Reload-proof acceptance test — the actual deliverable of the owner's request.** Change one contract rule, then assert the Hermes path reflects it with NO gateway restart. | Positive control: after the rule change the Hermes gate verdict changes (asserted by observation). Negative control: the previous verdict no longer appears. This test is what makes "`hermes gateway restart` is gone from the enforcement path" a verified claim rather than a design assertion. |

## 5. Hard constraints (violating any of these is a stop-and-report, not a workaround)

1. **Never decrease enforcement to make a gate pass.** No `SKIP_SCORE`, no `--no-verify`, no
   widening a predicate `.filter`, no `2>/dev/null || true` on a check.
2. **Fail CLOSED on cannot-verify**, with the operator remedy printed — matching the pi adapter.
   An unreachable contract must not read as "carry on".
3. **The sanctioned recovery lane must survive.** `fleet-update.sh` (and its `--status`) stays
   runnable with no lock, or a broken contract becomes an unrecoverable host.
4. **No agent-usable override.** The only way to remove the gate is an operator-visible action.
5. **Security-critical change** → adversarial A4 gate on the enforcer; diff finding count against
   the range base.
6. **Rollback must be real:** the plugin is deployed by `fleet-update.sh`; the pre-change plugin
   stays deployable until CR5 passes.
7. **Deploy, then push** — never the reverse; deploy-map files must match the deployed tree.

## 6. Rejected options (recorded, not omitted)

- **Move the enforcer to a long-lived daemon with a socket.** Rejected: adds a port, a health
  check and a listener to secure for no benefit — the subprocess-per-call shape already gives
  reload-on-next-call, and `cross-agent-design` rule 13 explicitly warns away from a daemon
  nobody asked for. Also adds a new SPOF in the enforcement path.
- **Convert the enforcer plugin into an MCP server and drop the plugin.** Rejected: MCP servers
  *provide* tools; they cannot intercept the harness's own native write tools. This would silently
  remove write-time gating for Hermes — the exact hole `governance-gate` exists to close.
- **Rely on git hooks alone (Option B).** Rejected by the owner: post-hoc, and it is deliberately
  scoped to the fleet repo only, so a session in a project repo would have no gate at all — the
  gap already documented in `registry.yaml`'s `governance-gate` rationale.
- **Hot-reload the plugin in-process (`importlib.reload`).** Rejected: it mutates a running
  security module mid-call, and any module-level state carried over silently. The subprocess shape
  gets the same freshness with none of that.

## 7. Risks and unknowns

- **Highest risk is CR2→CR3.** The enforcer gates every write this agent makes, including the
  writes that fix it. If the shim is wrong, the host can be locked out of its own repair. Hence:
  parity proof before the plugin swap, the sanctioned lane preserved, and a real rollback.
- **The classifier is duplicated today** (enforcer + pi extension). Unifying it is a
  behaviour-change surface: the parity corpus in CR3 must include the metacharacter-evasion cases
  that motivated the pi extension's own deny-by-default rewrite.
- **Unknown until probed:** whether `claude-code` and `codex` expose a pre-tool interception hook
  equivalent to pi's `tool_call`. CR4 measures each host's own API; if a host has none, it is
  declared `layer: none` **with the reason**, per Rule 2.
- **Not verified in this plan:** the exact set of Hermes plugin hooks that must remain host-local.
  CR1's extraction boundary is decided by measurement (what the contract can express without a
  harness import), and the boundary is recorded in the plan's next revision.

## 8. Sequence

CR1 → CR3 (parity harness may be built alongside CR1) → CR2 → **CR3 gate** → CR5 → CR4 →
deploy → push. Reviewer gate: this plan goes to the adversarial reviewer BEFORE CR1 begins;
the close-time review happens at the end, MCP-owned and outside the worker lane.
