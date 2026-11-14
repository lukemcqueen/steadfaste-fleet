---
name: enforcement-immutability
version: 1.0.0
category: devops
description: "Use when enforcement files lose their immutable flag."
author: Esther
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [governance, immutability, chattr, deploy, audit, enforcement]
    related_skills: [sudoers-audit, governance-lock-lifecycle, deploy-load-verification]
---

# Enforcement Immutability (chattr +i) Across Deploys

**Class of task:** managing the immutable-flag layer that protects the enforcement
files, diagnosing an auditor alert that the flag is missing, and recovering it.

This is NOT the governance LOCK FILES (`.governance-*.json`, the loop-governance
session lock) — those are a different mechanism with a different lifecycle. This
skill is the kernel-level write protection on the enforcement code itself.

## What the layer is

A sudo-gated helper holds the target list and applies the flag. On Linux that is
`chattr +i`; on macOS the equivalent is `chflags uchg` (owner-applied, no root).
Targets are the enforcement surface: the enforcer plugin, the git hooks, the
scoring hooks, the loop-governance MCP server, and the helper itself.

Two command shapes, and the difference matters:

```bash
hermes-plugin-lock lock      # apply the flag   — any agent
hermes-plugin-lock status    # list flag state  — any agent
hermes-plugin-lock unlock --fleet-update   # release, sanctioned caller only
```

`lock` and `status` take no argument token; only `unlock` is token-gated. Treat the
helper's own `status` target list as authoritative — it can be longer than what an
auditor names.

## Normal lifecycle, and how to verify it happened

A deploy unlocks the targets early in the run and relocks them in a **separate block
far below**. To confirm a deploy relocked, read its LOG rather than the current flag
state:

```bash
grep -c 'LOCKED:' /tmp/deploy.log
grep -c 'NOT locked' /tmp/deploy.log        # must be 0
grep -n 'Locking enforcement files' /tmp/deploy.log
```

A healthy run shows the relock block, one `LOCKED:` line per target, zero `NOT locked`
warnings, and the last unlock line BEFORE the first `LOCKED:` line. Ordering matters:
an unlock that lands after the relock is the failure.

## The failure mode: an interrupted deploy

Because unlock and relock are separated by the whole run, **anything that stops the
script between them leaves the enforcement files writable.** The highest-risk path is
a DETACHED update worker — the update-request flow spawns the deploy in the
background, so a worker that crashes, is killed, or times out mid-run dies inside the
unlock/relock window. The deploy now installs an **EXIT trap** (`_relock_enforcement`) that runs the relock on
success, on error and on signal alike, and `fleet-dogfood.sh` carries its own
(`_dogfood_relock`) so the mandated pre-push gate never depends on a CHILD script's trap
for a security property. That turns this failure mode into a REGRESSION signal: missing
flags after a non-zero deploy mean the trap is absent or was dropped, or the relock
helper is unreachable from that path. The trap prints `relock ran on a FAILED exit` when
it fires on a failing run, so grep the deploy log for it before blaming anything else. A
trap that resolves the helper only from the deploy home still relocks when that path is
wrong, because it falls back to the helper on `PATH` — keep that fallback when editing it.

**Fingerprint of a died detached worker:** a `*.running` marker under the deploy state
dir (e.g. `~/.steadfaste-fleet/state/pending-update-results/`) containing only a start
timestamp, with no matching completion result and no live handler/deploy process.
That marker plus missing flags is the diagnosis; it is not a configuration regression.

## Detect

```bash
hermes-plugin-lock status                       # authoritative target list + flags
lsattr <file>                                   # cross-check one file
```

Expected good state carries the immutable flag — `----i---------e-------`.
`--------------e-------` (extent only, no `i`) means UNLOCKED.

An auditor on a periodic cadence will alert minutes-to-hours AFTER the loss, so the
alert is downstream of the cause. Reconstruct the timeline from the deploy logs and
the update-worker markers, not from the alert time.

## Recover

