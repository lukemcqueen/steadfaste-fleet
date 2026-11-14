# Governance Change Catalog — August 2026 wave

Concrete commits from the Aug 2026 governance wave (Esther's triage session
2026-08-25, triggered by "did something change with governance? we are having
issues on titus"). Use as a lookup table when a host's symptoms match an archetype
from the umbrella SKILL.md.

## Timeline (all times KST, repo ~/hermes-cortex)

| Date | SHA | Change | Host-visible effect |
|---|---|---|---|
| 08-20 | `a51f1a70` | TDD Iron Law: write-time + commit-time gates. NEW production code files (`.py/.ts/.js/.go/...`) must carry a test file in the SAME commit when the repo has test infra. Fail-closed, no bypass. | Dev agents (Titus) committing new prod files in repos with `tests/` get "TDD IRON LAW VIOLATION" blocks. |
| 08-20 | `c9744a71` | Always-section skills 10→7 (canonical gate); TDD added to always-set. | Sessions with stale manifests get "session skills not fully loaded" write blocks. |
| 08-24 13:04 | `cee06a73` | Real test pass-rate for monorepos: pre-commit previously fabricated pass-pct=100 for `apps/<svc>` layouts. Now measures via per-repo `ops/scripts/test-command.sh` opt-in or discovery; warns on measurement failure. | Project repos get honest scores instead of free 100s; commits/cycles may score lower. |
| 08-24 13:53 | `05cfd9e4` | parse_pass_pct: pytest ERRORS now count as failures ('598 passed, 15 errors' → 98, not 100). secret-leak-detector false-positive fix. pre-commit-doc-audit scoped to hermes-cortex repo only. | Lower scores on repos with test errors; fewer false leak warnings. |
| 08-24 13:58 | `5f4a19b6` | Deployed-only governance: pre-commit-score uses ONLY `~/.hermes-cortex/scripts/change-validate.sh` + `precommit_test_discovery.py` (deployed copies). No repo copies/overrides. client-av's alembic revision-length gate folded into HC change-validate.sh as SELF-SCOPING (fires on `*/versions/*.py` in any repo). | Repos resolve gates from deployed copy; missing deployed file = silent skip (governance OFF); new alembic check fires where repo had no governance. |
| 08-24 19:58 | `c1427d0d` | Deployed adversarial-verify TOON check was SILENTLY DISABLED (lib dir resolved one level too shallow; `except:pass` swallowed import error → malformed TOON produced 0 findings in deployed gate). Fix: try both candidate lib dirs. | Commits that passed the deployed gate silently now get malformed-TOON medium findings → new blocks. Symptom looks like "stricter out of nowhere" but is a fix. |
| 08-24 20:08 | `b0c3c2f8` | Doctor "Deployed gate smoke" check: executes the DEPLOYED verifier against a malformed-TOON fixture; FAILs if the finding is absent. Catches repo-works/deployed-broken. | Doctor FAILs on hosts whose deployed verifier is broken. |
| 08-24 22:16 | `c3b0410f` | agent-review-queue-sweep.py: no_agent cron, auto-closes STOP cycles >3d with clean outcome notes (mirrors feedback_accept; never touches overrides), surfaces LOOP/MOVE ON/hard-fail/PENDING. First live run closed 50/6191; 19 stale hard-fail cycles surfaced. | Old STOP cycles in the review queue get auto-closed; hard-fail cycles surface as alerts. |
| 08-25 09:37 | `42625c80` | Upstream 11 fleet-vetted devops skills (orch-skill-evaluate). | Skill-library change; deployed 09:38 (enforcer mtime matched). |

## Diagnostic shortcuts proven in this session

- Enforcer deployed mtime (`~/.hermes/plugins/governance-enforcer/__init__.py`)
  matched the 09:38 deploy of `42625c80` — mtime is a reliable "when did this host
  actually deploy" signal.
- `git log --since="2 days ago" --pretty=format:"%h %ad %s" --date=format:"%m-%d %H:%M"`
  gives KST timestamps directly — no timezone math.
- Diff deployed vs repo with the `# SOURCE:` header stripped:
  `diff <(sed 's/^# SOURCE:.*//' ~/.hermes/plugins/governance-enforcer/__init__.py) \
        <(sed 's/^# SOURCE:.*//' plugins/governance-enforcer/__init__.py)` → MATCH.

## Titus-specific facts (inbox-only macOS host)

- No HTTP health endpoint; `health-state.json` shows "unreachable" — EXPECTED.
  Liveness = `inbox-health-state.json` (fresh pushed vector) + `last-seen.json`.
- No ssh alias in `~/.ssh/config` (only mosesaaron) — cannot be reached directly.
- Reachability evidence this session: health push 07:52 KST same day (vector 9/9),
  but learning-reports stopped 08-24 06:05 and fleet-update-check showed the update
  task "consumed/unknown" (pulled, never confirmed) → agent crons may have stalled.

## Recurring archetypes to watch (independent of these commits)

1. TDD Iron-Law commit gate (new prod code without same-commit test → block)
2. Deployed-only governance resolution (missing deployed file = silent skip)
3. Pass-rate measurement changes (fabricated 100 → real measured)
4. Silently-disabled deployed check fixed (new findings on old commits)
5. Auto-close sweeps (review queue / stale cycles)
6. Always-skills count changes (10→7 canonical gate)

If a future host issue matches one of these, the culprit is the corresponding
commit class — verify with the Phase 1/2 forensics before concluding.
