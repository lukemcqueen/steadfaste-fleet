#!/usr/bin/env python3
"""Discrimination proof for test_fix_advice_is_not_unconditionally_destructive.

Drives the SAME assertion at the PRE-FIX module (git HEAD), so the regression test is
shown to be non-vacuous: if the pre-fix code does NOT emit the bare destructive
`rm -rf <path>`, then the new assertion proves nothing about the fix.

Exit code = number of claims that FAILED to hold:
  0 -> the pre-fix module DOES emit the destructive form, so the new test discriminates.
  1 -> it does not, so the test would be vacuous (a bug in the probe).
"""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REL = "ops/scripts/manage/fleet_doctor/stale_premigration_home.py"


def main() -> int:
    # Extract the pre-fix content. Real `.py` basename: a suffix-less temp file loads as
    # an empty module and every assertion would pass vacuously (measured trap).
    tmp_dir = Path(tempfile.mkdtemp())
    tmp = tmp_dir / "stale_premigration_home_prefix.py"
    blob = subprocess.run(["git", "show", f"HEAD:{REL}"],
                          cwd=REPO, capture_output=True, check=True).stdout
    tmp.write_bytes(blob)

    spec = importlib.util.spec_from_file_location("prefix_mod", tmp)
    assert spec is not None and spec.loader is not None, f"cannot load {tmp}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class FakeRes:
        def __init__(self):
            self.checks = []

        def add(self, name, status, detail="", fix=""):
            self.checks.append((name, status, detail, fix))

    p = tmp_dir / ".hermes-cortex"
    p.mkdir()
    mod.STALE_PREMIGRATION_PATHS = [p]
    res = FakeRes()
    mod.check_stale_premigration_home(res)
    fix = res.checks[0][3]

    print("PRE-FIX fix text:")
    print(f"  {fix}")
    print()

    failed = 0
    emits_destructive = ("rm -rf " + str(p)) in fix
    print(f"claim 1: pre-fix emits bare destructive form  -> {emits_destructive}")
    if not emits_destructive:
        failed += 1

    names_safe_tool = "migrate-victoria-data.sh" in fix
    print(f"claim 2: pre-fix does NOT name the safe tool   -> {not names_safe_tool}")
    if names_safe_tool:
        failed += 1

    print()
    if failed == 0:
        print("RESULT: the pre-fix module emits the destructive form and omits the safe tool,")
        print("        so the regression assertion FAILS against it and PASSES after the fix.")
        print("        The test discriminates.")
    else:
        print(f"RESULT: {failed} claim(s) did not hold — the probe is wrong, not the code.")
    return failed


# Inert on import: nothing runs until invoked as a script, so a pytest collection pass
# (or any importer) cannot execute the probe or trip its SystemExit.
if __name__ == "__main__":
    sys.exit(main())
