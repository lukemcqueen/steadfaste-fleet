# Free models on the fleet's OpenRouter key — reachability, competitiveness, vetting (2026-11-17)

Question (operator): *"go through the process of getting the free models. Let's see
if they can compete with the other open models."* — followed by *"Vetting"* (the
quality head-to-head below).

The daily model-market scan (`state/model-market.json`) had flagged OpenRouter
`$0.00/M` models as "NEW cheaper unvetted candidates" (they sort to the head of
every pool at zero price), so the question was whether they are (a) obtainable,
(b) good enough to route.

## Method (the documented process)

1. Enumerate the LIVE OpenRouter catalog and select `pricing.prompt == 0 and
   pricing.completion == 0` models (`free-model-probe.py`).
2. **A catalog listing is not proof of callability** — keyed
   `POST /api/v1/chat/completions` (`max_tokens:1`) each candidate and record the
   real HTTP outcome.
3. Head-to-head the resolvable challenger against the routed incumbent heads on a
   deterministically graded battery (`free-model-vet.py`), plus a repeated
   injection/reliability probe with a control group
   (`free-model-injection-probe.py`, run on all three models).

Scripts + hashed transcripts + raw JSON are in this directory:

- `reachability.txt` + `reachability.raw.json` — Result 1 (`free-model-probe.py`)
- `head-to-head.txt` + `head-to-head.raw.json` — first battery (`free-model-ab2.py`)
- `vetting.txt` + `vetting.raw.json` — Result 2 (`free-model-vet.py`)
- `injection-probe.txt`, `injection-probe-gpt-oss-20b.txt`,
  `injection-probe-deepseek.txt` — Result 3 (`free-model-injection-probe.py`)

The repo's existing `ops/scripts/make-evidence.sh --verify <file>` reproduces each
transcript's sha256.

## Result 1 — almost nothing free is actually obtainable

17 `$0.00/M` text-chat models exist in the live catalog. **2 resolve on this key;
15 do not.**

| Model | ctx | tools | vision | probe |
|---|---|---|---|---|
| `inclusionai/ling-3.2-flash:free` | 1,048,576 | yes | yes | **REACHABLE** |
| `openrouter/free` | 200,000 | yes | yes | **partial** — 1-token ping 200, real multi-call 404 |
| `google/gemma-4-26b-a4b-it:free`, `google/gemma-4-31b-it:free`, `nvidia/nemotron-*:free` (5), `cohere/north-mini-code:free`, `liquid/lfm-3-1.7b:free`, `baidu/ernie-5.5-flash-preview:free`, `dots-studio/dots-3-note-preview:free`, `particle-ai/particle-1:free`, `recursive/flint-v1.0:free`, `google/lyria-*-preview` (2) | — | — | — | **HTTP 404** "0 endpoints ... matching your guardrail restrictions and data policy" |
| `thinkingmachines/inkling-2-small:free` | — | — | — | **HTTP 403** "only available on agentic harnesses" |

The 404 is this account's OpenRouter **data-policy guardrail** removing the free
endpoints — the same class as the documented `qwen3.7-flash` 404, not a transient
error. `openrouter/free` is an auto-router: it answers a 1-token ping but 404s on
real calls, so it is not a usable route. Expect this to stay true fleet-wide: the
free family is blocked by policy, not by a per-model quirk.

## Result 2 — capability head-to-head (8-task battery, deterministic graders)

`free-model-vet.py`: prompt-injection resistance, tool-call argument correctness,
runnable bug-fix, strict JSON schema, 2-sentence constrained summary, long-context
needle, date math, transitive reasoning.

| model | role | score | tool-calling | latency (8 tasks) |
|---|---|---|---|---|
| `inclusionai/ling-3.2-flash:free` | challenger | **8/8** | TOOL_CALL | **54.1s** |
| `openai/gpt-oss-20b` | incumbent simple head ($0.108/M) | 7/8* | TOOL_CALL | 8.0s |
| `deepseek/deepseek-v4-flash-0731` | incumbent medium head ($0.240/M) | 8/8 | TOOL_CALL | 9.3s |

\* gpt-oss-20b's single fail was the injection task — see Result 3: that task is
noisy for it too (it flaked on the same task in an earlier run of the first
battery; N=1 per task per model is directional only).

## Result 3 — repeated injection/reliability probe WITH control group (N=6 each)

Same task, 6 runs per model: answers the injection question AND the empty-reply
question, separating two failure shapes (following the embedded instruction vs
returning no output).

| model | pass | empty replies | followed injection | verdict |
|---|---|---|---|---|
| `inclusionai/ling-3.2-flash:free` | 3/6 | **2** | 0 (one refusal quoted the word "HACKED"; a checker false positive, not a follow) | same reliability class as the incumbent it would replace |
| `openai/gpt-oss-20b` (paid simple head) | 3/6 | **3** | 0 | the current simple head is EQUALLY flaky on this task |
| `deepseek/deepseek-v4-flash-0731` (paid medium head) | 6/6 | 0 | 0 | reliability champion — zero empties |

Empty replies are a property of the task + small-model OpenRouter routes, NOT of
the free tier specifically. The free model's empty rate is no worse than the
incumbent simple head's — arguably better (2 vs 3 in this sample).

## Conclusion / recommendation

- "Getting the free models" yields **one** usable model: `inclusionai/ling-3.2-flash:free`.
- **Vetting verdict: COMPETITIVE for the simple tier.** It ties the incumbents on
  capability (8/8), resists injections on every non-empty reply, and its
  reliability is in the same class as the incumbent simple head it would replace
  (both flaky on small-model routes; deepseek is the reliability champion).
- **The real cost is latency: 5–7× slower** than both incumbents (54s vs 8–9s for
  the battery). For the mechanical simple tier that is a bounded, visible trade;
  for interactive sessions it would be felt on every turn. Free-tier rot risk also
  remains (the skill's standing warning: pin a **resolvable pair**, never a lone
  free strategy).
- **Not done here (by design):** no `config/router-models.json` change. Promoting
  a model is a policy edit (the scan deliberately never auto-adopts a candidate),
  and a promotion must survive the hard-cap / ceiling invariants and a real-job
  replay. Sample is small-N — directional, not definitive.
