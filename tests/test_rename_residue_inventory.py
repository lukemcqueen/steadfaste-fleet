"""Tests for skills/devops/rename-sweep/scripts/rename-residue-inventory.py.

The script measures residue of a rename sweep over a git tree. Its two pure,
side-effect-free units carry the logic the whole probe exists for:

- `families()` builds the family regexes whose GUARDS must be
  case-insensitive (a lowercase guard like `my` must still protect the
  UPPERCASE spelling `MY` in `MYCORTEX_X`) and must block a swept term that
  is a SUBSTRING of a keep (`cortex` inside `mycortex`).
- `is_record()` excludes record directories and dated filenames from every
  count, so a record quoting the OLD name is not double-counted as residue.

Hermetic: families() and is_record() never touch the filesystem or shell out.
Run: bash tests/run-tests.sh tests/test_rename_residue_inventory.py -q
"""

import importlib.util
import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
SCRIPT = SKILLS / "devops/rename-sweep/scripts/rename-residue-inventory.py"

# The script's filename is hyphenated (rename-residue-inventory.py), which is
# not importable by `import`, so load it by path.
_spec = importlib.util.spec_from_file_location("rename_residue_inventory", SCRIPT)
ri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ri)


def test_families_guard_is_case_insensitive_for_env_const_family():
    # `my` must protect `MYCORTEX_X` in the uppercase env/const family.
    fam = ri.families("cortex", ["my"])
    env_pattern = fam["CORTEX_ (env/const)"]
    # The guard must be a case-insensitive negative lookbehind on `my`.
    assert re.search(r"\(\?i:\(\?<!my\)\)", env_pattern) is not None
    # `MYCORTEX_X` must NOT match the CORTEX_ family (guard blocks it).
    assert re.search(env_pattern, "MYCORTEX_COMPOSE_DIR") is None
    # A genuine `CORTEX_X` still matches.
    assert re.search(env_pattern, "CORTEX_HOME") is not None


def test_families_bare_term_does_not_match_substring_keep():
    # `cortex` must not match inside `mycortex` at the bare-word family.
    fam = ri.families("cortex", ["my"])
    bare = fam["bare cortex"]
    assert re.search(bare, "mycortex") is None
    assert re.search(bare, "cortex") is not None


def test_families_hyphen_family_matches_hyphenated_term():
    fam = ri.families("cortex", [])
    assert re.search(fam["cortex- (hyphen)"], "cortex-update") is not None
    # bare `cortex` with no boundary should not match a plain word char run
    assert re.search(fam["cortex- (hyphen)"], "cortexupdate") is None


def test_is_record_excludes_record_dirs():
    assert ri.is_record("docs/evidence/skill-drift-parity.txt") is True
    assert ri.is_record("docs/older/migration-notes.md") is True
    assert ri.is_record("tests/artifacts/foo.txt") is True


def test_is_record_excludes_dated_filenames():
    assert ri.is_record("anywhere/migration-2026-11-13.md") is True


def test_is_record_keeps_live_surface_files():
    assert ri.is_record("skills/devops/rename-sweep/SKILL.md") is False
    assert ri.is_record("src/fleet_bus.py") is False
