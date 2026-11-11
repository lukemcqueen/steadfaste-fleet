---
name: concurrent-session-isolation
version: 1.0.0
category: devops
description: "Use when two or more agent sessions share one host."
author: Hermes Cortex curator
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [governance, git, worktree, identity, concurrency, deploy]
    related_skills: [governance-lock-lifecycle, governance-closeout, cross-agent-design, cortex-bus]
---

# Concurrent Sessions on One Host

**Class of task:** two or more agent sessions alive on the SAME host — a second
interactive session, a background/delegated session, a cron run — and everything
they collide over. Also the recovery when a collision has already happened.

Diagnose first: sessions are easy to conflate.

```bash
ls ~/.steadfaste-fleet/state/.governance-*.json           # one lock per live session
cat ~/.steadfaste-fleet/agent.env                         # NOTE: one identity for the WHOLE host
~/.steadfaste-fleet/scripts/loop-gov.py session_identity '{}'   # which source resolved THIS caller
git status --short                                        # uncommitted work may not be yours
git worktree list
```

**`check_repo_lock(repo_path)` answers "is this repo covered by SOMEONE's lock" — read it
before deciding you are blocked.** A `covered: true` whose lock names a PEER's session id
means the repo is BUSY, not that your own cycle is impossible: `begin_change` still creates
YOUR OWN lock file (`.governance-<your-session>.json`, distinct from theirs) and your writes
proceed, because the write gate is satisfied by any live lock for that repo. Two
consequences to say out loud rather than assume: never release, purge, or repair the lock a
peer's id names (that is theirs), and remember that working merely because THEIR lock exists
is the hole pitfall 10 describes — with no `begin_change` of your own there is no session
identity, so the commit trailer credits your commits to them. Scope your writes to your own
paths and leave their in-flight files (tracked and untracked) untouched.

## What collides, and what to do

| Shared resource | Failure it causes | Disposition |
|---|---|---|
| git index + working tree | a commit in one session sweeps the other's STAGED work under the wrong message | **scope** — one worktree per session; until then, forbid BARE commits (pitfall 11) |
| git author identity | commits unattributable; author-scoped gates cannot tell sessions apart | **scope** — per-worktree identity |
| commit ATTRIBUTION (a `Cortex-Session` trailer) | two live locks matching ONE repo ⇒ the hook stamps EVERY commit with the SAME id, so a session-scoped review filter cannot separate them | **scope** — one worktree AND one lock namespace per session; a trailer cannot distinguish sessions that share it |
| governance lock file | a CLOSE is session-scoped (own file, own cycle rows) — but a purge sweep removes past-TTL locks for ANY session, so a peer's deploy can end an idle session's lock | scope (already correct) for close; **serialise/verify** the purge — see pitfall 9 |
| loop-governance DB | writes serialise | scope (rows carry `session_id`) |
| MCP server process | one session's blocking handler freezes the server for EVERY session sharing that parent — all calls time out at once | **keep handlers off-loop** — see "A wedged shared MCP server" below |
| in-repo `.hermes-cortex/.governance-lock` | ONE path per repo shared by all sessions, so a peer's `end_change` could unlink your marker while you still hold your lock | **fixed** — `_release_lock` removes it only when the marker's `session_id` is its own (or absent); the session-scoped `.governance-<session>.json` stays authoritative either way |
| handoff state dirs | one session's stale marker appears in another's sweep | scope — session subdir |
| `cortex-update.sh` deploy | interleaved unlock/relock; a killed run leaves enforcement files WRITABLE | **serialise** — one deploy per host |
| immutable enforcement flags | come off during a deploy that dies | **serialise** (pitfall 4) |

**The principle: scope what can be scoped, serialise what cannot.** There is
exactly ONE deployed enforcement tree per host. Concurrency there must be
excluded by a lock, never "scoped" — scoping a shared resource only produces two
sessions that each believe they own it.

## A wedged shared MCP server ("every call times out")

