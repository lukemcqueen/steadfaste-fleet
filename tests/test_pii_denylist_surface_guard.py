#!/usr/bin/env python3
"""Surface guard: the repo must carry no deny-listed identifier.

Run: tests/run-tests.sh tests/test_pii_denylist_surface_guard.py

The enforcer's PII gate (plugins/governance-enforcer/__init__.py) refuses a
WRITE that carries a deny-listed identifier, but nothing checked the repository
that already exists — so an identifier could enter the tree by a path the gate
never saw (a redaction that missed a fragment, an import from another host, a
file added before its entry landed). This test closes that read-side gap: it
hashes every window of every tracked file against the enforcer's OWN digest set
and fails on any hit outside the one sanctioned context.

Source of truth is the enforcer module itself:

  * the digests and their lengths come from `_PII_SENSITIVE_DIGESTS` /
    `_PII_SENSITIVE_LENGTHS` — never a hand-typed list, which would go stale and
    would itself be an index of the identifiers;
  * the sanctioned context reuses `_PII_SENSITIVE_URL_ALLOWED` and
    `_PII_REPO_URL_RE`, so the repo's own public GitHub URL (whose owner segment
    embeds the surname) stays allowed exactly as it is at write time.

The scan is over `git ls-files` (TRACKED files only): a gitignored local config
legitimately holds real values and is not part of the public surface.

Cost: the window sweep is O(total tracked bytes x len(_PII_SENSITIVE_LENGTHS)),
~12 MB on this repo, so the per-file sweep is sharded across processes with a
serial fallback — measured, not assumed (see the evidence record this test
came with).

No term is ever printed — a failure names path, line and the digest's base64
prefix (already committed in the enforcer), never the identifier.
"""
import base64
import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_ENFORCER = REPO / "plugins" / "governance-enforcer" / "__init__.py"
_SPEC = importlib.util.spec_from_file_location("enforcer_surface_guard", _ENFORCER)
assert _SPEC is not None and _SPEC.loader is not None, f"cannot load {_ENFORCER}"
enf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(enf)

LENGTHS = tuple(enf._PII_SENSITIVE_LENGTHS)
DIGESTS = enf._PII_SENSITIVE_DIGESTS
URL_ALLOWED = enf._PII_SENSITIVE_URL_ALLOWED
REPO_URL_RE = enf._PII_REPO_URL_RE
OPERATOR_OK_RE = enf._PII_SENSITIVE_OK_RE

_SHA = hashlib.sha256
_SCAN_CACHE = []


def _scan_once():
    """The sweep is deterministic and costs ~40 s; run it once per session."""
    if not _SCAN_CACHE:
        _SCAN_CACHE.append(_scan())
    return _SCAN_CACHE[0]


def _class_key(digest):
    return base64.b64encode(digest).decode()[:6]


def _tracked_files():
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _scan_file(rel):
    """Sweep one tracked file. Returns (unsanctioned, sanctioned, visited).

    Windows are hashed over BYTES — one encode per file instead of one per
    window. The first-byte filter (alnum only) mirrors the gate, which skips
    windows that do not start alphanumeric.
    """
    try:
        raw = (REPO / rel).read_bytes()
    except FileNotFoundError:
        # Tracked (still in the index) but absent from the worktree — a pending
        # deletion or a sparse checkout. The tracked surface IS the index, so
        # read its blob; only a file absent from BOTH is unverifiable.
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(REPO), "show", ":" + rel], stderr=subprocess.DEVNULL
            )
        except (subprocess.CalledProcessError, OSError):
            # Cannot read it at all — the guard must FAIL, never report clean.
            return [(rel, 0, "READFAIL", 0)], 0, 1
    except OSError:
        # Some other read failure — the guard must FAIL, never report clean.
        return [(rel, 0, "READFAIL", 0)], 0, 1
    low = raw.lower()
    n = len(low)
    unsanctioned = []
    sanctioned = 0
    for length in LENGTHS:
        if length > n:
            continue
        for i in range(n - length + 1):
            b = low[i:i + length]
            if not (48 <= b[0] <= 57 or 97 <= b[0] <= 122):
                continue
            digest = _SHA(b).digest()
            if digest not in DIGESTS:
                continue
            ctx = low[max(0, i - 60):i + length + 40]
            if digest in URL_ALLOWED and (
                REPO_URL_RE.search(ctx.decode("utf-8", "ignore"))
                or OPERATOR_OK_RE.search(ctx.decode("utf-8", "ignore"))
            ):
                sanctioned += 1
                continue
            unsanctioned.append(
                (rel, low.count(b"\n", 0, i) + 1, _class_key(digest), length)
            )
    return unsanctioned, sanctioned, 1


def _scan():
    files = _tracked_files()
    unsanctioned = []
    sanctioned = 0
    visited = 0
    results = None
    try:
        from concurrent.futures import ProcessPoolExecutor
        workers = min(8, os.cpu_count() or 1)
        if workers > 1 and len(files) > 64:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(_scan_file, files, chunksize=32))
    except Exception:                        # noqa: BLE001 — fall back, never
        results = None                       # silently skip the sweep
    if results is None:
        results = [_scan_file(rel) for rel in files]
    for u, s, v in results:
        unsanctioned.extend(u)
        sanctioned += s
        visited += v
    unsanctioned.sort()
    return unsanctioned, sanctioned, visited


def test_scanner_sees_the_sanctioned_repo_url_context():
    """Probe the probe: the scanner must FIND the sanctioned hits.

    The repo's own public URL (README, install one-liners) embeds the
    URL-allowed entry. A scanner that silently found nothing — a bad digest
    parse, a wrong path, an empty file list — would report a clean surface. So a
    zero here is a FAILURE of the scanner, not a clean tree.
    """
    unsanctioned, sanctioned, visited = _scan_once()
    total = len(_tracked_files())
    assert visited == total, (
        f"scanned {visited}/{total} tracked files — a file was excluded by a "
        "heuristic or unreadable, so the clean verdict is meaningless"
    )
    assert sanctioned > 0, (
        "scanner found no sanctioned repo-URL hit — the deny-list parse or the "
        "file walk is broken, so its verdict is meaningless"
    )
    assert isinstance(unsanctioned, list)


def test_no_unsanctioned_denylisted_identifier_in_tracked_surface():
    unsanctioned, _sanctioned, _visited = _scan_once()
    assert unsanctioned == [], (
        "deny-listed identifier in a tracked file (path, line, class, length): "
        + repr(unsanctioned)
    )
