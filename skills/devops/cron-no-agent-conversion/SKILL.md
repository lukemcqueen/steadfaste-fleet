---
name: cron-no-agent-conversion
version: 1.0.0
category: devops
description: "Convert LLM-driven Hermes agent crons to no_agent scripts with targeted API calls. Maximizes deterministic Python work, uses LLM only for the single creative task the model uniquely provides."
author: Steadfaste Fleet
license: MIT
metadata:
  hermes:
    tags: [cron, no-agent, automation, script, conversion, deepseek, ollama]
---

# Cron → no_agent Script Conversion

A class-level pattern: replace full agent-loop crons (which run an LLM agent with tools each tick) with lightweight `no_agent=true` scripts that handle deterministic orchestration in Python and make a single API call for creative work.

## When to Convert

Convert when the cron's LLM produces **5-50 token useless output** (1 API call, no real work). This is the signature of a small model struggling with a multi-step agentic workflow.

Small local models like `qwen2.5:3b` (3B params) are reliable for:
- ✅ Single-shot tasks (code gen, classification, short summaries)
- ✅ Deterministic transformation of structured input
- ❌ Multi-step tool loops (search → read → decide → act) — they lose the thread

If your cron's agent calls 3+ tools per tick but the output is boilerplate,
it's a candidate. Convert it.

## The Conversion Pattern

### Before (agent cron)

LLM-driven cron: the prompt asks the agent to gather data, reason, and
deliver — and the agent burns tokens on tool calls every tick.

### After (no_agent script)

```python
#!/usr/bin/env python3
"""Cron script: gather data deterministically, ONE LLM call for the summary."""
import json, subprocess, os, urllib.request

# 1. Deterministic data gathering (no LLM)
logs = subprocess.check_output(["grep", "-E", "ERROR", "/var/log/nginx/error.log"])
lines = logs.decode().splitlines()[-50:]

# 2. Single creative LLM call
payload = {
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": f"Summarize these nginx errors:\n{chr(10).join(lines)}"}],
    "max_tokens": 300,
}
req = urllib.request.Request(API_URL, json.dumps(payload).encode(), headers={"Authorization": f"Bearer {KEY}"})
summary = json.loads(urllib.request.urlopen(req).read())["choices"][0]["message"]["content"]

# 3. Deterministic delivery
print(summary)  # cron delivers stdout verbatim
```

### Cron registration

```bash
# Script lives at ~/.hermes/scripts/<name>.py
cronjob action=create
  name=local-nginx-error-summary
  schedule="0 6 * * *"
  no_agent=true
  script=~/.hermes/scripts/local-nginx-error-summary.py
```

Name it per the fleet convention: `agent-` (all agents) or `local-` (this
machine only) — see `cron-job-management`.

## Decision Rules

| Condition | Keep agent cron | Convert to no_agent |
|-----------|----------------|---------------------|
| Needs tool access (terminal, web, files) mid-loop | ✅ keep | ❌ |
| Needs skills / multi-step reasoning | ✅ keep | ❌ |
| Deterministic pipeline + one summary/decision at the end | ❌ | ✅ |
| Output is always same shape (status, digest, watchdog) | ❌ | ✅ |
| Small model + many tool calls + boilerplate output | ❌ | ✅ |

### State-transition watchdog shape (the dominant conversion)

When the cron's job is "read state, report only on change" (a monitor, a
watchdog, a "report only if something materially changed" job), convert it to a
fingerprint-diff no_agent script — it burns zero tokens on quiet ticks:

1. Project the MATERIAL state from each input file (drop volatile fields:
   timestamps, last_run/status, transient flags). What counts as material is
   the job's mandate — for a model-availability monitor it is prices/tiers/
   candidates/violations and config model pins; a jobs file's `updated_at` is
   NOT material, a candidate list change IS.
2. Hash the canonical JSON (`json.dumps(sort_keys=True)`); store
   `fingerprint|state_json` in a state file under `~/.steadfaste-fleet/state/`.
3. On each run: compute the new fingerprint; equal → empty stdout, exit 0
   (silent, nothing delivered); different → print the diff of what changed and
   rewrite the state file. First run writes the baseline.

## Verification After Conversion

```bash
# Run the script once manually
python3 ~/.hermes/scripts/local-nginx-error-summary.py

# Confirm output shape is stable
# Then in Hermes: cronjob action=run <job_id> → confirm delivery
```

## Pitfalls

- ❌ **Keeping LLM in the hot path** — if the deterministic part can compute
  the answer, don't pay for an LLM call every tick.
- ❌ **Converting a cron that genuinely needs tools** — tool loops belong in
  agent crons; forcing them into scripts creates brittle subprocess chains.
- ❌ **Secrets in the script** — read API keys from a file
  (`KEY = open(os.path.expanduser("~/.secrets/key")).read().strip()`), never hardcode.
- ❌ **Manual test ≠ scheduler test** — after fixing a cron, run
  `cronjob action=run job_id=<id>` so the scheduler's recorded status
  refreshes, then run the doctor to confirm it clears.
- **no_agent scripts must be silent on success** — empty stdout + exit 0
  delivers nothing; any stdout or non-zero exit delivers an alert.
- **Compare the fingerprint HALF of the stored line, not the whole line.** If
  the state file holds `fp|state_json`, comparing `stored == new_fp` can never
  match and every run reports a change. Partition on `|`, compare `prev_fp`
  only, and parse the state half separately for the diff.
- **The diff helper must read the SAME shape the state file stores.** A diff
  written against flat sections (model/cron/prices) fed the nested state dict
  ({config, market, jobs}) silently finds nothing and reports "changed but not
  attributable". Extract by the stored shape, then diff per section.
- **Deploy the script BEFORE updating the cron.** `cronjob action=update`
  validates the script path through the `~/.hermes/scripts` →
  `~/.steadfaste-fleet/scripts` symlink and fails "Script file not found" if
  it is not deployed. Register it in fleet-update.sh and run fleet-update.sh
  first, then point the job at `manage/<script>.py`.
- **A new production script needs its test in the SAME commit** (TDD iron-law
  gate refuses otherwise). Give the script env-var path overrides (e.g.
  `LSM_CONFIG_PATH`) so tests drive it via subprocess with tmp_path fixtures
  instead of touching the live home directory — matches the repo's existing
  test style (see tests/test_local_model_scan.py).

## Related
- `cron-job-management` — naming, installers, doctor truth source
- `cron-format-standard` — output format for LLM cron deliverables
- `cron-quality-gate` — preventing garbage cron output
