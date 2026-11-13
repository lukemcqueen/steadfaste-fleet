"""Canonical FLEET_* env resolution (release 2 - CORTEX_* fallback removed).

Canonical env var names are FLEET_*. Release 2 dropped the legacy CORTEX_*
spelling: reading through `fleet_env.get()` resolves FLEET_<name> then the
default. Callers that must read a name the external Hermes harness still sets as
CORTEX_SESSION_* read os.environ directly (the sweep kept those literal).

Migration history: the CORTEX_* -> FLEET_* migration (plan P4) used a dual-read
fallback (FLEET first, then CORTEX with a loud deprecation warning) via this
module and its shell twin `lib/fleet-env-compat.sh`. Both fallback layers were
removed in release 2 once every consumer read FLEET_* and hosts' .env carried
FLEET_* (consolidate-env.sh converts the .env to FLEET-only).
"""

import os


def get(name: str, default=None):
    """Resolve FLEET_<name>, else default. No legacy CORTEX_ fallback."""
    return os.environ.get("FLEET_" + name, default)


def get_bool(name: str, default: bool = False) -> bool:
    """env-style boolean: truthy strings ('1','true','yes','on') are True."""
    raw = get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
