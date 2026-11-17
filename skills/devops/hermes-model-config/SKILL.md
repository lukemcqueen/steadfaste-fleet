---
name: hermes-model-config
description: "Use when sessions run the wrong model or config regresses."
version: 1.0.0
category: devops
author: Titus
license: MIT
platforms: [linux, macos]
---
<!-- Backport provenance (deployed -> repo): the DEPLOYED copy of this file carried
     lessons the repo lacked. Every repo-only line it would drop was checked by phrase
     match and is a reflow or a deliberate CORRECTION, so the direction is
     deployed -> repo. Everything below is the deployed lesson copied verbatim, plus this
     note; it was NOT authored in the backport range that landed it.
     SECOND backport the same day (2026-11-16, model-servability / selector-value
     work): further lessons again lived only in the deployed copy — the
     upstream-servability mechanism (now references/model-upstream-servability.md),
     the CORRECTED acceptance recipe (`Fallback activated` + `Turn ended: …
     model=<SERVED>`, NOT the `conversation turn:` line, which prints the
     CONFIGURED model), and the CORRECTED latency claim (percentiles are
     auth-only; the free feed gives availability). Repo-only lines dropped in this
     pass were checked by phrase match and are superseded IN PLACE, not lost. -->

# Hermes Model & Provider Config

**Trigger:** "sessions use the wrong model", "model is X but should be Y",
deepseek-chat/deepseek-flash questions, `⚠ Deprecated .env settings detected`
warning, cron model overrides, model.default regressions after `hermes update`.

## Fleet model convention (the MECHANISM is the rule — the ids move)

- **Regular sessions run whatever `config.yaml model.default` says.** Read it
  with `hermes config get model.default`; never assume an incumbent id and
  never trust a doc/skill that names one (this section used to name a model;
  it rots). This is operator-owned config — set it with `hermes config set
  model.default <model>`; no fleet installer writes it (the env-driven
  `install-model-default.sh` writer was removed 2026-09-09).
- **Never hardcode a model id in a script, doc, or skill — resolve it.** The
  canonical reviewer lives in `ops/scripts/fleet_lib/models.py`
  (`REVIEWER_MODEL_DEFAULT`), resolved at runtime by `get_reviewer_model()` and
  overridable by the `ADVERSARIAL_REVIEWER_MODEL` env var; a consumer that
  cannot resolve it must FAIL CLOSED, never default silently. Point docs and
  agent-instructions at "the current canonical reviewer" (or the resolver),
  never a pinned id — a pinned id makes the next reader re-apply a model the
  operator already replaced. When the default moves, sweep the old id out of
  docs + comments + tests in the SAME change, and make any guard test DERIVE its
  expected value from `get_reviewer_model()` rather than a literal so it cannot
  re-rot.
- **`deepseek-chat` is LLM-cron-ONLY.** It must never be the session default.
  Crons pin it per-job: `"model": "deepseek-chat"` in `~/.hermes/cron/jobs.json`
  (see `LLM_CRON_MODEL=deepseek-chat` in `ops/scripts/install-crons.sh` and
  `ops/install/cron-manifest.yaml`). A config.yaml default of deepseek-chat is
  the regression symptom.

## What each tier is FOR — the tier decides which evidence qualifies a model

The tier a model sits in determines the evidence that picks it, and using the wrong
evidence selects a wrong model with full confidence:

- **`medium`, and the session default, carry general work INCLUDING building.** Codegen
  quality is the axis there (an A/B on hidden tests) because that tier writes code.
- **`hard` / the pro tier is REVIEW OR PLANNING ONLY — an operator standing rule, not a
  preference.** Nothing in this tier builds. So the qualifying evidence is the
  plan/reviewer A/B, never a codegen score:
  - **Recall is FLAT across reviewer models** — the record is
    `docs/evidence/plan-reviewer-model-eval-2026-11-13/README.md`, which states it
    directly ("Recall is NOT a differentiator. All four caught all three planted"). Every
    candidate in the reviewer A/B caught every planted defect, so recall separates nothing
    and a "best coder" ranking is the wrong input entirely.
  - What actually decides: **latency against the review ceiling**, **cost per review**,
    and **provider depth**. With equal recall, the cheap fast one is simply better — that
    same record carries its own cost comparison (roughly 6x cheaper per plan review at the
    same recall).
  - The measurement surface already exists: the plan-reviewer A/B runner, whose evidence
    sits under `docs/evidence/plan-reviewer-model-eval-<date>/`. EXTEND that eval for a new
    candidate instead of arguing from structure: a slot picked on "1M ctx and 6 providers"
    with no measured review recall is a guess wearing a table. Cite the eval's own record
    path in the verdict, so the next reader can re-run it rather than trust the summary.
