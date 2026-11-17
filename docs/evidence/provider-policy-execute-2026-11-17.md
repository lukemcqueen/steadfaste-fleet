# Provider exclusions: "registering is not executing" closed (2026-11-17)

Cycle 14945 · task `provider-policy-execute`

## The gap this closes

`cross-agent-design` rule 9: *a deploy that COPIES a per-host adapter into the
scripts directory has wired NOTHING — wiring is a step that RUNS.*
`ops/scripts/manage/provider-policy.py` (the projection that writes
`provider_routing.ignore` through each harness's own API) was REGISTERED in the
deploy map but never INVOKED, and the doctor had no row for it — so the Amazon
Bedrock exclusion existed only where someone had run `--write` by hand.

## The change

| File | Change |
|---|---|
| `ops/scripts/manage/fleet_doctor/router_layer.py` | `+ check_provider_policy()` (with `live_provider_ignore`, `_policy_exclusions`, `_load_provider_policy`) |
| `ops/scripts/manage/fleet_doctor/cli.py` | import + register `check_provider_policy` in the normal `all_checks` pass |
| `ops/scripts/fleet-update.sh` | post-deploy step RUNS `provider-policy.py --write` (idempotent projection), fail-soft |
| `tests/test_doctor_provider_policy.py` | 7 tests driving the real check against redirected seams |

No new file needed a deploy-map entry: `router_layer.py` (line 584) and `cli.py`
(line 575) were already registered; `provider-policy.py` at line 1056.

Severity: **FAIL** when the host has Hermes and the live config is MISSING an
exclusion the artifact requires (it will still route to the excluded upstream);
**WARN** for a declared gap (a harness present with no verified knob); **PASS**
when live matches. Fail-soft in the deploy — a projection that cannot verify is
WARNed and never reported as agreement.

## Verification (captured this session, this host)

1. Syntax / compile
   - `bash -n ops/scripts/fleet-update.sh` → OK
   - `python3 -m py_compile .../fleet_doctor/router_layer.py .../fleet_doctor/cli.py` → OK
2. Tests
   - `pytest tests/test_doctor_provider_policy.py tests/test_fleet_doctor_router_layer.py tests/test_provider_policy.py -q` → **24 passed**
3. Static adversarial gate
   - `adversarial-verify.py --file router_layer.py --level A4 --gate` → GATE_PASSED (no critical/high)
   - `adversarial-verify.py --file cli.py --level A4 --gate` → GATE_PASSED
   - `adversarial-verify.py --file fleet-update.sh --level A2 --gate` → GATE_PASSED
4. The new row against LIVE host state (repo doctor)
   - `Provider exclusions — excluded ['amazon-bedrock'] · live ignore ['amazon-bedrock'] — declared gap(s) ['claude-code', 'pi'] present on this host: no verified provider-exclusion knob, so the policy is NOT enforced for them`
5. The deploy RUNS the projection (the rule-9 half)
   - `Provider exclusions: projected onto 1 present harness(es)`
6. The deploy clears the "unsynced" FAILs
   - before: `3 fail` (Checksum mismatch: fleet-update.sh / cli.py / router_layer.py)
   - after: `0 fail` · `Overall: WARNING (511 pass · 16 warn)`
