"""Test the pii-scrubbing denylist-surface-scan.py CLI (read-side surface guard).

The scanner hash-sweeps tracked-file windows against the enforcer's deny-list
digests and never holds or prints an identifier. Recovered from the deployed
pii-scrubbing skill. These tests cover the two NON-DEFAULT exit classes plus
the positive-controlled clean path:

  - exit 3 (COULD-NOT-VERIFY): a tiny hermetic git repo whose tree has NO
    sanctioned hit must NOT pass silently — the scanner's own reach would be
    unproven, so a clean-looking result is flagged as unverifiable instead.
  - exit 0 (clean, proven): CONTRIBUTING.md carries the full sanctioned repo
    URL, which yields sanctioned repo-URL hits -> reach proven, no findings.

No deny-listed identifier is ever written here; the digest source is the
enforcer module read from PII_ENFORCER at runtime.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
SCANNER = (
    REPO / "skills" / "security" / "pii-scrubbing" / "scripts"
    / "denylist-surface-scan.py"
)
ENFORCER = REPO / "plugins" / "governance-enforcer" / "__init__.py"
VENV_PY = Path(os.path.expanduser("~/.steadfaste-fleet/venv/bin/python3"))
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable


def _run(*args, cwd, scan_repo=None):
    env = os.environ.copy()
    env["PII_SCAN_REPO"] = str(scan_repo or cwd)
    env["PII_ENFORCER"] = str(ENFORCER)
    return subprocess.run(
        [PY, str(SCANNER), *args], cwd=str(cwd), env=env,
        capture_output=True, text=True,
    )


@pytest.fixture
def bare_repo(tmp_path):
    """A tiny git repo with one clean committed text file (no sanctioned hit)."""
    (tmp_path / "hi.txt").write_text("hello world\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "hi.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "seed"],
        check=True,
    )
    return tmp_path


def test_bare_tree_cannot_verify_not_a_pass(bare_repo):
    """A tree with no sanctioned hit returns 3 (COULD-NOT-VERIFY), never a pass.

    This is the "scanner found nothing must be distinguishable from a clean
    tree" contract: an empty tree is UNPROVEN, so it is flagged, not passed.
    """
    r = _run(cwd=bare_repo)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "COULD NOT VERIFY" in r.stdout


def test_sanctioned_repo_file_clean_and_proven():
    """CONTRIBUTING.md carries the sanctioned repo URL -> reach proven, clean.

    At least one sanctioned repo-URL hit proves the deny-list parse and window
    walk are alive, and no unsanctioned finding means the one file is clean.
    """
    r = _run("CONTRIBUTING.md", cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("sanctioned repo-URL hits: ") == 1
    assert "no unsanctioned deny-listed identifier in the tracked surface" in r.stdout