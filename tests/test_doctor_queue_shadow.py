#!/usr/bin/env python3
"""The doctor must survive a repo-local `queue.py` shadowing the stdlib.

Root cause this pins (fixed 2026-11-16): the deploy places the bus's queue.py at
the DEPLOY ROOT —

    register "core/fleet_bus/queue.py" "${FLEET_DEPLOY_HOME}/queue.py"

which is load-bearing (the deployed gateway expects it). But
`fleet_doctor/checks.py` inserts `Path(__file__).resolve().parents[2]` on
sys.path, and on a deployed host that IS the deploy root — so `import queue`
resolved to the bus module and every
`concurrent.futures.ThreadPoolExecutor(...)` died with
`AttributeError: module 'queue' has no attribute 'SimpleQueue'`
(check_harness_registry). It never reproduced from the repo root because the
in-repo tree has no TOP-LEVEL queue.py (it is at core/fleet_bus/queue.py), so it
only ever broke on a deployed host.

The fix pins the stdlib `queue` in sys.modules before that insert. This test
runs the import in a SUBPROCESS with a hostile shadow on sys.path[0], because
`queue` is imported once per interpreter and an in-process test would pass on
the already-correct module.

Run:  pytest tests/test_doctor_queue_shadow.py
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK_PY = REPO / "ops" / "scripts" / "manage" / "fleet_doctor" / "checks.py"


def _run_with_shadow(tmp_path: Path, stub: str) -> subprocess.CompletedProcess:
    """Import fleet_doctor.checks with `stub` as queue.py ahead of the stdlib."""
    shadow = tmp_path / "shadow"
    shadow.mkdir(exist_ok=True)
    (shadow / "queue.py").write_text(stub)
    prog = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(shadow)!r})          # hostile: shadows stdlib queue
        sys.path.insert(0, {str(REPO / 'ops' / 'scripts' / 'manage')!r})
        import queue
        before = getattr(queue, '__file__', '?')
        import fleet_doctor.checks as C                # noqa: F401
        import queue as after
        has_sq = hasattr(after, 'SimpleQueue')
        print('HAS_SIMPLEQUEUE=' + str(has_sq))
        print('BEFORE=' + before)
        print('AFTER=' + getattr(after, '__file__', '?'))
        # the real crash site: ThreadPoolExecutor must construct
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=1) as ex:
            assert list(ex.map(lambda x: x + 1, [1])) == [2]
        print('TPE=ok')
    """)
    return subprocess.run([sys.executable, "-c", prog],
                          capture_output=True, text=True, cwd=str(REPO))


BUS_STUB = '"""A bus module that is NOT the stdlib queue (no SimpleQueue)."""\nBROKER = "pgmq"\n'


def test_stdlib_queue_wins_over_a_shadowed_queue(tmp_path):
    r = _run_with_shadow(tmp_path, BUS_STUB)
    assert r.returncode == 0, f"import failed:\n{r.stdout}\n{r.stderr}"
    assert "HAS_SIMPLEQUEUE=True" in r.stdout, (
        "the stdlib queue was shadowed — checks.py must pin it in sys.modules "
        f"before inserting the deploy root on sys.path\n{r.stdout}"
    )
    assert "TPE=ok" in r.stdout, f"ThreadPoolExecutor still fails:\n{r.stdout}\n{r.stderr}"


def test_the_shadow_really_would_win_without_the_pin(tmp_path):
    """Disproves the test: without checks.py's pin, the shadow takes effect.

    Guards against a vacuous pass — if the stub could never shadow anything, the
    test above would prove nothing.
    """
    prog = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(tmp_path / 'shadow')!r})
        import queue
        print('SHADOWED=' + str(not hasattr(queue, 'SimpleQueue')))
    """)
    shadow = tmp_path / "shadow"
    shadow.mkdir(exist_ok=True)
    (shadow / "queue.py").write_text(BUS_STUB)
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True)
    assert "SHADOWED=True" in r.stdout, (
        f"the stub did not shadow the stdlib — the test is vacuous:\n{r.stdout}{r.stderr}"
    )
