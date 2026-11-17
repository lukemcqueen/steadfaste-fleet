#!/usr/bin/env python3
"""Reclaim deploy orphans by PROVENANCE, never by heuristic.

WHY THIS EXISTS
---------------
The deploy copies registered repo files into ~/.steadfaste-fleet. When a
destination is renamed or dropped from the register() map, the previously
deployed copy is left behind forever: clean_stale_deploys() in fleet-update.sh
scans only scripts/*.py|sh and only AFTER a --force-all, and even on a hit it
warns instead of acting. It has to: without memory of what it wrote, a "stale"
file and a host-local file that must never be touched are INDISTINGUISHABLE.
That is not theoretical - on 2026-08-03 local-cwr-file-processing was lost by
exactly that mistake, and the response was to stop deleting anything.

This reclaims the ground we can stand on: what the deploy PROVABLY wrote.

    orphans = (paths in the PREVIOUS inventory) - (paths in the CURRENT deploy)

Anything absent from the previous inventory was not created by us - a host-local
helper, an operator's file, a local-* script - and is NEVER touched. That is a
structural guarantee, not a heuristic that happens to be tuned well.

INVENTORY OWNERSHIP
-------------------
This tool is the single writer of the inventory. It reads the existing file as
OLD, reclaims orphans, then atomically replaces it with the list we just
deployed and archives the previous one as <inventory>.prev for audit.

SAFETY INVARIANTS (all covered by tests)
----------------------------------------
1. A missing or empty OLD inventory is a NO-OP. We sweep nothing we cannot
   prove. This is why the first run after this ships deletes nothing.
2. A path is only ever reclaimed if it is INSIDE --deploy-home. A corrupt or
   hostile inventory cannot reach outside the deploy tree.
3. Orphans are QUARANTINED (moved), never deleted, into
   <quarantine-dir>/<utc-timestamp>/<relative-path>, and the exact restore
   command is printed. Unattended destructive steps default to no-op; deleted
   data cannot be remediated.
4. Symlinks are skipped, not moved: they may be intentional (the doctor's hooks
   are symlinks into registered scripts) and a link's target is not ours.
5. local-* basenames are never reclaimed (belt-and-braces: they cannot appear
   in an inventory, since they are never registered).
6. --dry-run moves nothing.

Exit: 0 reclaimed-or-nothing-to-do, 3 a quarantine move FAILED (fail closed so
the deployer surfaces it), 4 usage/config error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import sys
from pathlib import Path

PRESERVE_PREFIX = "local-"


def _read_list(path: Path) -> list[str] | None:
    """Read a one-path-per-line list. None when the file does not exist.

    Missing != empty: a missing inventory means 'no memory yet' and MUST behave
    like no memory, not like 'nothing was ever ours' (which would sweep nothing
    anyway) and never like 'everything is ours'.
    """
    if not path.exists():
        return None
    out = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _normalise(paths: list[str], deploy_home: Path) -> set[str]:
    """Make absolute, keep only paths under deploy_home; drop everything else.

    Invariant 2: the deploy tree is the only place we have authority over, so a
    path outside it is discarded here rather than defended against later.

    NEVER .resolve() an entry. Resolving follows symlinks, so an intentional
    hook -> registered-script symlink would be rewritten into its TARGET, and
    the reclaimer would then quarantine the live script it points at. That is
    the destroy-something-we-still-need failure this tool exists to prevent, so
    paths are normalised LEXICALLY and symlinks are detected later, as
    themselves. (Caught by test_symlink_orphan_is_skipped_target_intact.)
    """
    keep: set[str] = set()
    home = os.path.abspath(str(deploy_home))
    prefix = home.rstrip("/") + "/"
    for p in paths:
        ap = os.path.abspath(os.path.normpath(p))
        if ap == home:
            continue
        if ap.startswith(prefix):
            keep.add(ap)
    return keep


def reclaim(
    inventory: Path,
    deployed: Path,
    deploy_home: Path,
    quarantine_dir: Path,
    dry_run: bool = False,
) -> tuple[int, list[str], list[str], list[str]]:
    """Return (exit_code, messages, reclaimed, errors). Never raises for a
    reclaimable condition - the deployer needs a status it can act on."""
    messages: list[str] = []
    reclaimed: list[str] = []
    errors: list[str] = []

    deploy_home = Path(os.path.abspath(str(deploy_home)))  # lexical, never follow symlinks — see _normalise
    old_list = _read_list(inventory)
    new_list = _read_list(deployed)
    if new_list is None:
        messages.append(f"new deploy list {deployed} missing - refusing to touch the inventory")
        return 4, messages, reclaimed, errors

    if old_list is None or not old_list:
        messages.append(
            "no previous deploy inventory (first run after this shipped) - "
            "nothing is provably ours to reclaim, so NOTHING is touched"
        )
        orphans: set[str] = set()
    else:
        orphans = _normalise(old_list, deploy_home) - _normalise(new_list, deploy_home)

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for orphan in sorted(orphans):
        src = Path(orphan)
        if src.name.startswith(PRESERVE_PREFIX):
            messages.append(f"preserved (local-*): {src}")
            continue
        if not src.exists():
            messages.append(f"already gone: {src}")
            continue
        if src.is_symlink():
            messages.append(f"skipped symlink (target is not ours to move): {src}")
            continue
        rel = src.relative_to(deploy_home)
        dest = quarantine_dir / stamp / rel
        if dry_run:
            messages.append(f"WOULD QUARANTINE {src} -> {dest}")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        except OSError as exc:
            errors.append(f"FAILED to quarantine {src}: {exc}")
            continue
        reclaimed.append(str(rel))
        messages.append(f"QUARANTINED {src}")
        messages.append(f"  restore: mv '{dest}' '{src}'")

    # Commit the new inventory only when the reclaim actually succeeded; a
    # partial failure must not advance our notion of what we own.
    if not errors and not dry_run:
        try:
            if inventory.exists():
                shutil.copy2(inventory, inventory.with_suffix(inventory.suffix + ".prev"))
            tmp = inventory.with_suffix(inventory.suffix + ".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text("".join(f"{p}\n" for p in sorted(_normalise(new_list, deploy_home))))
            os.replace(tmp, inventory)
            messages.append(f"inventory updated: {len(_normalise(new_list, deploy_home))} owned path(s)")
        except OSError as exc:
            errors.append(f"FAILED to update inventory {inventory}: {exc}")

    if errors:
        return 3, messages, reclaimed, errors
    return 0, messages, reclaimed, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--inventory", required=True,
                    help="the deploy's ownership inventory (read as OLD, then replaced)")
    ap.add_argument("--deployed", required=True,
                    help="freshly computed list of paths the deploy just wrote")
    ap.add_argument("--deploy-home", required=True,
                    help="the only tree we have authority over")
    ap.add_argument("--quarantine-dir", default=None,
                    help="where orphans are moved (default: <deploy-home>/state/deploy-quarantine)")
    ap.add_argument("--dry-run", action="store_true", help="report orphans, move nothing")
    args = ap.parse_args(argv)

    deploy_home = Path(args.deploy_home)
    if not deploy_home.is_dir():
        print(f"ERROR: deploy home {deploy_home} is not a directory", file=sys.stderr)
        return 4
    quarantine_dir = (Path(args.quarantine_dir) if args.quarantine_dir
                      else deploy_home / "state" / "deploy-quarantine")

    code, messages, reclaimed, errors = reclaim(
        Path(args.inventory), Path(args.deployed), deploy_home, quarantine_dir,
        dry_run=args.dry_run,
    )
    for m in messages:
        print(m)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if reclaimed:
        print(f"reclaimed {len(reclaimed)} orphan(s) into {quarantine_dir} (restorable, not deleted)")
    return code


if __name__ == "__main__":
    sys.exit(main())