MCP servers are spawned per PARENT process, not per session: one gateway owns ONE
`loop-gov-mcp.py` child, and every session that parent serves (interactive, cron,
delegated) shares it. A session blocked INSIDE a handler therefore blocks all of
them, and the symptom — every MCP call timing out at once — reads like a broken
host. It is not. Read the block state; never infer it:

```bash
ps -eo pid,ppid,wchan:22,etime,pcpu,args --no-headers | grep loop-gov-mcp
```

| `wchan` | Meaning |
|---|---|
| `do_epoll_wait` | idle and healthy — waiting for work |
| `poll_schedule_timeout` | BLOCKED in `subprocess.run(capture_output=True)` waiting on a child → that child's `ppid` is your culprit |
| `futex_do_wait` | waiting on a lock or queue |

```bash
ps -eo pid,ppid,etime,args --no-headers | grep -E '<server pid>|<reviewer name>'
```

The mechanism: `loop-gov-mcp.py`'s `call_tool` is `async` but dispatches with a
plain `return handler(args)`, and `end_change` runs the adversarial reviewer via
`subprocess.run(timeout=900)` / `urllib.urlopen(timeout=300)`. The MCP dispatcher
DOES spawn a task per request (`mcp/shared/jsonrpc_dispatcher.py`, `tg.start_soon`),
but asyncio tasks are cooperative — a blocking sync call starves all of them.
Hermes' client ceiling is `_DEFAULT_TOOL_TIMEOUT = 300` s
(`hermes-agent/tools/mcp_tool_common.py`), so a 900 s reviewer can never be waited
on: every queued call on that server hits the ceiling. Check BOTH numbers before
blaming the network.

**The rule for any handler in a shared server: never do blocking work on the loop.**
Dispatch through `await asyncio.to_thread(handler, args)`, guard the shared state
mutations (purge sweeps, the in-repo marker) with one `threading.Lock`, and bound
every external call BELOW the client ceiling so one tenant cannot outlive the
caller's patience. The governance STATE layer is already per-session
(session-scoped lock files; `begin_change`'s close-out gate filters
`WHERE session_id = ?`) — the coupling is transport/process, so fix it there rather
than by weakening a lock.

**Prove the dispatch is non-blocking from a SECOND thread or process — never from
the same event loop.** A loop starved by the handler cannot run your probe either,
so a same-loop measurement reports ~0 s and passes vacuously: the first attempt at
this test measured `0.00 s` for a call that had in fact waited the whole block.
Drive the server loop from a background thread, submit the slow call, wait on an
`Event` the handler sets INSIDE its critical section, then time a cheap call from
the main thread. Keep a control that exercises the OLD dispatch shape and assert
it still blocks — without that, a green test proves nothing.

## The convention: one session = one worktree = one branch = one identity

```bash
# from the primary checkout
git worktree add ~/hc-<tag> -b session/<tag> origin/main
cd ~/hc-<tag>
git config extensions.worktreeConfig true          # REQUIRED once — see pitfall 1
git config --worktree user.name  "<agent>-<tag>"
git config --worktree user.email "<agent>+<tag>@<domain>"
```

Branch from `origin/main`, never from a peer's dirty HEAD, or you inherit their
uncommitted work. A plain numeric tag (`2`, `3`) distinguishes sessions.
Confirm independence before trusting it:

```bash
printf 'main -> %s\n' "$(git -C <primary>  config user.email)"
printf 'mine -> %s\n' "$(git -C <worktree> config user.email)"   # must differ
git diff --cached --name-only                                     # only YOUR files
```

## Pitfalls

1. **`git config` inside a worktree writes the SHARED config unless you ask otherwise.**
   Without `extensions.worktreeConfig=true` + `git config --worktree …`, setting an
   identity for one session silently changes EVERY worktree's — the primary checkout
   picks up the session identity, which is the opposite of isolation. Set the
   extension first, then read the identity back in BOTH trees.
2. **Never session-append the BUS identity.** `AGENT_NAME` (agent.env) decides the
   `inbox_<agent>` queue and the on-bus name; appending a session tag breaks routing.
   Sessions differ in git AUTHORSHIP, a separate value. A helper that sets the
   identity should re-read `AGENT_NAME` afterwards and abort if it changed.
