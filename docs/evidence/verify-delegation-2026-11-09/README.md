# Verify-delegation capability (v017) — GREEN evidence (cycle 14378)

Date: 2026-11-09 (KST)
Cycle: 14378 `verify-delegation-capability`
Plan review: adversarial_reviews row 14378, reviewer deepseek-v4-pro-0813,
verdict **CLEAN** (superseded the FINDINGS round that caught the
check_transition contradiction — ADV-14378-1, fixed in the plan before build).

## Red → Green (TDD)

- RED: `bash tests/test-tasks-schema.sh` with the AC-L1-27 section added and
  v017 absent → the v017 block failed on the missing
  `authorize_verify`/`verify_grants`/`verify_grant_active` surface
  (transcript: run at commit base, not quoted here; the failing lines were
  the self-authorize/grant-flow checks returning empty).
- GREEN: same battery with v017 present → **150 passed, 0 failed**.
  Transcript committed beside this file: `battery-150-green.txt`.

## Verification (all real runs, this cycle)

- `bash tests/test-tasks-schema.sh` → 150 passed / 0 failed
  - sha256 of the transcript: `340cdaa6c034cae811b3b0a2fa0fa561dc9387193d39a601a66eb4f5c0ac2336`  (computed over the committed file — `sha256sum` of the run output, unpiped capture)
- `python3 -m pytest tests/test_task_db_v4.py tests/test_task_mcp_descriptions.py -q`
  → 13 passed (4 new verify-grant CLI tests + the description guard, whose
  docstring now names the v017 mechanism)
- `bash -n` clean: ops/scripts/fleet-update.sh, tests/test-tasks-schema.sh
- `py_compile` clean: ops/scripts/manage/task-db.py, mcp-servers/task-mcp.py
- adversarial-verify A4 `--gate` on all six changed files → GATE_PASSED
- register map: v017 line added to fleet-update.sh (the register map IS the
  deploy — an unregistered migration never reaches hosts)

## What the battery proves (AC-L1-27, 13 checks)

1. worker verify refused without a grant (widened EXECUTE is not authorization)
2. ungranted worker raw UPDATE still refused (v014 trigger intact)
3. worker CANNOT self-authorize (authorize_verify → f, no row)
4. authorize_verify validation: ghost role / empty note / non-review slice →
   RAISE, no row recorded
5. orchestrator authorize_verify records the grant; worker verify → completed;
   grant CONSUMED
6. consumed grant refuses a second verify (one-shot)
7. grant is slice-scoped (other slice refused)
8. expired grant refuses (manufactured row, both timestamps past — the
   `expires_at > created_at` CHECK forbids authorize_verify from wanting one)
9. granted worker raw UPDATE passes the trigger (slice-scoped, grant retained —
   consumption is verify_slice's job; row cleaned up by the battery)
10. reject with grant works + consumes
11. is_orchestrator == tasks_verifier membership (de-named capability; esther t
    / probe f)
12. worker calls verify_grant_active (INVOKER-context helper) without
    permission errors; verify_grants table unreadable (no grants to roles)
13. migration grant loops carry unspaced role names (static guard — a spaced
    typo silently skips every IF EXISTS grant)

## Live-host migration (after deploy)

`fleet-update.sh` applies v017 to the host's mycortex DB (version-gated
runner): schema_version 16 → 17; tasks_verifier role created; existing
mycortex_reader_esther granted tasks_verifier membership; the six-profile
EXECUTE widening lands where those roles exist.
