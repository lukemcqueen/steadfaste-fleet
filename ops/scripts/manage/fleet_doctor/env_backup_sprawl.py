#!/usr/bin/env python3
"""Doctor check: WARN when .env backups sprawl past the single recovery point.

WHAT THE DEFECT IS. Every ad-hoc edit of the deploy-root `.env` conventionally
leaves a `.env.bak-*` copy behind, and each copy is an independent plaintext
duplicate of the ENTIRE secret set (bus token, PGPASS, basic-auth, container
passwords). Nothing in the fleet collected them, so they accumulated without
bound: six were found on this host, of which four declared no key the live file
did not, and the other two declared only a retired CORTEX_* generation that no
runtime reads. Six extra exposure paths bought zero recoverability.

WHY A DOCTOR CHECK. Retention is a standing rule, and a standing rule that lives
only in a skill note or an agent's diligence is not a control — it has to fire
without anyone's cooperation. This check is that gate: it counts the copies and
dictates the proof required before pruning them.

THE RULE. KEEP_MAX=1 — one recovery point, the newest, which the deploy skill
already names as the repair source for a corrupted .env. One is enough to restore
from; more than one is only more exposure.

The check NEVER prints a value, and never deletes anything itself: it reports
names and hands the operator a remedy that requires proving no unique KEY is lost
first. `.env.example` (tracked template) and `.env.lock` (0-byte lock) are not
copies and are not counted.

A leaf module (like stale_premigration_home.py / mcp_names.py) so it is
independently testable: no relative package imports. Depends only on the Results
`.add` interface.
"""

from pathlib import Path

# Module-level so tests can redirect it:
#   monkeypatch.setattr(mod, "ENV_ROOTS", [tmp / "deploy-root"])
# The two places a fleet .env lives: the repo (canonical) and the deploy dir
# (a sanctioned symlink to it). Only the repo root normally grows backups, but a
# stray copy in the deploy dir is the same defect.
ENV_ROOTS = [
    Path.home() / "steadfaste-fleet",
    Path.home() / ".steadfaste-fleet",
]

# One recovery point. The newest copy is what the deploy runbook tells you to
# restore damaged lines from; a second copy adds exposure and no capability.
KEEP_MAX = 1

# Legitimate `.env.*` names that are NOT backup copies:
#   .env.example — the tracked key-schema template (no secrets by design)
#   .env.lock    — a 0-byte advisory lock
NOT_BACKUPS = {".env.example", ".env.lock"}

CHECK_NAME = "Env backup sprawl"


def backup_candidates(roots=None):
    """Return the `.env.*` backup copies present, newest last. Never reads content."""
    found = []
    for root in (ENV_ROOTS if roots is None else roots):
        root = Path(root)
        if not root.is_dir():
            continue
        for p in sorted(root.glob(".env.*")):
            if p.name in NOT_BACKUPS:
                continue
            # The deploy dir's `.env` is a symlink to the repo's; a symlinked
            # `.env.*` is a reference, not a duplicate-carrying copy.
            if p.is_symlink() or not p.is_file():
                continue
            found.append(p)
    # Newest first: the remedy keeps the head of this list and prunes the tail.
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def check_env_backup_sprawl(res) -> None:
    """WARN when the .env backup copies outnumber the single recovery point."""
    cands = backup_candidates()
    if len(cands) <= KEEP_MAX:
        res.add(CHECK_NAME, "PASS",
                f"{len(cands)} .env backup(s) — at or under the {KEEP_MAX} recovery point")
        return

    names = ", ".join(p.name for p in cands)
    keep, extra = cands[0], cands[1:]
    res.add(
        CHECK_NAME, "WARN",
        f"{len(cands)} .env backup copies — each is a full plaintext duplicate of every "
        f"secret in .env; keep {KEEP_MAX}. Sprawl: {names}",
        "Keep ONE recovery point and delete the rest, but PROVE nothing is lost first "
        "(a bare `rm` is the careless destruction this check exists to prevent): "
        "for each copy list its KEY NAMES only and diff them against the live .env — "
        "`sed -n 's/^\\([A-Za-z0-9_]*\\)=.*/\\1/p' <file> | sort -u` — then confirm any "
        "key unique to a copy has no live reader (`grep -rn <KEY>` across the repo). "
        f"Keep the newest ({keep.name}), record the others' sha256, delete "
        f"{len(extra)}: " + " ".join(p.name for p in extra),
    )
