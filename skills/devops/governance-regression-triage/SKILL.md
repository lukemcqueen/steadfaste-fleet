---
name: governance-regression-triage
version: 1.0.0
category: devops
description: "Triage a host's governance issues: what changed."
author: Esther
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [governance, triage, forensics, enforcement, fleet, regression]
    related_skills: [enforcement-change-safety, governance-lock-lifecycle, deployed-component-verification, fleet-management, doctor-warning-resolution]
---

# Governance Regression Triage

**Class of task:** a fleet host/agent reports governance problems — blocked commits,
doctor failures, cycles closed unexpectedly, enforcement behaving differently — or
Luke asks "did something change with governance?". Goal: determine WHAT changed
recently and WHICH change is the likely culprit, ranked, with evidence.

The governance surface changes weekly (TDD gates, deployed-only paths, pass-rate
measurement, sweeps, skill-count gates). A host that was fine a week ago can break
without its own work changing. This skill is the forensics path.

## When to Use

- "did something change with governance?" / "we are having issues on <host>"
- A host's commits are blocked by a NEW gate that wasn't there last week
- Doctor fails or cycles close unexpectedly after a fleet-wide deploy
- Enforcement behaves differently than the repo source suggests

## Phase 0 — Get the exact symptom FIRST

The error message text identifies the culprit faster than any diff:
- "TDD IRON LAW VIOLATION" → TDD commit gate
- "adversarial-verify.py not found" → deployed tool missing
- "session skills not fully loaded" → always-skills gate (see task-start)
- "❌ PENDING cycles" at push → leaked cycles
- Commits blocked with a NEW finding that used to pass → a silently-disabled check
  was fixed (behavior change, not a new rule)

If the symptom is unknown, still do Phases 1-2 (they are cheap) and then ask.

## Phase 1 — Repo forensics (what changed)

Run via `terminal`:

```bash
cd ~/steadfaste-fleet
git log --since="10 days ago" --pretty=format:"%h %ad %s" \
  --date=format:"%m-%d %H:%M" | head -40
# governance-touching paths only:
git log --since="10 days ago" -- hooks/ plugins/ mcp-servers/ ops/scripts/ \
  ops/scripts/manage/fleet_doctor/ ops/scripts/quality/ | head -50
# per-file history for likely culprit files:
git log --oneline --since="3 days ago" -- ops/scripts/pre-commit-score \
  ops/scripts/change-validate.sh ops/scripts/precommit_test_discovery.py
```

Then read commit bodies (`git show --stat <sha>`, then `git show <sha> -- <file>`)
for the actual behavior change — the headline often understates the blast radius
(e.g. "deployed-only governance" changes WHERE every repo resolves its gates).

## Phase 2 — Deployed vs repo (is the host running what the repo says?)

- `ls -la ~/.hermes/plugins/governance-enforcer/__init__.py` — mtime tells you when
  the enforcer last deployed. Compare to `git log -1 --format=%ad plugins/...`.
- `~/.steadfaste-fleet/state/last-update.json` — exact deploy timestamp + SHA.
- `diff <(sed 's/^# SOURCE:.*//' deployed) <(sed 's/^# SOURCE:.*//' repo)`
  — deployed copies carry a `# SOURCE:` header; strip it before diffing.
- **Silent-skip trap:** many gates are `if [[ -f "$HOME/.steadfaste-fleet/scripts/<tool>" ]]`
  guarded. A MISSING deployed file silently SKIPS the gate (governance OFF), it does
  not error. Check file existence, not just repo source. If the host never deployed
  the latest, its gates may be silently disabled — the "issue" is a missing deploy.

## Phase 3 — Host state (reachability + liveness)

Every host is different; check the registry before assuming:
- `~/.steadfaste-fleet/state/agent-registry.json` — role, platform, health_method
  (`http` vs `inbox`), health_url, inbox schedule.
- **Inbox-only hosts (e.g. Titus, macOS):** no HTTP health endpoint. `health-state.json`
  showing "unreachable" for them is EXPECTED, not a failure — read
  `inbox-health-state.json` for the fresh pushed vector + `last-seen.json` for the
  last push timestamp.
- `~/.steadfaste-fleet/state/learning-reports/` — per-host report cadence; a gap here
  means the host's agent crons stalled (different failure from a blocked commit).
