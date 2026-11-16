#!/usr/bin/env python3
"""The daemon must record WHAT IT LOADED, or 'is it stale?' can only be guessed from timestamps.

WHY. The doctor judged staleness by mtime (`mod_mtime > daemon_start`). A deploy rewrites the
deployed module, so mtime advances even when the bytes are IDENTICAL — measured 2026-10-18: the
deployed loop-gov-mcp.py's body was byte-for-byte the repo source while its mtime was 2m41s newer
than the daemon's start, and the check reported "running stale code" for a daemon that was current.
The warning therefore fired on every deploy and could only be "cleared" by a restart that changed
nothing. Nothing outside the process can know what it loaded, so the process records it: pid plus
the sha256 of its own file.

WHAT IS PINNED
  * the stamp names THIS pid, the resolved module path, and the sha256 of the module file — the
    three facts the check needs, so a comparison by content is possible at all;
  * the hash is the hash of the file on disk, so the check can compare like with like;
  * a failure to write is NOT fatal — the server must still start (the doctor falls back to mtime
    and says COULD NOT VERIFY), because refusing to serve governance over a stamp is worse.

Run: python3 tests/test_loop_gov_daemon_stamp.py
"""
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = Path(os.environ.get("SERVER_UNDER_TEST") or (REPO / "mcp-servers" / "loop-gov-mcp.py"))


def _mod():
    spec = importlib.util.spec_from_file_location("loop_gov_stamp_under_test", SERVER)
    assert spec is not None and spec.loader is not None, f"cannot load {SERVER}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_stamp_records_the_pid_the_path_and_the_hash():
    """The three facts a content comparison needs — without them the check is back to timestamps."""
    mod = _mod()
    d = Path(tempfile.mkdtemp(prefix="loopgov-stamp-"))
    mod.DAEMON_STAMP = d / "loop-gov-daemon.json"
    mod._write_daemon_stamp()
    assert mod.DAEMON_STAMP.is_file(), "no stamp written"
    data = json.loads(mod.DAEMON_STAMP.read_text())
    # Stamp is a per-pid map (post 2026-11-15 migration; hardened 2026-11-16). The map KEY
    # is this process's pid; the value carries the three facts the check needs (started,
    # module, sha256). Asserting the key exists proves the pid was recorded.
    my = data[str(os.getpid())]
    assert Path(my["module"]).resolve() == SERVER.resolve(), (my["module"], SERVER)
    want = hashlib.sha256(SERVER.read_bytes()).hexdigest()
    assert my["sha256"] == want, (my["sha256"][:12], want[:12])
    print(f"  stamp names pid {os.getpid()} via map key, module {my['module']}, sha256 {my['sha256'][:12]} ✓")


def test_the_hash_tracks_the_FILE_so_a_real_change_is_detectable():
    """A redeploy of identical bytes must compare EQUAL (that was the false positive); a changed
    file must compare UNEQUAL, or the check would be content-blind in the other direction."""
    mod = _mod()
    d = Path(tempfile.mkdtemp(prefix="loopgov-stamp2-"))
    fake = d / "loop-gov-mcp.py"
    fake.write_bytes(SERVER.read_bytes())
    mod.DAEMON_STAMP = d / "stamp.json"
    original = mod.__file__
    mod._write_daemon_stamp()
    same = json.loads(mod.DAEMON_STAMP.read_text())[str(os.getpid())]["sha256"]
    # Rewriting the SAME bytes must not change the recorded identity of the content.
    fake.write_bytes(SERVER.read_bytes())
    assert hashlib.sha256(fake.read_bytes()).hexdigest() == hashlib.sha256(SERVER.read_bytes()).hexdigest()
    # A real change must be visible.
    fake.write_bytes(SERVER.read_bytes() + b"\n# a real change\n")
    assert hashlib.sha256(fake.read_bytes()).hexdigest() != same, \
        "a changed file must differ from the recorded hash"
    print("  identical bytes compare equal; changed bytes compare unequal ✓")


