# DRY the Reviewer Model — Single Consistent Source

> For Hermes: TDD, one slice per commit, no `--no-verify`. Pro-reviewed before implementation.

**Goal:** The canonical reviewer model (`deepseek/deepseek-v4-pro-0813`) is currently
hardcoded in 5+ places. Making it DRY means the reviewer model is pulled from ONE
consistent source, so changing it (e.g. to `xiaomi/mimo-v2.6-pro` per the plan-reviewer
A/B) is a one-line edit that every surface picks up.

**The consistent variable ALREADY exists:** `ADVERSARIAL_REVIEWER_MODEL` (env) — every
surface reads it. The DRY gap is the FALLBACK DEFAULT string, independently hardcoded in:
1. `mcp-servers/loop-gov-mcp.py` `REVIEWER_MODEL_DEFAULT` (line 4649) — the close-gate
   (enforcement core).
2. `ops/scripts/orch-bus/adversarial-review.py` — 3 sites (lines 179, 454, 504).
3. `mcp-servers/deepseek-review-mcp.py` `DEFAULT_MODEL` (line 66).
4. `ops/scripts/manage/reviewer-agent-pi.sh` `MODEL=...:-openrouter/deepseek/...` (line 21)
   — NOTE: wrong prefix (`openrouter/`), a latent inconsistency DRY will surface.
5. `ops/scripts/manage/fleet_doctor/checks.py` (lines 3167-3171) — reads
   `REVIEWER_MODEL_DEFAULT` from loop-gov-mcp source via regex.

## Design: anchor in fleet_lib/models.py (the established model-config home)
`fleet_lib/models.py` is deployed to `scripts/fleet_lib/models.py` and already resolves
model env vars via `get_model(env_var, default)`. It is the natural single source.

**New in `fleet_lib/models.py`:**
```python
REVIEWER_MODEL_DEFAULT = "deepseek/deepseek-v4-pro-0813"  # canonical reviewer

def get_reviewer_model() -> str:
    """Resolve the reviewer default: env ADVERSARIAL_REVIEWER_MODEL, else canonical."""
    return get_model("ADVERSARIAL_REVIEWER_MODEL", REVIEWER_MODEL_DEFAULT)
```

**Each surface resolves through it:**
- `adversarial-review.py`: replace the 3 hardcoded default literals with
  `get_reviewer_model()` (import fleet_lib.models; the script already can — deployed
  side-by-side in scripts/).
- `deepseek-review-mcp.py`: `DEFAULT_MODEL = fleet_lib.models.get_reviewer_model()` at
  load, or resolve per call. Keep `DEFAULT_MODEL` name (public in tool schemas) but bind
  it to the shared source.
- `reviewer-agent-pi.sh` (bash — can't import python): source the value. Options:
  (a) read `ADVERSARIAL_REVIEWER_MODEL` from `~/steadfaste-fleet/.env` if set, else keep
  a shell default — but that re-hardcodes. (b) generate the default into the env on
  deploy. Simplest honest fix: honor env `ADVERSARIAL_REVIEWER_MODEL` first, and fix the
  wrong `openrouter/` prefix so at least the fallback matches the canonical id. Full DRY
  for bash = document that bash surfaces read env, python surfaces read fleet_lib.
- `loop-gov-mcp.py` (enforcement core): keep `REVIEWER_MODEL_DEFAULT` as the canonical
  literal (the doctor reads it via regex AND it is already correct), but derive it from
  fleet_lib: `from fleet_lib.models import REVIEWER_MODEL_DEFAULT` — keeping the name so
  the doctor regex `^REVIEWER_MODEL_DEFAULT\s*=\s*"..."` still matches the assignment,
  OR update the doctor to read fleet_lib. Preference: keep the loop-gov literal as the
  single canonical assignment AND add a comment naming fleet_lib as the co-source —
  but that leaves two literals. The clean single-source choice is fleet_lib; update the
  doctor check to read `get_reviewer_model()` from fleet_lib instead of the regex.
- `fleet_doctor/checks.py`: change the `REVIEWER_MODEL_DEFAULT` regex read to resolve
  from `fleet_lib.models.REVIEWER_MODEL_DEFAULT` (import + read, fail-closed if missing).

## The doctor is a fifth consumer — include it
The doctor currently regex-reads loop-gov-mcp. If loop-gov imports the constant from
fleet_lib, `_rv_m is None` → the check goes WARN/COULD NOT VERIFY. So the doctor MUST be
updated in the SAME change: read the canonical value from fleet_lib, not the regex.
This is the classic "wire all consumers in the same commit" rule (P14).

## Tests
- `tests/test_dry_reviewer_model.py` (NEW): hermetic —
  1. `get_reviewer_model()` returns `REVIEWER_MODEL_DEFAULT` with env unset, and honors
     `ADVERSARIAL_REVIEWER_MODEL` when set.
  2. `adversarial-review.py` `_call_model` body carries `REVIEWER_MODEL_DEFAULT` as the
     fallback model when env unset (extend `test_plan_reviewer_budget` request-body
     capture to assert the model too).
  3. Every hardcoded `deepseek-v4-pro-0813`/`deepseek-v4-pro-0813` literal outside
     fleet_lib + docs is GONE (grep the consumed surfaces), proving the DRY.
  4. `reviewer-agent-pi.sh` no longer carries the wrong `openrouter/` prefix (grep).
- Discrimination: test 3 must FAIL on the pre-change tree (literals still present).

## Files changed
- `ops/scripts/fleet_lib/models.py` (+REVIEWER_MODEL_DEFAULT, +get_reviewer_model)
- `ops/scripts/orch-bus/adversarial-review.py` (3 sites → get_reviewer_model)
- `mcp-servers/deepseek-review-mcp.py` (DEFAULT_MODEL → shared)
- `mcp-servers/loop-gov-mcp.py` (REVIEWER_MODEL_DEFAULT → imported, name preserved)
- `ops/scripts/manage/reviewer-agent-pi.sh` (env-first + fix prefix)
- `ops/scripts/manage/fleet_doctor/checks.py` (doctor reads fleet_lib, not regex)
- `tests/test_dry_reviewer_model.py` (NEW)
- `.env.example` + docs/env-vars.md, reviewer-transport.md (mention the single source)

## Verification
1. `python3 tests/test_dry_reviewer_model.py` — GREEN on new, RED on pre-change.
2. Existing reviewer suites still pass: test_reviewer_budget, test_plan_reviewer_budget,
   test_reviewer_backends, test_reviewer_tier_wiring, test_reviewer_model_default.
3. Doctor "Adversarial reviewer model config" PASSes after deploy.
4. fleet-update deploys; adversarial gate A4 on changed .py (A2 .sh).

## Risks
- **The doctor must be updated in the same commit** or it fails closed (regex can't see
  an import). This is the #1 breakage risk — the reason the plan includes it explicitly.
- Bash can't import python; perfect DRY for .sh is impossible without a deploy-time env
  generation. Honest scope: .sh reads env `ADVERSARIAL_REVIEWER_MODEL` and the fallback
  is corrected, but the canonical LITERAL still lives in bash (documented). Fully DRY for
  bash = generate the default from fleet_lib at deploy (a later slice).
- Changing loop-gov `REVIEWER_MODEL_DEFAULT` from a literal to an import touches the
  enforcement core — keep the NAME identical and add a failsafe (if fleet_lib import
  fails, fall back to the literal) so a missing fleet_lib never breaks the close gate.
