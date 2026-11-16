#!/usr/bin/env python3
"""The doctor must not call a LIVE session's lock "legacy" — nor prescribe deleting it.

Incident (2026-11-16). The doctor's legacy-lock check classified a lock **from
its filename alone**:

    legacy = [f for f in state_dir.glob(".governance-*.json")
              if not re.search(r"\\d{8}_\\d{6}", f.name)
              and "sess_" not in f.name]

Accepted forms were only a Hermes timestamp id (`20260725_164953_750cbfe2`) or a
name containing `sess_` (the non-Hermes per-repo id). Everything else was
labelled *"slug-based naming superseded by session-scoped locks"* and the
REQUIRED ACTION printed to the operator was `Remove: rm -f <lockfile>`.

But the id namespace is wider than that list — Hermes itself mints a third form,
`local-<sha256>` (the runtime's state-key module:
`return 'local-' + hashlib.sha256(identity.encode()).hexdigest()`), and it is a
live session key in `loop-governance.db` and the `skills-{loaded,state,credit}`
stores. So a lock named `.governance-local-<64hex>.json` was reported as legacy
and the operator was told to delete it. Following that instruction removed the
lock of a session that was MID-RUN (the orch-model-market-scan cron, which went
on running for another hour and closed its cycles afterwards).

Two defects, and this test pins both:

1. **The accepted-id list is incomplete.** A `local-<hex>` id is not a slug.
2. **The remedy is destructive and unconditional.** Deletion was prescribed from
   a NAME PATTERN, with no liveness check at all — the check never read the
   lock's own `heartbeat_at`/`ttl_seconds`. A destructive remediation must fail
   closed: only a lock whose owner is PROVABLY gone may carry a `rm -f`.

The control assertion at the bottom is deliberate: it pins the OLD predicate's
verdict and asserts it DISAGREES with the new classification, so this test is
proven to discriminate the broken revision from the fixed one. A test that never
failed on the broken code proves nothing.

Run:  pytest tests/test_doctor_legacy_lock_check.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(REPO / "ops" / "scripts"))


def _load_checks():
    """Import the deployable checks module (package-relative imports need the
    scripts dir on sys.path)."""
    import fleet_doctor.checks as C  # noqa: PLC0415 — deliberate, after sys.path setup
    return C


C = _load_checks()

NOW = datetime.now(timezone.utc)
LOCAL_SHA = (
    "local-e4c1d78e72320771a2dc8d27471f07263117b1cd4f4e74ca9119ecceec076d4f"
)
FIXTURE_REPO = "/srv/example-repo"


def _lock_body(heartbeat_age_s: int, ttl: int = 3600) -> str:
    hb = (NOW - timedelta(seconds=heartbeat_age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    st = (NOW - timedelta(seconds=heartbeat_age_s + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({
        "task_id": "t", "session_id": "s", "agent": "a", "status": "executing",
        "started_at": st, "heartbeat_at": hb, "ttl_seconds": ttl,
        "repo_path": FIXTURE_REPO, "repo_slug": "example-repo",
    })


def _seed(tmp_path: Path, filename: str, body: str) -> Path:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / filename).write_text(body)
    return state


def _verdicts(state: Path) -> list[dict]:
    return C.classify_governance_locks(state)


def _fixes(state: Path) -> str:
    """All remediation text the check would print for this state dir."""
    return " ".join(v["fix"] for v in _verdicts(state))


# ── 1. the accepted id namespaces must include `local-<sha256>` ──────────────

def test_local_sha_id_is_not_legacy(tmp_path):
    state = _seed(tmp_path, f".governance-{LOCAL_SHA}.json", _lock_body(5))
    assert not [v for v in _verdicts(state) if "legacy" in v["status"].lower()], (
        "a live `local-<sha256>` session id was called legacy — the runtime mints "
        "this form and the governance DB uses it as a session key"
    )


def test_timestamp_and_sess_ids_still_accepted(tmp_path):
    state = _seed(tmp_path, ".governance-20261116_120352_a100c2aa.json", _lock_body(5))
    (state / ".governance-sess_ab12cd34ef56.json").write_text(_lock_body(5))
    assert not [v for v in _verdicts(state) if "legacy" in v["status"].lower()]


# ── 2. a destructive remedy must require a PROVEN-DEAD owner ────────────────

def test_stale_legacy_slug_still_gets_the_removal_remedy(tmp_path):
    """A genuinely abandoned slug-named lock (heartbeat far past its TTL) keeps
    its cleanup instruction — the fix must not disable the check."""
    state = _seed(tmp_path, ".governance-fix-auth-bug.json", _lock_body(7200))
    fixes = _fixes(state)
    assert "rm -f" in fixes, "a provably-dead legacy lock should still be removable"
    assert "fix-auth-bug" in fixes


def test_live_legacy_slug_must_not_prescribe_deletion(tmp_path):
    """THE CLASS FIX. An unrecognised NAME is not proof of a dead owner: with a
    fresh heartbeat the lock is live and the check must NOT tell anyone to
    delete it."""
    state = _seed(tmp_path, ".governance-fix-auth-bug.json", _lock_body(5))
    assert "rm -f" not in _fixes(state), (
        "prescribed deletion of a LIVE lock — a destructive remedy must fail "
        "closed on liveness, not fire on a filename pattern"
    )


def test_unparsable_lock_fails_closed(tmp_path):
    """If liveness cannot be determined, do not prescribe deletion."""
    state = _seed(tmp_path, ".governance-fix-auth-bug.json", "{not json")
    assert "rm -f" not in _fixes(state), "unverifiable owner must not yield rm -f"


# ── control: prove this test discriminates the pre-fix predicate ────────────

def test_control_old_predicate_would_have_misfired():
    """Pin the OLD rule's verdict so the regression is unambiguous.

    The two predicates are INVERSE (old returns "is legacy", new returns "id is
    known"), so the discriminating assertion compares the old verdict against
    the new one's equivalent — not two same-polarity booleans.
    """
    def old_predicate_is_legacy(name: str) -> bool:
        return not re.search(r"\d{8}_\d{6}", name) and "sess_" not in name

    def new_is_legacy(name: str) -> bool:
        return not C.governance_lock_id_is_known(name)

    local_name = f".governance-{LOCAL_SHA}.json"
    slug_name = ".governance-fix-auth-bug.json"

    assert old_predicate_is_legacy(local_name) is True, (
        "control broken: the old predicate no longer misfires on local-<hash>"
    )
    assert new_is_legacy(local_name) is False, (
        "the new predicate must accept the local-<sha256> form"
    )
    assert old_predicate_is_legacy(local_name) != new_is_legacy(local_name), (
        "the test does not discriminate the broken revision from the fixed one"
    )
    # a real slug is still legacy under BOTH rules — the fix did not disable it
    assert old_predicate_is_legacy(slug_name) is True
    assert new_is_legacy(slug_name) is True