- **Eligibility is a hard gate, and it is not just price or context.** A candidate must
  serve `tool_calls` across MORE THAN ONE distinct provider. Count DISTINCT providers —
  several endpoints under one provider is ONE upstream (the single-upstream trap). And
  check tool support on EVERY endpoint, because endpoints can disagree: a model can clear
  price, ctx and the whitelist bar and still be ineligible because routing picked the one
  endpoint that cannot call tools. A reviewer that cannot reliably call tools cannot read
  the diff it is reviewing. Note the whitelist's own `why` text claims reasoning + tool_call
  without verifying either — verify it yourself.

## Diagnose (in order)

1. **Truth for NEW sessions = config.yaml, not the banner.** Run
   `hermes config get model.default` (+ `model.provider`, `model.base_url`).
   The session banner snapshots the model at session START — a mid-session
   `hermes update` changes config underneath it (observed 2026-08-18: banner
   showed deepseek-v4-flash while config already said deepseek-chat).
2. **If config changed unexpectedly**, check
   `stat -f '%Sm %N' ~/.hermes/config.yaml` and `tail ~/.hermes/logs/update.log`
   — `hermes update` rewrites config.yaml AND ~/.hermes/.env in the same
   second. To rule out a migration, grep the key in
   `~/.hermes/hermes-agent/hermes_cli/config_migrations.py` (model.default had
   NO migration — it was a stale manual setting).
3. **Cron overrides:** `grep -n '"model"' ~/.hermes/cron/jobs.json` — per-job
   `model` field; `model_snapshot` records what it actually ran as. `no_agent`
   jobs (script-based) ignore `model` entirely.
4. **Check the whole repo for the convention:** `grep -rn '<model-id>'
   ~/steadfaste-fleet --include='*.sh' --include='*.yaml' --include='*.py'` —
   install scripts/manifests show the intended per-role model split.
5. **The session runs a model nobody configured → suspect a FALLBACK, not the
   config.** An id the provider rejects does NOT raise: Hermes falls through
   `fallback_providers`, the run still reports healthy, and the banner looks
   fine. The only traces are the rejection in the log — `grep -nE 'is not a
   valid model ID|BadRequestError' ~/.hermes/logs/agent.log` — and a `sessions`
   row whose `model` differs from the model the turns actually used. Trust the
   log over the banner AND over the config: the config states the intent, the
   log states what ran.
6. **A model that IS live, priced and correctly written can still be UNservable
   — check the UPSTREAMS, not just the id.**
   `curl -s https://openrouter.ai/api/v1/models/<author>/<slug>/endpoints` and
   count DISTINCT PROVIDERS, not endpoints — several endpoints under ONE
   provider (`endpoints=2`, both `Amazon Bedrock`) is the same single-provider
   fragility as `endpoints=1`, so reading the endpoint count alone lets it pass
   as healthy. A single upstream is a single point of incompatibility: `400 'Provider returned error'
   provider_name='Amazon Bedrock'` with `raw: "User messages cannot contain
   reasoning content. Please remove the reasoning content and try again."`
   means the model is served only by Bedrock, which rejects the reasoning
   Hermes replays — so EVERY turn 400s and rides the fallback chain while the
   banner stays clean. The catalog id lookup (`/api/v1/models`) proves
   PRESENCE; only the endpoints call proves SERVABILITY. Mechanism and why no
   config knob fixes it: `references/model-upstream-servability.md`.

## Fix

