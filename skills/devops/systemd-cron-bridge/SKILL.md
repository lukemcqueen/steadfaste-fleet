---
name: systemd-cron-bridge
version: 1.0.0
category: devops
description: "Use when hosting no_agent crons on systemd timers."
author: Steadfaste Fleet
license: MIT
metadata:
  hermes:
    tags: [cron, systemd, scheduler, independence, watchdog]
---

# systemd-cron-bridge — standalone hosting for no_agent crons

When a no_agent script cron must keep firing **even if the Hermes gateway / `cronjob` scheduler process is down**, host it on systemd user timers instead. This removes "gateway restart kills cron" and the "gateway down → N no_agent jobs don't run" coupling. Only **simple** no_agent jobs qualify (a script + deliver, no `context_from`/`continuity`/model pin); LLM and chained jobs stay on the Hermes `cronjob` scheduler by design (they need the agent loop).

## Pieces

- `fleet-bus-bridge-run.py` — the **delivery wrapper**. Runs the job's script standalone, delivers non-empty stdout. **empty stdout = silent** (the watchdog pattern is preserved). Source the fleet `.env` so tokens/models resolve.
- `fleet-bus-bridge-generate.py` — reads `~/.hermes/cron/jobs.json`, writes a user-scope systemd pair `~/.config/systemd/user/fleet-bridge-<job>.{service,timer}` per simple no_agent job, converting the cron expr to `OnCalendar`. Skips LLM/chained jobs and schedules it can't translate.

## `--deliver` routing (the load-bearing decision)

The wrapper MUST take `--deliver <target>` and route by it. **`local` = save-only to `<FLEET_DEPLOY_HOME>/cron-output/<job>.log`, never a send; anything else = Telegram.** Do NOT let the runner default to Telegram: many no_agent jobs (`agent-message-handler`, `agent-learning-collector`, `agent-push-metrics`, `agent-session-mine`) are internal collectors whose stdout must never reach the channel. Migrating one without `--deliver local` silently spams the home channel with internal output.

## Migration = TWO steps per job, or you DOUBLE-RUN

For each job: `systemctl --user enable --now fleet-bridge-<job>.timer` **then
REMOVE the matching Hermes entry** — `hermes cron list` to find the job's `id`,
then `hermes cron remove <id>` (always list first; never guess IDs). A live
timer + a live `cronjob` tick = the job runs twice.

**Remove, do not merely pause.** A paused row is not the endpoint: it is an
owner-in-waiting (a reinstall can resume it) and it leaves two sources of truth
for one job. Pausing was an intermediate migration step; removal finishes it.
Removal is safe now because the installers, the doctor and the manifest check
all understand bridge ownership — see *Guards* below. (Before those guards,
removing a job was a trap: `create_cron` recreates any missing job, so the next
install would resurrect a second owner.)

**Leave already-paused jobs alone.** A job paused for a reason (bible-reading,
hermes-update, regression-gate, bus-retry-sweep) must get a `disabled` (not
enabled) unit — `enable --now` undoes the pause. Those are **not** migration
targets: keep their Hermes entry, still paused.

**Ownership marker.** `~/.config/systemd/user/timers.target.wants/fleet-bridge-<name>.timer`
present ⇒ the bridge owns the job. This is filesystem-only on purpose (no DBus
session is available in a cron/install/doctor context). A **disabled** unit is
absent from `wants/`, so a paused-for-a-reason job is never mistaken for a
migrated one.

Full per-host procedure, the 3-part verification, and pitfalls:
`docs/runbooks/cron-bridge-migration.md`.

### Guards that make removal safe

- **Installers** — `create_cron()` in `install-crons.sh` and
  `install-orch-crons.sh` returns early for a bridge-owned job, so an install
  run can never recreate one and hand the bridge a second owner.
- **Doctor** — `fleet_doctor/checks.py::_bridge_owned_jobs()` excludes bridged
  names from the expected-cron, orphan and extra scans.
- **Manifest** — `cron_manifest.py::check_drift()` skips bridged names, so a
  migrated job is not reported as "declared in manifest, absent from live jobs"
  (that FAIL blocks the pre-push deploy-sync gate).

### How a host learns it needs this

You do not have to reason about it: the doctor reports the gap itself. The
`Cron bridge migration` check counts ENABLED eligible `no_agent` jobs that have
no bridge unit AT ALL, and WARNs with the runbook path in its remediation — so
every host discovers its own un-migrated state on a routine scan, with no
fleet message to lose. Two details make it quiet enough to trust:

- Its eligibility predicate mirrors the generator's (`no_agent` + script, no
  `context_from`/`continuity`, no model pin), so it can never disagree with what
  the bridge can host.
