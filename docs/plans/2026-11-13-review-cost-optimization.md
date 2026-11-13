# Reviewer & Worker Cost Optimization — Implementation Plan

> For Hermes: execute with TDD, one behavior per slice, commit after each task, no `--no-verify`.

**Relationship to the canonical cost work:** this plan extends
`docs/design/adversarial-review-cost-reduction.md` (Luke's reference for the
close-gate cost program: levers A/B/C/D, the 300 s ceiling constraint, and the
"fit inside the ceiling, never raise it" rule). That doc is about the
CLOSE-GATE reviewer. This plan targets the two surfaces it did not:
**the plan reviewer** (`adversarial-review.py`, the dominant measured spend,
74%) and **the worker's reasoning effort**. Same invariants apply: quality is
the gate's reason to exist; every claim measured, never modeled; the ceiling
is a design constraint, not a budget shortfall.

**Goal:** Cut measurable review + worker LLM spend without degrading adversarial-review effectiveness (recall), by (1) bounding the plan-review call's output/thinking budget, (2) A/B-ing cheaper plan-reviewer output models, and (3) lowering the v4-flash worker's reasoning effort where the files allow and the operator confirms.

**Pro-review receipt (cycle 8258, `plan-reviewer-pro-pass1`, deepseek-v4-pro-0813, 2026-11-13):** verdict FINDINGS — 2 high (ADV-0-1, ADV-0-2), 1 info (ADV-0-3). All folded in below:
- ADV-0-1: the measured-baseline numbers were prose. **Evidence step added** (Task 0): commit the ledger-query output and the 2026-11-13 price-source record as artifacts in this cycle before any change.
- ADV-0-2: Task 3's effort ground truth is contradictory (config `medium`, live routing.json has no effort block, operator says `high`) — the mechanism must not be picked by guess. **Resolution step added** (Task 3): commit the live-setting evidence (`hermes config get agent.reasoning_effort`, routing.json excerpt) and record the operator directive with a pointer before choosing config.yaml vs routing.json.
- ADV-0-3: the dead-scorer claim belongs to the 10-29 eval's own review; Task 2's output will carry the corrected scorer + recall matrix so it can be checked there.

**Architecture:** The fleet close-gate reviewer (loop-gov-mcp.py) already carries a bounded reasoning budget + hard deadline (env `REVIEWER_REASONING_EFFORT`, `REVIEWER_MAX_TOKENS`, wall-clock ceiling). The **plan reviewer** (`ops/scripts/orch-bus/adversarial-review.py:_call_model`) sends **no max_tokens and no reasoning_effort** — it runs the model's default (effectively max/high), which is why plan reviews produce ~6–15K output tokens and drive 74% of the measured spend. The fix is to give the plan reviewer the SAME budget plumbing the close gate has, then A/B the output-model price.

**Tech Stack:** Python 3 (stdlib urllib), OpenRouter chat-completions, loop-governance.sqlite ledger (`review_calls`), config.yaml + jev routing.json for the worker effort.

---

## Measured baseline (from `review_calls` ledger — NOT modeled)

- Fleet 4-day window (11-10→11-13): **$9.55** (esther $5.66, titus $3.89), extrapolates ~$70–100/mo, and the ledger only materialized 11-10 so earlier weeks are unmeasured but on the same surfaces.
- **plan-review surface = 74%** of that bill: esther 780K completion / 51 calls (~15K each) + titus 789K / 131 calls.
- close-gate surface is cheap: ~350 output tokens/review (~$0.002).
- Live prices (fetched 2026-11-13): pinned `deepseek-v4-pro-0813` ≈ $0.11/M in, **$4.44/M out**; unpinned `deepseek-v4-pro` ≈ $0.9/M in, $1.5/M out; candidates under $1/M out exist (mimo-v2.6-pro $0.87 out, v3.2 ~3× cheaper than pro).

---

## Scope — three slices, each independently verifiable