- **Decide the OWNER before touching the value.** On a fleet host the session
  default is not free-standing: the Jev list's `baseline` must equal it, and the
  daily `orch-model-market-scan` cron owns re-pointing both. Check
  `config/router-models.json` → `baseline`; when it names this host's model,
  the AUTOMATED process owns the change — run that job rather than hand-writing
  the config: `cronjob_manage action=list` to find the job id (never guess one),
  then `cronjob_manage action=run` with that id; pass your diagnosis in the
  transient `prompt` (that fire only, never persisted) so the job's own STEP 1.2
  decides with the facts, and do NOT poll — its outcome re-enters the
  conversation. Note the job runs on the MAIN model, so a broken default
  surfaces in its transcript too. A hand `hermes config set` is for a bare host
  with no such owner, or to repair a label — the operator will reject a
  hand-switch of a fleet-owned value even when the new value is right, because
  the point is that the process, its gates and its invariants move together.
  **Decide the owner BEFORE the first write, not after the objection.** If you
  already hand-wrote a fleet-owned value, REVERT it in the same session — set it
  back to the list's `baseline` (bare id) and read it back both ways (`hermes
  config get` AND the file on disk) — then hand the decision over by running the
  owning job. Leaving your value standing while the process supposedly "decides"
  IS the hack the operator objects to, and defending it invites the same
  correction again. Choosing no value yourself is the deliverable; a revert is
  not a new model decision.
- `hermes config set model.default <model-id>` — **NEVER hand-edit
  config.yaml** (stray indent breaks the live gateway; hermes-agent hard
  invariant). Agent file-write tools refuse the file outright
  (`Refusing to write to Hermes config file: … security-sensitive configuration`),
  so the CLI is not just preferred — it is the ONLY path from an agent.
  Verify by reading back BOTH the resolved value (`hermes config get
  model.default`) and the file on disk; the change applies to NEW sessions
  (the running session keeps its start-of-session model).
- `provider` / `base_url` are untouched by this command.
- No gateway restart needed: `_resolve_gateway_model` (gateway/run.py) reads
  `model.default` fresh per session from raw YAML.
- **Re-anchor the Jev router when the default moves.** If the Jev layer is live,
  its pricing-list `baseline` is the anchor every multiplier and every
  "cheaper than" claim is measured against, and the doctor's cost guard compares
  pool heads to the host's actual model. `model.default` change → update the
  baseline to the same model, swap the medium pools' in-pool fallback (a
  cheaper baseline can leave the old fallback above the 1x ceiling), and
  express hard-tier budget caps in absolute $/M rather than multiples of the
  baseline so a cheaper baseline cannot evict whitelisted models
  (see the pricing-list-policy reference of the system-one-judgment skill).
  The selftest checks EVERY model in a pool and catches the stranded fallback
  the tests miss.
- **A cheaper baseline is not a local edit — it cascades through the ceilings.**
  Ceilings are MULTIPLES of the baseline (`simple: 0.5`) and the simple rule is
  asserted for EVERY member of a simple pool, not just its head. A baseline
  below twice the dearest simple member therefore forces that member out of
  every simple pool, which re-picks those heads, which changes what the cheap
  tier runs fleet-wide. Compute that cascade BEFORE proposing a re-anchor, and
  treat any change to a ceiling multiple or to the vetted set as a POLICY call:
  state the concrete consequence and let the operator decide — never restructure
  the cheap tier on your own initiative just to make `--selftest` pass.
  So answer an "is there a cheaper default?" question by computing that cascade
  FIRST: count how many vetted models sit AT the current baseline price point
  (usually exactly one, so "no third option" is the common truthful answer),
  then note that any cheaper baseline evicts the dearest simple member from
  EVERY simple pool. The honest options are therefore (a) a model already in the
  list at or above the price the ceilings require (state the multiple it costs),
  (b) a NEW model that must first pass A/B vetting to hold the same price point,
  or (c) a policy change to the ceiling multiple — never a silent swap.
- **The config's DOCUMENTED fields go stale SILENTLY while the tests stay
  green.** Enforcement reads `prices[baseline]` LIVE, so `derivation.heads`,
  `candidates`, and `baseline_price_usd_per_m` can contradict reality (a head
  listed as model A while the pool order actually leads with model B) and
  nothing fails. Recompute `derivation.heads` FROM `tiers` + `prices` (first
  model in each pool) — never hand-edit — and re-check `candidates` (a model
  promoted into a routed pool is no longer a mere candidate).
- **Re-anchoring is a two-part change that must move together:** the
  enforcement-visible `baseline` field AND the baseline test that asserts it
  equals the host's session default — a half-migrated config (baseline moved,
  the test still pinning the old model) is exactly the drift that test exists
  to catch. Prove it with `router-pools.py --selftest` plus the router pool
  tests; the selftest checks every pool member, not just the head.
- **The fleet AUTOMATES the re-order** — the daily `orch-model-market-scan`
  cron re-prices from the live catalog, re-orders each pool cheapest-first
  WITHIN the pre-vetted set (it never introduces a new model = quality
  preserved), flags new unvetted cheaper candidates as A/B alerts (`rc=2`,
  never auto-adopted), and on a price/order change (`rc=1 apply_pending`) does
  a governed apply that re-points the session/reviewer default only if the
  vetted head changed. So a manual re-price is the exception; let the scan
  drive it and verify with `router-pools.py --selftest`. The three surfaces, in
  the order you use them: a BARE run (`router-pools.py`, == `--check`) is the
  read-only drift check and prints `no findings — every pool matches the list`
  against the live `$HERMES_HOME/jev/routing.json`; `--write` projects the list
  into that live routing (the sanctioned rebuild — `--refresh` re-prices the
  list itself); `--selftest` must PASS and checks every pool member, not just
  the head. Run the detector first (`orch-model-market-scan.py --json`) and read
  its exit code as the contract before touching either.
- **The selector is automatic on PRICE only — acceptance is not an input, so a
  broken-but-cheapest head never moves.** Its re-point trigger is
  `order_changed`: a model that is still the cheapest vetted candidate keeps
  head position and stays in `model.default` indefinitely while every turn 400s,
  because nothing in the ladder measures whether the model ANSWERS.
  `missing_models` means "absent from the catalog at a price" — it is NOT a
  servability verdict. Extend the existing ladder rather than adding a parallel
  one: the miss gate ALREADY fails closed, so feed the same shape a head that is
  detected unservable, demote it out of head position, and re-point the default
  to the next cheapest model that is ALREADY VETTED — never adopt an unvetted
  model to fill the gap (that is an A/B quality decision, not a mechanical one).
  Evidence shapes to key on and the gate gap to close:
  `references/model-upstream-servability.md`.
  **The operator's policy for that gate — do not re-litigate the weights:**
  value, not price alone. Quality stays held by the PRE-VETTED set (a cheaper
  unvetted model is an A/B decision, never an auto-adopt); PRICE orders within a
  pool; AVAILABILITY is a hard ELIGIBILITY gate (no usable upstream, or a
  configured-but-never-served model, is not eligible to head a pool and is a
  MOVE trigger, not a warning); PERFORMANCE only VETOES a pathologically slow
  head — "price beats speed … unless it's really slow" — measured from our own
  paired turn logs, with provider percentiles as corroboration only when the
  call is authenticated. So a broken-but-cheapest head MUST be movable: "it is
  still the cheapest vetted head" is never a reason to leave it in
  `model.default`.
- **Read the scan's exit code as the CONTRACT, not a hint:** `0` nothing to do,
  `1` a price/order move is pending, `2` an unvetted cheaper candidate was
  found, `3` **could not verify — nothing may be auto-applied**. `3` is also
  what an id the live catalog no longer serves must produce: an unpriceable
  model belongs in `missing_models` AND in the violation set that gates the
  run. Without that gate a vanished id rides the ordinary apply path (its
  absent price reads as "changed"), gets sorted silently to the BACK of its
  pool, and stays in the list — and in `model.default` — indefinitely. When you
  touch that ladder, keep the availability condition feeding it: a condition
  that is measured but never consulted is a report, not a gate. `missing_models`
  is therefore load-bearing — do not treat it as a decorative field.
- **Ids in the pricing list carry a ROUTING LABEL — strip it before any provider
  call.** Entries are written `openrouter:<provider>/<model>`, the same shape
  `~/.hermes/jev/routing.json` holds, and a request built from the label
  verbatim is answered `… is not a valid model ID` for a model that IS live.
  Diagnose that error by checking the live catalog FIRST: if the id is priced
  there, the LABEL is the bug, not the model, and the fix is on the consumer's
  side (strip the `label:` prefix, then split on the FIRST `/`).
- **Never WRITE the label into config.yaml — it is not a model id, and the host
  degrades SILENTLY.** `model.default` holds the BARE id
  (`qwen/qwen3-coder-30b-a3b-instruct`); the provider lives in `model.provider`.
  A label there is answered `… is not a valid model ID` (HTTP 400, not
  retryable), and because Hermes then falls through to `fallback_providers`,
  every new session runs a DIFFERENT model than configured while nothing
  reports it — measured 2026-11-16: a host 400'd on every turn for hours (17:07,
  17:10, 17:21, 17:28 in agent.log) with the banner looking fine. The writer is
  the daily `orch-model-market-scan` STEP 1.2 (its "new head" is a label), and
  `fleet_doctor.router_layer._current_model` merely TOLERATES a colon in `default`, so
  the cost guard stayed green through it. The doctor's `Model default` check now
  FAILs on a label; fix with `hermes config set model.default <bare-id>` and
  verify against the LIVE surface, not a fixture.
- **When DETECTING a label, never fire on "any colon".** An Ollama tag
  (`qwen2.5:3b`, which `hermes config set model.default qwen2.5:3b` legitimately
  writes) contains a colon, so a colon-alone test flags every local-model host
  forever — a permanent false alarm is worse than no gate. The tells are a colon
  followed by a provider-qualified `org/model` id, or the value carrying this
  host's own `model.provider` as a prefix; assert the tag case in the tests.

## Verify end-to-end (mandatory)

1. Spawn a real one-shot: `hermes chat -q "Reply with exactly: OK"` — note the
   `Session:` id in output.
2. Confirm the recorded model:
   `sqlite3 ~/.hermes/state.db "SELECT id, model, billing_provider,
   billing_base_url FROM sessions WHERE id='<session-id>';"`
   ⚠️ sessions table has `model` + `billing_provider` — there is NO `provider`
   column; `billing_provider` is the provider.
   ⚠️ `model` records the id the session was CONFIGURED with, NOT proof the
   provider accepted it: a row naming a routing label or an unknown id can have
   400'd on every turn and run a fallback model underneath.   Prove ACCEPTANCE
   from the log: the ABSENCE of `Fallback activated` for that id in the window,
   plus a rejection grep that must not name it
   (`grep -nE 'is not a valid model ID|Provider returned error' ~/.hermes/logs/agent.log`).
   ⚠️ Do NOT read the `conversation turn: session=<id> model=<model>` line as
   what RAN — it prints the session's CONFIGURED model, so a turn that 400'd and
   rode the fallback still logs the configured id (seen: turn lines naming the
   configured model in the same seconds as `Fallback activated: <configured>
   → <actual>` lines for that same turn). Prove ACCEPTANCE two ways, preferring
   the second: `Fallback activated: <configured> → <actual>` (its arrow is a
   UNICODE `→`, U+2192 — a grep for ASCII `->` matches none of them), and the
   per-turn `Turn ended: reason=… model=<SERVED> api_calls=… session=<id>` line,
   which POSITIVELY records the model that ANSWERED that turn. Pair it with the
   `conversation turn:` line for the same session: configured != served means the
   configured model did not serve, which is the acceptance verdict and needs no
   error-text regex. The pair also yields turn DURATION (end minus start) — the
   only per-model latency we own, since provider percentiles are auth-only.
