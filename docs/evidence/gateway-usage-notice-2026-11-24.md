# Gateway usage notice — evidence (2026-11-24)

Re-runnable checks for the periodic model/token/cost notice in gateway prompts.
Every command below was executed on the host; the output shown is what it printed.

## 1. Unit + integration suite

```bash
python3 -m pytest tests/test_gateway_usage_notice.py -q
```

```
22 passed in 0.61s
```

## 2. Gateway surface (no regressions)

```bash
python3 -m pytest tests -k gateway -q
```

```
304 passed, 3500 deselected in 40.19s
```

(The count moved 303 → 304 when the end-to-end hermes dispatch test was added
after the first review; both runs are the same command on the same tree.)

## 3. The real pi reader (live session, not a fixture)

```bash
python3 <REPO>/ops/scripts/fleet_gateway/pi_control.py stats \
  --session-id hc-pi-1270130526-g3 --model openrouter/deepseek/deepseek-v4.1-flash
```

```
model=... tokens=0 cost=0 context=19.872665405273438% context_tokens=52095 contextWindow=262144
```

`tokens=0` / `cost=0` is what pi reports for that session's provider; the
formatter therefore omits zero values, so the line the agent sees is:

```
[gateway-usage] model=... · context=19.9%
```

## 4. The same line through the parser and formatter

```bash
python3 -c "import sys; sys.path.insert(0,'<REPO>/ops/scripts'); \
from fleet_gateway import usage as U; \
print(U.format_notice(U.parse_usage('model=openrouter/x tokens=5127 cost=0.00086925 context=30.2')))"
```

```
[gateway-usage] model=openrouter/x · tokens=5,127 · cost=$0.0009 · context=30.2%
```

## Notes

- The hermes backend reads `sessions` in the agent's own `state.db` (read-only,
  `mode=ro`). A missing DB/table means NO notice — never a guessed number.
- The command backend runs the declared `usage_command` argv; without the three
  config keys the prompt is byte-identical to before this change.
