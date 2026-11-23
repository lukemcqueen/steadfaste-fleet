#!/usr/bin/env python3
"""fleet_lib.envfile — ONE env-file ladder and ONE quoting rule.

This replaces ~18 hand-rolled key=value parsers that each used a different path
ladder (FLEET_ENV_FILE→repo→deploy→~/.hermes vs a fixed 2-path vs ~/steadfaste-
fleet only) and a different quoting rule — so the SAME key placed in the
deploy-home .env resolved for one service and not another. These tests pin the
canonical contract every consumer now depends on.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "ops" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from fleet_lib import envfile  # noqa: E402


def _write_env(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ── the four-path ladder ──────────────────────────────────────────────
# Keys in an OTHER file earlier in the ladder win over a later file, and the
# FIRST match within a file wins.

def test_ladder_resolves_all_four_positions(tmp_path):
    env = {
        "FLEET_ENV_FILE": str(tmp_path / "custom.env"),
        "FLEET_REPO": str(tmp_path / "repo"),
        "FLEET_DEPLOY_HOME": str(tmp_path / "deploy"),
    }
    paths = envfile.env_file_paths(env)
    assert str(paths[0]).endswith("custom.env")                 # from FLEET_ENV_FILE
    assert str(paths[1]).endswith("repo/.env")                  # from FLEET_REPO
    assert str(paths[2]).endswith("deploy/.env")                # from FLEET_DEPLOY_HOME
    assert paths[3].name == ".env" and ".hermes" in str(paths[3])  # legacy


def test_first_match_wins_across_ladder(tmp_path):
    # same key in repo and deploy .env — repo (earlier) wins
    repo = _write_env(tmp_path / "repo" / ".env", "TOKEN=repo-token\n")
    deploy = _write_env(tmp_path / "deploy" / ".env", "TOKEN=deploy-token\n")
    env = {"FLEET_REPO": str(tmp_path / "repo"), "FLEET_DEPLOY_HOME": str(tmp_path / "deploy")}
    assert envfile.env_file_value("TOKEN", env=env) == "repo-token"
    # ladder order: repo (index 0) before deploy (index 1), since FLEET_ENV_FILE is unset
    assert str(repo.parent) in str(envfile.env_file_paths(env)[0])
    assert str(deploy.parent) in str(envfile.env_file_paths(env)[1])


def test_present_file_beats_later_absent(tmp_path):
    _write_env(tmp_path / "deploy" / ".env", "SECRET=from-deploy\n")
    env = {"FLEET_DEPLOY_HOME": str(tmp_path / "deploy"), "FLEET_REPO": str(tmp_path / "no-repo")}
    assert envfile.env_file_value("SECRET", env=env) == "from-deploy"


def test_first_occurrence_within_a_file_wins(tmp_path):
    _write_env(tmp_path / "repo" / ".env", "K=first\nK=second\n")
    env = {"FLEET_REPO": str(tmp_path / "repo")}
    assert envfile.env_file_value("K", env=env) == "first"


# ── one quoting rule ──────────────────────────────────────────────────
# surrounding '"' or "'" is stripped once; bare values pass through.

def test_double_and_single_quotes_are_stripped(tmp_path):
    body = 'DQ="double-value"\nSQ=\'single-value\'\nBARE=bare-value\n'
    _write_env(tmp_path / "repo" / ".env", body)
    env = {"FLEET_REPO": str(tmp_path / "repo")}
    assert envfile.env_file_value("DQ", env=env) == "double-value"
    assert envfile.env_file_value("SQ", env=env) == "single-value"
    assert envfile.env_file_value("BARE", env=env) == "bare-value"


def test_value_spaces_after_equals_are_trimmed(tmp_path):
    _write_env(tmp_path / "repo" / ".env", "WHITESPACE=   padded   \n")
    env = {"FLEET_REPO": str(tmp_path / "repo")}
    assert envfile.env_file_value("WHITESPACE", env=env) == "padded"


def test_comment_and_data_lines_are_ignored_for_other_keys(tmp_path):
    # a full comment line or a non-matching key must not pollute OUR key
    _write_env(tmp_path / "repo" / ".env", "# comment line\nOTHER=1\nTARGET=real\n")
    env = {"FLEET_REPO": str(tmp_path / "repo")}
    assert envfile.env_file_value("TARGET", env=env) == "real"


# ── process-env-first contract ───────────────────────────────────────
# A value in the process environment (or injected env dict) always beats the files.

def test_process_env_wins_over_files(tmp_path, monkeypatch):
    _write_env(tmp_path / "repo" / ".env", "P=from-file\n")
    monkeypatch.setenv("FLEET_REPO", str(tmp_path / "repo"))
    monkeypatch.setenv("P", "from-process")
    assert envfile.env_value("P") == "from-process"


def test_injected_env_dict_wins_over_files(tmp_path):
    _write_env(tmp_path / "repo" / ".env", "P=from-file\n")
    env = {"FLEET_REPO": str(tmp_path / "repo"), "P": "from-dict"}
    assert envfile.env_value("P", env=env) == "from-dict"


# ── failure semantics ────────────────────────────────────────────────
# absent files are skipped; a missing/empty key returns ''.

def test_absent_key_returns_empty(tmp_path):
    _write_env(tmp_path / "repo" / ".env", "OTHER=1\n")
    env = {"FLEET_REPO": str(tmp_path / "repo")}
    assert envfile.env_value("MISSING", env=env) == ""


def test_missing_file_is_skipped_not_fatal(tmp_path):
    env = {"FLEET_REPO": str(tmp_path / "no-such-repo")}
    assert envfile.env_value("ANY", env=env) == ""


def test_env_file_value_skips_custom_paths_when_given(tmp_path):
    # passing paths= forces the scan list, ignoring the env ladder
    custom = _write_env(tmp_path / "custom.env", "CUSTOM=here\n")
    assert envfile.env_file_value("CUSTOM", paths=[custom]) == "here"
    assert envfile.env_file_value("CUSTOM", paths=[], env={}) == ""