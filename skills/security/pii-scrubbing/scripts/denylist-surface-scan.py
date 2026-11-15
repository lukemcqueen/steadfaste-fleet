#!/usr/bin/env python3
"""Sweep the TRACKED surface against the enforcer's deny-list, printing no term.

Why this exists: the deny-list identifiers are stored as SHA-256 digests, so
they cannot be grepped. Nothing re-reads the repository after a write, so a
committed leak is invisible to the write-time gate. This script closes that gap
by hashing every window of every tracked file and comparing against the
enforcer's own digest set — it never holds, and never prints, an identifier.

Usage:
    python3 denylist-surface-scan.py                      # whole tracked tree
    python3 denylist-surface-scan.py path/one path/two    # named tracked files

Output (per finding): <path>:<line> class=<b64-prefix>/<length>
Exit code: 0 = no unsanctioned hit, 1 = findings, 3 = COULD NOT VERIFY (no
sanctioned hit was found at all, so the scanner's own reach is unproven and a
clean result must not be read as a pass).

Cost: O(total tracked bytes x number of deny-list lengths) SHA-256 calls.
Hash BYTES (one lower() per file) and shard across processes, or a ~12 MB tree
single-process takes minutes and can blow a tool timeout. An 8-way shard runs
in seconds. Always keep the positive control below: a scanner that found
nothing must be distinguishable from a clean tree.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ.get("PII_SCAN_REPO", Path.cwd())).resolve()
ENFORCER = Path(os.environ.get(
    "PII_ENFORCER",
    REPO / "plugins" / "governance-enforcer" / "__init__.py",
)).resolve()


def _load_enforcer():
    spec = importlib.util.spec_from_file_location("enforcer_scan", ENFORCER)
    if spec is None or spec.loader is None:
        sys.exit(f"cannot load {ENFORCER} — set PII_ENFORCER")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENF = _load_enforcer()
LENGTHS = tuple(ENF._PII_SENSITIVE_LENGTHS)
DIGESTS = ENF._PII_SENSITIVE_DIGESTS
URL_ALLOWED = ENF._PII_SENSITIVE_URL_ALLOWED
REPO_URL_RE = ENF._PII_REPO_URL_RE
# The gate has a SECOND sanction clause: the operator-placeholder regex
# ("fleet-operator"-style). Mirrored so a legitimate placeholder context is not
# reported as a finding — the guard must allow exactly what the gate allows.
OPERATOR_OK_RE = ENF._PII_SENSITIVE_OK_RE


def _tracked_files():
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _scan_file(rel):
    """Return (unsanctioned, sanctioned, visited) for one tracked file."""
    try:
        raw = (REPO / rel).read_bytes()
    except OSError:
        return [], 0, 0
    if b"\x00" in raw[:8192]:                 # binary is not prose
        return [], 0, 0
    low = raw.lower()
    n = len(low)
    hits, sanctioned = [], 0
    for length in LENGTHS:
        if length > n:
            continue
        for i in range(n - length + 1):
            window = low[i:i + length]
            if not (48 <= window[0] <= 57 or 97 <= window[0] <= 122):
                continue                       # the gate skips non-alnum starts
            digest = hashlib.sha256(window).digest()
            if digest not in DIGESTS:
                continue
            if digest in URL_ALLOWED:
                ctx = low[max(0, i - 60):i + length + 40].decode("utf-8", "ignore")
                if REPO_URL_RE.search(ctx) or OPERATOR_OK_RE.search(ctx):
                    sanctioned += 1            # repo URL or sanctioned placeholder
                    continue
            hits.append((rel, low.count(b"\n", 0, i) + 1,
                         base64.b64encode(digest).decode()[:6], length))
    return hits, sanctioned, 1


def _scan(files):
    results = None
    try:
        from concurrent.futures import ProcessPoolExecutor
        workers = min(8, os.cpu_count() or 1)
        if workers > 1 and len(files) > 64:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(_scan_file, files, chunksize=32))
    except Exception:                          # noqa: BLE001
        results = None                         # fall back, never skip silently
    if results is None:
        results = [_scan_file(rel) for rel in files]
    hits, sanctioned, visited = [], 0, 0
    for h, s, v in results:
        hits.extend(h)
        sanctioned += s
        visited += v
    return sorted(hits), sanctioned, visited


def main(argv):
    files = argv or _tracked_files()
    hits, sanctioned, visited = _scan(files)
    print(f"scanned {visited} tracked text files; "
          f"sanctioned repo-URL hits: {sanctioned}")
    for rel, line, key, length in hits:
        print(f"{rel}:{line} class={key}/{length}")
    if sanctioned == 0:
        print("COULD NOT VERIFY: no sanctioned hit found — the deny-list parse or "
              "the file walk is broken, so a clean result proves nothing. "
              "This is NOT a pass.")
        return 3
    if not hits:
        print("no unsanctioned deny-listed identifier in the tracked surface")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