```bash
sudo -n /usr/local/sbin/hermes-plugin-lock lock   # NOPASSWD-granted
hermes-plugin-lock status                         # verify every target shows the flag
```

The helper path is granted in sudoers on hosts that use it, so recovery needs no
interactive password and should not be escalated to the operator as a blocker.

## Adding a NEW file inside a locked target DIRECTORY

A locked DIRECTORY (`hooks/` is one of the targets) refuses `ln -sf` with `Operation not
permitted` — and the failure is INVISIBLE while every file already inside it is correct. A
deploy step that repairs existing targets takes the "already correct" early exit for each of
them, so only a BRAND-NEW hook exposes the problem: its symlink cannot be created, the deploy
prints at most a warning, and the new gate is silently absent from every host.

So any deploy step that creates a file inside a locked target needs its own sanctioned window,
and it must be placed where the directory is unlocked OR unlock it itself:

```bash
# resolve the helper the way the rest of the deploy does — PATH first
helper="$(command -v hermes-plugin-lock || true)"
[[ -n "$helper" ]] || helper="${FLEET_DEPLOY_HOME}/scripts/hermes-plugin-lock"
[[ "$(uname -s)" != "Darwin" ]] && lp="sudo -n"   # macOS needs no root
```

- **Path first, deploy-home second — the deploy-home copy is NOT always in sudoers.**
  `sudo -n <deploy-home>/scripts/hermes-plugin-lock unlock` answers *"sudo: a password is
  required"* on a host whose NOPASSWD grant names only `/usr/local/sbin/hermes-plugin-lock`.
  With stderr discarded the caller sees only a failed link and no reason: resolve from `PATH`
  first (that is the granted path, and the fallback the EXIT trap already uses).
- **Never swallow the unlock or the relock.** An unlock failure leaves the hook undeployed
  (`✓`/`warn` at most, no hard error); a RELOCK failure leaves the enforcement directory
  WRITABLE — the highest-value target on the host. Report both, naming the consequence and the
  command that fixes it, and re-check the flag afterwards.
- **Verify the LINK, not the command.** `ln -sf` succeeding is not a deployed hook: assert
  `[[ -L $dest ]] && [[ "$(readlink $dest)" == "$src" ]]` after the retry, because a link that
  does not resolve is absent as far as git is concerned.
- **Prove it end-to-end by deleting the artifact under a LOCKED dir** (unlock, `rm`, relock),
  then running the deploy: it must recreate the link AND leave `lsattr -d <dir>` showing
  `----i`. A deploy step verified only on the already-correct state proves nothing.
- **A MISSING new gate is a rollout gap, not a broken host.** When the doctor learns to check
  it, make its absence a WARN and keep drift FAIL for a present one: a host that merely has not
  run the deploy yet must not be blocked from pushing.
- **A new doctor check must be PROVEN to run, because a check in the wrong function never does.**
  The repo-crawl checks return early when the crawl finds nothing, so a check placed after that crawl
  is silently absent: the deploy is clean, the doctor prints no FAIL, and the gate you just added does
  not exist. Run the doctor and grep for the check's OWN label before believing it shipped, then
  exercise the non-clean branch too — plant the condition (e.g. a temporary second lock file for one
  repo) and confirm the WARN names it, removing the artifact in the same command.

## Pitfalls

- **Do not run a proxy probe and call passwordless sudo broken.** NOPASSWD is
  per-command: a generic probe that exercises a command nobody granted fails even when
  the grant for the helper is present and working. Probe the granted path itself, and
  read the full grant listing rather than a truncated slice — see the `sudoers-audit`
  skill for the procedure.
- **Do not blame the deploy pipeline before reading its log.** A completed deploy does
  relock; "the lock has been broken for weeks" is unsupported if the current flag state
  is the only evidence. Find the run that lost the flag.
