#!/usr/bin/env python3
"""Part 2 identity gate tests — the fleet-committer orchestration predicate.

Titus (host worker-4, directory-services home /Users/luke) may update otherwise
orchestrator-only fleet paths. The gate is HOST-DERIVED (home, never env),
EXACT-match, fail-closed, and must NOT grant the config-authorship escalation
(SOLID: the guard's config-file self-protection is true-orchestrator-only).

We test the REAL `_is_fleet_committer` body — extracted verbatim from the
committed source hook — against REAL git repos with docs/fleet-committers.txt
committed at HEAD, so the exact command path (`git show HEAD:`) is exercised.

Run: python3 -m pytest tests/test_orchestrator_identity.py -v
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "ops" / "scripts" / "pre-commit-score"


# ── helpers: extract the real functions from the committed hook ──

def _extract_body(name: str) -> str:
    lines = HOOK.read_text().splitlines()
    for i, ln in enumerate(lines):
        if ln == f"{name}() {{":
            body = []
            depth = 0
            for inner in lines[i + 1:]:
                if inner.startswith("}"):
                    if depth == 0:
                        return "\n".join(body)
                    depth -= 1
                elif inner.rstrip().endswith("{"):
                    depth += 1
                body.append(inner)
    raise AssertionError(f"{name}() not found in {HOOK}")


_READ = _extract_body("_read_dir_home")
_IS_FC = _extract_body("_is_fleet_committer")

HARNESS = """#!/bin/bash
read_dir_home() {
%(read)s
}
is_fleet_committer() {
%(is_fc)s
}
%(inject)s
"""


def _run(repo: str, read_home_override: str, committers_content: str,
         home_env_spoof: str | None = None) -> subprocess.CompletedProcess:
    """Drive the real is_fleet_committer() in a bash harness with the repo's
    committed committers file and a simulated directory-services home."""
    inject = (
        # The extracted body needs the _G_* command-pin assignments the source
        # hook defines at the top (they are the PATH-shadow defense) PLUS a
        # _read_dir_home that returns the simulated host home.
        "_G_ID=/usr/bin/id; _G_GETENT=/usr/bin/getent; _G_DSCL=/usr/bin/dscl\n"
        "_G_HOSTNAME=/bin/hostname; _G_GIT=/usr/bin/git; _G_CUT=/usr/bin/cut\n"
        "_G_AWK=/usr/bin/awk; _G_GREP=/usr/bin/grep; _G_HEAD=/usr/bin/head; _G_RM=/bin/rm\n"
        f"_read_dir_home() {{ echo {read_home_override}; }}\n"
    )
    script = ("%s\n%s\n" % (
        HARNESS % {"read": _READ, "is_fc": _IS_FC, "inject": inject},
        ""
    ))
    extra = {}
    if home_env_spoof:
        extra["HOME"] = home_env_spoof
    with open(f"{repo}/_harness.sh", "w") as f:
        f.write(script + f"\ncd '{repo}' && is_fleet_committer; echo rc=$?\n")
    p = subprocess.run(
        ["bash", f"{repo}/_harness.sh"],
        capture_output=True, text=True, env={**os.environ, **extra},
    )
    return p


def _make_repo(committers_content: str, *, hooks_sandbox: bool = True) -> str:
    import tempfile
    repo = tempfile.mkdtemp(prefix="orchident-")
    def git(*a, **kw):
        subprocess.run(["git", *a], cwd=repo, check=True,
                       capture_output=True, text=True, env={**os.environ, **kw})
    git("init", "-q")
    git("config", "user.email", "esther@test"), git("config", "user.name", "esther")
    if hooks_sandbox:
        # Keep the host's fleet pre-commit hook OUT of the throwaway fixture repo.
        subprocess.run(["git", "config", "core.hooksPath", f"{repo}/.git/hooks"],
                       cwd=repo, capture_output=True, text=True)
    p = pathlib.Path(repo) / "docs"
    p.mkdir()
    (p / "fleet-committers.txt").write_text(committers_content)
    git("add", "-A")
    git("commit", "-q", "-m", "seed committers")
    return repo


# ── _is_fleet_committer: EXACT home match on the COMMITTED list ──

def test_committer_home_matches_when_committed():
    repo = _make_repo("# header\n/luke\n")
    p = _run(repo, read_home_override="/luke", committers_content="")
    assert "rc=0" in p.stdout, p.stdout + p.stderr


def test_prefix_does_not_match():
    repo = _make_repo("/luke\n")
    p = _run(repo, read_home_override="/luke2", committers_content="")
    assert "rc=1" in p.stdout, "prefix-like home must fail closed"


def test_unresolvable_home_fails_closed_ignoring_env():
    repo = _make_repo("/luke\n")
    # env-spoof HOME=/luke must NOT grant — identity is directory-services home only.
    p = _run(repo, read_home_override="", committers_content="",
             home_env_spoof="/luke")
    assert "rc=1" in p.stdout, "unresolved home (/luke via env) must fail closed"


def test_no_committed_list_fails_closed():
    """A repo whose docs/fleet-committers.txt does NOT exist at HEAD -> absent list
    -> fail closed (never a silent grant)."""
    import tempfile
    repo = tempfile.mkdtemp(prefix="orchident-nolist-")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "config", "core.hooksPath", f"{repo}/.git/hooks"],
                   cwd=repo, capture_output=True, text=True)
    (pathlib.Path(repo) / "x.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "no committers"],
                   cwd=repo, check=True, capture_output=True, text=True,
                   env={**os.environ,
                        "GIT_AUTHOR_EMAIL": "e@t", "GIT_AUTHOR_NAME": "e",
                        "GIT_COMMITTER_EMAIL": "e@t", "GIT_COMMITTER_NAME": "e"})
    p = _run(repo, read_home_override="/luke", committers_content="")
    assert "rc=1" in p.stdout, "absent committed list must fail closed"


# ── contract tether: the committed list rows / no-env / escalation guard ──

def test_committers_doc_contains_luke_row():
    doc = (REPO / "docs" / "fleet-committers.txt").read_text()
    rows = [ln.strip() for ln in doc.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
            and not ln.strip().startswith("@")]
    # Titus's verified directory-services home (macOS NFSHomeDirectory):
    # /Users/luke — the row must match it EXACTLY or the capability never fires
    # but fails silent. See docs/evidence/cortex-update-3-final-2026-10-14.txt
    # and enforcement-plumbing-verify-2026-10-23.md for /Users/luke as the home.
    assert rows == ["/Users/luke"], f"committers doc must contain exactly '/Users/luke', got {rows}"


def test_hook_uses_only_absolute_command_paths_for_identity():
    """The extracted identity body must not resolve getent/id/dscl/git via PATH."""
    body = _IS_FC + _READ
    for tool in ("getent", "id", "dscl", "git", "cut", "awk", "grep", "head", "rm"):
        # any bare appearance would be a PATH-shadowable identity call
        assert f"/usr/bin/{tool}" in body or f"/bin/{tool}" in body or f"_G_{tool.upper()}" in body, \
            f"{tool} must be path-pinned or via a _G_* variable"


def test_hook_fails_closed_never_uses_dollar_home_for_identity():
    body = _READ + _IS_FC
    # "HOME" must appear only inside the pin definitions or comments, never as a
    # fallback identity source. We assert the resolved home never falls back to $HOME.
    assert "${HOME}" not in body and "$HOME" not in body.replace("${HOME}", ""), \
        "identity must never fall back to $HOME (env-settable)"


def test_config_file_edits_gate_on_true_orch_not_committer():
    """ADV-0-3 regression: a fleet-COMMITTER must NOT be able to edit the two
    config files that define its capability (docs/fleet-committers.txt,
    docs/orchestrator-only-paths.txt). Those edits must be gated on `_detect_orch`
    (true moses/esther), independent of the IS_ORCH grant that committers receive
    for ordinary path writes. Without this, Titus could rewrite the committer list
    to add its own escalated rows — a privilege-escalation hole."""
    src = HOOK.read_text()
    assert "ORCH_VIOLATION" in src  # both blocks still set the violation
    # Each config-file edit is gated by `if echo "$STAGED_FILES" | grep -q "^docs/<file>"`.
    # Find those exact guard lines by `grep -q "^docs/<file>"` and inspect the block
    # they open — the decision must consult _detect_orch, never the committer-widened IS_ORCH.
    for fname in ("docs/orchestrator-only-paths.txt", "docs/fleet-committers.txt"):
        needle = f'grep -q "^{fname}"'
        idx = src.find(needle)
        assert idx != -1, f"{fname}: guard line not found"
        # Bound the window to the guard's own block: the next standalone `fi`
        # (the guard close brace) — so later IS_ORCH/committer code isn't read
        # in as part of this config-file decision.
        close = src.find("\nfi\n", idx)
        if close == -1 or close - idx > 500:
            close = idx + 500  # fallback bound
        block = src[idx: close + 4]
        assert "_detect_orch" in block, f"{fname} guard must gate on _detect_orch (true orch)"
        # The committer-widened IS_ORCH / _IS_COMMITTER must not be consulted
        # inside the config-guard decision; only true-orch _detect_orch decides.
        assert "IS_ORCH" not in block, f"{fname} guard must not use the committer-widened IS_ORCH"
        assert "_IS_COMMITTER" not in block, f"{fname} guard must not use _IS_COMMITTER (committer)"
        # and ORCH_VIOLATION must still be set for the sanctioned/skip bookkeeping.
        assert "ORCH_VIOLATION" in block, f"{fname} guard must still set ORCH_VIOLATION"


def test_committer_cannot_edit_the_guard_script_second_gate():
    """ADV-0-1 regression (post-review): a fleet-committer must be unable to
    edit the guard script itself (ops/scripts/pre-commit-score), AGENTS.md,
    skills/, tests/ — the ORCHESTRATOR_ONLY_PATHS array re-covers these and its
    gate consults `_detect_orch` (TRUE orchestrator), which a committer does not
    satisfy. Without this independent layer, a committer could rewrite
    `_is_fleet_committer`/`_detect_orch` to grant itself orchestrator rights."""
    src = HOOK.read_text()
    assert "ORCHESTRATOR_ONLY_PATHS=(" in src
    array = src.split("ORCHESTRATOR_ONLY_PATHS=(", 1)[1].split("\n)", 1)[0]
    # The sensitive self-editing surface must be in the array:
    for pattern in ("^ops/scripts/pre-commit-score$", "^AGENTS\\.md$",
                    "^skills/", "^tests/", "test_.*\\.py$"):
        assert pattern in array, \
            f"ORCHESTRATOR_ONLY_PATHS must cover {pattern} (committer self-edit block)"
    # The array gate's identity decision must use _detect_orch, never the
    # committer-widened IS_ORCH (committers pass IS_ORCH but fail _detect_orch).
    gate_idx = src.find("AGENT_TYPE=\"server\"")
    assert gate_idx != -1
    gate_block = src[gate_idx: gate_idx + 400]
    assert "_detect_orch" in gate_block, "array gate must consult _detect_orch"
    assert "IS_ORCH" not in gate_block, \
        "array gate must not use the committer-widened IS_ORCH (that would open the guard to committers)"


def test_committer_allow_window_is_narrow_not_constitution():
    """ADV-0-7 critical regression: a fleet-committer's exemption inside the
    config-driven guard is a NARROW allow-list, NOT a blanket IS_ORCH bypass.
    The constitution (SOUL.md), plugins/, mcp-servers/, skills/, tests/,
    profiles/, workflows, hooks and the guard script must all remain blocked.
    The previous design (`_is_fleet_committer && IS_ORCH=1`) opened every
    config-driven path including SOUL.md — the reviewer flagged it critical."""
    src = HOOK.read_text()
    # 1. The committer predicate must NOT set IS_ORCH (no blanket bypass).
    committer_grant = src.split("_IS_COMMITTER=1\nif [[ \"$IS_ORCH\"", 1)
    assert "_is_fleet_committer && IS_ORCH=1" not in src, \
        "committer must not set IS_ORCH (blanket bypass is the ADV-0-7 hole)"
    # 2. The committer exemption lives inside the config-driven guard loop and
    #    is limited to the ops/scripts allow-window (manage/ subtree).
    #    Constitution/config paths never qualify.
    grant_idx = src.find("_COMMITTER_ALLOWED=\"\"")
    assert grant_idx != -1, "committer allow-list not found"
    allow_block = src[grant_idx: grant_idx + 600]
    assert "_IS_COMMITTER" in allow_block and "ops/scripts" in allow_block, \
        "committer exemption must be the narrow ops/scripts allow-list"
    # 3. The allow-list can only ever let a committer pass for the manage/
    #    subtree; a staged file outside manage/ keeps ORCH_VIOLATION=1.
    violating_clause = src[grant_idx: grant_idx + 900]
    assert "manage/" in violating_clause, \
        "committer ops/scripts exemption must be scoped to the manage/ subtree"
    # 4. The config-driven list still contains the constitution; a committer
    #    bypassing it for those is impossible because the exemption is absent.
    assert "SOUL.md" in src, "SOUL.md must remain in the config-driven guard"
    # The committer exemption branch must ONLY trigger for ops/scripts, so
    # SOUL.md/plugins/mcp-servers/etc. never become committer-writable.
    assert "ops/scripts" in allow_block, \
        "only the ops/scripts entry has a committer exemption"
    # The non-exempt path still fails closed.
    assert "ORCH_VIOLATION=1" in src and "_COMMITTER_ALLOWED" in src