3. **Governance binds to the repo the SESSION was started in — a worktree is not it.**
   A worktree isolates the index, not the gate: a commit on a worktree branch is
   absent from the tree the close-gate diffs, so the review fails on material that
   cannot contain the change (it reports the new files as missing). Run the session
   FROM the worktree when the work lives there, or land the work in the session's own
   tree before closing — `begin_change` cannot be re-pointed mid-cycle, so a
   worktree-only change under a main-tree session is unclosable.
4. **A deploy that exits non-zero can leave the enforcement files WRITABLE.**
   `cortex-update.sh` unlocks the immutable targets early and relocks late, with no
   `EXIT` trap: a run that dies in between (non-zero exit, timeout, killed detached
   worker) leaves `chattr +i` off every target while the deploy merely looks failed.
   Detect with `hermes-plugin-lock status` — protected entries read
   `----i---------e-------`; `--------------e-------` means the flag is MISSING (the
   doctor's `Immutable: <name>` checks report the same as FAIL). Restore with
   `sudo -n hermes-plugin-lock lock`, which is NOPASSWD-granted. Re-check after ANY
   failed deploy; "ships unprotected" is a governance regression, not cosmetic.
5. **`sudo -n true` proves NOTHING about the command you need.** It tests `/bin/true`,
   which no NOPASSWD rule grants, so it always answers *"a password is required"* —
   even where the helper you need IS granted. Read the full `sudo -n -l` list and test
   the SPECIFIC command (`sudo -n -l <cmd>` for policy, then `sudo -n <cmd>` for
   execution). Never conclude a rule is absent from a TRUNCATED listing: the list runs
   to dozens of grants and `head`/`tail` hides the entry you want. Either mistake
   invents a wrong root cause and a wrong "needs the operator's password" blocker.
6. **Never assume uncommitted changes are yours.** With a session alive in the same
   tree, `git status` is shared news. Investigate before you stash, revert, or commit,
   and stage explicit paths rather than `git add -A`.
7. **Do not race a peer's in-flight work.** If another session is mid-update on shared
   state, `git pull --rebase` cannot pass its dirty tree ("cannot pull with rebase: You
   have unstaged changes") and a deploy will fight it. Work in your own worktree, or
   stand down until it finishes. Never resolve it by stashing, checking out or resetting
   their tree: prove the overlap FIRST — their paths from `git status --short` against
   the incoming paths from `git diff --name-only <old-head>..origin/main` — and only then
   `git merge --ff-only`, which is safe when the sets are disjoint and leaves their
 working tree untouched.

 **Operator-directed exception.** When the operator explicitly says to stash and land a
 peer's work, do it with a safety net and PROVE the round-trip — never on your own
 initiative. Backup first, then stash only their paths:

 ```bash
 BK=~/.hermes/cache/scratch/peer-stash-<date>; mkdir -p "$BK"
 git ls-files -s -- <paths> > "$BK/index-snapshot.txt"             # their index blobs
 git diff --cached --binary -- <paths> > "$BK/peer-staged.patch"   # binary-safe patch
 cp -a <paths> "$BK"/ ; (cd <dir> && sha256sum <files>) > "$BK/before.sha256"
 TREE=$(git write-tree)                                            # their staged tree
 SAFE=$(git commit-tree "$TREE" -p HEAD -m "safety: peer staged snapshot")
 git tag -f peer-stash-safety "$SAFE"        # durable ref: a prune cannot lose it
 git stash push -m "<what and why>" -- <paths>   # reset index AND worktree for those paths
 git merge origin/main
 git stash pop && (cd <dir> && sha256sum -c "$BK/before.sha256")
 ```

 - Find the blocking set before stashing anything — the merge names it, and
   `comm -12 <(git diff --name-only HEAD origin/main | sort) <(git status --porcelain | sed 's/^...//' | sort)`
   states it independently.
 - Use `git stash push -- <paths>`, NOT `--staged`: `--staged` resets only the index and
   leaves the worktree content behind, so the merge is refused exactly as before.
 - A file whose upstream side ALSO moved comes back from `git stash pop` as an UNSTAGED
   3-way merge, which is correct but changes their index state — re-`git add` it so their
   staged set is restored as it was. Only the file whose base moved can be affected.
 - Keep committing by PATHSPEC afterwards: their staged paths ride into any plain
   `git commit`.
 - **Before assuming the peer will land its own work, check that it CAN.** A cron session
   has no `skill_view` tool, so the pre-commit reflexion gate refuses EVERY commit it
   attempts — its staged bundle is stranded by construction (its own DOCS-INDEX row said
   so) and only an orchestrator can land it. That is the case in which the operator's
   "stash and close it out" instruction is the only exit.
 - **Modifying a peer's artifact needs the OPERATOR, not the gate.** If the gate refuses
   their file as-is (swallowed errors that can print a false PASS) and then refuses your
   hardening as `scope-drift` against the description that promised it unchanged, stop
   and present both findings: the gate can demand a change only the artifact's owner may
   authorise, and self-authorising it is the same class of error as overriding a finding.
   A cycle whose DESCRIPTION contradicts the diff cannot be talked out of it — see
   `governance-closeout`.
8. **A deploy+push races the peer's next commit.** The push gate compares HEAD with the
   last-deploy commit, so any commit landing during the minutes-long deploy invalidates
   it: a *successful* deploy is followed by "Deploy sync — HEAD ahead of last deploy".
   Re-deploying in a loop is whack-a-mole and multiplies deploy windows on a shared tree
   (each deploy also re-publishes whatever the peer has uncommitted there). The commits
   are already local — `git status -sb` shows `ahead N` — so ANY push from that host,
   theirs included, carries yours. Confirm what actually reached the remote with
   `git fetch -q && git log --oneline -1 origin/main` and coordinate rather than
   thrashing deployments; if the peer is mid-cycle, their push is the one that lands it.

9. **The deploy's sweep is the ONE shared step that can remove a PEER's lock — and it resolves no cycle.** Lock files are per session, so no session's close can touch another's; the collision lives entirely in the purge. Two things about it, both measured:
   - **A purge is TTL-only.** The standalone purge script (run by the deploy and by a cron) ages a lock by `heartbeat_at` against its own `ttl_seconds` and stops there — it has NO commit-liveness rescue, unlike the MCP's staleness predicate. So a session that is alive but has made no governed call for an hour is judged dead by a peer's deploy. You cannot arm this from the victim side; keep the heartbeat fresh (any governed call refreshes it) or accept the release.
   - **A purge removes lock FILES only; closing the cycles is a SEPARATE step.** The repair is `_resolve_orphaned_pending_cycles` (which `begin_change` invokes), not the purge. So a sweep that removes an abandoned session's lock leaves its cycle PENDING — and a PENDING cycle whose task has no non-terminal lock is a doctor FAIL that blocks EVERY agent's push on the host. The doctor only auto-resolves cycles older than 24h, so inside that window the leak sits with no owner left to clear it.

   **Verify any claim about this class by planting sessions, never by reasoning about them.** Repoint the module's state dir and DB globals at a throwaway directory, plant locks for each role (a mid-deploy session, a session in another repo, one about to push, one past its TTL), then drive the shipped purge, release and repair functions and assert what each touched — including that live locks are BYTE-IDENTICAL (sha256) before and after. Recipe and the exact checks: `references/lock-purge-and-ownership.md`.

10. **The WRITE gate is per REPO, so a second session never takes its own lock — it rides yours.**
   The enforcer's rule is "this repo has an active lock", satisfied by ANY session's lock for that
   repo, so a peer session commits freely while yours is live and acquires nothing. The hook then
   finds exactly ONE matching lock and stamps **your** id on the peer's commits. Locks are unique
   per session BY FILE NAME (`_session_lock_path(session_id)`), but that uniqueness never gets
   exercised: nothing forces the second session to call `begin_change`. So "are locks unique per
   session?" is the wrong question — the holes are (a) ownership at the write gate, and (b)
   attribution at commit time, and git gives the hook NO session identity to attribute with.

   **Diagnose "a peer's files are in my review" in this order — do not guess:**

   ```bash
   git status --short                      # EMPTY ⇒ the files are COMMITTED, not dirty
   git log --format='%h %s%n  trailer=%(trailers:key=Cortex-Session,valueonly)' -8
   grep -H -e '"session_id"' -e '"repo_path"' ~/.steadfaste-fleet/state/.governance-*.json
   ```

   - Empty `status` kills the working-tree/`dirty_at_start` theory immediately: the offending files
     are in the COMMITTED range, so stashing or reverting changes nothing and destroys real work.
     Never run a peer's stash/revert on this evidence — the peer's work is committed and yours to
     leave alone.
   - **Every commit carrying the SAME trailer while only ONE lock exists for that repo** ⇒ the peer
     rode your lock (pitfall above), not a two-lock ambiguity. A per-commit trailer cannot separate
     two committers who share one id, in either direction.
   - A stale commit trailer cannot be corrected after the fact; only a new commit gets a new one.

   **Two scope bugs follow from the same root**, and both need fixing for N sessions:
   a) the **per-blob review demand is the WHOLE UNPUSHED RANGE** (`push_base..HEAD`), regardless of
      author or session — so whichever session closes first is judged on every unpushed file, its
      peer's included. Scope it by the attributed session the way the diff already is, disclosing
      peers by name instead of demanding verdicts for them.
   b) the consumer must treat an **unattributed** commit as in-scope (fail closed, disclosed at
      info) — never as "mine by default", which is what makes a wrong stamp silently harmful.

   **Attribution is now session-scoped in code; the shared lock NAMESPACE is the part left to you.**
   The hook stamps a commit only for the SINGLE claimant whose lock was refreshed recently (the server
   rewrites the lock on every governed call, so its mtime IS that session's last activity) and writes
   NO trailer when zero or several claimants are equally fresh — an absent trailer keeps the commit in
   scope for everyone (fail-closed, visible), while a wrong one silently credits your files to a peer
   and makes theirs yours to answer for. The per-blob DEMAND is scoped the same way: each path is
   attributed to the commit that produced its CURRENT content (range walked newest-first) and a
   peer-attributed path is disclosed rather than demanded — the receipt then covers only your own
   blobs, so the push gate still refuses that range until its OWNER closes it. What no hook can fix is
   that a session taking no lock of its own still commits under yours, so give each session its own
   git worktree: separate `repo_path` ⇒ separate locks ⇒ the path match is unambiguous. Do that before
   filing another scope bug.

11. **The INDEX carries no provenance — a bare `git commit` sweeps whatever is staged, and a
    pathspec commit structurally cannot.** The index is ONE shared mutable structure whose entries
    record nothing about WHICH session staged them, so "these staged files are not mine" is not
    answerable from the tree. The cheapest control needs no provenance at all: a PARTIAL commit uses
    its own temporary index, so it can only ever include the paths you name. Measured as seen by a
    `pre-commit` hook:

    ```
    bare commit      GIT_INDEX_FILE=[.git/index]
    pathspec commit  GIT_INDEX_FILE=[…/.git/next-index-<pid>.lock]
    # and after the pathspec commit the foreign entry is STILL staged
    ```

    So a hook CAN refuse a bare commit in the shared repo and require `git commit -- <paths>`, which
    kills the sweep class instead of merely discouraging it. Compare `$GIT_INDEX_FILE` against
    `$(git rev-parse --git-dir)/index`; a difference means a partial commit. Prove the detection with
    a self-test inside the hook (the way an identity guard is self-tested), and exempt a sanctioned
    automated committer whose allowlist already proves every staged path is its own.

    **The same shared index also blocks a MERGE**, and the refusal misleads: `git merge` stops with
    *"Your local changes to the following files would be overwritten by merge"* while entries are
    staged, listing paths the merge would not even touch. Determine WHOSE they are before deciding —
    if they are your OWN earlier pass's leftovers (an evidence dump staged and never committed),
    unstaging is legitimate: `git reset -q -- <paths>` leaves the files on disk as untracked and
    unblocks the merge. Never do that to a live peer's staged work; see pitfall 7. Note also that
    `git ls-tree origin/main -- <path>` returning empty is the proof those files were never pushed —
    do not infer from the merge's wording that the remote already has them.

12. **A non-Hermes coding agent's governance id collapses to ONE per repo unless the
    harness exports a per-session env value — and detecting the sharing is not fixing it.**
    `get_session_id()` resolves: per-call injection (Hermes enforcer) → env
    `CORTEX_SESSION_KEY` then `PI_SESSION_ID` → per-repo persisted `state/.session-<slug>.id`.
    That fallback is ONE id per repo BY CONSTRUCTION (a restart-stability trade for bare
    harnesses), so every env-less Pi/Claude/Codex session in the repo resolves the SAME id and
    the SAME lock file: a new session "picks up" the existing session's lock, and a close can
    release a peer's. Pitfall 10 is the Hermes write-gate half of this; this is the non-Hermes
    resolver half, and it fires even when the second session correctly calls `begin_change`.

    - **Uniqueness comes ONLY from the env step, and only if the harness actually puts the
      value in the MCP child's environment — a variable can reach ONE subprocess class and be
      absent from ANOTHER, so measure the seam for the exact consumer, never trust a documented
      merge (cross-agent-design rule 6).** Two variants each dead-end the resolver's env read:
      an MCP `env` block that REPLACES the child env (a harness passing `config.env` straight to
      Node `spawn(env=…)` strips every inherited var, `CORTEX_SESSION_KEY` included), and the
      subtler one on Pi — Pi's stdio transport MERGES (`{...process.env, ...config.env}`), so the
      block is fine, but Pi exports `PI_SESSION_ID` only into bash-tool commands and never onto
      its own `process.env`, so the governor's MCP child inherits nothing regardless. Verify
      which variant the host has by driving the harness's own launcher and reading the child's
      survives — measured: `loop-gov.py session_identity '{}'` answers `per-repo, shared` on a
      live Pi session.
    - **The env-var NAME and its reach differ per harness — survey the exact one, never assume
      a sibling's `*_SESSION_ID` is the other's.** Verified from each harness's own source (a
      name documented as *set by the agent* does not mean it lands on the MCP child): Claude
      Code injects `CLAUDE_CODE_SESSION_ID` (note `CODE`; `CLAUDE_SESSION_ID` is NOT set) into
      stdio MCP children as of v2.1.154, so it needs no adapter; Codex sets `CODEX_SESSION_ID`
      but forwards it only to shell commands, so an MCP child gets it only when pinned in
      `[mcp_servers..env]` or by an upstream change. The resolver honours all of them
      (`_ENV_SESSION_ID_SOURCES` in loop-gov-mcp.py) and falls back to the shared per-repo id
      for a harness that supplies none — full matrix: `cross-agent-design` 'N heterogeneous
      coding agents'.
    - **Diagnose which source resolved with `loop-gov.py session_identity '{}'`.** A `per-repo`
      answer on a live session proves no unique env id reached the child.
    - **Fail-closed ownership (a `shared` id that is never claimable) converts silent theft
      into visible blocking; it does NOT give sessions separate locks.** The fix lives at the
      propagation seam — not in more resolver hardening. A static MCP `env` block CANNOT carry
      a per-session value, so for Pi the working fix is the harness's lifecycle hook setting
      `CORTEX_SESSION_KEY` from its own session manager (`ctx.sessionManager.getSessionId()` in
      `before_agent_start`, run BEFORE the MCP child spawns; only-if-unset so a gateway pin
      wins) — proof at `ops/install/harnesses/pi/extensions/fleet-context.ts`. The
      fleet-gateway `command` backend already pins it per chat when a session runs through the
      gateway; passing an explicit per-call `session_id` WITH `repo_path` remains the
      CLI/Claude-Code workaround.
    - **Slug instability is the opposite failure of the same fallback:** when slug resolution
      falls through to the child's cwd and that cwd is a temp dir, the slug is `tmp.<random>` —
      a fresh random id per spawn (unstable across an MCP-child restart, so locks orphan) and
      orphaned `state/.session-tmp.*.id` files accumulate.

