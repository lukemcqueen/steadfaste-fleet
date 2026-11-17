<!-- Backport provenance (deployed -> repo), 2026-11-16: the lessons below were
     authored earlier the SAME DAY in the DEPLOYED copy on this host (a session
     written to the deployed tree, which the repo copy did not share) during the
     model-servability / selector-value work. They are copied into the repo so the
     deploy carries them fleet-wide. Every repo-only line this supersedes was
     checked by phrase match: each is superseded IN PLACE (a corrected acceptance
     recipe and a corrected latency claim), not lost. -->

# Model upstream servability — why a live model can still 400 every turn

## When to Use

A session reports healthy but runs a model nobody configured (a fallback), the
id resolves in the catalog, and `model.default` is a correct BARE id.
"Present in the catalog" and "servable by this client" are different facts;
this file is how to prove the second one.

## The probe (no auth needed)

```bash
curl -s https://openrouter.ai/api/v1/models/<author>/<slug>/endpoints
```

Read two things: the number of entries in `endpoints`, and each
`provider_name`. Then confirm what actually happened on the wire:

```bash
grep -nE "Fallback activated|BadRequestError|is not a valid model ID" ~/.hermes/logs/agent.log | tail
```

## The signature: one upstream, reasoning-hostile

A model with a SINGLE endpoint has no routing freedom — no provider preference
can rescue it. When that one provider is Amazon Bedrock, the request fails with:

```
400 'Provider returned error'  provider_name='Amazon Bedrock'
raw: "User messages cannot contain reasoning content. Please remove the reasoning content and try again."
```

It is HTTP 400 and marked NOT retryable, so Hermes activates
`fallback_providers` immediately. Every turn pays one wasted round-trip and the
user sees a `⚠️ Model fallback: … unavailable` line, while the banner, the
config and the session row all still name the configured model.

## Mechanism (why the replay is never suppressed)

Reading the client, not guessing it:

- `agent/message_sanitization.py` sets `_DEFAULT_CARRIERS = frozenset({RC})`,
  so stored `reasoning_content` is replayed on assistant turns to EVERY
  chat-completions route unless a host rule or a recorded rejection removes it.
- `apply_reasoning_content_policy()` returns early for any message whose role
  is not `assistant`, while Bedrock's wording complains about USER messages —
  the sanitizer is not looking at the message Bedrock objects to.