- A unit file in ANY state counts as "considered" — a job deliberately left on
  the Hermes scheduler has a DISABLED unit, so it is never flagged as a gap.

**macOS:** systemd user timers are Linux-only, so the check reports INFO there
rather than warning forever about a mechanism the host cannot use. Do not
"port" the bridge to a host without systemd — that needs a different runner.

## Verify the standalone run actually delivers

A silent watchdog is a poor proof — it exits 0 without exercising delivery. Prove delivery on an **always-output** job: start the bridge service, then confirm the messenger's state/log shows a send at that timestamp. Silent-when-clean stays silent; an always-printing job proves the full systemd → runner → messenger chain.

### ⚠️ Retiring a bridged job: the timer OUTLIVES the job

Removing a job from `~/.hermes/cron/jobs.json` does **not** remove its bridge units.
`fleet-bus-bridge-generate.py` only CREATES units; it has no prune step, so a
retired job keeps firing from its systemd timer and keeps delivering to the user
— with the cron gone from every listing, so nothing looks wrong. Real case
(2026-10-02): `orch-task-board-digest` was retired, yet Luke kept receiving its
daily Telegram digest; the job was absent from `jobs.json` and from the `cronjob`
listing, and only `systemctl --user list-timers | grep board` showed it.

**Retire a bridged job in BOTH places:**
```bash
systemctl --user disable --now fleet-bridge-<name>.timer
rm -f ~/.config/systemd/user/fleet-bridge-<name>.{service,timer}
systemctl --user daemon-reload
systemctl --user list-timers --all | grep <name> || echo "gone"
```
Then remove the `create_cron` block from the installer (keep the name in the
uninstall array so other hosts clean up too), drop the `cron-manifest.yaml`
entry, and grep the docs/skills for the name.

**Pin the retirement with a test, or the next reinstall re-arms it.** Assert
(a) no `create_cron` block for the name, (b) the name is STILL in the uninstall
array, (c) no manifest entry, (d) `bash -n` on the installer exits 0. The
uninstall-only entry is deliberate — `fix-cron-duplicates.py` will warn about it,
and that warning is expected for a retirement rather than a defect to clear.

**Check the live host, not just the repo.** A timer can outlive the job for a
long time before anyone notices, so when a user reports noise from a job you
cannot find, grep the timer list by NAME FRAGMENT (`list-timers --all | grep -i
<word>`) — the unit is named `fleet-bridge-<job>`, which does not appear in any
cron listing.

### Renaming a bridged job — create the NEW name, then retire the OLD unit

A rename of a bridge-owned job is migration of a new name PLUS retirement of the
old unit (the generator only CREATES units and only reads `jobs.json`, so it
cannot rename in place). The bridge-ownership guard means the NEW name is not
yet owned, so the deploy's `create_cron` creates it; enable its timer first, then
you may remove it from Hermes (the guard now recognizes it) and delete the old
unit safely. Order matters — enable before remove, or the job briefly has no
owner:

```bash
# 1. Rename in installer create_cron + uninstall array, cron-manifest.yaml, and
#    the docs/skill rows; commit. Then deploy (install-crons creates the NEW
#    name in jobs.json since it isn't yet bridge-owned).
# 2. Generate the NEW unit from the REPO path (never the deployed copy — the repo
#    path is what makes its repo-root math resolve):
python3 ops/scripts/fleet-bus-bridge-generate.py
# 3. Enable + START the new timer (a bare `enable --now` can leave it
#    enabled-but-inactive if timers.target was already reached):
systemctl --user enable --now fleet-bridge-<newname>.timer
systemctl --user start timers.target
# 4. Remove the new name from Hermes (it is now bridge-owned):
hermes cron remove <new-job-id>     # the two-step migration completes here
# 5. Retire the OLD-name unit:
systemctl --user disable --now fleet-bridge-<oldname>.timer
rm -f ~/.config/systemd/user/fleet-bridge-<oldname>.{service,timer}
systemctl --user daemon-reload
# 6. Verify only the new timer exists and is ACTIVE (not just enabled):
systemctl --user list-timers --all | grep <fragment>
```

While the new-name job is still in `jobs.json` (between steps 2 and 4), the
doctor's `Cron bridge migration` check may WARN that it has no bridge unit —
that is the transient state of the procedure, not a defect; it clears once the
timer is enabled. A rename that touches an LLM sibling job (not bridge-hosted)
is the plain case: deploy creates the new name, remove the old-name row
from `jobs.json`, no systemd units involved.

## Cron → OnCalendar translation

