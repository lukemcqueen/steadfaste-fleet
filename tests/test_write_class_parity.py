#!/usr/bin/env python3
"""Write-classification PARITY: the extracted contract == the Hermes enforcer.

`ops/services/fleet-governance/write_class.py` is the host-neutral extraction of
the enforcer's write-classification (`plugins/governance-enforcer/__init__.py`).
This test is the "as well as the hermes gateway" gate: for the SAME command or
tool call, the contract and the enforcer must return the SAME verdict.

The enforcer is loaded from its real file via importlib (not copied), so any
future change to the enforcer that is NOT reflected in the contract is caught
here as a parity break — the drift guard that closes the gap where the two
implementations existed without anything asserting they agree.

Run: python3 -m pytest tests/test_write_class_parity.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CONTRACT = _load(_REPO / "ops" / "services" / "fleet-governance" / "write_class.py",
                  "write_class_t")
_ENFORCER = _load(_REPO / "plugins" / "governance-enforcer" / "__init__.py",
                  "governance_enforcer_t")


# ── the corpus: plain read-only, compound read-only, writes, evasions ────────
READ_ONLY = [
    "ls -la",
    "cat README.md",
    "grep -n foo file.py",
    "find . -name '*.py'",
    "git status",
    "git log --oneline -5",
    "git diff",
    "git -C /tmp/x status",
    "git --no-pager log",
    "git show HEAD:file",
    "git rev-parse --show-toplevel",
    "docker ps",
    "docker compose ps",
    "docker logs web",
    "systemctl status x",
    "systemctl show unit",
    "systemctl list-unit-files",
    "journalctl -u hermes-gateway",
    "journalctl -u x | tail -20",
    "curl -sI https://example.com",
    "curl -s http://localhost:13004/health",
    "pip list",
    "npm search x",
    "hermes mcp list",
    "mycortex list -n 5",
    "mycortex answer search foo",
    "ps aux",
    "df -h",
    "uname -a",
    "echo hello",
    "printf '%s' hi",
    "cd ~ && pwd",
    "command -v python3",
    "test -f x",
    "true",
]

COMPOUND_READ_ONLY = [
    "ls -la | grep .py",
    "git status --short | head",
    "git log --oneline && git diff",
    "cat a.txt | grep x | wc -l",
    "journalctl -u x | tail -5",
]

WRITES = [
    "rm -rf x",
    "mv a b",
    "cp a b",
    "touch newfile",
    "mkdir dir",
    "git commit -m 'x'",
    "git push origin main",
    "git reset --hard",
    "git branch -d foo",
    "git checkout main",
    "docker run -it x",
    "systemctl restart x",
    "pip install foo",
    "npm install foo",
    "sudo systemctl restart x",
    "echo x > file",
    "printf 'x' > file",
    "cat <> file",
]

EVASIONS = [
    "python3 -c 'print(1)'",
    "bash -c 'rm x'",
    "python3 script.py",
    "bash setup.sh",
    "node app.js",
    "find . -exec rm {} +",
    "find . -delete",
    "sort -o out.txt in.txt",
    "date -s '2020-01-01'",
    "curl -s URL -d x=y",
    "curl -s URL -X POST",
    "env bash -c 'x'",
    "env FOO=bar python3 x.py",
    "git branch newbranch",
    "journalctl --vacuum-time=1d",
    "dmesg -C",
    "nohup python3 x.py",
    "crontab -e",
    "wget -O x url",
    "echo x > ~/.bashrc",
    "git status > foo",
    "cat file > out",
]

SANCTIONED_UPDATE = [
    "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh",
    "~/steadfaste-fleet/ops/scripts/fleet-update.sh --status",
    "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh --dry-run --status",
]
NOT_SANCTIONED_UPDATE = [
    # STALE-path literal: the pre-migration repo path is no longer sanctioned
    # (2026-11-12) — a host on ~/hermes-cortex is a straggler, not a sanctioned
    # updater.
    "bash ~/hermes-cortex/ops/scripts/fleet-update.sh",
    "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh && echo hi",
    "sudo ~/steadfaste-fleet/ops/scripts/fleet-update.sh",
    "bash ~/other/ops/scripts/fleet-update.sh",
    "fleet-update.sh",
]

SANCTIONED_TMP = [
    "rm -f /tmp/x /tmp/y && echo cleaned",
    "rm -rf /tmp/templates",
    "rmdir /tmp/empty",
    "/bin/rm -f /tmp/a",
]
NOT_SANCTIONED_TMP = [
    "rm -rf /tmp",
    "rm -rf /tmp/../repo/x",
    "rm -rf /var/lib/x",
    "rm -rf /tmp/*",
    "sudo rm -rf /tmp/x",
    "rm -rf /tmp/x; rm -rf /tmp/y",
    "mv /tmp/x /tmp/y",
    "cd /tmp && rm -rf x",
]

SANCTIONED_REVIEW = [
    "python3 ops/scripts/orch-bus/adversarial-review.py --cycle-id 1 "
    "--material-file /tmp/plan.md --review --reviewer-id esther",
    "python3 ~/.steadfaste-fleet/scripts/orch-bus/adversarial-review.py --cycle-id 1 "
    "--material 'text' --re-review --reviewer-id moses",
]
NOT_SANCTIONED_REVIEW = [
    "python3 adversarial-review.py --sweep",
    "python3 adversarial-review.py --cycle-id 1 --material-file x --review "
    "--reviewer-id e --sweep",
    "python3 adversarial-review.py --cycle-id 1 --material-file x --review",
    "sudo python3 adversarial-review.py --cycle-id 1 --material-file x --review --reviewer-id e",
]

DB_MUTATION = [
    "sqlite3 ~/.steadfaste-fleet/data/loop-governance.db \"DELETE FROM adversarial_reviews;\"",
    "python3 -c \"import sqlite3; c=sqlite3.connect('loop-governance.db'); c.execute('DROP TABLE x')\"",
    "python3 /tmp/adv.py --db ~/.steadfaste-fleet/data/loop-governance.db",
]
NOT_DB_MUTATION = [
    "python3 ops/scripts/orch-bus/adversarial-review.py --cycle-id 1 --material-file x --review --reviewer-id e",
    "python3 record-review.py --cycle-id 1",
    "sqlite3 ~/.steadfaste-fleet/data/loop-governance.db \"SELECT * FROM adversarial_reviews;\"",
]


@pytest.mark.parametrize("cmd", READ_ONLY + COMPOUND_READ_ONLY)
def test_readonly_parity(cmd):
    assert _CONTRACT.is_readonly_command(cmd) == \
        _ENFORCER._is_readonly_terminal_command(cmd), f"parity break on: {cmd}"


@pytest.mark.parametrize("cmd", WRITES + EVASIONS)
def test_write_parity(cmd):
    assert _CONTRACT.is_readonly_command(cmd) is False, f"contract let a write through: {cmd}"
    assert _ENFORCER._is_readonly_terminal_command(cmd) is False, f"enforcer: {cmd}"


@pytest.mark.parametrize("cmd", READ_ONLY + COMPOUND_READ_ONLY)
def test_spot_readonly(cmd):
    assert _CONTRACT.is_readonly_command(cmd), f"read-only misclassified: {cmd}"


@pytest.mark.parametrize("cmd", WRITES + EVASIONS)
def test_spot_write(cmd):
    assert _CONTRACT.is_readonly_command(cmd) is False
    assert _ENFORCER._is_readonly_terminal_command(cmd) is False


@pytest.mark.parametrize("cmd", SANCTIONED_UPDATE)
def test_sanctioned_update(cmd):
    assert _CONTRACT.is_sanctioned_fleet_update_command(cmd) == \
        _ENFORCER._is_sanctioned_fleet_update_command(cmd), cmd
    assert _CONTRACT.is_sanctioned_fleet_update_command(cmd) is True, cmd


@pytest.mark.parametrize("cmd", NOT_SANCTIONED_UPDATE)
def test_not_sanctioned_update(cmd):
    assert _CONTRACT.is_sanctioned_fleet_update_command(cmd) == \
        _ENFORCER._is_sanctioned_fleet_update_command(cmd), cmd
    assert _CONTRACT.is_sanctioned_fleet_update_command(cmd) is False, cmd


@pytest.mark.parametrize("cmd", SANCTIONED_TMP)
def test_sanctioned_tmp(cmd):
    assert _CONTRACT.is_sanctioned_tmp_cleanup_command(cmd) == \
        _ENFORCER._is_sanctioned_tmp_cleanup_command(cmd), cmd
    assert _CONTRACT.is_sanctioned_tmp_cleanup_command(cmd) is True, cmd


@pytest.mark.parametrize("cmd", NOT_SANCTIONED_TMP)
def test_not_sanctioned_tmp(cmd):
    assert _CONTRACT.is_sanctioned_tmp_cleanup_command(cmd) == \
        _ENFORCER._is_sanctioned_tmp_cleanup_command(cmd), cmd
    assert _CONTRACT.is_sanctioned_tmp_cleanup_command(cmd) is False, cmd


@pytest.mark.parametrize("cmd", SANCTIONED_REVIEW + NOT_SANCTIONED_REVIEW)
def test_plan_review_parity(cmd):
    assert _CONTRACT.is_sanctioned_plan_review_command(cmd) == \
        _ENFORCER._is_sanctioned_plan_review_command(cmd), cmd


@pytest.mark.parametrize("cmd", DB_MUTATION + NOT_DB_MUTATION)
def test_db_mutation_parity(cmd):
    assert _CONTRACT.is_governance_db_mutation_command(cmd) == \
        _ENFORCER._is_governance_db_mutation_command(cmd), cmd


# ── tool-name dispatch parity ────────────────────────────────────────────────

_TOOL_CASES = [
    ("write_file", {"path": "/tmp/x"}, True),
    ("patch", {"path": "/tmp/x"}, True),
    ("execute_code", {"code": "print(1)"}, True),
    ("memory", {"action": "add"}, True),
    ("text_to_speech", {"text": "hi"}, True),
    ("read_file", {"path": "/tmp/x"}, False),
    ("search_files", {"pattern": "x"}, False),
    ("terminal", {"command": "ls"}, False),
    ("terminal", {"command": "rm -rf x"}, True),
    ("terminal", {"command": "python3 -c 'x'"}, True),
    ("cronjob", {"action": "create"}, True),
    ("cronjob", {"action": "list"}, False),
    ("process", {"action": "kill"}, True),
    ("process", {"action": "list"}, False),
    ("computer_use", {"action": "click"}, True),
    ("computer_use", {"action": "screenshot"}, False),
    ("browser_exec", {"code": "x"}, False),
]


@pytest.mark.parametrize("tool_name,args,expect", _TOOL_CASES)
def test_write_tool_parity(tool_name, args, expect):
    got_contract = _CONTRACT.is_write_tool(tool_name, args)
    got_enforcer = _ENFORCER._is_write_tool(tool_name, args)
    assert got_contract == got_enforcer, \
        f"parity break on {tool_name} {args}: contract={got_contract} enforcer={got_enforcer}"