"""Canonical FLEET_* env resolution (release 2 — CORTEX_* spelling retired).

Canonical env var names are FLEET_*. Reading through `fleet_env.get()`
resolves FLEET_<name> then the default. There is NO legacy fallback: the
CORTEX_* spelling is fully retired, and any reader that still needs a
legacy name is a regression to fix.

Migration history: the CORTEX_* -> FLEET_* migration (plan P4) used a dual-read
fallback (FLEET first, then CORTEX with a loud deprecation warning) via this
module. The fallback layer was removed in release 2 once every consumer read
FLEET_* and hosts' .env carried FLEET_* (consolidate-env.sh converts the .env
to FLEET-only).
"""

import os


def get(name: str, default=None):
    """Resolve FLEET_<name>, else default. No legacy fallback."""
    return os.environ.get("FLEET_" + name, default)


def get_bool(name: str, default: bool = False) -> bool:
    """env-style boolean: truthy strings ('1','true','yes','on') are True."""
    raw = get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