## What a shared-server fix must also preserve

- **A shared fixed-path file is only removed by its owner.** The in-repo
  `.hermes-cortex/.governance-lock` marker is ONE path for the whole repo, so
  `_release_lock` unlinks it only when the marker's `session_id` is its own (or
  absent). Otherwise session A's close silently unmarks session B's live lock.

**A lock's `repo_slug` is a HOST fact, not a session fact.** `_derive_slug()`
prefers the canonical `~/steadfaste-fleet`, so a session working in another repo
gets a lock tagged with the canonical one — and the close gate then reviews a
tree that cannot contain the work: permanent FINDINGS, an unclosable cycle, and
one full review re-run per retry. The enforcer injects the session's real repo
(learned from the paths its own tool calls touch, since a `pre_tool_call` hook
is never given a cwd), and the gate refuses loudly when the lock's repo shows no
diff and no commits in the window while another candidate repo gained commits.

**`repo_slug` is a NAME, so carry the ABSOLUTE path too.** The slug resolves as
`HOME/<slug>` — a contract the git hooks share — which silently mis-resolves (or
resolves to nothing) for a checkout nested below a HOME child, i.e. a dev host
with many project repos (`~/projects/<org>/<repo>`), re-creating the wrong-repo
review on exactly the hosts a slug-only fix cannot reach. Carry BOTH: `repo_path`
(absolute, observed per session and injected by the enforcer) decides which tree
is parsed, while `repo_slug` (the basename) MUST stay, because the git hooks match
locks by name. Resolve in this order — recorded `repo_path`, then legacy
`HOME/<slug>`, then FAIL CLOSED; never guess a tree to review.