3. Re-check the cron overrides still pin the cron-only model where intended.

## Provider latency / "why is it slow, what should we switch to" triage

When the complaint is model SLOWNESS or a request for better models (not a
wrong-model regression), separate the THREE layers before concluding anything:

1. **Platform health** — check the provider's status page first.
   OpenRouter: `https://status.openrouter.ai/` (Datadog). "All Systems
   Operational" + no incidents in the recent window means the platform is not
   the failure; per-endpoint load on the specific model's provider is then the
   prime suspect. Status pages cover gateway + chat completions, not per-model
   hosts.
2. **Network latency to the gateway** — BOTH the platform and your config could
   be fine while the box is far or congested.
   ```bash
   curl -s -o /dev/null -w "ttfb=%{time_starttransfer}s total=%{time_total}s http=%{http_code}\n" \
     --max-time 60 https://openrouter.ai/api/v1/models
   ```
   A TTFB around 60–100ms is normal for OpenRouter from a home/cloud box;
   that rules out a routing problem and isolates model-side throughput.
3. **Model-side comparison** — for prices/context across candidates, hit the
   public model catalog (no auth needed):
   `curl -s https://openrouter.ai/api/v1/models` → `jq '.data[] | select(.id=="<id>") | [.id, (.context_length|tostring), .pricing.prompt, .pricing.completion]'`.
   ⚠️ **Ranking candidates by measured speed needs AUTH; the FREE feed gives you
   AVAILABILITY instead — and a null must not be read as "no such data".** The
   `/models` catalog's `top_provider.throughput` / `.latency` are null for many
   ids, and the per-model `/endpoints` feed's `latency_last_30m` /
   `throughput_last_30m` (p50/p75/p90/p99 objects; latency = time to first token)
   are populated ONLY when the call is AUTHENTICATED — unkeyed they come back
   null for every upstream, which is a property of the CALL, not evidence that
   the provider publishes nothing. `status` and `uptime_last_5m/30m/1d` need no
   auth and are the free performance fact: never author a "switch to X it's
   faster" claim from an unauthenticated feed, and assert availability instead.
   Field semantics, the reading that matters (DISTINCT PROVIDERS, never the
   endpoint count) and how they arm a selection gate:
   `references/model-upstream-servability.md`.