Use `references/systemd-timer-oncalendar.md` — the correct syntax variants and the **`systemd-analyze calendar <expr>` validator** that catches a wrong form before it ships. **The `/N` step goes on the field it steps: an hour interval is `00/N:00:00`, a minute interval is `*:0/N:00`. Do NOT write an "every N hours" job as `*:00/N:00` — that puts the step on the MINUTE field and it fires every N MINUTES** (the silent-cadence bug: the value still validates, only the `Next elapse:` line reveals it). Read `Next elapse:` to confirm cadence (hours away, not minutes), then `systemd-analyze verify` the unit pair.

## Pitfalls

- **A unit test that invokes a delivery wrapper MUST route hermetically** — pass `--deliver local` + a temp `FLEET_DEPLOY_HOME` so the run saves to a temp log and can never reach the real messenger. Omitting `--deliver` on a "does it execute" test makes every `pytest` run send a real message to the user's channel (subject `cron:<name>`), a side-effect a unit test must never have.
- **The bridge runs the REPO-path runner, not the deployed copy** — units `ExecStart` the repo `ops/scripts/...` path. When testing, invoke the same path the timers use; a stale deployed copy lacks the latest flags and errors (`unrecognized arguments`). Deploy anyway for consistency but test the live path.
- **Run the GENERATOR from the repo path, not the deployed copy** — `fleet-bus-bridge-generate.py`
  computes its repo root inline as `Path(__file__).resolve().parent.parent.parent`, which assumes the
  `ops/scripts/…` depth. Invoke the deployed `~/.steadfaste-fleet/scripts/fleet-bus-bridge-generate.py`
  (two levels deep, no `ops/`) and every generated unit's `ExecStart` points at `$HOME/ops/scripts/…`
  — one level short of the repo, so the bridged job fails at fire time with the runner not found.
  Run the repo copy (`ops/scripts/fleet-bus-bridge-generate.py`), then `grep ExecStart
  ~/.config/systemd/user/fleet-bridge-<job>.service` to confirm the path resolves before trusting the
  timer. A pre-existing unit carrying this bad path is fixed by regenerating from the repo path.
- **Resolve `manage/`-prefixed and `.sh` scripts correctly** — `_resolve_script` must resolve relatives under the deployed scripts dir (so `manage/agent-x.sh` and flat `agent-x.py` both land), else a migrated `.sh` or subdir job silently no-ops.
- **A disabled quality watchdog is how garbage reaches the user** — if a user reports LLM-cron garbage delivered, check the watchdog timer is `active` (a `*/10` scan is the post-flight guard for exactly this); re-enable it before blaming the model. Pinning (`pinned:true`) locks the model but does NOT stop fallback substitution on provider outage — a garbage fallback model still runs and delivers.
- **Migrate in batches with a counter-verification**, not one blind sweep: after each batch, cross-check timer count vs remaining `enabled` no_agent jobs; the two must be disjoint.
- **Completion is a THREE-way check: two owners and zero owners are both failures.** "The timers are active" proves nothing on its own, and the disjointness check above catches only the first mode. Audit all three and report the counts, not a boolean:
  1. **double-run** — timer active AND the job still present in `jobs.json` → two owners, it fires twice.
  2. **orphaned job (the mode nothing reports)** — no timer owns it AND it is gone from `jobs.json` → **silently stopped**. A remove-the-Hermes-row migration creates this whenever a timer was never actually enabled; no scheduler is left looking, so nothing flags it. Detect it by name-set difference, never by counting.
  3. **orphan timer** — a `fleet-bridge-*.timer` unit whose job no longer exists in `jobs.json`.

  Practical form: build the name set from `timers.target.wants/fleet-bridge-*.timer` and the name set from `jobs.json`, then assert `overlap == 0` **and** that every job you intended to migrate lands in exactly one of the two sets.
- **The raw `jobs.json` row field is `id`, not `job_id`.** The `cronjob` tool and `hermes cron` CLI address `job_id`, but the file uses `id` — a mass-removal loop reading `j['job_id']` gets `None` for every row, removes nothing, and still prints one success-shaped line per job.
- **`systemctl --user enable` does NOT start the timer if `timers.target` was already reached** — it only drops the `timers.target.wants/` symlink. On a host already past boot (the normal cron/install context, no DBus session), a fresh `enable` leaves the unit `enabled-but-inactive`, silently never firing. Real case (2026-10-02): all 61 fleet-bridge timers were `enabled` but inactive — the wants symlinks were created after `timers.target` had been reached, no start was issued, and every stale-mycortex-source watchdog showed nothing out of place until the sensor flagged 7 stale sources. Fix: `systemctl --user start timers.target` — starts every wanted-but-inactive timer at once. Then verify with `systemctl --user list-timers --all | grep fleet-bridge` (count ACTIVE, not just ENABLED — `enabled` ≠ `active` for a timer).