### Task 0: Commit the evidence the baseline claims rest on (pro finding ADV-0-1)
- **Objective:** the measured-baseline numbers in this plan must be re-derivable from committed artifacts, not prose.
- **Files:**
  - `docs/evidence/review-cost-ledger-2026-11-13.txt` — the `review_calls` query + output from esther/titus (the bus EXEC dispatch this session) and moses local, verbatim.
  - `docs/evidence/reviewer-prices-2026-11-13.txt` — the OpenRouter catalog query + the ids/prices it returned (pinned 0813, unpinned pro, v3.2, mimo).
- **Verify:** each file carries its exact command; re-run the command reproduces the numbers. Commit in this cycle before Task 1.

### Task 1: Bound the plan-review call budget
- **Files:** Modify `ops/scripts/orch-bus/adversarial-review.py` `_call_model` (~line 178) to add:
  - `"max_tokens": <REVIEWER_PLAN_MAX_TOKENS or 24000>` — concrete default: **24000** (measured plan output is 6–15K tokens; 24K bounds runaway while leaving headroom; env `REVIEWER_PLAN_MAX_TOKENS` overrides; a value below the floor `REVIEWER_PLAN_MAX_TOKENS_FLOOR=16000` is rejected loudly). NOT the close-gate's 16000 — the plan review legitimately emits more prose than a JSON verdict, and 16K would truncate the top of the measured range.
  - `"reasoning": {"effort": <REVIEWER_PLAN_REASONING_EFFORT or "low">}` — distinct env var from the close-gate's `REVIEWER_REASONING_EFFORT` (whose default is `none` — correct for a short JSON verdict, wrong for long findings prose). Default **`low`**: enough thinking to produce real findings, bounded. Env-overridable. An invalid value falls back LOUDLY (log + default), never 400s at review time (same rule as the close gate).
  - Read budget env from the same sources `_call_model` already uses for the key (env then `~/.hermes/.env`).
- **Tests:** `tests/test_plan_reviewer_budget.py` (NEW) — hermetic stub of `urllib.request.urlopen` like `test_reviewer_budget.py`:
  - RED: request body currently lacks `max_tokens`/`reasoning` → assertion fails on pre-change module.
  - Assert `max_tokens == REVIEWER_PLAN_MAX_TOKENS` (default 24000) and an env override changes it; `reasoning.effort` in provider vocab (`none|low|medium|high`), default `low`, env-overridable; an invalid effort falls back loudly to the default.
  - Assert a `max_tokens` below the floor is rejected loudly (not silently clamped).
  - Assert `_record_review_call` still records usage (ledger unchanged).
  - **Change-test-loop discipline:** run the failing test against the pre-change `_call_model` (discrimination), then against the changed one.

### Task 2: A/B cheaper plan-reviewer output models
- Objective: pick a plan-review model that keeps planted-defect recall while cutting output-token cost. This is a **measurement slice**, then (if it wins) a config pin.
- **Runner:** extend/reuse `docs/evidence/reviewer-model-eval-2026-10-29/runner.sh` pattern. **Fix the dead scorer** from that eval: it keyed `planted_caught` on a `technique` field reviewers never emit (all models scored 0). The corrected scorer reads findings from the extracted JSON array directly (verdict + per-finding severity/evidence), like `_extract_reviewer_json` does.
- Candidates against the **plan** prompt (material = a plan, not a diff): pinned `v4-pro-0813` (baseline), unpinned `deepseek-v4-pro`, `deepseek-v3.2`, `xiaomi/mimo-v2.6-pro`.
- **Output:** `docs/evidence/plan-reviewer-model-eval-<date>/` with curated recall + cost-per-review per model. Only if a cheaper model matches recall do we change `ADVERSARIAL_REVIEWER_MODEL` — and that is a separate, operator-approved pin, **not** auto-landed by this slice.

