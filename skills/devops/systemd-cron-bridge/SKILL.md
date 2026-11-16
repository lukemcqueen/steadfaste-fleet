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
<!-- Backport provenance (deployed -> repo): the DEPLOYED copy of this file carried
     lessons the repo lacked. Every repo-only line it would drop was checked by phrase
     match and is a reflow or a deliberate CORRECTION, so the direction is
     deployed -> repo. Everything below is the deployed lesson copied verbatim, plus this
     note; it was NOT authored in the backport range that landed it. -->

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

**For bulk removal prefer the `cronjob_manage` tool over the CLI loop.**
`action='remove', job_id=<id>` per job, all issued in ONE batched tool-call
block, removes them together and returns each removed job — reserving the slow
CLI path below for a host without the tool (a non-orchestrator has no `cronjob`).
Either way, verify the result in `jobs.json` (the row field is `id`, not
`job_id`): "removed" in a message is not evidence, and the file is.

**Batch-removing Hermes entries is slow per call — budget for it.** `hermes
cron remove` costs ~3–4s per job (CLI startup + scheduler write), so a 50–60
entry migration crosses the terminal's default foreground timeout mid-loop and
the command is killed with a PARTIAL state (some removed, some not). Run the
loop with a generous `timeout` (or in chunks), and make it idempotent: compute
the to-remove set from the CURRENT `jobs.json` each pass (already-removed names
are simply absent), and after any timeout re-list which bridge-owned names
remain in `jobs.json` instead of assuming the loop finished or double-removing.

**The double-fire is not merely duplicate work — the second run collides on
shared state.** A double-owned job that stages files (a backup staging dir, a
symlink, a lockfile) fails its second run with `Errno 17` / `File exists` on
state the first run already wrote — so "job failed with EEXIST" is a
double-ownership signature to check for, not a disk problem. Make staging
writes idempotent anyway (`link.unlink(missing_ok=True)` before
`symlink_to`/overwrite; clean stale partial staging from failed runs) so a
leftover partial run can never break the next one, then remove the second
owner.

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
- **Doctor, ownership** — `Cron bridge ownership` enforces the one-owner rule in
  the directions the migration checks do not cover, and reports by NAME:
  `DOUBLE OWNERSHIP` (a timer AND a `jobs.json` row — FAIL, the job runs twice)
  and `UNDECLARED TIMER` (declared in neither manifest nor `jobs.json` — INFO,
  undetermined by construction). Read its per-verdict remedy strings: each
  direction has a different fix, and the wrong one creates a second owner.

**Verify a unit is SELF-CONTAINED before removing its Hermes row.** The
mechanical reason removal is the safe side is that the generated `ExecStart`
carries the job inline — `--name <job> --script <file> --deliver <target>` — and
the runner never reads `jobs.json`:

```bash
grep ExecStart ~/.config/systemd/user/fleet-bridge-<job>.service
grep -c 'jobs.json' ops/scripts/fleet-bus-bridge-run.py    # expect 0
```

Confirm BOTH before a bulk removal. It also shows the bridge delivers exactly as
Hermes did (same `notify()` lib, same target, same silent-when-clean contract),
so the user loses no delivery — only the duplicate one.
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

### Bulk-renaming the unit NAMESPACE in place (e.g. `cortex-bridge-*` → `fleet-bridge-*`)

When a rename sweep changes the unit PREFIX for every bridged job, rename the
unit FILES in place — do not regenerate. The generator reads only `jobs.json`,
so regeneration produces units for jobs still there and silently drops
on-disk jobs whose Hermes rows were already removed. In-place rename preserves
every unit exactly:

```bash
cd ~/.config/systemd/user
# For each old-name timer: move the unit files AND the enable symlink, then reload.
# Delete (disable + rm) any STALE old-name duplicate that sits next to a
# current-name unit (e.g. cortex-bus-failover-watchdog beside fleet-bus-failover-
# watchdog) — renaming it would COLLIDE with the live name.
systemctl --user daemon-reload
systemctl --user reset-failed      # clears phantom failed records (see below)
systemctl --user start timers.target  # starts every wanted-but-inactive timer
```

Four pitfalls in this exact order cost a turn each:

