# Lock ownership, purge semantics, and how to verify a multi-session claim

## When to Use

Load this when two or more sessions share a host and you must answer what one session's deploy,
close or sweep does to another's lock and cycle — or when you must PROVE that answer rather than
reason about it. Depth for pitfall 9 and the lock row of the collision table; everything below was
measured by planting sessions in a throwaway state dir + DB.

## Who may remove what

| File | Owner rule |
|---|---|
| `~/.hermes-cortex/state/.governance-<session_id>.json` | one per session; a session's release unlinks only ITS OWN |
| `<repo>/.hermes-cortex/.governance-lock` | ONE path for the whole repo, so it is removed only when its recorded `session_id` is the remover's (or absent) |
| loop-governance DB rows | carry `session_id`; a close filters on it |

The consequence to rely on: **a session's own close cannot disturb a peer's lock or cycle.**
The consequence to watch: a purge sweep has no such filter — it removes any lock it judges stale.

## Purge semantics (both implementations)

- `ops/scripts/manage/purge-stale-governance-locks.py` — standalone, run by the deploy and by a
  cron. Ages the lock by `heartbeat_at` (falling back to the file's mtime when the timestamp
  cannot be parsed in-band) and stops there. **TTL-only: no commit-liveness rescue.**
- The MCP's `_purge_stale_locks` — a thin file remover. Its staleness predicate is the richer
  one (a peer that has committed since is treated as alive), so the two disagree by design: the
  MCP keeps a lock the standalone sweep would remove.
- **Neither one closes cycles.** The repair is `_resolve_orphaned_pending_cycles(task_ids=None)`
  — `begin_change` calls it, and `None` sweeps every lockless PENDING cycle. A lockless PENDING
  cycle younger than the doctor's 24h auto-resolve window is a FAIL that blocks every push on the
  host, with no owner left to fix it.
- An already-purged lock cannot be reclaimed by a tool that judges lock FILES — the repair path
  for that state is the resolver, not the reclaim.

## Recipe: prove three simultaneous sessions stay isolated

Throwaway state dir and DB, then drive the REAL functions (see
`docs/evidence/three-session-isolation-probe.py` in the fleet repo for a working instance):

1. Load the MCP module by file path, then repoint `LOOP_DB` and `GOVERNANCE_STATE_DIR` at a
   `mkdtemp` tree BEFORE creating any lock or cycle.
2. Plant one lock per role, each with its own `session_id`, `task_id` and `repo_path`: a session
   mid-deploy, a session in its OWN repo, a session about to push, and one whose heartbeat is
   past its own TTL. Seed a PENDING cycle per task.
3. Hash the live locks (`sha256` truncated) so "untouched" is a byte claim, not an eyeball.
4. Run the deploy's own purge against the throwaway dir (repoint its `STATE_DIR` first — and see
   the import warning below). Assert: only the past-TTL lock goes; the three live locks survive
   byte-identical; the abandoned session's cycle is STILL PENDING (that is the gap, not a bug in
   your probe).
5. Release the pushing session's lock and close its cycle. Assert: its file is gone, its cycle is
   MOVE_ON, and every peer's lock and cycle are untouched.
6. Run the repair and assert it closes the orphaned cycle — and that it still touches no live
   session's cycle.

Two traps that cost real time here:

- **A script that calls its work at module level fires on IMPORT.** Repointing its `STATE_DIR`
  after the import is too late; the sweep already ran against the live directory. Before
  importing any file you do not own, read the END of that file with `read_file` for a bare call +
  `sys.exit`, and if it has one, either fix the guard (the durable fix) or copy the source and
  strip the entrypoint.
- **`mod._release_lock({"session_id": ...})` is the honest way to prove a close is scoped** —
  passing the session id explicitly removes the doubt that identity was resolved from ambient
  state.

## When the host itself is the subject

Measuring a defect's signature on the LIVE tree is the strongest evidence, but only when it is
read-only: count the entries in the real state dir before and after an import and assert the
count is unchanged. A probe that mutates live state to prove something is a defect, not a test.
