#!/usr/bin/env python3
"""A live lock must LABEL whose it is, so no caller reads a peer's lock as its own.

Titus Pi incident (2026-11-10): a Pi session with a UUID ``PI_SESSION_ID``
(``01a1c490-…``) resolved a governance id via the per-repo fallback
(``sess_a955…``) — the SAME id every session in that repo resolves without an env
discriminator — and the MCP ``check_lock``/``session_identity`` surfaced that
shared id as ``active``/``effective`` with NO "whose is this" label. It read the
peer's lock as its own and wrote believing it was covered.

The fix is explicit ownership labelling, fail-closed:
  * ``_identity_source(args)`` -> (source_label, own_id, shared) — the caller's id
    is unique ONLY when it came from the enforcer (injected) or an env var; the
    per-repo fallback is SHARED by construction.
  * ``_lock_is_mine(own_sid, lock_sid, shared)`` -> ownership is claimable only
    for a unique id whose value equals the lock's. A shared id NEVER claims a lock.
  * ``check_lock`` and ``session_identity`` add ``identity_source``,
    ``shared_identity``, ``lock_is_mine``, ``ownership`` and label every
    ``locks[]`` entry with ``is_mine``. ``active:true`` / ``effective_session_id``
    still mean what they always meant (a lock exists / the evidence key) — the new
    fields are what a caller must read before claiming ownership.

Hermetic: the MCP module's ``GOVERNANCE_STATE_DIR`` is patched to a tmp dir and
env vars are monkeypatched, so no real lock, DB or session is touched.
Run: python3 tests/test_lock_ownership_labels.py   or   pytest tests/test_lock_ownership_labels.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _mcp():
    spec = importlib.util.spec_from_file_location("loop_gov_lockown", REPO / "mcp-servers" / "loop-gov-mcp.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["loop_gov_lockown"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_lock(state_dir: Path, session_id: str, **over) -> Path:
    state = {
        "task_id": "own-task",
        "session_id": session_id,
        "status": "executing",
        "heartbeat_at": "2099-01-01T00:00:00Z",
        "ttl_seconds": 3600,
        "repo_slug": "steadfaste-fleet",
        "repo_path": str(REPO),
    }
    state.update(over)
    p = state_dir / f".governance-{session_id}.json"
    p.write_text(json.dumps(state))
    return p


# ── _identity_source: which identity resolved THIS caller ────────────────────

def test_injected_id_is_unique(monkeypatch):
    m = _mcp()
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.delenv("CORTEX_SESSION_KEY", raising=False)
    src, sid, shared = m._identity_source({"session_id": "sess_injected_abc"})
    assert src == "injected"
    assert sid == "sess_injected_abc"
    assert shared is False


def test_env_id_is_unique(monkeypatch):
    m = _mcp()
    monkeypatch.setenv("PI_SESSION_ID", "01a1c490-2f30-")
    src, sid, shared = m._identity_source({})
    assert src == "env:PI_SESSION_ID"
    assert sid.startswith("sess_pi_session_id_")
    assert shared is False


def test_per_repo_fallback_is_shared(monkeypatch):
    m = _mcp()
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.delenv("CORTEX_SESSION_KEY", raising=False)
    # no injected id, no env -> per-repo fallback, SHARED
    src, sid, shared = m._identity_source({})
    assert src == "per-repo"
    assert shared is True


# ── _lock_is_mine: ownership is claimable only for a unique, equal id ────────

def test_equal_unique_id_is_mine():
    m = _mcp()
    assert m._lock_is_mine("sess_me", "sess_me", shared=False) is True


def test_unequal_unique_id_is_not_mine():
    m = _mcp()
    assert m._lock_is_mine("sess_me", "sess_other", shared=False) is False


def test_shared_id_never_claims_a_lock_even_when_equal():
    m = _mcp()
    # THE Titus case: caller resolved the shared per-repo id, and it happens to
    # equal a lock on disk — but that id is shared by every session in the repo,
    # so it is NOT proof of ownership. Fail closed: not mine.
    assert m._lock_is_mine("sess_shared", "sess_shared", shared=True) is False


# ── _live_locks annotates every lock with is_mine ────────────────────────────

def test_live_locks_adds_is_mine_per_entry(tmp_path, monkeypatch):
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    _write_lock(tmp_path, "sess_mine")
    _write_lock(tmp_path, "sess_other")
    locks = m._live_locks(own_sid="sess_mine", shared=False)
    by_sid = {l["session_id"]: l["is_mine"] for l in locks}
    assert by_sid == {"sess_mine": True, "sess_other": False}, by_sid
    # additive: caller that passes nothing keeps the pre-hardening shape
    locks_bare = m._live_locks()
    assert "is_mine" not in locks_bare[0], locks_bare


# ── _check_lock: active:true no longer implies MY lock ───────────────────────

def test_check_lock_labels_a_shared_id_lock_as_not_mine(tmp_path, monkeypatch):
    """THE Titus reproducer: caller resolved the shared per-repo id, a lock sits
    under it. `active:true` (a lock file exists) must NOT read as 'I own it'."""
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.delenv("CORTEX_SESSION_KEY", raising=False)
    # Make the caller resolve the SHARED per-repo id (the per-repo fallback's
    # process-scoped value), so `_read_lock` finds the same id's lock on disk —
    # the exact Titus collision: two sessions in one repo, one shared id.
    m._PROCESS_SESSION_ID = "sess_shared"
    _write_lock(tmp_path, "sess_shared")

    got = json.loads(m._check_lock({}).content[0].text)
    # THE Titus fix: a lock exists, but this caller cannot PROVE it owns it (its
    # id is the shared per-repo one) — so active is FALSE (fail closed). The lock
    # is still fully disclosed via lock_exists:true + locks[] for a caller that
    # keys on the array, and lock_is_mine says it is NOT this caller's.
    assert got["active"] is False, got
    assert got["lock_exists"] is True, got
    assert got["identity_source"] == "per-repo"
    assert got["shared_identity"] is True
    assert got["lock_is_mine"] is False, got
    assert got["ownership"] == "other-session-not-mine", got
    # the live-lock array carries per-entry is_mine too
    assert got["locks"][0]["is_mine"] is False, got


def test_check_lock_labels_own_env_lock_as_mine(tmp_path, monkeypatch):
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    monkeypatch.setenv("PI_SESSION_ID", "01a1c490-2f30-")
    own = m.get_session_id({})               # env-derived: sess_pi_session_id_…
    _write_lock(tmp_path, own)

    got = json.loads(m._check_lock({}).content[0].text)
    assert got["active"] is True
    assert got["identity_source"] == "env:PI_SESSION_ID"
    assert got["shared_identity"] is False
    assert got["caller_session_id"] == own
    assert got["lock_session_id"] == own
    assert got["lock_is_mine"] is True, got
    assert got["ownership"] == "mine", got
    assert got["locks"][0]["is_mine"] is True, got


def test_check_lock_no_lock_is_not_mine(tmp_path, monkeypatch):
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    got = json.loads(m._check_lock({}).content[0].text)
    assert got["active"] is False
    assert got["lock_exists"] is False
    assert got["lock_is_mine"] is False
    assert got["ownership"] == "none"   # no locks at all — honest "none", not a peer implication


# ── _session_identity: effective_session_id is the EVIDENCE key, not ownership ─

def test_session_identity_foreign_covering_lock_is_not_mine(tmp_path, monkeypatch):
    """effective_session_id still equals the covering lock's (evidence-keying rule
    UNCHANGED), but lock_is_mine:false + a note must say it is not the caller's."""
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    monkeypatch.setenv("PI_SESSION_ID", "01a1c490-2f30-")
    own = m.get_session_id({})
    # a foreign interactive lock covering the same repo
    _write_lock(tmp_path, "sess_OTHERINTERACTIVE", task_id="other-task")

    got = json.loads(m._session_identity({"repo_path": str(REPO)}).content[0].text)
    # evidence-keying rule intact: effective id is the covering lock's
    assert got["effective_session_id"] == "sess_OTHERINTERACTIVE", got
    # ownership is NOT claimed
    assert got["lock_is_mine"] is False, got
    assert got["ownership"] == "other-session-not-mine", got
    assert got["lock_session_id"] == "sess_OTHERINTERACTIVE"
    assert got["identity_source"] == "env:PI_SESSION_ID"
    # the is_mine-marked per-lock array is present with the correct labelling
    assert {"session_id": "sess_OTHERINTERACTIVE", "is_mine": False} in got["covering_locks_owned"], got
    assert "ANOTHER session's lock" in got["note"], got


