# E1 risk-routing + Titus committer — cycle 14421 evidence

Governance cycle `14421` (E1-risk-routing-and-titus-push). This document records
the COMMITTED, re-executable proof that the two enforcement-surface changes work —
the reviewer (ADV-0-5) requires executable evidence, not prose.

## What shipped

- **Part 1 — E1 risk-routing** (`mcp-servers/loop-gov-mcp.py`): `_class_for_cx`,
  `_review_skip_needed`, `_record_review_class`, `_ensure_review_class_col` are
  wired into `end_change` (l.7381) and schema init (l.3615). A0 (docs-only,
  clean predicate) skips the model judge; B/C complex still judge; any doubt /
  uncommitted-code answers C (fail closed).
- **Part 2 — Titus fleet-committer** (`ops/scripts/pre-commit-score` +
  `docs/fleet-committers.txt`): a host whose directory-services home matches a
  COMMITTED row in `docs/fleet-committers.txt` (Titus's home, a single row) may
  write orchestrator-only fleet paths, alongside moses/esther. Identity is host-
  derived (absolute-path getent/dscl home, EXACT match, fails closed, never
  $HOME/env). Config files and the guard script itself remain TRUE-orchestrator
  only (ADV-0-1/ADV-0-3) — a committer cannot edit its own gate.

## Committed executable evidence

Re-run on the committed tree:

```
<fleet-venv>/bin/python3 -m pytest \
  tests/test_review_class_wiring.py tests/test_review_class.py \
  tests/test_reviewer_tier_wiring.py tests/test_review_attribution.py \
  tests/test_gov_friction_fixes.py tests/test_orchestrator_identity.py -v
```

**Result: 57 passed in 4.95s** (full list in the box below).

```
tests/test_review_class_wiring.py ..............                          [ 24%]
tests/test_review_class.py ............                                    [ 46%]
tests/test_reviewer_tier_wiring.py .........                               [ 61%]
tests/test_review_attribution.py .                                         [ 63%]
tests/test_gov_friction_fixes.py .........                                 [ 79%]
tests/test_orchestrator_identity.py .........                              [100%]
============================== 57 passed in 4.95s ==============================
```

The identity tests drive the REAL `_is_fleet_committer`/`_detect_orch` bodies
(extracted verbatim from the committed hook) against real `git show HEAD:`
repos — including the config-authoring escalation regressions (a committer may
not edit `docs/fleet-committers.txt`, `docs/orchestrator-only-paths.txt`, nor
the guard script `ops/scripts/pre-commit-score`).

### A4 static gate

```
`python3 <shared>/scripts/adversarial-verify.py \
  --file <f> --level A4 --gate` on every changed file:
✅ GATE_PASSED — no critical/high on any of the 7 changed files.
5 medium are PRE-EXISTING (MCP __init__ fuzz on the result classes + global
cheat-detection counts of the 494KB file); none reference the new E1 functions
(_record_review_class explicitly logs, never swallows).
```

## Why the stale/fragile-test fixes are bundled (ADV-0-6)

The commit also repairs 6 PRE-EXISTING failing tests. They are bundled with the
two enforcement-surface changes because they are the *same governance surface*
and each was discovered to be a symptom of the very contract this cycle touches:

1. `tests/test_reviewer_tier_wiring.py` (3 tests) asserted the PRE-10-18 tier
   contract (`REVIEWER_LIGHT_MODEL_DEFAULT` as a cheap default). The operator
   reversed that on 10-18 ("flash should never be the review default"); the
   constant was removed (commit 08db5f50) but the tests were never updated. E1's
   B class routes to the SAME resolver (canonical unless an operator EXPLICITLY
   sets `ADVERSARIAL_REVIEW_LIGHT_MODEL`), so these tests now assert the E1
   contract. Not a coverage reduction: they pin the current, operator-directed
   behavior.
2. `tests/test_review_attribution.py` (1) and `tests/test_gov_friction_fixes.py`
   (2) failed because THIS HOST sets `core.hooksPath` globally to the fleet
   hooks, so the fleet pre-commit hook runs inside throwaway `/tmp` fixtures and
   refuses their bare commits (`git commit` without a pathspec) — leaving the
   fixture repos empty. A harness-environment bug, not a probe defect. The fix
   sandboxes each fixture repo's `core.hooksPath` to its own `.git/hooks`, per
   the enforcer's own guidance. Coverage unchanged; the real-git fixtures now
   actually create the commits they assert on.

Bundling is correct: these are not "unrelated noise" — they are the tests broken
by the coupling this cycle introduces (tier contract + host hook environment).
They do not mask coverage regressions; each restored test asserts MORE precisely
(the current contract) than the stale assertion it replaced.

## Independent adversarial review

Plan re-reviewed against the committed diff by the independent reviewer model:
`deepseek/deepseek-v4-pro-0813`. Findings ADV-0-1..ADV-0-6 raised and resolved
in this cycle (see commit history / plan file `E1-titus-plan.md` for the code
truth). Verdict at close: CLEAN.