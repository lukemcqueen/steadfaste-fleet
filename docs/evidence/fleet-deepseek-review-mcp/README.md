# Fleet-owned DeepSeek review MCP server — landing evidence

Date: 2026-11-11. Cycle: `fleet-deepseek-review-mcp` (#14466).
Operator: Esther. Approved by Luke (owner decision 2026-11-11) — this reverses the
earlier "never copy the server into steadfaste-fleet" rule documented in
`docs/external/mcp-deepseek-review.md`.

## What landed

- `mcp-servers/deepseek-review-mcp.py` — NEW fleet-owned DeepSeek adversarial-review
  MCP server (default `deepseek/deepseek-v4-pro-0813`, the cost-controlled reviewer).
- `ops/scripts/fleet-update.sh` — 1-line register adding the server to the deploy map.
- `docs/external/mcp-deepseek-review.md` + `ops/install/harnesses/pi/ADDON-deepseek-mcp.md`
  — source-of-record updated to the fleet copy; default-model rule (0813, never plain
  v4-pro/chat-v3) stated for every copy.
- `tests/test_deepseek_review_mcp.py` — 13 hermetic tests.

Titus's submitted code was NOT landable verbatim: two `[SECRET]` redaction artifacts made
it a syntax error, it targeted the old lowlevel MCP SDK (the fleet uses `mcp.types`),
and it named a ruling dated 2026-11-10 that did not exist on record. All three were
reconstructed/grounded during landing.

## Evidence runs (command + rc)

### Hermetic test suite
```
env -u PYTHONPATH -u PYTHONHOME ~/.steadfaste-fleet/venv/bin/python3 -m pytest \
  tests/test_deepseek_review_mcp.py tests/test_shell_portability.py -q
# 16 passed in 2.63s   (13 new + 3 shell-portability sibling)   rc=0
```

### Model pin + syntax
```
grep -c "deepseek-v4-pro-0813" mcp-servers/deepseek-review-mcp.py        => 2
grep -nE 'deepseek-v4-pro"|deepseek-chat-v3"' mcp-servers/deepseek-review-mcp.py  => no banned defaults
python3 -m py_compile mcp-servers/deepseek-review-mcp.py                rc=0
```

### Adversarial static gate (A4, mandatory for mcp-servers/)
```
env -u PYTHONPATH -u PYTHONHOME <venv>/python3 ops/scripts/quality/adversarial-verify.py \
  --file mcp-servers/deepseek-review-mcp.py --level A4 --gate
# GATE_PASSED — 0 findings, rc=0
```

### Real-process stdio handshake (spawn server, drive initialize/tools/call)
`stdio-smoke.txt` — PASS initialize; PASS tools/list [review, ask_deepseek,
list_available_models]; PASS empty-plan returns isError; PASS unknown tool returns
isError. rc=0.

### LIVE end-to-end OpenRouter call (key resolved from .env, real model)
`live-e2e.txt` — `<!-- DeepSeek response (deepseek/deepseek-v4-pro-0813) in 0.9s --> PROBE_OK`,
rc=0. Confirms the fleet copy reaches real OpenRouter under the pinned model with the
resolved key.

## Status notes

- The pi `deepseek` registration stays pointed at the product repo (deliberate — the fleet
  copy is the owned source/fallback, not what the MCP client currently loads).
- OpenRouter prices (2026-11-11): v4-pro-0813 input $0.111/M vs plain v4-pro ~$0.90/M —
  the 0813 pin is the input cost lever; note 0813 output ($4.44/M) is HIGHER than v4-pro
  ($1.50/M), so the pin optimises the input-heavy review surface.