def test_session_identity_own_lock_is_mine(tmp_path, monkeypatch):
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    monkeypatch.setenv("PI_SESSION_ID", "01a1c490-2f30-")
    own = m.get_session_id({})
    _write_lock(tmp_path, own)

    got = json.loads(m._session_identity({"repo_path": str(REPO)}).content[0].text)
    assert got["effective_session_id"] == own
    assert got["lock_is_mine"] is True, got
    assert got["ownership"] == "mine", got


def test_session_identity_no_lock_uses_own_id_and_is_mine(tmp_path, monkeypatch):
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    monkeypatch.setenv("PI_SESSION_ID", "01a1c490-2f30-")
    own = m.get_session_id({})
    got = json.loads(m._session_identity({"repo_path": str(REPO)}).content[0].text)
    assert got["lock_session_id"] is None
    assert got["effective_session_id"] == own
    # no covering lock -> nothing is held; ownership must be "none", not a false
    # implication that another session holds it
    assert got["lock_is_mine"] is False, got
    assert got["ownership"] == "none", got
    assert "no covering lock" in got["note"]


def test_session_identity_read_failure_is_unknown_not_none(tmp_path, monkeypatch):
    """ADV-0-4 (2026-11-10): a failed lock-state read must report `ownership: unknown`,
    never a false 'no covering lock / nothing is held' assurance a caller would trust
    to write. effective_session_id still falls back to the caller's own id."""
    m = _mcp()
    monkeypatch.setattr(m, "GOVERNANCE_STATE_DIR", tmp_path)
    monkeypatch.setenv("PI_SESSION_ID", "01a1c490-2f30-")
    own = m.get_session_id({})

    # _check_repo_lock raises -> the lock-state read path fails.
    def boom(args=None):
        raise RuntimeError("store unavailable")
    monkeypatch.setattr(m, "_check_repo_lock", boom)
    monkeypatch.setattr(m, "_live_locks", lambda *a, **k: [])

    got = json.loads(m._session_identity({"repo_path": str(REPO)}).content[0].text)
    assert got["lock_state_unknown"] is True, got
    assert got["ownership"] == "unknown", got
    assert got["lock_is_mine"] is False, got
    assert got["effective_session_id"] == own   # evidence-keying fallback intact
    assert "could not read the lock state" in got["note"], got
    assert "Nothing is held" not in got["note"], got


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))