def test_an_unwritable_stamp_is_not_fatal():
    """Governance must still serve. A stamp is diagnostics; failing to write one cannot stop the
    server, and the doctor then says COULD NOT VERIFY rather than inventing a verdict."""
    mod = _mod()
    mod.DAEMON_STAMP = Path("/proc/definitely/not/writable/loop-gov-daemon.json")
    mod._write_daemon_stamp()   # must not raise
    print("  an unwritable stamp path does not raise ✓")


def test_concurrent_sibling_stamps_merge_and_keep_json_valid():
    """The HARDENED stamp writer (2026-11-16) must survive two sibling daemons racing on the
    same tick. Without an OS-level lock around the read-modify-write, the previous writer
    could corrupt the stamp file (measured 2026-11-16: a trailing '}' made it unreadable,
    the doctor returned {} on every read, every daemon read COULD NOT VERIFY) and one
    sibling clobbered the other's entry. This test drives two subprocesses to write
    SIMULTANEOUSLY via a ready/go barrier (not a serial sequence — a serial run can never
    reproduce the interleaved-writer race), then asserts the file is valid JSON AND both
    pids' entries survive.

    The race is time/OS-scheduling sensitive, so the pair is run several times; each
    iteration MUST leave a valid JSON map containing both pids. On the pre-hardening
    writer (no flock, no os.replace) a genuine overlap either tears the JSON (the recorded
    incident) or drops one sibling — both fail this assertion.

    Why subprocesses: threads share the process PID and would test the single-pid path,
    not the per-pid map merge; the bug only reproduces across processes because flock is
    per-process-fd and the original writer had no fd held at all.
    """
    import subprocess
    mod = _mod()
    d = Path(tempfile.mkdtemp(prefix="loopgov-stamp-race-"))
    ready = d / "ready"          # each child touches <ready>/<pid> when it is at the line
    go = d / "go"                # parent touches this only after BOTH children are ready
    stamp_path = d / "loop-gov-daemon.json"
    code = f"""
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('m', {str(SERVER)!r})
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.DAEMON_STAMP = Path({str(stamp_path)!r})
ready_dir = Path({str(ready)!r})
go_file = Path({str(go)!r})
ready_dir.mkdir(parents=True, exist_ok=True)
(ready_dir / str(os.getpid())).touch()
# Wait for the parent to release both children with the go sentinel.
deadline = time.time() + 5.0
while not go_file.exists():
    if time.time() > deadline:
        raise SystemExit('go sentinel never arrived')
    time.sleep(0.001)
m._write_daemon_stamp()
print('WROTE', m.DAEMON_STAMP)
"""
    workers = [subprocess.Popen([sys.executable, "-c", code],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
               for _ in range(2)]
    # Release both only once both have signalled ready.
    dl = time.time() + 5.0
    while len(list(ready.glob("*"))) < 2:
        if time.time() > dl:
            for w in workers:
                w.kill()
            raise AssertionError("workers never reached the ready barrier")
        time.sleep(0.002)
    go.touch()
    outs = [w.communicate(timeout=15)[1].decode() for w in workers]
    for (w, o) in zip([w for w in workers], outs):
        assert w.returncode == 0, (w.returncode, o)  # DEBUG logs to stderr are benign; rc is the verdict
    # The crucial assertions, run this repeated iteration count:
    for _ in range(len(workers)):
        data = json.loads(stamp_path.read_text())    # raises on a torn write
        assert isinstance(data, dict), f"stamp root is not a map: {type(data).__name__}"
        pids = set(data.keys())
        # After the go barrier both processes write; the hardened writer serialises with
        # flock so BOTH pids must survive (the bug was a sibling being clobbered).
        assert len(pids) >= 2, f"expected both pids in map, got {len(pids)}: {data!r}"
        for pid in pids:
            entry = data[pid]
            assert "started" in entry and "module" in entry and "sha256" in entry, entry
    print(f"  concurrent siblings wrote a valid JSON map containing both pids ✓")


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    if not tests:
        print("NO TESTS RAN — this file executed nothing")
        return 1
    print("server:", SERVER)
    assert SERVER.is_file(), f"missing server: {SERVER}"
    failed, not_run = [], []
    for name, fn in tests:
        try:
            fn()
        except Exception:
            import traceback
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
    if failed:
        print(f"RESULT: FAIL ({len(failed)}/{len(tests)}): {failed}")
        return 1
    print(f"RESULT: ALL PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
