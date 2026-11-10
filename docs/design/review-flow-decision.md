# Review-flow decision: the recorded gate keeps one writer; the DeepSeek review MCP stays advisory

Status: **ADOPTED by operator (Luke), 2026-11-10**, after deepseek-v4-pro review
(cycle #14431; verdict rows + findings recorded in `adversarial_reviews` /
`adversarial_reviews_history`). The independent reviewer grades design soundness;
the operator adopts policy. This document is the decision and the reasoning, so
any agent or future reviewer can see the boundary without re-deriving it.

## The two surfaces

1. **`ops/scripts/orch-bus/adversarial-review.py`** — the sanctioned, recorded
   reviewer. Worker-invocable (`--cycle-id N --review`), reads the committed
   reviewer-prompt template, appends material as untrusted data, calls the pinned
   pro model, records verdict + reviewer_id + model into `adversarial_reviews`.
   Idempotent per cycle (`cycle_id UNIQUE`). Feeds the plan-review-before-slice
   gate. This is the ONLY writer the gate trusts.
2. **`mcp-servers/deepseek-review-mcp.py`** — a DeepSeek review MCP server (new
   component, committer-shippable). Advisory only: returns an interactive critique
   to the session. NO write path to `adversarial_reviews`, does NOT own the
   reviewer prompt, does NOT take a verdict as input.

## The decision

**The recorded gate keeps one writer: `adversarial-review.py`. The DeepSeek
review MCP is advisory-only, always, and is never a bridge to a recorded row.**

Why:

- **Recorded == pro reviewed through the sanctioned path.** A row in
  `adversarial_reviews` authorises a non-trivial slice. The only safe meaning of
  "a recorded review exists" is "pro genuinely reviewed the plan via the
  committed template and its raw verdict is what is stored."
- **The boundary is verdict-forced-by-real-pro-call vs verdict-as-input.**
  `adversarial-review.py --review` forces a real pro call and records its raw
  verdict — invocability is not forgery. `record-review.py` takes `--verdict` as
  a CLI argument — forgeable — so it stays orchestrator-run-only and never
  reaches workers or the MCP.
- **An automated MCP→recorded bridge is a forgery surface**, not a convenience:
  a bridge is safe only if it calls back into the sanctioned pro backend and
  records only what that backend returns. That is just `adversarial-review.py`
  wearing a new interface; making the MCP the interface adds code and a
  harder-to-certify write path for zero gate benefit. Simpler and safer: one
  writer, one command.

## Per-agent effort

- Formal/gated work: one command —
  `python3 ~/.steadfaste-fleet/scripts/adversarial-review.py --cycle-id N --material-file <plan> --review --reviewer-id <you>`.
  Fleet-deployed, self-resolves the API key, no orchestrator needed.
- Routine planning: the DeepSeek MCP review (free, advisory).
- No double review, no new forge surface.

## Security properties (enforced, not assumed)

- **`--review` is idempotent per cycle**: `adversarial_reviews.cycle_id` is
  UNIQUE; a re-run is a benign no-op. A worker cannot resample by rerunning.
- **`plan-review-check.py` (the slice gate) consumes EXISTENCE, not verdict**
  (l.129-131 `SELECT 1 ... ORDER BY ts DESC LIMIT 1`; tests `row is not None`).
  It never inspects CLEAN vs FINDINGS, and the close-time reviewer runs fresh on
  the committed diff. So the `--re-review` resample-to-CLEAN concern
  (ADV-14431-7) does not grant a pass on this gate. `--re-review` remains the
  legit orchestrator path for genuinely re-judging a plan after findings are
  fixed; the audit trail is preserved in `adversarial_reviews_history`.
- **The review MCP has no write path, enforced by contract test**: no import of
  record-review.py, no `INSERT INTO adversarial_reviews`, no sqlite connect to
  the loop-governance store, AND no indirect path (`subprocess`/`os.system`/
  `Popen` spawn of record-review.py or adversarial-review.py). Because the MCP is
  committer-writable, "advisory" is a test-enforced property, not good behaviour.
- **Prompt ownership**: the recorded reviewer prompt lives in the committed
  orchestrator-owned template (`docs/templates/adversarial-reviewer-prompt.md`),
  never in a committer-writable server.
- **Both writers are outside committer reach**: `adversarial-review.py`,
  `record-review.py`, `plan-review-check.py`, `review-provenance-check.py` are in
  the committer deny-list (see below).

## Committer deny-list (companion change, same review)

The committer boundary is a DENY LIST (not the old docs+manage allow-list): a
fleet-committer may write ANY fleet path EXCEPT the governance spine + review
pipeline + instruction/CI surfaces. New mcp-servers/* components (the review MCP
included), ops/install/, ops/scripts/manage/, docs/ (non-config), tests/ are
committer-writable so the capability does its own job. See
`_COMMITTER_DENY_FILES` / `_COMMITTER_DENY_DIRS` in `ops/scripts/pre-commit-score`.

## Traceability

- Decision review cycle #14431 (multiple `--re-review` rounds; findings
  ADV-14431-1..8 superseded into history; final CLEAN).
- Recorded rows carry reviewer_id + reviewer_model per verdict — a reader can
  always see which model gave which verdict, per `review-provenance-check.py`.