"""Regression tests: non-Hermes governance callers (Claude Code) — 2026-09-29.

Luke's report: Claude Code on a separate repo (titus) "couldn't use governance
properly". Two root causes, both covered here:

  A. get_session_id() for a caller WITHOUT the enforcer's per-call injection
     used to fall back to the HOST-GLOBAL ~/.hermes/session.id cache and the
     Hermes marker files — every session on the box resolved to the SAME id,
     so begin_change from one Claude invocation blocked the next, and any
     session could release another's lock. Now: a process-scoped id, stable
     within one MCP child (= one Claude session), disjoint across processes,
     and NEVER adopts a Hermes marker or the shared cache.

  B. (installer) install-claude-governance.sh registers the governance MCP
     servers at USER scope in ~/.claude.json so every repo gets them
     (.mcp.json stays as the per-repo override). Tested by
     test_claude_governance_installer.sh.

  C. (2026-10-27) get_session_id() gained resolution step 0e: a caller's own
     environment (FLEET_SESSION_KEY, then PI_SESSION_ID) is used before the
     per-repo fallback, because one MCP child serves one session. That path is
     covered by tests/test_harness_session_id_env.py; THIS file measures the
     no-env fallback, so it removes those variables for the run.

Run:  python3 tests/test_non_hermes_session_id.py
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

_MCP_PATH = Path(__file__).resolve().parents[1] / "mcp-servers" / "loop-gov-mcp.py"
_spec = importlib.util.spec_from_file_location("loop_gov_mcp_session", _MCP_PATH)
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)

_FAIL = []

# Ambient-environment guard. Resolution step 0e reads a session id from the
# caller's own environment (FLEET_SESSION_KEY, then PI_SESSION_ID), so a runner
# that happens to have one set makes these tests measure the RUNNER instead of
# the fallback rule they name — measured 2026-10-27: with FLEET_SESSION_KEY set,
# the per-repo file is never written and three checks fail. Save and remove them
# for the duration of the run, restore in main()'s finally. The env-provided path
# is covered on purpose by tests/test_harness_session_id_env.py.
_ENV_SESSION_KEYS = ("FLEET_SESSION_KEY", "PI_SESSION_ID")
_SAVED_ENV = {k: os.environ.pop(k, None) for k in _ENV_SESSION_KEYS}


def _restore_env():
    for _k, _v in _SAVED_ENV.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


def _check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        _FAIL.append(name)
        print(f"  FAIL  {name} — {detail}")


def test_hermes_injection_unchanged():
    """Priority 0 (enforcer per-call injection) still wins and is stable."""
    sid = mcp.get_session_id({"session_id": "sess_hermes_abc"})
    _check("hermes: injected id returned verbatim", sid == "sess_hermes_abc")
    sid2 = mcp.get_session_id({"session_id": "sess_hermes_def"})
    _check("hermes: a new injected id wins on the next call",
           sid2 == "sess_hermes_def", f"got {sid2}")


def test_non_hermes_gets_process_scoped_id():
    """No injection → process id, NOT the host-global cache or a Hermes marker."""
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        state.mkdir()
        # A STALE Hermes marker + a poisoned shared cache — the old code would
        # adopt one of these and collide with whatever session wrote them.
        (state / ".hermes-session-current.id").write_text("sess_stale_hermes")
        (Path(td) / "shared-session.id").write_text("sess_shared_cache")

        mcp._PROCESS_SESSION_ID = ""  # fresh process
        mcp.SESSION_FILE = Path(td) / "shared-session.id"  # poisoned cache
        mcp.GOVERNANCE_STATE_DIR = state
        mcp._derive_slug = lambda: "proj-x"

        a = mcp.get_session_id(None)
        b = mcp.get_session_id(None)
        _check("non-hermes: id is stable across calls in one process", a == b and a)
        _check("non-hermes: does NOT adopt the stale Hermes marker",
               a != "sess_stale_hermes", a)
        _check("non-hermes: does NOT adopt the shared host cache",
               a != "sess_shared_cache", a)
        _check("non-hermes: id has the sess_ prefix", a.startswith("sess_"))
        _check("non-hermes: persisted per project",
               (state / ".session-proj-x.id").read_text().strip() == a)


def test_two_processes_get_disjoint_ids():
    """Different projects stay disjoint; a mid-session MCP-child RESTART in
    the same project keeps the SAME id (adversarial ADV-2872-2: an
    in-memory-only id would orphan the lock on restart)."""
    def fresh_process(slug):
        spec = importlib.util.spec_from_file_location(
            f"lgm_{os.urandom(4).hex()}", _MCP_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._derive_slug = lambda: slug  # control the project identity
        return mod

    with tempfile.TemporaryDirectory() as td:
        stateA = Path(td) / "stateA"; stateA.mkdir()
        stateB = Path(td) / "stateB"; stateB.mkdir()

        # Session in project alpha: two fresh processes (original + restart).
        m1 = fresh_process("alpha"); m1.GOVERNANCE_STATE_DIR = stateA
        id1 = m1.get_session_id(None)
        m2 = fresh_process("alpha"); m2.GOVERNANCE_STATE_DIR = stateA
        id2 = m2.get_session_id(None)
        _check("restart in same project keeps the SAME id (lock survives)",
               id1 == id2 and id1, f"{id1} vs {id2}")

        # Session in a different project: must be disjoint.
        m3 = fresh_process("beta"); m3.GOVERNANCE_STATE_DIR = stateB
        id3 = m3.get_session_id(None)
        _check("different project gets a DISJOINT id", id3 != id1, f"{id1} vs {id3}")

        # Persistence file exists per project.
        _check("per-project session file written",
               (stateA / ".session-alpha.id").read_text().strip() == id1)


def test_same_repo_concurrent_sessions_serialize():
    """Two concurrent non-Hermes sessions in ONE repo that supply NO session id
    (neither FLEET_SESSION_KEY nor PI_SESSION_ID) share the per-repo project id
    — and the SECOND begin_change is REFUSED while the first's cycle is PENDING
    (serialization via the close-out gate, not silent lock theft).

    That sharing is the documented FALLBACK, not the whole behaviour: a session
    whose environment carries an id gets its own (tests/test_harness_session_id_env.py,
    added 2026-10-27 with resolution step 0e). This test pins the fallback so it
    cannot silently change, and the ambient-environment guard above keeps it
    measuring the fallback rather than whatever the runner exported.
    """
    with tempfile.TemporaryDirectory() as td:
        # Hermetic cwd: a tiny git repo so end_change's complexity measurement
        # sees an empty diff (trivial → no live adversarial review in a test)
        # instead of measuring THIS repo's untracked files.
        repo = Path(td) / "sandbox-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
        # core.hooksPath is GLOBAL on a fleet host, so the sandbox commit runs the
        # cortex pre-commit hook — which refuses a bare commit ("BARE COMMIT
        # REFUSED — it would commit whatever is staged") and made this test RED on
        # every host with the hooks installed. Point the sandbox at no hooks.
        subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/nonexistent",
                        "-c", "user.email=t@t.t",
                        "-c", "user.name=t", "commit", "--allow-empty", "-m", "init"],
                       check=True, capture_output=True)
        prev_cwd = os.getcwd()
        os.chdir(repo)
        try:
            state = Path(td) / "state"; state.mkdir()
            spec = importlib.util.spec_from_file_location(
                f"lgm_{os.urandom(4).hex()}", _MCP_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.GOVERNANCE_STATE_DIR = state
            mod.LOOP_DB = Path(td) / "loop.db"
            mod.HOME = Path(td)  # review gate resolves repo as HOME/<repo_slug>
            mod._PROCESS_SESSION_ID = ""  # fresh non-Hermes MCP child

            # Session A and session B resolve to the SAME project id.
            sid_a = mod.get_session_id(None)
            sid_b = mod.get_session_id(None)
            _check("same-repo sessions share the project id", sid_a == sid_b)

            # Session A begins a change.
            r1 = mod._begin_change({"task_id": "task-a", "description": "session A work"})
            t1 = r1.content[0].text
            _check("session A begin_change succeeds",
                   "Governance session started" in t1, t1[:120])

            # Session B (same repo, same id) must be REFUSED while A holds it.
            r2 = mod._begin_change({"task_id": "task-b", "description": "session B work"})
            t2 = r2.content[0].text
            _check("session B begin_change REFUSED (serialized, not stolen)",
                   "already" in t2.lower() or "pending" in t2.lower() or "refus" in t2.lower()
                   or "cannot" in t2.lower(),
                   t2[:200])

            # A closes out (scored) and releases; only then can B begin.
            mod._feedback_accept({"task_id": "task-a", "note": "done, verified",
                                  "completeness": 8, "quality": 8, "progress": 8})
            re = mod._end_change({"task_id": "task-a"})
            te = re.content[0].text
            _check("session A end_change releases",
                   "closed" in te.lower() or "released" in te.lower(), te[:150])
            r3 = mod._begin_change({"task_id": "task-b", "description": "session B now"})
            _check("session B can begin after A closed",
                   "Governance session started" in r3.content[0].text, r3.content[0].text[:120])
        finally:
            os.chdir(prev_cwd)


def _seed_stale_lock(state_dir: Path, sid: str, task_id: str, repo_path: str = ""):
    """Write a lock under `sid` whose heartbeat aged past its TTL, deterministically.

    The seeded agent name matches no commit anywhere (liveness checks return
    False), so staleness is decided by age alone — no git history to rescue it.
    ``repo_path=""`` mirrors the incident shape (a session launched outside any
    git repo records an empty repo_path).
    """
    old = (datetime.now(timezone.utc) - timedelta(hours=2)) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")
    lock = {
        "task_id": task_id,
        "description": "seeded stale lock",
        "repo_slug": "proj-x",
        "repo_path": repo_path,
        "started_at": old,
        "agent": "definitely-not-an-author",
        "session_id": sid,
        "session_type": "interactive",
        "ttl_seconds": 3600,
        "heartbeat_at": old,
        "scored": False,
        "status": "executing",
    }
    path = state_dir / f".governance-{sid}.json"
    path.write_text(json.dumps(lock))
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(path, (old_ts, old_ts))
    return path


def _fresh_sandbox_module(td: str):
    """A fresh non-Hermes MCP module in a hermetic sandbox git repo.

    cwd sits INSIDE the repo (so ``_session_repo_path`` resolves and the
    dogfood gate passes), with temp state dir + loop DB, temp HOME (the
    anywhere-liveness scan finds nothing), and no ambient session env. Returns
    ``(mod, repo, state)`` — the caller chdirs around the calls it makes.
    """
    repo = Path(td) / "sandbox-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    # core.hooksPath is GLOBAL on a fleet host, so the sandbox commit runs the
    # pre-commit hook — point the sandbox at no hooks.
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/nonexistent",
                    "-c", "user.email=t@t.t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "init"],
                   check=True, capture_output=True)
    state = Path(td) / "state"
    state.mkdir()
    spec = importlib.util.spec_from_file_location(
        f"lgm_{os.urandom(4).hex()}", _MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.GOVERNANCE_STATE_DIR = state
    mod.LOOP_DB = Path(td) / "loop.db"
    mod.HOME = Path(td)  # anywhere-liveness scan finds no repos, no matching author
    mod._PROCESS_SESSION_ID = ""  # fresh non-Hermes MCP child
    return mod, repo, state


def test_shared_id_stale_lock_is_refused_not_overwritten():
    """THE 2026-11-23 regression (Titus Pi incident): session B's begin_change
    with a STALE same-id lock must REFUSE and leave the peer's lock AND cycle
    intact — never purge-and-rewrite.

    Pre-fix, Step 0's purge removed the aged-heartbeat lock (same host, same
    session-id file, same empty repo_path), Step 1 then read "no lock" and B
    overwrote session A's live lock while A was still working. With a shared id
    the purge cannot distinguish "crashed me" from "live peer", so the only
    safe action is to refuse and disclose; the TTL sweep is the sole cleaner.
    """
    with tempfile.TemporaryDirectory() as td:
        mod, repo, state = _fresh_sandbox_module(td)
        prev_cwd = os.getcwd()
        os.chdir(repo)
        try:
            sid = mod.get_session_id(None)  # shared per-repo id (no env)
            _seed_stale_lock(state, sid, "task-a", repo_path="")
            # Cycle seeded under a DIFFERENT session id so the close-out gate
            # cannot pre-empt the lock refusal — the purge must be the only
            # thing that could have removed the lock.
            mod._create_pending_cycle("task-a", "sess_peer_other", "seeded by test")

            r = mod._begin_change({"task_id": "task-b", "description": "peer tries to begin"})
            t = r.content[0].text
            _check("E1: begin_change REFUSED (lock not clobbered)",
                   "already active" in t, t[:200])
            _check("E1: refusal names the shared-id hazard",
                   "NOT provably yours" in t, t[:250])
            lock_path = state / f".governance-{sid}.json"
            _check("E1: peer lock file still exists", lock_path.exists())
            if lock_path.exists():
                kept = json.loads(lock_path.read_text())
                _check("E1: lock still belongs to task-a (not overwritten)",
                       kept.get("task_id") == "task-a", str(kept.get("task_id")))
            conn = mod._db()
            row = conn.execute(
                "SELECT decision FROM loop_cycles WHERE task_id='task-a'").fetchone()
            conn.close()
            _check("E1: task-a cycle still PENDING (purge did not resolve it)",
                   bool(row) and row["decision"] == "PENDING", str(dict(row) if row else None))
            locks = list(state.glob(".governance-*.json"))
            _check("E1: no task-b lock was created", len(locks) == 1, ", ".join(x.name for x in locks))
        finally:
            os.chdir(prev_cwd)


def test_unique_id_self_heal_preserved():
    """A session with a UNIQUE (env) id may still purge its OWN stale leftover
    lock at begin_change — the skip applies ONLY to the shared fallback."""
    os.environ["FLEET_SESSION_KEY"] = "e2-unique"
    try:
        with tempfile.TemporaryDirectory() as td:
            mod, repo, state = _fresh_sandbox_module(td)
            prev_cwd = os.getcwd()
            os.chdir(repo)
            try:
                sid = mod.get_session_id(None)  # env-derived unique id
                _check("E2: env id is unique-shaped", sid.startswith("sess_") and "e2" in sid, sid)
                _seed_stale_lock(state, sid, "e2-stale", repo_path="")
                mod._create_pending_cycle("e2-stale", sid, "seeded by test")

                r = mod._begin_change({"task_id": "e2-new", "description": "fresh start"})
                t = r.content[0].text
                _check("E2: begin_change SUCCEEDS (own stale lock self-healed)",
                       "Governance session started" in t, t[:150])
                locks = list(state.glob(".governance-*.json"))
                _check("E2: exactly one lock, the new task",
                       len(locks) == 1
                       and json.loads(locks[0].read_text()).get("task_id") == "e2-new",
                       ", ".join(x.name for x in locks))
            finally:
                os.chdir(prev_cwd)
    finally:
        os.environ.pop("FLEET_SESSION_KEY", None)


def test_shared_caller_purges_different_id_stale_lock():
    """A SHARED-id caller may still purge a stale lock owned by a DIFFERENT id
    (crash cleanup for shared callers preserved) — the skip set is exactly the
    caller's own id, never a blanket same-id exemption."""
    with tempfile.TemporaryDirectory() as td:
        mod, repo, state = _fresh_sandbox_module(td)
        prev_cwd = os.getcwd()
        os.chdir(repo)
        try:
            sid = mod.get_session_id(None)  # shared per-repo id (no env)
            _seed_stale_lock(state, "sess_e3_peer_dead", "e3-peer", repo_path="")
            mod._create_pending_cycle("e3-peer", "sess_e3_peer_dead", "seeded by test")

            r = mod._begin_change({"task_id": "e3-new", "description": "cleanup and start"})
            t = r.content[0].text
            _check("E3: begin_change SUCCEEDS after purging the DIFFERENT-id stale lock",
                   "Governance session started" in t, t[:150])
            locks = list(state.glob(".governance-*.json"))
            _check("E3: peer lock gone, exactly the new lock remains",
                   len(locks) == 1
                   and json.loads(locks[0].read_text()).get("task_id") == "e3-new",
                   ", ".join(x.name for x in locks))
        finally:
            os.chdir(prev_cwd)


def main():
    try:
        print("A. Hermes per-call injection (unchanged)")
        test_hermes_injection_unchanged()
        print("B. Non-Hermes caller isolation")
        test_non_hermes_gets_process_scoped_id()
        print("C. Process disjointness + restart stability")
        test_two_processes_get_disjoint_ids()
        print("D. Same-repo concurrent sessions serialize")
        test_same_repo_concurrent_sessions_serialize()
        print("E. Shared-id stale locks are refused, never purged-and-rewritten")
        test_shared_id_stale_lock_is_refused_not_overwritten()
        test_unique_id_self_heal_preserved()
        test_shared_caller_purges_different_id_stale_lock()
    finally:
        # Put back whatever the runner had, even when a check raises — a test that
        # leaks a mutated environment poisons every later section of the process.
        _restore_env()
    print()
    if _FAIL:
        print(f"{len(_FAIL)} FAILED: {', '.join(_FAIL)}")
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
