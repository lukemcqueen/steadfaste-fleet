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
     note; it was NOT authored in the backport range that landed it. -->

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

## Fix

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
  drive it and verify with `router-pools.py --selftest`.
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
  `fleet_doctor.jev._current_model` merely TOLERATES a colon in `default`, so
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
   400'd on every turn and run a fallback model underneath. Prove ACCEPTANCE
   from the log (`grep -n 'is not a valid model ID' ~/.hermes/logs/agent.log`
   must not name that id) and read the model a turn really used off its own log
   line (`conversation turn: session=<id> model=<model>`).
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
   ⚠️ **The catalog does NOT expose throughput/latency for these models** —
   `top_provider.throughput` / `.latency` come back null for many ids, so you
   CANNOT rank candidates by measured speed from that feed. Real per-token
   latency needs an AUTHENTICATED probe against the live endpoint; do not
author a "switch to X it's faster" claim from the catalog alone.

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
