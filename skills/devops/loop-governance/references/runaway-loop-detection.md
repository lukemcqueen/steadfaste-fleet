# Runaway LOOP cycles — mechanism, detection, and breaking

## The mechanism

- **The decision is a PURE function of the composite.** In `core/governance/loop_scorer.py`
  the branch is literally `if comp >= stop ... elif comp >= loop: "LOOP"`. There is no
  memory of prior iterations, so a task that settles at one score re-derives LOOP forever
  and **nothing terminates it**. Do not look for the off-switch in the scorer; it is not
  there.
- **`no_progress` / `no_progress_limit` are computed and DISCARDED.** A config comment once
  promised "consecutive no-progress before HARD FAIL" and no hard fail was ever implemented.
  Settle this by grepping the **callers** of `get_no_progress_streak()` — its only readers are
  analytics and auto-tune range validation, never the decision path or the insert. Never
  trust the config comment for what a threshold *enforces*; read the caller list.
- **`pass-pct=100 -> LOOP` is the design, not a defect in the commit.** pass-pct=100 gives
  completeness=10, which with default weights lands the composite ~7.0-7.2 — inside the LOOP
  band (loop=5.0, stop=8.0). So a fully green commit legitimately scores LOOP and the hook
  prints `score-cycle: LOOP ... (pass-pct=100)` on your own work. Read it as the calibration
  signal it is; do not chase your commit for it.

## You cannot break a loop by writing a decision

The DB is **not a driver**: it lives outside the repo (under the fleet state dir) and the
scoring path issues no git or subprocess calls, so a runaway is driven by an EXTERNAL
re-firing process (hook, cron, timer). Writing a different decision therefore does not stop
it — the driver fires again and mints another row. Breaking must happen where the loop's
**energy enters**:

1. **INVOCATION — throttle the scorer itself.** The only layer that stops a driver loop.
   Gate it on an actor classifier (the pre-commit hook already has one) so agent traffic is
   limited while rebases, cherry-picks and human amends are never touched. **An un-gated
   rate limiter that suppresses legitimate rapid commits is worse than the loop it fixes** —
   that is the objection to test first.
2. **WRITE — cap identical `(task_id, composite)` LOOP rows** at the single write choke
   point (`log_cycle()`). Use a UNIQUE partial index + `INSERT OR IGNORE`, **not** a
   SELECT-before-INSERT: that function holds `flock(LOCK_EX)` and a pre-check doubles the
   lock-hold. Suppress the row; **never fabricate a decision** to make the loop look closed.
   Two traps: pre-check in `log_cycle_with_content()` *before* `store_content()` or you
   orphan content rows, and note that while callers ignore the returned id, `full_score()`
   sets `logged=True` unconditionally — suppression must signal back or the record lies.
3. **ESCALATION — flag and alert.** Never a destructive or decision-fabricating terminal
   action; unattended destructive steps default to no-op.

## The detection SHAPE decides whether you see it at all

- **Per-task detection is blind to a diffuse storm.** Grouping by `task_id` and counting
  `>= N` requires one task to accumulate N rows; a storm spread thinly (measured order:
  ~1.4 rows per task_id across ~1.8k task_ids) never trips it, no matter how large the total.
  Always pair the per-task check with a **system-level rate over a short window**, and report
  the **distinct task_id count beside the row count** — that ratio is what separates a diffuse
  storm from ordinary iteration. A bare row count cannot tell an operator which they have.
- **Never tune a rate threshold to the storm that prompted it.** That storm was SLOW (spread
  over weeks, a couple of rows an hour), so a threshold low enough to catch it also fires on
  ordinary traffic. Ship a provisional threshold that *says so out loud* in the constant, the
  flag help and the commit; record the rate on every run to accumulate the baseline; re-tune
  from recorded numbers, not by feel.
- **A drain plus alert dedup can MASK a live runaway.** Drained rows make alert counts DROP,
  which reads as progress while the runaway keeps minting. Key dedup on
  `(task_id, last_drained_at)` and emit a distinct RE-STALLED alert when a previously drained
  task re-accumulates — otherwise the re-emergence is silently suppressed as "seen".
- **`user_overrode` is overloaded**: NULL = unreviewed, 0 = accepted, 1 = overridden. A drain
  that writes 0 for a *terminated* cycle inflates every accuracy metric that reads 0 as
  "correctly decided". Give automated resolution its own marker rather than reusing the
  human vocabulary.

## Where storms actually hide: the reporting layer

A backlog can be invisible for weeks **with all the data present**, because the reporting
layer filtered it — a census dismissed the stalled rows as "auto-resolved entries that the
scorer closed without requiring a user decision", which was simply false about what the
rows said. When a report dismisses a pile as "not requiring a decision", re-derive the claim
against the actual decision values before accepting it, and make sure any new detector does
not inherit the same filter.

## Draining safely

- Scope the drain tightly and pair its threshold with the alert's, **pinned by a test**
  asserting both, so "what the alert reports" and "what the drain clears" cannot drift apart.
- A per-task drain does **not** cover real work: a broad `precommit-*` prefix spans real
  repos, not just test artifacts. Keep an age gate, and treat the age gate as the only thing
  protecting a live investigation.
- Prove a drain on a **copy** of the prod DB, and assert idempotency (a second run drains 0)
  plus preservation of every case it must not touch (recent, still-moving, already-reviewed).
