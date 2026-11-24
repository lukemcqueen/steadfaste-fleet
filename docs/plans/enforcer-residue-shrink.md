# Plan — Enforcer residue shrink: retire the restart-bound rule set in the Hermes governance enforcer

> **Status:** PLAN — awaiting the governed pro review + owner go-ahead. No code changed.
> **Cycle:** #15144 · **task_id:** `enforcer-residue-scope-plan`
> **Builds on:** `docs/plans/hermes-governance-adapter.md` (master plan, CLEAN) · CR1
> (contract `ops/services/fleet-governance/write_class.py`, commit `a7283c10`) · CR2
> (`ops/scripts/loop-gov-classify.py` + the enforcer wiring, cycle #14394).
> **Owner direction (2026-11-24):** *"scope properly with pro to story/slice for you to
> integrate"* — i.e. scope the residue shrink (the option the owner was offered as option 1),
> pro-review the scope, and decompose it into story/slices. This is NOT an override of the
> master plan's rejected long-lived-daemon option.
> **Audience:** owner, orchestrator, builder.

## 0. Why this plan exists rather than a bare task list

The master plan's Option A is already half-built: the terminal write-class decision no longer
lives in the plugin. What is left is the **rest** of the rule set, which is still module-level
code, and therefore still restart-bound. This plan states which remainder is worth moving,
which is frozen by construction, and what has to be proven first — because the master plan's
own parity slice (CR3) turns out to be **not yet satisfied**, for a reason found at §1.4.

## 1. The defect, restated measurably

### 1.1 What "residue" is

The plugin is loaded **once, in-process, at gateway start**. The doctor's `Stale enforcer
plugin` row compares the DEPLOYED file's content-change epoch against the running gateway's
start time, so **any** byte change to the plugin requires `hermes gateway restart` — which
agents cannot run (lifecycle guard) and therefore costs the host operator. Measured this
session, after the operator's restart at 08:22:53Z:

```
✅ Stale enforcer plugin — running gateway loaded the current enforcer revision (1 process(es) checked)
```

Provenance for that row: measured and scored in cycle **#15138**, transcript committed in the
same range at `docs/evidence/gateway-reload-and-rename-residue-quarantine-2026-11-24.txt`
(`make-evidence.sh`, `--verify OK`) — the row is quoted from that run, not from memory.

### 1.2 What is already NOT residue

Terminal write-class, delegated per call and live on the next tool call — `plugins/governance-
enforcer/__init__.py` (deployed; 3,656 lines):

```python
1906 def _classify_terminal(command: str) -> Optional[Dict[str, Any]]:
1912     log). A fresh subprocess per call IS the reload mechanism: a contract rule
1913     change is picked up on the next call with no gateway restart. The hook
1917     cli = _classify_cli_path()
1923         proc = subprocess.run([sys.executable, str(cli), command], ...)

