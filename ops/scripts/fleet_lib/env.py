"""Dual-read env resolution for the CORTEX_* -> FLEET_* migration (plan P4).

Canonical env var names are now FLEET_*. During the migration a host may still set
the legacy CORTEX_* name in ~/steadfaste-fleet/.env / agent.env / systemd
Environment=. Reading through `fleet_env.get()` resolves FLEET_<name> first, then
falls back to CORTEX_<name> with a LOUD one-time deprecation warning - never a
silent fallback, because a silent fallback is exactly how a half-migrated host
hides. This makes renaming every repo literal to FLEET_* safe for hosts that have
not repointed their env yet.

Resolution order (per name): FLEET_<name>, then CORTEX_<name>, then default.
If both are set, FLEET_ wins (it is the canonical spelling).

When the migration is complete (release 2), the CORTEX_ fallback and this module
are removed, and every consumer reads os.environ directly again.

Shell twin: `lib/fleet-env-compat.sh` mirrors CORTEX_* -> FLEET_* at env load so
shell code and Python agree on the canonical FLEET_ name.
"""

import os
import sys

_warned = set()
_DEPRECATION_LOGGING = True


def set_deprecation_logging(on: bool) -> None:
    """Tests silence the spam; production keeps it ON (loud-once per name)."""
    global _DEPRECATION_LOGGING
    _DEPRECATION_LOGGING = bool(on)


def get(name: str, default=None):
    """Resolve FLEET_<name> then CORTEX_<name> then default, loud on legacy."""
    fleet = os.environ.get("FLEET_" + name)
    if fleet is not None:
        return fleet
    cortex = os.environ.get("CORTEX_" + name)
    if cortex is not None:
        if name not in _warned and _DEPRECATION_LOGGING:
            _warned.add(name)
            print(f"[fleet-env] DEPRECATED: CORTEX_{name} set; rename to FLEET_{name}",
                  file=sys.stderr)
        return cortex
    return default


def get_bool(name: str, default: bool = False) -> bool:
    """env-style boolean: truthy strings ('1','true','yes','on') are True."""
    raw = get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
