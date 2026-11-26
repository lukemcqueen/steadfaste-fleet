# OpenRouter account provider policy, and how it breaks the session default

Two different causes produce a 404 from OpenRouter, and they need different fixes.
Read the error TEXT before concluding anything.

## 1. Retired model — genuinely gone

Absent from the live catalogue (`GET https://openrouter.ai/api/v1/models`).
Nothing will make it resolve; repoint. Verify by enumerating the catalogue, not by
reading the 404 alone.

## 2. Account provider policy — the model exists but cannot be served HERE

The account enforces an **allowed-providers allow-list**, and separately an
ignored-providers list. Two distinct messages:

- `No allowed providers are available for the selected model. Providers serving
  <model>: <a,b>, but your account's allowed-providers setting permits only: <c,d>`
  → every provider for that model sits OUTSIDE the allow-list. **Hard 404, always.**
- `All providers have been ignored ... settings/privacy`
  → the ignored-providers filter excluded them.

**A model with at least one permitted provider can still 404 INTERMITTENTLY** when
routing resolves to an excluded one. So a 404 is NOT proof of retirement, and
"I see this often" (rather than "always") is the tell.

### Probe servability; never infer it

A 1-token completion costs ~$0.00001 and takes ~2-3s — cheap enough to run
before trusting any model as a default:

```python
body = json.dumps({"model": m, "max_tokens": 1,
                   "messages": [{"role": "user", "content": "hi"}]}).encode()
req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
```

Read the key in-process from `~/steadfaste-fleet/.env`; never print it. Measured
2026-11-26: `qwen/qwen3-coder-30b-a3b-instruct` OK, `deepseek/deepseek-v4.1-flash`
OK, `deepseek/deepseek-v4-flash` OK, `qwen/qwen3-coder-flash` **hard 404** (served
only by alibaba, outside the allow-list).

## The session default: source priority and a hidden coupling

`ops/scripts/fleet_lib/models.py` resolves the session/cron model in this order:

1. process env / env-dict (`SESSION_MODEL` / `SESSION_PROVIDER`) — one-off override
2. per-host `~/steadfaste-fleet/.env` — same two keys
3. `config/router-models.json` → `baseline` — the fleet canonical

`materialize-fleet-model.sh` is a thin caller that writes the result into
`~/.hermes/config.yaml` (`model.default` + `model.provider`). The id must be BARE
(`deepseek/x`, never `openrouter:deepseek/x`) — a routing label is answered
`is not a valid model ID` (HTTP 400) and the host silently runs a fallback on
every session. The materializer prints `label: false` and `source: "env"` when the
per-host override won.

### Changing `baseline` is NOT a one-line edit

`baseline` is simultaneously (a) the fleet session default and (b) the anchor for
the pool price ceilings — `simple` = 0.5x baseline, `medium` = 1.0x
(`policy.max_multiple_of_baseline`), against `baseline_price_usd_per_m`.

- A **cheaper** baseline TIGHTENS the ceilings and can push existing pool heads
  over the limit → `tests/test_router_pools.py` fails.
- A **dearer** baseline relaxes them, so existing heads stay valid.
- After any baseline change re-derive the ceilings and run
  `ops/scripts/manage/router-pools.py --selftest`.

A per-host `SESSION_MODEL` override is the safe way to move one host off a broken
default without touching fleet policy — it wins over `baseline` by priority 2.

## The systemic root cause

Selection reads catalogue **prices** and never tests whether anything will
**serve** the model. A policy-blocked model therefore still looks like the cheap
vetted pick and gets re-selected — including by the materializer, which reports
`no-op — already at fleet target` while pinning a default that 404s. Servability
must be probed at selection time, not assumed.