### Task 3: Lower the v4-flash worker reasoning effort high → low
- **Objective:** reduce worker (session model `deepseek-v4-flash-0731`) spend by lowering reasoning effort *if* low preserves needed capability.
- **Ground-truth resolution first (pro finding ADV-0-2 — do NOT pick the mechanism by guess):**
  1. Commit the live-setting evidence: `hermes config get agent.reasoning_effort` output, the live `~/.hermes/jev/routing.json` effort-section excerpt, and a `state.db` / agent.log reasoning-effort sample from the running gateway.
  2. Record the operator directive (Luke: worker runs at `high`; `medium` is not a real level for v4-flash) in the evidence file with the session pointer, so the mechanism choice is traceable to a directive, not a guess.
  3. THEN choose the mechanism from the evidence: `hermes config set agent.reasoning_effort low` (config.yaml path) vs a change to the Jev `effort.levels` table in the live routing.json.
- **Approach:** make the worker effort a single, easily-revertible setting and measure the difference. Two candidate mechanisms:
  - (a) `hermes config set agent.reasoning_effort low` (safe, reversible, applies to new sessions via `_resolve_gateway_model`).
  - (b) If the effort is actually the Jev `effort.levels` table, add/change the `effort` block in the live `routing.json` to map down.
- **Verify (mandatory):** spawn `hermes chat -q "Reply with exactly: OK"`, check `state.db` siphon for reasoning tokens before/after; confirm the worker still routes tools + answers (no tool-less regression — the exact failure mode the Jev `none` default regression produced).
- **Do NOT auto-land:** the effort change affects every session. Run as an easily-revertible test, present measured difference, get operator confirmation before keeping.

---

## Files changed (plan)
- `ops/scripts/orch-bus/adversarial-review.py` (Task 1)
- `tests/test_plan_reviewer_budget.py` (NEW, Task 1)
- `docs/evidence/plan-reviewer-model-eval-<date>/` runner + results (Task 2)
- `~/.hermes/config.yaml` OR `~/.hermes/jev/routing.json` (Task 3, after operator confirm)
- docs/env-vars.md, reviewer-transport.md (document new plan-reviewer budget vars)

## Verification
1. `python3 tests/test_plan_reviewer_budget.py` — RED on pre-change, GREEN on changed.
2. `python3 -m py_compile ops/scripts/orch-bus/adversarial-review.py` — OK.
3. `python3 tests/test_reviewer_budget.py` (close-gate) still passes — no regression to shared plumbing.
4. Task 2: recall matrix + cost per review, curated, committed.
5. Task 3: before/after reasoning-token measurement from state.db / chat one-shot.
6. `fleet-doctor.py --quiet` clean after deploy; `verify-landed.py`.

## Risks / tradeoffs
- **Quality is the gate's reason to exist.** Dropping plan-review effort to `none` would mirror the close-gate fix but would likely gut a long-findings review. The A/B (Task 2) and a measured effort floor are how we keep recall while cutting cost — never guess; measure.
- **Changing worker effort is operator-sensitive** — flag, don't silently land.
- **Cheaper model pin is operator-approved** — Task 2 produces the recommendation; the pin is separate.
- Shared env var names must not break the close gate: use distinct `REVIEWER_PLAN_*` vars (or carefully shared defaults) so the close-gate `REVIEWER_REASONING_EFFORT=none` is untouched.

## Open questions for the reviewer
1. ~~Is a distinct plan-reviewer effort var better than sharing?~~ **Resolved in plan:** distinct `REVIEWER_PLAN_REASONING_EFFORT`, default `low` — the close-gate's `none` default is correct for JSON verdicts and wrong for long findings prose; sharing would break plan review on the close-gate default.
2. ~~What max_tokens cap?~~ **Resolved in plan:** default 24000 with a 16000 floor, env `REVIEWER_PLAN_MAX_TOKENS` — bounds the measured 6–15K range with headroom, below-floor rejected loudly.
3. Where does the worker `high` effort really live? (open — Task 3 resolves it with committed evidence before choosing a mechanism, per ADV-0-2)