- `fleet-update-check.py` (ops/scripts/manage/) — did the host consume the fleet
  update task, and did it reply? "consumed/unknown" = pulled but never confirmed.
- `~/.ssh/config` — most hosts have NO ssh alias; don't assume direct access.

## Phase 4 — Bus checks (read the host's queues)

- Raw PGMQ depth API works without client certs:
  `GET <bus-base>/api/pgmq/depth/<queue>` on the local raw bus port with
  `Authorization: Bearer $FLEET_BUS_TOKEN` (from ~/steadfaste-fleet/.env).
- The HTTPS bus URL may return 401 for direct requests — the MCP client reaches it
  via a sudo proxy (`/usr/local/bin/mcp-inbox-proxy`) that needs passwordless sudo.
  Don't burn cycles fighting the 401; use the raw API, or the agent-bus MCP tools.
- `docker exec mycortex-postgres psql -U mycortex -d mycortex` — schema:
  `bus.messages` (msg_id uuid, queue_name, body **jsonb**, enqueued_at). No `left()`
  on jsonb — cast `body::text`. `messages_archive` holds consumed rows.
- **Stale-DB trap:** the local docker postgres may hold only old test/migration data
  (e.g. July rows) while the live fleet traffic flows through the HTTP/PGMQ layer.
  An empty-looking local DB is NOT proof of no traffic — check the raw PGMQ API too.

## Change-archetype catalog (recent behavior changes that break hosts)

These keep recurring; when one matches the symptom, the culprit is found:

1. **TDD Iron-Law commit gates** — NEW production code files (`.py/.ts/.js/.go/...`)
   blocked unless a test file ships in the SAME commit, in repos with test infra.
   Fail-closed, no bypass. Dev agents with many project repos hit this first.
2. **Deployed-only governance** — pre-commit resolves gates ONLY from
   `~/.steadfaste-fleet/scripts/` (change-validate.sh, precommit_test_discovery.py,
   adversarial-verify.py); repo-local copies/overrides ignored. Missing deployed
   file = silent skip (see Phase 2).
3. **Pass-rate measurement changes** — monorepo discovery (`apps/<svc>` layouts),
   pytest ERRORS counted as failures. Free fabricated 100s become real scores →
   commits/cycles score lower or warn where they used to pass.
4. **Silently-disabled check fixed** — deployed verifier that swallowed its own
    import error (except:pass) now actually fires → NEW findings on commits that
    used to pass. Symptom looks like "stricter out of nowhere" but it's a fix.
    Same applies to a *newly-working* probe: fixing a cross-platform gap
    (e.g. lsattr→chflags) makes a previously-skipped check run on hosts where it
    never executed — its first findings there are the check working, not a
    regression. When fixing a probe, audit EVERY call site for OS assumptions
    (symlink resolution, flag-field parsing) in the same cycle: fixing one and
    leaving siblings guarantees a second round of host alerts.
5. **Auto-close sweeps** — review-queue stale-sweep auto-closes STOP cycles >3d
   and surfaces hard-fail cycles. Cycles "disappearing" = sweep, not tampering.
6. **Always-skills count changes** — canonical gate (10→7) changes which skills a
   session must load before write tools unblock. Stale manifest → blocked sessions.

## Pitfalls

- change-validate.sh is **warn-only** (`exit 0`); real blocks come from
  pre-commit-score gates. A "warning" host issue is different from a "blocked" one.
- Don't conclude "no change" from an empty `git log` on one path — the enforcer
  plugin, hooks, and doctor checks all live in different trees; scan them all.
- Don't ssh-scan every host — check the registry's health_method first.
- Rank candidates by (a) symptom-text match, (b) deploy timing vs host's
  last-update, (c) host role (dev agent vs server agent).

## Verification

- State the ranked culprits with commit SHAs + dates + one-line behavior change.
- Ask for/confirm the exact error text if the symptom was unknown — the message
  resolves the ranking.
- Investigation-only cycles: begin_change → investigate → feedback_accept →
  end_change; no file changes needed (score the cycle MOVE_ON with a notes).

## Reference

- `references/governance-change-catalog-2026-08.md` — concrete commit SHAs,
  dates, and behavior deltas from the Aug 2026 wave (TDD gate, deployed-only
  governance, monorepo pass-rate, TOON fix, review-queue sweep, skills 10→7).