Three consequences: a same-basename repo sitting at `HOME/<slug>` must never win
over the recorded path; the in-repo `.hermes-cortex/.governance-lock` marker
follows the absolute path, so a nested repo gets its marker inside itself rather
than at `HOME/<slug>`; and the wrong-repo guard's candidate scan must walk HOME a
few levels (skipping hidden dirs except `.hermes-cortex`, and treating a directory
that IS a repo as a leaf) or a nested repo's commits are invisible to the guard
and it will happily review the canonical repo instead. Accept an injected path
only when it is a real git repo, so a bogus or stale value cannot aim a review at
an arbitrary directory.

**deploy is not load — and the two halves reload differently.** An MCP fix needs
the daemon restarted (kill the child; the parent respawns it on the next call —
probe and post-restart check in `governance-closeout`). An ENFORCER/plugin fix
needs a session reload an in-session agent CANNOT perform, and the deploy says so
itself: grep its output for `Run /reset to load the updated enforcer plugin`
rather than inferring the reload happened. `kill` is an approval-gated
destructive command — if the approval does not arrive, do NOT retry it and do NOT
route around it (no alternate kill, no pkill variant): stand down, name which
half is still unloaded, and close the cycle. Always state which half is loaded
and which is pending; never report a fix as live because it deployed.

**A timing window must be compared as an epoch, not with `git log --since=`**:
approxidate returned 0 commits for a commit made seconds earlier when the bound
was a Z-suffixed UTC instant and the commit date carried a local offset. Use
`git log --format=%ct` and compare integers.

## Verification

- `git worktree list` shows one tree per active session, each on its own branch.
- Each tree's `config user.email` differs; `AGENT_NAME` is unchanged fleet-wide.
- A commit in each tree is authored by that tree's identity
  (`git log -1 --format='%h %an <%ae>'`).
- After any deploy: `hermes-plugin-lock status` shows `----i` on every target.

## Related

- `governance-closeout` — closing a cycle the gate refuses; the worktree/repo binding
  pitfall is stated there from the close-out side.
- `governance-lock-lifecycle`, `cortex-bus`, `fleet-commands`, `sudoers-audit` —
  relevant but USER-OWNED: `hermes curator adopt <name>` before extending them.