1946 def _is_readonly_terminal_command(command: str) -> bool:
1947     """Fail-closed read-only terminal check — DELEGATED to the contract. ...
1952     v = _classify_terminal(command)
1953     return bool(v and v.get("read_only"))
```

The access layer exists and is deployed (`ops/scripts/loop-gov-classify.py`, registered at
`ops/scripts/fleet-update.sh:992`); it prints one JSON verdict `{read_only, sanctioned,
db_mutation}` and returns rc 3 = could not verify (fail CLOSED).

### 1.3 The residue, measured (region churn, not a guess)

Subject: `plugins/governance-enforcer/__init__.py`, 3,656 lines, **102** commits touching it.
**Anchor for every line number in this plan:** the REPO file and the DEPLOYED file are
byte-identical at this revision — `sha256 dccecc7398471e8e5aef009a1b5ea04a8f2a890994f0da4050f02edf72271904`,
3,656 lines, both — so the regions below are unambiguous against the tip. They are **that
revision's** line numbers: a later edit shifts them, so a builder must re-run the `-L` probe
(and not trust these numbers as anchors) before using them.
Region churn = `git log --oneline -L <start>,<end>:<file>` — commits that actually MODIFIED the
line region, following the code through history. Raw output is committed in
`docs/evidence/enforcer-residue-churn-2026-11-24.txt`.

| Region (deployed lines) | What it decides | Region commits |
|---|---|---|
| 2266-2440 | PII detection: email/repo-URL/phone/UUID/server-URL/home-abs regexes, sensitive digests + lengths + allow-list | **17** |
| 1808-1860 | `WRITE_TOOLS`, `READ_TOOLS`, `CONDITIONAL_WRITE_TOOLS`, `WRITE_PROCESS_ACTIONS`, `WRITE_COMPUTER_USE_ACTIONS`, `WRITE_CRON_ACTIONS`, `WRITE_SKILL_ACTIONS` | **13** |
| 1981-1990 | the sanctioned `fleet-update.sh` recovery regex | 8 |
| 87-95 | `_REQUIRED_SKILLS_DEFAULT` + the `skills.yaml` manifest path/fingerprint | 7 |
| 383-417 | `_EXT_DOMAIN_SKILLS`, `_FILENAME_DOMAIN_SKILLS`, `_PATH_CTX_HINTS`, `_YAML_DOCKER_MARKERS` (skill routing) | 5 |
| 489-512 | `_ADVERSARIAL_COMMIT_PATTERNS`, `_ADVERSARIAL_CRITICAL_PATHS`, `_ADVERSARIAL_DISPLAY` | 3 |
| 1882-1886 | `_COMMAND_FORBIDDEN_METACHARS`, `_COMMAND_SEPARATORS` (block-message rendering only) | 2 |
| 2619-2640 | `_COMMIT_BYPASS_TOKENS`, `_COMMIT_SCOPE_BARE_MSG`, `_COMMIT_SCOPE_BYPASS_MSG` (block-message text) | 2 |
| 445-450 | `_TDD_CODE_EXTS`, `_TDD_TEST_PATH_MARKERS` | 1 |

**Probe correction, disclosed rather than buried.** The first measure tried was
`git log -S<NAME>`, which reported 1-3 for everything. That probe is wrong: `-S` counts commits
in which the NUMBER OF OCCURRENCES of the literal changed, so editing a table's CONTENTS does
not register at all. Both runs are in the evidence file, the wrong one labelled. Anyone
re-running must use `-L`.

### 1.4 FINDING F0 — the master plan's CR3 parity proof is not satisfied, and the test that
looks like it is now passes vacuously

`tests/test_write_class_parity.py:194` asserts:

```python
assert _CONTRACT.is_readonly_command(cmd) == _ENFORCER._is_readonly_terminal_command(cmd)
```

After CR2, `_is_readonly_terminal_command` (enforcer 1946-1953) DELEGATES to `_classify_terminal`
→ `loop-gov-classify.py` → the **same contract**. The comparison is therefore contract-vs-contract
through a subprocess: it still proves the WIRING works (the enforcer reaches the contract, rc
handling is right) and it is worth keeping for that, but it **cannot detect drift** — both sides
move together by construction. So "parity is proven" is currently true only about the wiring,
and CR3 ("a corpus run through BOTH the current enforcer and the shim: identical verdicts, both
directions"; "asserting on source text is not acceptable") is genuinely outstanding.

This matters to THIS plan because every slice below moves more decisions into the contract and
changes the plugin; without a drift guard that can FAIL, each move is unverifiable.

**Run, at this revision** (not asserted): `pytest tests/test_write_class_parity.py -q` →
`209 passed in 9.09s`, `pytest_rc=0`; full transcript committed at
`docs/evidence/write-class-parity-tautology-2026-11-24.txt`, which also inlines the test body,
the delegation it exercises and the pinned line numbers. So the test is **green and
tautological at the same time** — which is exactly why F0 is a finding and not a nit: a green
drift guard that cannot fail is read as coverage the change does not have.

## 2. What already exists (do NOT rebuild)

- **The contract** — `ops/services/fleet-governance/write_class.py`. Pure-predicate, stdlib
  only, no harness import (`agent-interop.md` rule 1).
- **The access layer** — `ops/scripts/loop-gov-classify.py`, `--contract <path>` is the test/ops
  seam (an explicit path, not invented config), default resolution = repo layout then deployed.
- **CR2's cost design** — the verdict is computed **once per hook invocation** and threaded as an
  explicit parameter; no cache, no module-level mutable state (CR2 §3/§4), so reload lag is zero.
  This plan extends the SAME call with more inputs rather than adding calls (see §3).
- **The reloadable-manifest pattern already in the plugin** — `skills.yaml` with an mtime
  fingerprint (`_REQUIRED_MANIFEST_PATH` / `_REQUIRED_MANIFEST_FP`, lines 93-94). This is the
  precedent for data that must reload WITHOUT paying a subprocess.
- **The repo-file-at-call-time pattern** — `_RECEIPT_CHECK_REL` (line 1465), the review-receipt
  checker, is resolved and run per call.

### 2.1 Pinned excerpts (every §2 claim, read from the file at this revision)

```
87  _REQUIRED_SKILLS_DEFAULT: set = {
88      "task-orient", "execute-right", "verify-and-ship",
89  }
90  _REQUIRED_SKILLS: set = set(_REQUIRED_SKILLS_DEFAULT)
91  # Deployed manifest the set derives from; mtime fingerprinted so a refresh is
92  # cheap and a deploy/new manifest takes effect without a gateway restart.
93  _REQUIRED_MANIFEST_PATH = Path.home() / ".steadfaste-fleet" / "skills.yaml"
94  _REQUIRED_MANIFEST_FP: int = -1
...
48  GOVERNANCE_STATE_DIR = Path.home() / ".steadfaste-fleet" / "state"
49  SURVEY_MARKER = GOVERNANCE_STATE_DIR / ".cron-survey-done"
...
65  SKILLS_MARKER = GOVERNANCE_STATE_DIR / ".skills-loaded"          # legacy — inert
66  SKILLS_STATE_FILE = GOVERNANCE_STATE_DIR / "skills-state.json"   # legacy — inert
67  SKILLS_MARKER_DIR = GOVERNANCE_STATE_DIR / "skills-loaded"       # per-session markers
68  SKILLS_STATE_DIR = GOVERNANCE_STATE_DIR / "skills-state"         # per-session state
...
1026 # first call and 0 after. Same content, same files, same visibility to the MCP
1027 # daemon — pure write-elision, no logic change and no security change.
1028 _LAST_MARKER_SESSION: str = ""
...
1465 _RECEIPT_CHECK_REL = "ops/scripts/lib/review-receipt-check.py"
1466 _RECEIPT_CHECK_MOD = None
```

**Disclosed, not omitted:** line 1028 is module-level MUTABLE state — a write-elision cache for
the skills marker. It already exists, it is inside the frozen plumbing (§3), and constraint 4
("no shared mutable state") is therefore scoped to the DECISION path this plan extends: RS0-RS3
add no new module-level mutable state, and the pre-existing counter is named here so a later
reviewer does not have to discover it.

## 3. The decision rule — what moves, what stays frozen

**The target shape: ONE classify call per `pre_tool_call` invocation.** Today the hook spawns
the CLI only on the terminal branch. The cheapest coherent design routes *every* tool through
the same single call, carrying `{tool, action, command, paths}` — so the non-terminal decisions
cost no additional spawn, and CR2's "compute once, thread explicitly" property is preserved.

**MOVE-eligible** iff both hold:
1. it is a **renewal surface** — the fleet or the operator plausibly edits it to change
   behaviour (measured churn in §1.3 is the discriminator), and
2. it is a **pure function of the call's own inputs** — deciding it needs nothing the contract
   cannot see (no harness session id, no marker state, no filesystem write).

**FROZEN by construction** (must not depend on the system it recovers — CR2 §4's reasoning
generalised):
- the `fleet-update.sh` recovery lane (1981-2001) and the fail-closed default itself;
- the skills-gate marker/credit plumbing and its state paths (48-68, 153, 1028);
- host-local extras (Hermes session id, context injection).
  The frozen set is a CLAIM, and RS4 tests it rather than asserting it (note: that region
  churned 8 times, so "never changes" is not self-evident).

**Not worth moving, stated honestly:** `_TDD_CODE_EXTS` / `_TDD_TEST_PATH_MARKERS` (churn 1) and
the block-message metachars (churn 2). Moving them buys near-zero renewal benefit while touching
the gate's message path. They stay, recorded as deliberate.

## 4. Slices

| Slice | What | Acceptance (what the TEST asserts) |
|---|---|---|
| **RS0 (GATE — nothing below starts before this is green)** | Close F0. Re-pin parity to the **hook decision path** and build the hermetic reload-proof test. | (a) A drift test drives the hook's allow/deny for the full corpus and compares against the corpus's known truth — NOT against the contract's own function — so it fails when the contract is mutated at a scratch path and the hook does not follow. (b) The hermetic reload test: copy `write_class.py` to a scratch dir, run the CLI with `--contract` on a probe → baseline; mutate the copy → verdict differs; **nothing restarted anywhere**. (c) Both directions asserted; range base sha cited. |
| **RS1** | Hoist the PII predicate set (17 commits) into the contract: a `pii_violation(path, content)` predicate; the enforcer consumes the verdict instead of holding the regexes. | The corpus (existing PII fixtures) yields IDENTICAL allow/deny through the hook path, both directions; a scratch-copy mutation proves reload with no restart; deliberately-malformed input still fails CLOSED with the operator remedy named. |
| **RS2** | Hoist the tool/action sets (13 commits) and move to ONE classify call per hook invocation carrying `{tool, action, paths}`. | Parity both directions for every tool in the old sets; **measured** p50/p95 latency for a non-terminal write tool call before and after, committed. If p95 regresses past the agreed budget, STOP and report the numbers — the slice is not adopted on an unmeasured performance claim (CR2 §9's precedent). |
| **RS3** | Hoist the remaining pure-data surfaces: adversarial lists + display (3), skill-routing maps (5), block-message + commit-scope message text (2). | Same shape as RS1: verdict/message byte-identical through the hook, both directions; reload proven by scratch-copy mutation. Block-message TEXT asserted byte-identical. |
| **RS4** | Freeze-proof for the recovery lane: the frozen set is TESTED, not asserted. | A committed test asserts the sanctioned regex accepts exactly the documented invocations and rejects its neighbours (chained, piped, `sudo`, `-c`, other scripts), and that it is honoured with the contract DELETED and the CLI unreachable — while a non-recovery write is still blocked. |
| **RS5** | The shrink GAUGE — so the residue cannot silently regrow. | A committed check (doctor row or test) counts renewable decision tables still declared module-level in the plugin and FAILS above the agreed floor, and is PROVEN discriminating by pointing it at a case it must reject. A check that cannot fail is not a check. |

Slices are sequential: RS0 → RS1 → RS2 → RS3 → RS4 → RS5. RS4 may be built alongside RS0.

## 5. Hard constraints (violating any is a stop-and-report, not a workaround)

1. **Never weaken a gate.** No `SKIP_SCORE`, no `--no-verify`, no widened predicate, no
   `2>/dev/null || true` on a check. Block-message text stays byte-identical.
2. **Fail CLOSED on cannot-verify**, with the operator remedy printed.
3. **The sanctioned recovery lane survives even when the contract cannot.** The recovery lane
   must not depend on the system it recovers.
4. **No cache, no shared mutable state** in the enforcer — verdict computed once per hook
   invocation, threaded explicitly (CR2 §4).
5. **Enforcer changes take the adversarial A4 gate**, finding count diffed against the range base.
6. **Rollback is real:** the plugin is one deployed file; the pre-change plugin stays deployable
   until RS0's reload test is green.
7. **Deploy, then push** — never the reverse.

## 6. Rejected options (recorded, not omitted)

- **A long-lived HTTP governance daemon (systemd unit + transport).** Rejected again, on
  measurement rather than taste: the subprocess shape ALREADY gives reload-on-next-call, proven
  in production by CR2 + this session's doctor row, so a daemon buys no freshness while adding a
  port, a listener to secure and a new SPOF in the enforcement path (`cross-agent-design` rule
  13). The owner's 2026-11-24 direction was to scope the residue shrink, not to override this.
- **Move the whole residue at once.** Rejected: the churn-1/2 tables carry the gate's message
  path and buy nothing; a blanket move is risk without benefit.
- **`importlib.reload` / in-process hot reload.** Rejected by the master plan §6 (mutates a
  running security module mid-call).
- **Put the recovery lane in the contract.** Rejected: it is the predicate that must work when
  the contract is broken (constraint 3).
- **Data-driven rules + mtime fingerprint for EVERYTHING.** Partially adopted (it is the right
  shape for the frozen default): kept as the fallback for any surface whose measured per-call
  subprocess cost turns out badly, per RS2's stop condition.

## 7. Risks and unknowns

- **Highest risk is RS2's latency.** Non-terminal tool calls (write_file, cronjob, skill_manage,
  process, computer_use) currently pay NO subprocess. Moving them onto the classify path trades
  a spawn for renewability. Hence the measured stop condition, not an estimate.
- **The PII gate must never become permissive.** Hoisting it into a shared contract means a
  contract bug is a fleet-wide privacy bug; the corpus must include the near-miss cases the
  current regexes accept, and the fail-closed direction must be asserted.
- **Unknown until measured:** whether the hook uniformly exposes `action` and path arguments for
  every non-terminal tool (RS2 measures; if a tool exposes neither, it is declared frozen WITH
  the reason, per `cross-agent-design` Rule 2).
- **Not yet decided:** the RS5 floor (how many module-level tables may remain). It is set from
  the post-RS3 measurement, not guessed now.

## 8. Sequence

RS0 (parity re-pin + hermetic reload) → RS4 alongside → RS1 → RS2 (measured, stoppable) → RS3 →
RS5 floor chosen from the post-RS3 count → full suite + doctor → deploy → push → report with the
reload transcript. Pro review of THIS plan happens before RS0 begins; the close-time review is
MCP-owned and outside the worker lane.