**Measuring live latency requires the API key — and it is NOT in config.yaml.**
`~/.hermes/config.yaml` carries `provider: openrouter` + `base_url` but
references the key only as the env var `OPENROUTER_API_KEY` in a comment; it is
not inline and not in the gateway's systemd env file. The actual key lives in
`~/.hermes/auth.json` (Hermes auth store). Reading a credential file to fire
live probes is a consent-gated action — ask the operator before pulling it, and
report the status/network/catalog findings (all unauth'd) while that's pending
decision.

## Deprecated .env settings warning

- The runtime warning comes from `warn_deprecated_cwd_env_vars`
  (hermes_cli/config.py) and checks **process env** for `MESSAGING_CWD` /
  `TERMINAL_CWD` — it can false-positive on Hermes's own launch-dir bridge
  (the message says "found in .env" even when the var came from Hermes).
- Authoritative check = `hermes doctor`, which scans the **.env file**
  (`hermes_cli/doctor.py` `_DEPRECATED_ENV_VARS`): `TERMINAL_CWD` →
  `terminal.cwd`, `MESSAGING_CWD` → `terminal.cwd`,
  `HERMES_TOOL_PROGRESS` / `HERMES_TOOL_PROGRESS_MODE` → `display.tool_progress`,
  `QQ_HOME_CHANNEL` / `QQ_HOME_CHANNEL_NAME` → `QQBOT_HOME_CHANNEL[_NAME]`.
- `hermes update` auto-comments deprecated .env entries. Check for ACTIVE
  leftovers with:
  `grep -nE '^(TERMINAL_CWD|MESSAGING_CWD|HERMES_TOOL_PROGRESS|HERMES_TOOL_PROGRESS_MODE|QQ_HOME_CHANNEL|QQ_HOME_CHANNEL_NAME)=' ~/.hermes/.env`
- Migration target `terminal.cwd` is PER-PROJECT: keep `terminal.cwd: .`
  (launch dir) on multi-project hosts. Do NOT set a global project path just
  to silence the warning.

## Pitfalls

- state.db sessions table: `billing_provider`, not `provider` (query error).
- The `.env` deprecated entry may already be commented by a recent `hermes
  update` — verify before "removing" anything; an empty grep result means the
  warning is already neutralized.
- model ids evolve: verify the exact id against
  `grep -n '"model"' ~/.hermes/cron/jobs.json` and `~/steadfaste-fleet/.env`
  before assuming a shorthand ("deepseek-flash" = `deepseek-v4-flash`).

  2026-08-18 deepseek-chat regression: investigation trail + verification
  output.
