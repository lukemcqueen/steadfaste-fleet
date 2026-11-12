#!/usr/bin/env python3
"""Doctor check: WARN if any old hermes-cortex home folder remains after the rename.

The hermes-cortex -> steadfaste-fleet migration
(docs/runbooks/fleet-local-dir-rename.md, migrate-home-dirs.sh) is only
proof-complete when the OLD home folders are gone. A host still carrying
~/hermes-cortex, ~/.hermes-cortex, or the empty pip leftover
~/.local/lib/hermes-cortex is not fully migrated, and a leftover transition
symlink keeps old-path references resolving. The check names the folders and
points at the runbook so the operator completes the migration — deleting the
folder alone is not the migration.

A leaf module (like mcp_names.py / jev.py) so it is independently testable:
no relative package imports. Depends only on the Results `.add` interface.
"""

from pathlib import Path

# Module-level so tests can redirect it:
#   monkeypatch.setattr(mod, "STALE_HERMES_CORTEX_PATHS", [tmp / ...])
STALE_HERMES_CORTEX_PATHS = [
    Path.home() / "hermes-cortex",
    Path.home() / ".hermes-cortex",
    Path.home() / ".local" / "lib" / "hermes-cortex",
]

CHECK_NAME = "Stale hermes-cortex folders"
RUNBOOK = "docs/runbooks/fleet-local-dir-rename.md"


def check_stale_hermes_cortex_home(res) -> None:
    """WARN if any old hermes-cortex home folder remains, pointing at the runbook."""
    present = [str(p) for p in STALE_HERMES_CORTEX_PATHS if p.exists() or p.is_symlink()]
    if not present:
        res.add(CHECK_NAME, "PASS",
                "old hermes-cortex home folders absent — host fully migrated")
        return
    res.add(
        CHECK_NAME, "WARN",
        "old hermes-cortex home folder(s) remain: " + ", ".join(present),
        "Complete the migration, then remove them as the completion proof: read "
        f"{RUNBOOK} and run migrate-home-dirs.sh; then `rm -rf "
        + " ".join(present) + "`",
    )