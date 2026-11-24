#!/usr/bin/env python3
"""Doctor check: WARN if any pre-migration home folder remains after the rename.

The hermes-cortex -> steadfaste-fleet migration
(docs/runbooks/fleet-local-dir-rename.md, migrate-home-dirs.sh) is only
proof-complete when the OLD home folders are gone. A host still carrying one
of the STALE pre-migration paths below (~/hermes-cortex, ~/.hermes-cortex, or
the empty pip leftover ~/.local/lib/hermes-cortex) is not fully migrated, and a
leftover transition symlink keeps old-path references resolving. The check
names the folders and points at the runbook so the operator completes the
migration — deleting the folder alone is not the migration.

The path literals below are STALE by design: they are the pre-migration names
this check exists to detect. Do not rename them to steadfaste-fleet — the check
would then look for the CURRENT home dir and always WARN.

A leaf module (like mcp_names.py / router_layer.py) so it is independently testable:
no relative package imports. Depends only on the Results `.add` interface.
"""

from pathlib import Path

# Module-level so tests can redirect it:
#   monkeypatch.setattr(mod, "STALE_PREMIGRATION_PATHS", [tmp / ...])
# STALE pre-migration home dirs (hermes-cortex era) — the check exists to detect them.
STALE_PREMIGRATION_PATHS = [
    Path.home() / "hermes-cortex",
    Path.home() / ".hermes-cortex",
    Path.home() / ".local" / "lib" / "hermes-cortex",
]

CHECK_NAME = "Stale pre-migration home folders"
RUNBOOK = "docs/runbooks/fleet-local-dir-rename.md"


def check_stale_premigration_home(res) -> None:
    """WARN if any pre-migration home folder remains, pointing at the runbook."""
    present = [str(p) for p in STALE_PREMIGRATION_PATHS if p.exists() or p.is_symlink()]
    if not present:
        res.add(CHECK_NAME, "PASS",
                "pre-migration home folders absent — host fully migrated")
        return
    # SAFETY — the advice this check prints must not be unconditionally destructive.
    # MEASURED: on this host `.hermes-cortex/victoria-data` is the RUNNING
    # VictoriaMetrics time-series DB, bind-mounted RW into the `victoria-metrics`
    # container. The bare `rm -rf` this check used to print would WIPE the metrics DB,
    # because the check's own premise ("stale folder = dead weight") is false for a live
    # bind-mount source. A doctor that tells an operator to destroy data is worse than no
    # doctor: it launders the destructive step as maintenance. Name the precondition and
    # the purpose-built tool instead.
    #   migrate-victoria-data.sh — stops+renames the container (rollback artefact), MOVES
    #   the data atomically (never copy-then-delete), verifies against markers measured
    #   from real startups, and only then removes the stale dir.
    #   docs/runbooks/live-bindmount-stale-dir.md — the runbook for this exact trap.
    res.add(
        CHECK_NAME, "WARN",
        "pre-migration home folder(s) remain (STALE paths): " + ", ".join(present),
        "Complete the migration, then remove them as the completion proof. CHECK FIRST "
        "whether any path is a live bind-mount source: a naive `rm -rf` on one DESTROYS "
        "live data (on a Docker host, a stale `.hermes-cortex` can hold the running "
        "VictoriaMetrics TSDB at `victoria-data/`). If it is a live mount source, use "
        "ops/scripts/manage/migrate-victoria-data.sh (see "
        "docs/runbooks/live-bindmount-stale-dir.md). If it is confirmed dead weight, read "
        f"{RUNBOOK} and run migrate-home-dirs.sh, then remove it.",
    )