- The self-heal `rejected_reasoning_fields()` only fires on UNKNOWN-FIELD
  phrasings ("extra inputs are not permitted", "is unsupported", "no such
  field", …). Bedrock's wording matches none of them, so the rejection is never
  RECORDED, the route is never narrowed, and the 400 repeats indefinitely.

Consequence: a model served only by such an upstream is structurally unusable
as a session default AND as a routed pool tail — it fails the same way wherever
it is chosen.

## Why no config knob fixes it

Check these before promising a config-only fix:

- `supports_reasoning: false` (per-model metadata) gates only OUTGOING reasoning
  params (`reasoning_params.unset_reasoning_default`) — it does not stop the
  REPLAY of stored reasoning.
- `provider_routing: {ignore: [...]}` cannot help a single-upstream model —
  ignoring that provider leaves zero providers, so the request has nowhere to go.
- `reasoning_echo` is per-entry opt-IN (adds the must-echo pad); there is no
  mirror-image flag that declares a route strict.

So this is fleet policy, not a local edit. It now lives as DATA — one entry per
EXCLUDED upstream — in `config/router-models.json -> provider_exclusions`
(reason + evidence + scope, the same rule `effort.excluded` already carries), and
is projected into each coding agent through that harness's OWN api by
`ops/scripts/manage/provider-policy.py`. A harness with no verified knob is
DECLARED there as a gap rather than guessed at: Hermes is one adapter, not the
policy (docs/design/agent-interop.md). For a MULTI-upstream model, excluding a
reasoning-hostile upstream simply sends the request to a usable one — the
single-upstream case above is the one where an excluded provider leaves nowhere
to go, which is why the entry states its scope. (See the re-anchor cascade in
SKILL.md for the model-level consequences.)

## Picking a replacement

Prefer a model with MANY endpoints — the endpoint count is itself a fragility
signal, since any single upstream owns the model's compatibility. Compare like
with like via the endpoints feed (`pricing` per endpoint), and prove ACCEPTANCE,
not just configuration:

```bash
hermes chat -q 'Reply with exactly: OK'
grep -nE "Fallback activated|reasoning content|Provider returned error" ~/.hermes/logs/agent.log | tail
```

A clean run shows NO `Fallback activated` and NO `400` in that window. Do NOT
read the `conversation turn: session=<id> model=<model>` line as evidence of
what ran: it prints the CONFIGURED model, so a turn that 400'd and rode the
fallback still logs the configured id, which reads as a healthy turn. Positive
proof comes from TWO lines: `Fallback activated: <configured> → <actual>` (that
arrow is UNICODE, `→`, U+2192 — a grep for ASCII `->` finds none of them), and
the per-turn `Turn ended: reason=… model=<SERVED> api_calls=… session=<id>`, which
records which model actually ANSWERED that turn. Pair the `Turn ended` line with
the `conversation turn:` line for the same session: configured != served means
the configured model failed to serve, and the pair's timestamps give the turn's
duration — the per-model latency WE own, since the provider's own percentiles are
visible only to an authenticated call.

## Availability and performance fields (what each one is, what to do with it)

Read from the per-model endpoints feed; only the first group is free.

| field | auth | meaning | how to use it |
|---|---|---|---|
| `status` | no | `0` healthy; NEGATIVE = degraded/deranked (observed 0, -2, -5) | an upstream with a negative status is not usable |
| `uptime_last_5m/30m/1d` | no | share of minutes in which >=80% of provider attempts succeeded, counting only minutes with >=10 attempts; rate-limited and caller-caused failures EXCLUDED | below a floor (e.g. 50%) = not usable; `null` = UNKNOWN, never "down" |
| `latency_last_30m` | YES | p50/p75/p90/p99, ms, TIME TO FIRST TOKEN | ranking by speed; null when unauthenticated |
| `throughput_last_30m` | YES | p50/p75/p90/p99, tokens/s | as above |
| `perf_last_30m_by_workload` | YES | the same stats keyed by workload (text vs image generation) | prefer it over the legacy singular fields where present |

Rules that follow:

- **A model is head-ELIGIBLE only if at least one upstream is usable** (status
  non-negative AND uptime unknown-or-above-floor). Zero usable upstreams -> not
  eligible to head a pool; if it already heads one, that is a MOVE trigger.
- **`null` means UNKNOWN — not zero, not down.** Reporting an unmeasured latency
  as "fast", or a traffic-starved uptime as "down", are both false verdicts.
  Say UNMEASURED and keep it OUT of the gate.
- **Uptime would NOT have caught the reasoning-replay breakage** (that model
  reported uptime 100, status 0) — it is an OUTAGE signal, so it complements the
  acceptance pairing above rather than replacing it.
- **Never read the endpoint COUNT as health** — two endpoints under one provider
  is the same single-provider fragility as one.
- **A speed veto built on provider percentiles must degrade honestly:** measure
  turn durations from the paired log lines we already have, and use the
  percentiles only when the call is authenticated. Reading a key out of the auth
  store to fire such a probe is consent-gated — ask first; read the key from the
  ENVIRONMENT only when the operator has already made it available.

## Gate gap to close when you touch the scanner

The market scan's availability gate keys on PRICE: a model absent from the live
catalog lands in `missing_models` and fails the run closed. A model that is
priced but served only by a reasoning-hostile upstream passes that gate and
stays in the list and in `model.default` indefinitely. If you extend that
gate, keep the same shape as the missing-model one: name the model, fail
closed, and consult the condition in the run's exit ladder — a condition that is
measured but never consulted is a report, not a gate.
