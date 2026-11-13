#!/usr/bin/env python3
"""Measure the residue of a rename sweep over a git tree BEFORE planning it.

A previous session's "N references remain" is a hypothesis scoped to the files
that session read. Re-measure every spelling family over the tracked tree with
the record directories excluded, and print the exclusion list beside the count
so the coverage claim is checkable.

Usage:
  rename-residue-inventory.py --term cortex [--guard my --guard hermes-]
                              [--repo .] [--json residue.json] [--top 25]

--guard is a prefix that must NOT be swept because it belongs to a DIFFERENT
product or family (e.g. `my` protects `mycortex`, `hermes-` protects a deferred
skill family). Guards become negative lookbehinds on every family pattern, matched
CASE-INSENSITIVELY so a lowercase guard still protects the uppercase spelling (`my`
must protect `MYCORTEX_X` in the env/const family) - a swept term is often a
substring of the keep, so the boundary/guard is what makes the count meaningful.

Exit code is always 0; this is a read-only probe, safe to run outside a
write-class tool. Run it from inside a governance cycle anyway if the repo
requires a lock for interpreters.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys

# Record directories are EXCLUDED from every count: a record quotes the old name
# as the thing that WAS there at that revision. Rewriting one falsifies it.
RECORD_DIRS = ("docs/older/", "docs/evidence/", "tests/artifacts/", "docs/reviews/")
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
LINE = re.compile(r"^\./(.+?):(\d+):(.*)$")


def families(term: str, guards: list[str]) -> dict[str, str]:
    # Guards MUST be case-insensitive. The env/const family is UPPERCASE, so a
    # case-sensitive `(?<!my)` never matches the `MY` in `MYCORTEX_X` and the keep
    # it protects is counted as residue - the exact over-count this probe exists
    # to prevent (a swept term is routinely a SUBSTRING of a keep).
    guard = "".join("(?i:(?<!%s))" % re.escape(g) for g in guards)
    low, up = term.lower(), term.upper()
    return {
        f"{low}- (hyphen)": guard + r"(?i)" + re.escape(low) + "-",
        f"{low}_ (underscore)": guard + r"(?i)" + re.escape(low) + "_",
        f"{up}_ (env/const)": guard + re.escape(up) + "_",
        f"{term.title()} (title):": guard + re.escape(term.title()),
        f"bare {low}": guard + r"(?<![A-Za-z0-9_])" + re.escape(low) + r"(?![A-Za-z0-9_])",
    }


def rg(pattern: str, repo: str) -> list[str]:
    try:
        out = subprocess.run(
            ["rg", "--no-messages", "-I", "-H", "-n", "--pcre2", pattern, "."],
            cwd=repo, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        sys.exit("ripgrep (rg) not found — install it or fall back to git grep")
    return out.stdout.splitlines()


def is_record(path: str) -> bool:
    return path.startswith(RECORD_DIRS) or bool(DATED.search(os.path.basename(path)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--term", required=True)
    ap.add_argument("--guard", action="append", default=[])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    kis = args.guard
    all_resid: list[dict] = []
    union_files: set[str] = set()

    print(f"# residue inventory for term={args.term!r} repo={repo}")
    print(f"# guards (protected prefixes): {kis or 'none'}")
    print(f"# excluded record dirs: {', '.join(RECORD_DIRS)} + any dated filename")

    for label, pattern in families(args.term, kis).items():
        total = rec = 0
        files, carriers = set(), collections.Counter()
        for ln in rg(pattern, repo):
            m = LINE.match(ln)
            if not m:
                continue
            path, num, text = m.group(1), int(m.group(2)), m.group(3)
            total += 1
            if is_record(path):
                rec += 1
                continue
            files.add(path)
            carriers[path] += 1
            all_resid.append({"file": path, "line": num, "family": label, "text": text.strip()})
        union_files |= files
        print(f"\n[{label}] total={total} files={len(files)} record-excluded={rec} in-scope={total - rec}")
        for path, n in carriers.most_common(args.top):
            print(f"    {n:5d}  {path}")

    print(f"\n# UNION: {len(all_resid)} in-scope lines across {len(union_files)} files")
    print("# record-excluded lines were counted above but are NOT in the union list.")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(all_resid, fh, indent=0)
        print(f"# wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
