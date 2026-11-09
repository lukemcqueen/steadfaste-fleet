# CR2 plan — wire the Hermes enforcer to the shared write-classification contract

> **Status:** PLAN — awaiting adversarial pro review + owner go-ahead. No code changed.
> **Cycle:** #14394 · **task_id:** `write-class-cr2-plan`
> **Builds on:** `docs/plans/hermes-governance-adapter.md` (master plan, CLEAN) + CR1
> slice 1 (contract `ops/services/fleet-governance/write_class.py`, parity 209/209 GREEN,
> commit `a7283c10`).
> **Owner direction (2026-11-09):** *"proceed. do a pro review before implementing."*
> Approved shape: subprocess delegation on write-classification, sanctioned recovery
> lane preserved.

## 1. The slice, stated as one sentence

Make the Hermes enforcer's terminal write-classification a **subprocess call to a fast
classifier CLI over the shared contract**, so a rule change is picked up on the next tool
call with **no `hermes gateway restart`** — while keeping every block message and the
fail-closed behaviour identical to today.

## 2. Code map (what exists, what changes)

| File | Today | Change |
|---|---|---|
| `ops/services/fleet-governance/write_class.py` | the contract (CR1) | unchanged (adds nothing) |
| **NEW** `ops/scripts/loop-gov-classify.py` | — | fast CLI over the contract: `loop-gov-classify <command>` → one JSON verdict `{read_only, sanctioned, db_mutation}`; rc 0/3 contract (3 = could not verify, fail closed). Registers in `fleet-update.sh`. |
| `plugins/governance-enforcer/__init__.py` | in-process regexes + sanctioned-lane regexes (lines ~1854–2320) decide terminal write-class; called at hook lines 3307 (read-only fast path), 3324 (sanctioned lane), 3336+3421 (`_is_write_tool`) | the terminal branch asks the CLI once per tool call and consumes the verdict; the in-process regex tables are **removed** (the duplicate dies); the tool-level constant sets (`WRITE_TOOLS`, cron/skill/process/computer-use action sets) stay in-process because they are stable and non-renewable |
| `tests/test_write_class_parity.py` | parity vs enforcer regex functions | the enforcer's regex functions are gone — the parity test re-pins against the enforcer's **hook verdict** path instead (see §6) |
| **NEW** `tests/test_loop_gov_classify.py` | — | CLI contract tests: rc codes, JSON shape, fail-closed on missing contract |
| **NEW** `tests/test_enforcer_reload_hermetic.py` | — | the CR5 reload-proof, hermetic (§5) |

## 3. The classify verdict (one call answers every terminal question)

```
loop-gov-classify 'git status --short'
→ {"read_only": true, "sanctioned": null, "db_mutation": false}        rc 0
loop-gov-classify 'bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh'
→ {"read_only": false, "sanctioned": "fleet_update", "db_mutation": false}  rc 0
loop-gov-classify 'sqlite3 ~/.steadfaste-fleet/data/loop-governance.db "DELETE ..."'
→ {"read_only": false, "sanctioned": null, "db_mutation": true}       rc 0
```

- The CLI resolves `write_class.py` repo-layout → deployed copy (the `loop-gov.py`
  `_server_path()` pattern), imports stdlib-only, classifies, prints JSON. A fresh process
  per call **is** the reload mechanism: it always loads the current contract.
- **The enforcer computes the verdict ONCE per `pre_tool_call_hook` invocation** (one
  subprocess per terminal tool call) and threads it through the three sites (3307 / 3324 /
  lock-gate) as an explicit parameter. There is **no cache** — no TTL, no module-level
  mutable state, so cross-session leakage is impossible by construction and a rule change is
  picked up on the very next call (reload lag is zero, not ≤2 s).
- Fail closed: rc 3 / timeout / unparseable JSON / missing contract → the terminal call is
  treated as write-class (the current fail-closed default) and the operator remedy (the
  fleet venv + `fleet-update.sh`) is printed, mirroring `fleet-governance.ts`'s
  `unavailableMessage` — **except** the frozen recovery recognition in §4, which runs
  before the subprocess.

## 4. Sanctioned lanes survive unchanged

The `/tmp`-cleanup / plan-review / governance-DB gates live inside the same classify
verdict. Their semantics — exact-match, metachar-rejecting, narrow — are the contract's
(parity-proven), and their verdict is honoured before any gate.

**The fleet-update recovery lane gets a FROZEN in-process fallback, and that is
deliberate.** The classify verdict for the exact `fleet-update.sh [--status|--dry-run|...]`
invocation is `sanctioned: fleet_update`, and the hook honours it before any gate — BUT if
the contract/CLI is unavailable, fail-closed would block that very command and recreate the
DOGFOOD deadlock the lane exists to cure. So the enforcer keeps ONE tiny, stable,
non-renewable exact-match regex for the recovery invocation, checked BEFORE the classify
subprocess call. Justification, stated plainly: **the recovery lane must not depend on the
system it recovers.** This is not the renewable rule set (it never changes), so it does not
undermine the reload goal — it is the one predicate that must keep working when everything
else is broken.

