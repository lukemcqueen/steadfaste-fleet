#!/usr/bin/env python3
"""Hermetic test for the collapsed single-git-log parsing in the doctor.

_check_changed_script_execution used to spawn one `git log -1 --format=%ct
-- <file>` subprocess per changed file (362 files in a 30-day window => ~360
git spawns, ~13s). It now feeds ONE `git log --since=... --name-only
--pretty=format:%ct -- ops/scripts/` run into _changed_scripts_from_git_log,
which maps each touched file basename to its LATEST commit time.

These tests cover the parser against representative git-log output: filenames
containing digits, blank lines between commits (git inserts them between log
records), non-.py/.sh lines that must be ignored, dir-less files (no '/')
that must be ignored, and the newest-first rule (setdefault keeps the first
seen = latest) so the map matches what `git log -1 --format=%ct -- <file>`
per file would have produced.

Run: python3 -m pytest tests/test_doctor_changed_script_parse.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "ops" / "scripts" / "manage") not in sys.path:
    sys.path.insert(0, str(_REPO / "ops" / "scripts" / "manage"))

from fleet_doctor.checks import _changed_scripts_from_git_log  # noqa: E402


def test_single_commit_maps_files_to_their_timestamp():
    out = "1700000000\nops/scripts/foo.py\nops/scripts/sub/bar.sh\n"
    assert _changed_scripts_from_git_log(out) == {"foo.py": 1700000000,
                                                  "bar.sh": 1700000000}


def test_newest_first_keeps_the_latest_timestamp_per_file():
    # foo.py is touched by BOTH a newer (1700000100) and an older (1700000000)
    # commit; the per-file result must be its LATEST commit time, exactly what
    # `git log -1 --format=%ct -- foo.py` would have returned.
    out = ("1700000100\nops/scripts/foo.py\n"
           "\n"
           "1700000000\nops/scripts/foo.py\nops/scripts/baz.py\n")
    assert _changed_scripts_from_git_log(out) == {"foo.py": 1700000100,
                                                  "baz.py": 1700000000}


def test_filters_non_py_sh_and_dirless_files_keeps_digit_filenames():
    # notes.md ignored (not .py/.sh); README (no ext) ignored; Makefile ignored
    # (no '/'); ops/scripts/Makefile ignored (no .py/.sh); 123.py kept (a real
    # path with '/' and a valid extension, not a timestamp).
    out = ("1700000000\nops/scripts/notes.md\nops/scripts/README\n"
           "ops/scripts/123.py\nMakefile\nops/scripts/Makefile\n")
    assert _changed_scripts_from_git_log(out) == {"123.py": 1700000000}


def test_blank_lines_between_commits_are_ignored():
    out = "1700000000\nops/scripts/a.py\n\n\n1700000100\nops/scripts/b.py\n"
    assert _changed_scripts_from_git_log(out) == {"a.py": 1700000000,
                                                  "b.py": 1700000100}


def test_empty_output_yields_empty_map():
    assert _changed_scripts_from_git_log("") == {}
