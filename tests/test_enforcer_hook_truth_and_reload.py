#!/usr/bin/env python3
"""RS0 — the enforcer's hook DECISION PATH vs known truth, and the reload proof.

WHY THIS FILE EXISTS (docs/plans/enforcer-residue-shrink.md, FINDING F0):
tests/test_write_class_parity.py compared the contract against
enforcer._is_readonly_terminal_command — but since CR2 that function delegates
to the SAME contract through ops/scripts/loop-gov-classify.py, so the test was
a contract-vs-contract self-comparison: green, and blind to drift. This file
closes that hole with three independent assertions:

  1. TRUTH     — drive the hook's allow/deny over the corpus and compare against
                 the corpus's OWN hand-stated expectations (NOT against the
                 contract's function). A contract regression in EITHER
                 direction (over-block or over-allow) fails this.
  2. RELOAD    — mutate a scratch copy of write_class.py and assert the SAME
                 already-loaded enforcer module starts returning the mutated
                 verdict. That proves the verdict is computed from the contract
                 FILE at call time: no restart, no module-level verdict cache,
                 no inlined re-implementation of the rules. A cache or an
                 inlined copy fails this test. The same proof is run one level
                 down against the CLI alone (the master plan's CR5 shape).
  3. FAIL-CLOSED — with the contract deleted and the CLI interpreter intact, a
                 non-recovery write is still blocked.

The sanctioned recovery lane (a bare fleet-update.sh invocation surviving a
missing contract) is RS4's subject and is deliberately NOT asserted here.

Counts (finding ADV-15148-1: the record's numbers are load-bearing, so they are
pinned rather than asserted in prose):
    40 ALLOW + 40 BLOCK + 6 standalone = 86 collected
test_corpus_sizes_are_the_documented_ones pins the corpus sizes;
test_collected_count_matches_the_documented_total pins the collected total by
actually collecting. The committed collection transcript, including the raw
node-by-node output that names all six standalone tests, is in
docs/evidence/rs0-collection-2026-11-24.txt (summary in
docs/evidence/rs0-parity-and-reload-2026-11-24.txt).

Run: python3 -m pytest tests/test_enforcer_hook_truth_and_reload.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "ops" / "scripts" / "loop-gov-classify.py"
_CONTRACT = _REPO / "ops" / "services" / "fleet-governance" / "write_class.py"
_PLUGIN = _REPO / "plugins" / "governance-enforcer" / "__init__.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ENFORCER = _load(_PLUGIN, "governance_enforcer_rs0")

# ── the corpus with HAND-STATED truth (the point of this file) ───────────────
# expected = the verdict a human must be able to justify WITHOUT reading the
# contract. If the contract drifts, these do not move with it.
ALLOW = [
    "ls -la", "cat README.md", "grep -n foo file.py", "find . -name '*.py'",
    "git status", "git log --oneline -5", "git diff", "git -C /tmp/x status",
    "git --no-pager log", "git show HEAD:file", "git rev-parse --show-toplevel",
    "docker ps", "docker compose ps", "docker logs web",
    "systemctl status x", "systemctl show unit", "systemctl list-unit-files",
    "journalctl -u hermes-gateway", "journalctl -u x | tail -20",
    "curl -sI https://example.com", "curl -s http://localhost:13004/health",
    "pip list", "npm search x", "hermes mcp list", "mycortex list -n 5",
    "mycortex answer search foo", "ps aux", "df -h", "uname -a",
    "echo hello", "printf '%s' hi", "cd ~ && pwd", "command -v python3",
    "test -f x", "true",
    # compound read-only: a pipe of reads is still a read
    "ls -la | grep .py", "git status --short | head", "git log --oneline && git diff",
    "cat a.txt | grep x | wc -l", "journalctl -u x | tail -5",
]

BLOCK = [
    # ordinary writes
    "rm -rf x", "mv a b", "cp a b", "touch newfile", "mkdir dir",
    "git commit -m 'x'", "git push origin main", "git reset --hard",
    "git branch -d foo", "git checkout main", "docker run -it x",
    "systemctl restart x", "pip install foo", "npm install foo",
    "sudo systemctl restart x", "echo x > file", "printf 'x' > file",
    "cat <> file",
    # evasions: a write smuggled through a tool that has a read-only mode
    "python3 -c 'print(1)'", "bash -c 'rm x'", "python3 script.py",
    "bash setup.sh", "node app.js", "find . -exec rm {} +", "find . -delete",
    "sort -o out.txt in.txt", "date -s '2020-01-01'", "curl -s URL -d x=y",
    "curl -s URL -X POST", "env bash -c 'x'", "env FOO=bar python3 x.py",
    "git branch newbranch", "journalctl --vacuum-time=1d", "dmesg -C",
    "nohup python3 x.py", "crontab -e", "wget -O x url",
    "echo x > ~/.bashrc", "git status > foo", "cat file > out",
]


def test_corpus_sizes_are_the_documented_ones():
    """The evidence artifact quotes these counts as load-bearing numbers.
    Pinned here so a corpus edit cannot silently invalidate the record
    (finding ADV-15148-1: the artifact said 44 block cases; the truth is 40)."""
    assert len(ALLOW) == 40, len(ALLOW)
    assert len(BLOCK) == 40, len(BLOCK)


def test_collected_count_matches_the_documented_total():
    """Pin the COLLECTED total by collecting, not by asserting it in prose
    (finding ADV-15148-1 asked for exactly this). This test is itself one of
    the standalone tests, so the documented total includes it:
        40 ALLOW + 40 BLOCK + 6 standalone = 86 collected.
    A future corpus entry or test that lands without updating the record in
    docs/evidence/rs0-parity-and-reload-2026-11-24.txt fails HERE.
    """
    r = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "--collect-only", "-q"],
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"collection failed: {r.stderr[-400:]}"
    m = re.search(r"(\d+) tests collected", r.stdout)
    assert m, f"no collection summary in: {r.stdout[-400:]}"
    assert int(m.group(1)) == 86, (
        f"collected {m.group(1)}, documented 86 "
        f"(40 ALLOW + 40 BLOCK + 6 standalone) — update the evidence artifact"
    )


def _scratch_layout(root: Path, mutate=None) -> Path:
    """A self-contained CLI+contract tree the CLI resolves relative to itself.

    Layout mirrors the repo so the CLI's own contract lookup (repo layout
    relative to the script) lands on the scratch copy, never the real one.
    """
    cli_dst = root / "ops" / "scripts" / "loop-gov-classify.py"
    con_dst = root / "ops" / "services" / "fleet-governance" / "write_class.py"
    cli_dst.parent.mkdir(parents=True, exist_ok=True)
    con_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_CLI, cli_dst)
    shutil.copy2(_CONTRACT, con_dst)
    if mutate is not None:
        con_dst.write_text(con_dst.read_text() + "\n\n" + mutate + "\n")
    return cli_dst


_OVERRIDE_BLOCK_ALL = (
    "def is_readonly_command(command):  # TEST MUTATION: block everything\n"
    "    return False"
)
_OVERRIDE_ALLOW_ALL = (
    "def is_readonly_command(command):  # TEST MUTATION: allow everything\n"
    "    return True"
)


@pytest.mark.parametrize("command", ALLOW)
def test_hook_allows_known_read_only(command):
    """(1) TRUTH — the hook must allow what a human calls read-only."""
    assert _ENFORCER._is_readonly_terminal_command(command) is True, command


@pytest.mark.parametrize("command", BLOCK)
def test_hook_blocks_known_write(command):
    """(1) TRUTH, both directions — and it must block what a human calls a write."""
    assert _ENFORCER._is_readonly_terminal_command(command) is False, command


def test_hook_follows_contract_mutation_without_restart(monkeypatch):
    """(2) RELOAD — the hook reads the contract FILE at call time.

    The enforcer module is loaded ONCE. Its contract is then mutated behind its
    back at a scratch path, and the SAME already-answered command is asked
    again. If the verdict flips, nothing about the plugin was restarted and
    nothing caches the rules: the live service picks up a write_class.py change
    by file update alone (the fleet's whole recovery from a stale contract). A
    module-level verdict cache, or an inlined copy of the rules, fails this.
    """
    before = id(_ENFORCER)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cli = _scratch_layout(root)
        con = root / "ops" / "services" / "fleet-governance" / "write_class.py"
        monkeypatch.setattr(_ENFORCER, "_classify_cli_path", lambda: cli)

        # baseline through the real seam
        assert _ENFORCER._is_readonly_terminal_command("ls -la") is True
        assert _ENFORCER._is_readonly_terminal_command("rm -rf x") is False

        # mutate the contract behind the plugin's back, in place
        con.write_text(con.read_text() + "\n\n" + _OVERRIDE_BLOCK_ALL + "\n")

        assert id(_ENFORCER) == before, "the plugin must not be reloaded"
        assert _ENFORCER._is_readonly_terminal_command("ls -la") is False, (
            "the hook did not follow the contract mutation — the verdict is "
            "cached or reimplemented inside the plugin"
        )
        assert _ENFORCER._is_readonly_terminal_command("rm -rf x") is False


def test_hook_follows_permissive_mutation_both_directions(monkeypatch):
    """(2) RELOAD, the other direction — an over-allowing contract must also be
    followed, so a future contract regression that opens the gate is visible.
    Same shape: ask first, mutate, ask the identical command again."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cli = _scratch_layout(root)
        con = root / "ops" / "services" / "fleet-governance" / "write_class.py"
        monkeypatch.setattr(_ENFORCER, "_classify_cli_path", lambda: cli)

        assert _ENFORCER._is_readonly_terminal_command("rm -rf x") is False

        con.write_text(con.read_text() + "\n\n" + _OVERRIDE_ALLOW_ALL + "\n")

        assert _ENFORCER._is_readonly_terminal_command("rm -rf x") is True, (
            "the hook did not follow the permissive contract mutation"
        )


def test_cli_contract_mutation_reload_proof():
    """(2) RELOAD one level down — the master plan's CR5 acceptance shape:
    run the CLI with --contract on a scratch copy, mutate the copy, and the
    verdict differs. Same process requirements: no restart anywhere."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        clean = _scratch_layout(root / "clean")
        dirty = _scratch_layout(root / "dirty", mutate=_OVERRIDE_BLOCK_ALL)

        def run(cli: Path, command: str, contract: Path | None = None):
            argv = [sys.executable, str(cli)]
            if contract is not None:
                argv += ["--contract", str(contract)]
            argv.append(command)
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, f"rc={r.returncode} {r.stderr[:300]}"
            return json.loads(r.stdout)["read_only"]

        dirty_contract = (root / "dirty" / "ops" / "services" /
                          "fleet-governance" / "write_class.py")
        assert dirty_contract.is_file(), dirty_contract
        assert run(clean, "ls -la") is True
        assert run(dirty, "ls -la") is False, "the CLI did not reload the contract"
        # --contract is the explicit seam: it must select the copy asked for
        assert run(clean, "ls -la", contract=dirty_contract) is False


def test_missing_contract_fails_closed(monkeypatch):
    """(3) FAIL-CLOSED — no contract, no allow. A write stays blocked even when
    the classification cannot be produced; the hook never fails open.

    HOME is pointed at the scratch root for the child so the CLI's deployed
    fallback (~/.steadfaste-fleet/services) cannot rescue the lookup — this
    asserts the fail-closed branch itself, which is otherwise unreachable on a
    host where a deployed contract exists.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cli = _scratch_layout(root)
        (root / "ops" / "services" / "fleet-governance" / "write_class.py").unlink()
        monkeypatch.setattr(_ENFORCER, "_classify_cli_path", lambda: cli)
        assert _ENFORCER._is_readonly_terminal_command("rm -rf x") is False
        # and the CLI itself reports COULD NOT VERIFY rather than a verdict
        env = {**os.environ, "HOME": str(root)}
        r = subprocess.run([sys.executable, str(cli), "rm -rf x"],
                           capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr[:200])
        assert "COULD NOT VERIFY" in r.stderr