- **A flagged file is not a corrupted file.** Unlocked means writable, not altered.
  Verify content integrity separately (the doctor's checksum checks) before implying
  tampering.
- **The auditor's count can be narrower than the helper's target list.** Fixing the
  files it named may leave others unlocked — reconcile against `hermes-plugin-lock
  status` every time.
- **Inspect each target ITSELF — use the directory-aware flag.** A flag-reading command
  applied to a DIRECTORY lists its CHILDREN: `ls -lO DIR` (macOS) lists the contents plus
  a total header, and `lsattr DIR` (Linux) reports the files inside it. Both then report
  the directory's own flag as absent, so a correctly locked `hooks/` reads as "not
  locked". The consequences compound: the output names one path per child (so the same
  target appears twice, or a child appears where the target was expected), and any
  auto-remediator reading that verdict re-locks an already-locked directory on every
  cycle. Use `ls -ldO` / `lsattr -d`. When you fix one platform's branch, fix the other
  in the same change — a macOS-only report usually means the identical defect is live on
  every Linux host, including the gateway's. Prove it rather than assuming symmetry: run
  the bare form against a real target directory and confirm it names a child.
- **A detected-but-unremediated security state is still an open state.** Detection without
  remediation is how an unlocked flag survives until a human happens to notice: the doctor
  and the auditor reported it, and nothing repaired it. Wire every degradable security
  state to a fix in the periodic path — the doctor's fix step now relocks when an
  `Immutable:` check fails or warns, so any run with `--fix` heals it. Gate that fix on the
  check's **status** (`FAIL`/`WARN`), never on the check's name alone: the name is present
  on `PASS` too, so a name-only match fires the remediation on every clean run — harmless
  for an idempotent `lock`, but it hides whether the condition is ever actually true. Prove
  the gate with a CONTROL that asserts the remediation does NOT fire when the check passes.
- **An over-eager relock is harmless; a missing one is not.** `lock` is idempotent and
  applies to the helper's whole target list, so calling it on a clean path costs a moment
  and nothing else. Prefer the unconditional guard over a clever conditional one — the
  failure that matters is the path where nobody relocked.
- **A FAILED dogfood is an unlock-leaving path, not a re-run.** `fleet-dogfood.sh`
  deploys (unlock → relock) and then verifies; when verification fails the flags are left
  off. So the first move after a failing dogfood — before any retry or diagnosis — is
  `hermes-plugin-lock status` plus a relock, not another push. That same run also moves
  HEAD and invites you to keep working, which is how a writable enforcement tree gets
  edited unnoticed.

- **A periodic auditor samples INSIDE the deploy's unlock window.** The deploy unlocks
  early and relocks at the END, so the tree is legitimately writable for the whole run.
  An auditor on a cadence will eventually land in that window and report an unlocked gate
  that the deploy is about to relock itself — the giveaway is that it names EXACTLY the
  targets the deploy unlocks, and prints as its "fix" the relock already in flight.
  Confirm by comparing the alert's timestamp with the deploy's writes (`stat -c '%y %n'
  <deploy>/deploy-manifest.tsv`) and with `hermes-plugin-lock status`: if every target
  shows `----i`, nothing was broken. The deploy now publishes the window as
  `<deploy>/state/deploy-in-progress` (written at start, removed by the EXIT trap AFTER
  the relock — clearing it earlier reopens the same false positive) and the auditor
  defers while that marker is fresh. Trust the marker only while FRESH, or a deploy killed
  hard enough to strand it silences the check forever. Never "fix" this alert by deleting
  the check or by relocking mid-deploy: the first removes the detection that catches a
  genuine unlock, the second breaks the deploy.

## Verification

- [ ] `hermes-plugin-lock status` shows the immutable flag on EVERY listed target
- [ ] Content integrity independently confirmed (doctor checksums clean)
- [ ] The run that lost the flag identified from logs/worker markers, and the relock guard
      itself checked (a non-zero deploy must still leave every target locked)
- [ ] The guard proven by a real failure, not by reading the code: unlock the targets,
      force the deploy to fail (`FLEET_DEPLOY_HOME=/nonexistent bash ops/scripts/fleet-update.sh`
      exits non-zero), then confirm every target shows `----i` again.
- [ ] Any stale `.running` update marker accounted for (otherwise a phantom completion
      result follows)
