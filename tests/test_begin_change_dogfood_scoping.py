#!/usr/bin/env python3
"""Regression test: the begin_change MCP dogfood gate must be scoped to the
steadfaste-fleet repo — the MCP-layer mirror of the pre-commit hook scoping
already fixed on 2026-10-01 (tests/test_dogfood_gate_scoping.py).

Bug (fleet-wide outage, 2026-11-14, reported by Luke):
`_require_dogfood()` in mcp-servers/loop-gov-mcp.py compared the FLEET
enforcer's repo source against its DEPLOYED copy for EVERY begin_change in
EVERY repo — including completely unrelated non-fleet repos. When any host's
enforcer deploy went stale (gisu: fleet-update.sh exited rc=1 because the
enforcer is `chattr +i` immutable and the NOPASSWD sudoers rule for
hermes-plugin-lock is missing, so it could not relock/deploy), the repo-vs-
deployed hashes differed and the gate blocked begin_change for EVERY agent on
that host, in every repo — "all agents can't work".

Why it happened: the pre-commit HOOK version gained the `${HOME}/steadfaste-fleet`
scope guard on 2026-10-01, but the MCP `begin_change` gate (runs first, at the
enforcement tool layer) was never given the same scoping.

The fix: `_require_dogfood(session_repo)` returns None (allows) whenever the
session's governed repo is NOT ~/steadfaste-fleet (a non-fleet or unknown repo
cannot act on the fleet enforcer's deploy state). Inside the fleet repo the
gate still hard-blocks — enforcement is preserved for the repo that owns it.

Run: python3 -m pytest tests/test_begin_change_dogfood_scoping.py -q
"""
import importlib.util
import tempfile
from pathlib import Path

import pytest

_MCP_PATH = Path(__file__).resolve().parents[1] / "mcp-servers" / "loop-gov-mcp.py"
_spec = importlib.util.spec_from_file_location("loop_gov_mcp", _MCP_PATH)
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)


@pytest.fixture
def sandbox(monkeypatch):
    """Point the module's HOME + governance paths into a temp sandbox."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        monkeypatch.setattr(mcp, "HOME", tmp)
        monkeypatch.setattr(
            mcp, "GOVERNANCE_REPO_PATH",
            tmp / "steadfaste-fleet" / "plugins" / "governance-enforcer" / "__init__.py",
        )
        monkeypatch.setattr(
            mcp, "GOVERNANCE_DEPLOY_PATH",
            tmp / ".hermes" / "plugins" / "governance-enforcer" / "__init__.py",
        )
        yield tmp


def _write(path: Path, content: str = "aaa") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _fleet(sandbox: Path) -> Path:
    return sandbox / "steadfaste-fleet"


# ── Bug-repro: an UNRELATED repo must never be dogfood-blocked ─────────────

def test_unrelated_repo_is_never_dogfood_blocked(sandbox: Path):
    """A non-fleet repo must NOT block begin_change even when the fleet
    enforcer is stale (repo source != deployed). This is the outage: it was
    blocking every unrelated repo."""
    _write(mcp.GOVERNANCE_REPO_PATH, "repo-v1")
    _write(mcp.GOVERNANCE_DEPLOY_PATH, "deployed-v1")  # differ -> stale enforcer
    foreign = _fleet(sandbox).parent / "some-unrelated-project"
    foreign.mkdir(parents=True)

    assert mcp._require_dogfood(foreign) is None, (
        "a non-fleet repo must not be blocked by the fleet enforcer's deploy state"
    )


def test_nested_subdir_of_unrelated_repo_is_not_blocked(sandbox: Path):
    _write(mcp.GOVERNANCE_REPO_PATH, "repo-v1")
    _write(mcp.GOVERNANCE_DEPLOY_PATH, "deployed-v1")
    nested = _fleet(sandbox).parent / "project" / "nested" / "deeper"
    nested.mkdir(parents=True)

    assert mcp._require_dogfood(nested) is None


# ── Guard: the fleet repo STILL dogfood-blocks (enforcement preserved) ─────

def test_fleet_repo_still_blocks_on_stale_enforcer(sandbox: Path):
    """In ~/steadfaste-fleet with a stale deploy, the gate MUST still return
    the block string — the fix must not weaken enforcement for the repo it
    governs."""
    _write(mcp.GOVERNANCE_REPO_PATH, "repo-v1")
    _write(mcp.GOVERNANCE_DEPLOY_PATH, "deployed-v1")
    fleet = _fleet(sandbox)
    fleet.mkdir(parents=True, exist_ok=True)

    msg = mcp._require_dogfood(fleet)
    assert msg is not None, "fleet repo with stale enforcer must be blocked"
    assert "DOGFOOD" in msg


def test_fleet_repo_allows_when_deployed_matches(sandbox: Path):
    _write(mcp.GOVERNANCE_REPO_PATH, "same-content")
    _write(mcp.GOVERNANCE_DEPLOY_PATH, "same-content")
    fleet = _fleet(sandbox)
    fleet.mkdir(parents=True, exist_ok=True)

    assert mcp._require_dogfood(fleet) is None


def test_unknown_repo_is_never_blocked(sandbox: Path):
    """An unresolvable/unconfirmed repo must not freeze the agent — the very
    outage is a host whose enforcer state could not be confirmed."""
    _write(mcp.GOVERNANCE_REPO_PATH, "repo-v1")
    _write(mcp.GOVERNANCE_DEPLOY_PATH, "deployed-v1")
    assert mcp._require_dogfood(None) is None


def test_default_argument_is_safe_without_repo(sandbox: Path):
    """Calling with no argument (as anything that still calls bare) must not
    block — equivalent to an unknown repo."""
    _write(mcp.GOVERNANCE_REPO_PATH, "repo-v1")
    _write(mcp.GOVERNANCE_DEPLOY_PATH, "deployed-v1")
    assert mcp._require_dogfood() is None