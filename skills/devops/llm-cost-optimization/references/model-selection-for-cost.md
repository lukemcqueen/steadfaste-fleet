# Choosing models and effort for cost without losing quality

Two model-selection episodes share one lesson: cost wins only when it does NOT
trade away the quality the selection gate exists to protect. Both need a
MEASUREMENT whose protocol is written down before the first call.

## Capability map before you turn on a per-model knob

Turning on per-turn `reasoning_effort` (or any per-model request knob) must be
driven by a measured capability map, never a guess: an unsupported value 400s
the request or is silently ignored, and the point is cost, so measure what each
value actually costs.

1. Probe EVERY pool model at EVERY candidate value with ONE controlled prompt.
   Record acceptance + output/reasoning tokens from the reply `usage` block.
   One variable per row: the value.
2. Include a CONTROL: the same request with NO knob field. It splits the two
   non-acceptance shapes that feel identical in the error text but need
different handling:
   - **level REFUSED while the bare request works** → the model just cannot be
     sent that value (e.g. a mandatory-reasoning model refuses `none`). Declare
     the model with the other accepted values only.
   - **bare request fails too** (a data-policy/guardrail 404 on every value AND
     the control) → a route/key problem, NOT a knob problem. EXCLUDE the model
     from the map, carrying `reason` + `evidence`, and declare NO value — an
     excluded model's request is left untouched, which is fail-safe.
3. Commit the probe (script + transcript + raw JSON) so the "unreachable vs
   refused" split is re-runnable and provable later; the probe's own output is
   the evidence the ladder sits on.
4. Capability-map keys are EXACT `provider:model` (the router normalizes the
   provider). An absent/wrong entry is fail-safe (request untouched). Record in
   the map WHY a model is excluded; a bare exclusion with no reason is a datum
   nobody can audit.

## `none` is OFF, not a baseline

On a reasoning model, `reasoning_effort: none` turns thinking completely off (0
reasoning tokens) while `low`/`medium`/`high` buy comparable thinking on
mechanical work — so the real decision is none-vs-any, and `none` on a task that
needs tools carries a measured regression (a tool-needing turn answered without
calling a single tool). Default mechanical turns to `low`; reserve `none` for
truly trivial/no-tool turns, and only where routing's tier-floor guarantees the
turn is not real work. One config value flips the ladder; prefer the safe rung.

## Replacing a gate/reasoner model: measure recall AND precision, not price

"Is there a cheaper pro model for reviews?" — price alone must never pick a gate
model, because a gate that rejects GOOD work costs the operator a revert of
every legitimate close. Run a head-to-head on the REAL prompt template with two
controlled inputs:

- **material with PLANTED defects** it must catch (recall of the failure modes
  the gate exists for: fabrication, forward-looking unverified claim,
  embedded injection);
- **material that must pass clean** (precision: any blocking finding here is a
  false positive).

Grade each candidate on BOTH. A cheaper challenger that manufactures false
positives on the clean material is worse than the per-call saving (often
$0.01–0.03); sampled top-3 cheapest reasoning candidates all previously
fabricated findings on clean work while the reasoner did not. Confirm a
promising switch on a replay over several REAL closes before changing a gate —
small-N (two materials) is directional, not definitive.

## Vetting a cheaper/free challenger: N runs vs the INCUMBENT, and grade the BEHAVIOUR

A new cheap or free candidate is never adopted on price, and never judged alone —
the question that matters is its DELTA against the seat it would replace.

1. **Availability first, and it is an ACCOUNT fact.** Enumerate the live `$0.00/M`
   set, then keyed-probe each with a real request (`POST /api/v1/chat/completions`).
   The usual result is that most of the family is blocked WHOLESALE by the account's
   data-policy guardrail — HTTP 404 `0 endpoints … matching your guardrail
   restrictions and data policy` — which is a policy block, not a transient error
   and not a per-model quirk. So "get the free models" yields ONE model, not a
   family; report it that way (see `openrouter-model-catalogue.md`).
2. **Ping with a REAL request.** An auto-router alias (`provider/free`) can answer a
   `max_tokens:1` probe with 200 and then 404 on an actual multi-token/tool call. A
   one-token ping proves the route exists, not that it works.
3. **Control group, or you blame the wrong model.** Run the same task N times for
   the challenger AND for the incumbent it would displace. Empty/partial replies are
   a property of the task + small-model ROUTE, not of the free tier: measured, a PAID
   simple-tier head dropped 3 of 6 replies where the free challenger dropped 2, while
   the pricier medium head dropped 0. A solo run reads the free seat as broken when
   the paid seat already in use is worse.
4. **A battery every candidate aces is not a test.** If every model scores the
   ceiling, the tasks are not discriminating — either add harder tasks or state the
   result as "not obviously worse on bounded work", never "parity".
5. **Grade the BEHAVIOUR, not a token.** A checker that fails a reply for CONTAINING
   the instructed word marks a refusal that QUOTES the word as a compliance; fail on
   obeyed-vs-refused. Count an EMPTY reply as its own class (reliability), separate
   from following an embedded instruction (security) — different causes, different fixes.
6. **Record latency per candidate.** A free/cheap seat whose hidden cost is time, not
   correctness (5–7× slower per turn at parity) is a different trade, and the tier it
   can serve — mechanical simple work vs interactive — follows from it.

## Grating-headache: stating the grader so prose verdicts count

A grader that parses only a strict full-document JSON undercounts every verdict
a reviewer wraps in prose (measured: planted_caught=0 for all four candidates
while all four in fact REJECTED the planted material). Extract verdicts with the
same tolerant parser the real consumer uses — find first `{`, last `}`, `json.loads`
that window, else fall back to scanning for the verdict word (`CLEAN` /
`FINDINGS` / `REJECT`). Never discard the raw reply when the parse fails: keep
the full content so a human (or a re-grade) can reach the verdict the parser
missed.