1. **`[ -e ]` is FALSE on a dangling symlink.** If you `mv` the unit file BEFORE the enable symlink in `timers.target.wants/`, the symlink dangles and `-e` returns false, so the symlinks silently stay behind under the old name (60 of 62 in one run). Test with `[ -L ]` (`[ -e ] || [ -L ]`), or move the symlinks first.
2. **Renaming unit files leaves phantom `not-found failed` records.** The manager keeps the old unit names in failed state (`systemctl --user list-units --state=failed` shows the old names) → the doctor's `Systemd stale units` check FAILs. `systemctl --user reset-failed` clears all of them.
3. **The enable symlink preserves ENABLED, not ACTIVE.** The running timer instances died with the old names; after reload they are loaded-but-inactive with `Trigger: n/a`. `systemctl --user start timers.target` starts every wanted-but-inactive timer; verify with `list-timers --all | grep fleet-bridge` counting ACTIVE (not just the `wants/` symlinks).
4. **Verify against the ownership marker, not the file count.** The doctor/installers read `timers.target.wants/fleet-bridge-<name>.timer`; the marker flips to the new prefix automatically once the symlinks are moved. After the rename, remove the duplicate Hermes entries for every enabled-timer job (the two-step migration), keep entries for disabled-timer (paused) jobs, and check the `Cron bridge migration` check for NEW gaps (jobs that never got a unit — generate those).

### Hand-installed service units (bus/gateway/dashboard) — rename by FLIP, not regeneration

The generator owns only `fleet-bridge-*` units. A hand-installed service unit (`fleet-bus.service`, `fleet-gateway.service`, dashboard) has no generator — rename it with the flip sequence, and never let old + new run at once:

1. Write the NEW unit (`~/.config/systemd/user/fleet-<name>.service`) with the current ExecStart/env, `systemctl --user daemon-reload` + `enable` (staged, NOT started — enable alone avoids the port/token conflict).
2. `systemctl --user stop <old>.service` BEFORE `start fleet-<name>.service` — two services on one port (8903) fail to bind, and two pollers on one Telegram bot token get a 409. The order is stop-then-start, never start-then-stop.
3. Verify health on the SAME probe the old service answered (e.g. `curl 127.0.0.1:8903/health` → 200) before touching the old unit.
4. Only then: `disable` + `rm` the old unit, `daemon-reload`, and remove any orphaned bytecode shim the old module left behind (`core/cortex_bus/`) — confirm it is git-untracked and unreferenced (`search_files` for the old module name in the deployed scripts) before `rm -rf`.
5. Grep the deploy map + installers for `register`/template references that would re-create the old name on the next fleet-update — a hand-renamed unit the deploy regenerates is a rename that did not stick.

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
- **A regeneration never prunes or renames stale units, and it silently skips jobs no longer in `jobs.json`** — `fleet-bus-bridge-generate.py` only CREATES units from `jobs.json`. After a rename sweep (e.g. `cortex-bridge-*` → `fleet-bridge-*`), running the generator to re-emit leaves every old-name unit on disk untouched AND produces new units only for jobs still present in `jobs.json` — so the on-disk set (which can include jobs whose Hermes rows were already removed) does NOT equal what regenerates. Blindly regenerate-then-enable then (a) loses any on-disk job absent from `jobs.json` (its timer is never re-created → silently stopped) and (b) briefly double-owns the jobs that are still present until the old-name timers are disabled. Before regenerating, diff the on-disk timer name set against the `jobs.json` no_agent name set (`comm -23 ondisk jobs`), reconcile every on-disk-only name (retire or keep it deliberately), and disable the old-name timers before enabling the regenerated ones. A generated-unit count that is LOWER than the on-disk count is the tell — verify the gap is intended, not silently dropped.
- **A double-owned WATCHDOG job surfaces as a host health flap, not a double-fire.** When a job is STILL enabled in `jobs.json` AND has an active bridge timer (split-brain), its `last_run_at` is refreshed ONLY by the Hermes scheduler — the bridge timer never writes it. During a scheduler stall/gap the field crosses the health check's stale threshold, so the host's `no_stale_crons` bit reddens for a few minutes, and the OTHER independently-timer-driven watchdog on that host (which reads the host's health vector) reports it down→restored on loop. So a recurring down/restored pair on a host whose timer is active is a leftover `jobs.json` row, NOT a service outage: grep `jobs.json` for the enabled job, confirm the timer is also active, and remove the Hermes row (`hermes cron remove <id>`) — the timer is the owner. Removing the jobs.json entry is the durable fix; pause alone is an owner-in-waiting.
- **Migrate in batches with a counter-verification**, not one blind sweep: after each batch, cross-check timer count vs remaining `enabled` no_agent jobs; the two must be disjoint.
- **Completion is a THREE-way check: two owners and zero owners are both failures.** "The timers are active" proves nothing on its own, and the disjointness check above catches only the first mode. Audit all three and report the counts, not a boolean:
  1. **double-run** — timer active AND the job still present in `jobs.json` → two owners, it fires twice.
  2. **orphaned job (the mode nothing reports)** — no timer owns it AND it is gone from `jobs.json` → **silently stopped**. A remove-the-Hermes-row migration creates this whenever a timer was never actually enabled; no scheduler is left looking, so nothing flags it. Detect it by name-set difference, never by counting.
  3. **undeclared timer** — a `fleet-bridge-*.timer` whose job is declared NOWHERE. **`jobs.json` alone cannot decide this**: a migrated job is *deliberately* absent from `jobs.json` (the timer is its owner), so "unit, no `jobs.json` row" is the NORMAL migrated shape, not an orphan. The discriminator is the manifest — a migrated job is still declared in `cron-manifest.yaml`, a retired one had its entry dropped — and even then the verdict is UNDETERMINED, because a wanted job can simply be missing from the manifest. **Never report it as retired.** Resolve it by inspecting the JOB, not the name: does its script still exist in the repo, and does the deploy/installer still reference it? Script gone and unreferenced → retire the unit in both places. Script present and wanted → it is a manifest GAP: declare it.
     - **A per-host job must NOT be declared in the shared manifest.** Some jobs are deliberately local ("NOT in repo, created per-host", preserved across deploys by an explicit keep-list). Adding one to `cron-manifest.yaml` makes every other host expect a job it does not have — a new defect created by "fixing" a report. Leave it undeclared and note why; the INFO is the honest reading.

  Practical form: build the name set from `timers.target.wants/fleet-bridge-*.timer` and the name set from `jobs.json`, then assert `overlap == 0` **and** that every job you intended to migrate lands in exactly one of the two sets.
- **The raw `jobs.json` row field is `id`, not `job_id`.** The `cronjob` tool and `hermes cron` CLI address `job_id`, but the file uses `id` — a mass-removal loop reading `j['job_id']` gets `None` for every row, removes nothing, and still prints one success-shaped line per job.
- **`systemctl --user enable` does NOT start the timer if `timers.target` was already reached** — it only drops the `timers.target.wants/` symlink. On a host already past boot (the normal cron/install context, no DBus session), a fresh `enable` leaves the unit `enabled-but-inactive`, silently never firing. Real case (2026-10-02): all 61 fleet-bridge timers were `enabled` but inactive — the wants symlinks were created after `timers.target` had been reached, no start was issued, and every stale-mycortex-source watchdog showed nothing out of place until the sensor flagged 7 stale sources. Fix: `systemctl --user start timers.target` — starts every wanted-but-inactive timer at once. Then verify with `systemctl --user list-timers --all | grep fleet-bridge` (count ACTIVE, not just ENABLED — `enabled` ≠ `active` for a timer).
- **The ownership marker directory holds SYMLINKS, so a file-listing tool that skips symlinks reports ZERO bridged jobs.** `timers.target.wants/` contains `lrwxrwxrwx` links, not regular files: `search_files` in either mode (and other ripgrep-backed listings) returns `total_count: 0` there. A 0 from those probes reads as "this host has no bridge at all" — the opposite of the truth, and precisely the reading that would "justify" re-creating every bridged job as a Hermes cron, double-owning all of them. Count them with a listing that shows links: `ls ~/.config/systemd/user/timers.target.wants/ | grep -c '^fleet-bridge-'` or `ls -la`; the unit FILES (`~/.config/systemd/user/fleet-bridge-*.timer`) are regular files and do list normally, so a nonzero file count beside a zero symlink count is the tell that you probed the wrong way.
- **Do not verify "is it running" by grepping for a literal `ACTIVE`/`waiting` string.** `list-timers` prints blank NEXT/LEFT cells for an idle timer rather than a keyword, so `grep -c 'waiting\|ACTIVE'` returns 0 on a perfectly healthy set. Read the LAST column instead — a real firing records `N min/s/h ago` — and treat a long gap as correct for a long cadence (a weekly job legitimately shows `2 days ago`); `--all` is what includes the inactive ones you are trying to rule out. A probe that reports "0 active" while 60 timers carry a recent LAST will send you re-enabling a healthy bridge.