## 5. The reload-proof acceptance test (CR5, hermetic)

1. Copy `write_class.py` to a scratch dir; run the CLI with `--contract <scratch-copy>`
   on a probe command → baseline verdict.
2. Append one rule to the scratch copy (e.g. add `fleetdoctor-check` to
   `READONLY_COMMAND_PATTERNS`).
3. Re-run the CLI on the same probe → verdict differs.
4. **Nothing was restarted anywhere.** A fresh process loaded the edited contract. This is
   the mechanism, proven; the enforcer consumes exactly this CLI.

Second acceptance case — **the recovery lane under a broken contract** (the ADV-14394-1
fix): with the contract copy deleted and the CLI's interpreter made unreachable, the
enforcer's hook (loaded via importlib) still lets the exact `fleet-update.sh --status`
invocation through (frozen recognition, before the subprocess), and still blocks a
non-recovery write with the fail-closed message. Both directions asserted.

The `--contract` path argument is the test/ops seam (an explicit path, not invented config —
it defaults to the same repo→deployed resolution in production).

## 6. Parity re-pin (what "as well as the hermes gateway" means after CR2)

Before CR2: contract == enforcer-side regexes (the 209-assertion corpus). After CR2 the
regexes are gone, so parity must move to the enforcer's **behaviour**: the parity test
drives the enforcer's hook decision path (loaded via importlib) for `terminal` and asserts
the block/allow decision equals the decision the corpus's known truth demands — for the same
full corpus. The subprocess CLI is the single implementation; the enforcer has no classifier
left to disagree with, so "parity" becomes "the hook enforces the contract's verdict
faithfully" (both directions: read-only passes the fast path; every write/evasion case is
blocked or routed to the lock gate exactly as before).

## 7. Hard constraints (violating any is a stop-and-report)

1. **Never weaken a gate.** Block messages keep their exact text; the block decision at each
   hook site is byte-identical for every corpus case.
2. **Fail closed on cannot-verify**, with the operator remedy named (the `fleet-governance.ts`
    pattern).
3. **The sanctioned recovery lane must survive CR2, even when the contract cannot.** Proof
   in two parts: (a) the frozen in-process recognition (§4) matches the exact
   `fleet-update.sh` invocation before the classify call, so it is honoured with NO
   contract and NO lock and NO skills; (b) acceptance test: with the contract copy deleted /
   the CLI unreachable, the recovery invocation is still allowed and a non-recovery write is
   still blocked (fail closed).
4. **No shared mutable state in the enforcer.** The verdict is computed once per hook call
   and passed explicitly — no cache, no module-level dict, so concurrent Hermes sessions
   cannot observe each other's verdicts by construction.
5. **Rollback is real:** the plugin change is one file deployed by `fleet-update.sh`; the
   previous plugin is redeployable until the reload test passes. The new CLI is inert if the
   enforcer reverts.
6. Enforcer change → adversarial **A4** gate, finding count diffed against the range base.
7. Deploy, then push — deploy-map must match the deployed tree.

## 8. Rejected options (recorded, not omitted)

- **`classify` as an MCP tool in `loop-gov-mcp.py`, enforcer shells to `loop-gov.py`.** The
  CLI loads the entire 9 K-line governance server per call — hundreds of ms on every terminal
  call. The fast CLI over the stdlib-only contract is the same one implementation behind a
  thin access layer; its startup is generally an order of magnitude smaller (stdlib import
  vs importing the full governance server) — **an estimate, not a claim: the §9 performance
  gate MEASURES the actual per-call latency before the enforcer is wired, and the wiring is
  not adopted on an unverified number.**
- **In-process import + `importlib.reload`.** Already rejected by the master plan §6 (mutates
  a running security module mid-call).
- **Data-driven rules + mtime fingerprint refresh** (the `skills.yaml` manifest pattern) —
  fast and reloadable, but requires refactoring the parity-proven regex tables into data and
  re-proving the whole corpus. A viable follow-up if the per-call subprocess cost ever
  measures badly; out of this slice.
- **Subprocess for `write_file`/`patch` too.** No classifier is needed for unconditional
  writes; the constant `WRITE_TOOLS` set is stable and needs no renewal. Not paying the cost.

## 9. Sequence and verification

Write the CLI (TDD: rc/JSON tests first) → **performance gate: measure the CLI's cold-start
to-verdict latency on this host (timed over ≥20 runs, p50/p95 recorded in
`docs/evidence/`, committed)** — if p50 exceeds 150 ms, STOP and report the measurement
before wiring the enforcer (subprocess-per-terminal-call is not adopted on an unverified
latency claim; the data-driven-rules alternative in §8 is the fallback) → register in deploy
map → re-pin the parity corpus against the hook behaviour → wire the enforcer (one file) →
A4 gate → hermetic reload + broken-contract-recovery tests GREEN → full suite + doctor →
deploy → push → report with the reload transcript.

Close-time code review is MCP-owned and outside this worker lane; this plan is the
before-build gate.
