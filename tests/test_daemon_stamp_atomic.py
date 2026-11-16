"""Regression test for the loop-gov daemon stamp corruption.

Root cause (Titus, 2026-11): _write_daemon_stamp() in mcp-servers/loop-gov-mcp.py
did a non-atomic read-modify-write (read_text -> write_text) with no lock. The MCP
daemons spawn in sibling pairs on the same tick; two writers racing interleaved the
torn file (a trailing stray brace), and only one sibling of each pair survived in
the stamp. The doctor then read the corrupt file as empty -> COULD NOT VERIFY.

Fix under test: the stamp is (1) serialised with fcntl.flock on a side lock file
and (2) written to a temp in the same dir then os.replace()d atomically, so the
on-disk file is never torn and no sibling's entry is lost.

This test spawns N concurrent writer PROCESSES (distinct pids => distinct keys)
behind a barrier to force the read-modify-write race, then asserts the file is
valid JSON and carries every process's entry. On spawn (macOS default) each worker
re-imports the source; the absolute source path is computed from this file.
"""

import json
import multiprocessing
import pathlib

LOOP_GOV = (pathlib.Path(__file__).resolve().parent.parent /
            "mcp-servers" / "loop-gov-mcp.py")


def _writer(path: str, barrier) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("lgm_writer", LOOP_GOV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DAEMON_STAMP = pathlib.Path(path)
    barrier.wait()
    mod._write_daemon_stamp()


def test_daemon_stamp_survives_concurrent_sibling_writers(tmp_path):
    stamp = tmp_path / "loop-gov-daemon.json"
    n = 8  # pairs +1: more than the two siblings that raced in production
    barrier = multiprocessing.Barrier(n)
    procs = [multiprocessing.Process(target=_writer, args=(str(stamp), barrier))
             for _ in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    for p in procs:
        assert p.exitcode == 0, f"writer process {p.pid} failed (exit {p.exitcode})"
    # The file must be VALID JSON (no torn write) ...
    data = json.loads(stamp.read_text())
    assert isinstance(data, dict)
    # ... and carry EVERY sibling's entry (no lost sibling from a clobbered RMW).
    assert len(data) == n, f"expected {n} daemon entries, got {len(data)}: {sorted(data.keys())}"
    for rec in data.values():
        assert isinstance(rec, dict)
        assert "sha256" in rec and "module" in rec and "started" in rec


def test_daemon_stamp_single_write_is_valid():
    # A single write (non-concurrent) still yields a clean, parseable stamp.
    import tempfile
    import importlib.util
    spec = importlib.util.spec_from_file_location("lgm_solo", LOOP_GOV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as d:
        stamp = pathlib.Path(d) / "loop-gov-daemon.json"
        mod.DAEMON_STAMP = stamp
        mod._write_daemon_stamp()
        data = json.loads(stamp.read_text())
        assert isinstance(data, dict) and len(data) == 1
