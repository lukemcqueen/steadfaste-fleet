"""fleet_lib.identity — ONE definition of fleet host identity (the authority primitive).

Orchestrator status is HOST-DERIVED and never env. The env vars AGENT_TYPE /
IS_ORCHESTRATOR grant NO orchestrator powers — they are spoofable (see the
fleet-update.sh `is_orch_host()` body, which refuses them). A host is an
orchestrator iff its short hostname is moses|esther AND its real home is
/home/<host>. This rule gates the MCP surface, cron manifest scopes, the
doctor's task-probe visibility and deploy behavior — so it lives exactly once,
here, and every python consumer imports it instead of re-deriving it.

Bash consumers (fleet-update.sh is_orch_host, mcp-surface.inc.sh is_orchestrator)
keep their own identical bodies ON PURPOSE: they are already the correct
host-derived rule, and shelling out to this module from the deploy hot path
(where it may be called many times for env forensics and mode-decision passes)
is a net loss versus a ~8-line native bash case. The python copies below were
the divergent, env-first ones; they now read here. If you change the rule,
change it here AND keep the two bash bodies the same shape.

MacOS safety: `getent` and `pwd` may be absent/limited on a Darwin host. The
basename rule (moses|esther) is checked first, so a non-orchestrator Darwin
host (titus) short-circuits before any getent/pwd work; for a match we fall
back to $HOME. A Darwin host is not an orchestrator family member either way.
"""

from __future__ import annotations

import os
import subprocess

# The only orchestrator family members. Keep in sync with every bash
# `is_orch_host` / `is_orchestrator` body (see module docstring).
_ORCH_HOSTS = ("moses", "esther")


def _short_hostname() -> str:
    try:
        proc = subprocess.run(
            ["hostname", "-s"], capture_output=True, text=True, timeout=5
        )
        out = (proc.stdout or "").strip()
        if out:
            return out.lower()
    except Exception:
        pass
    # fall back to uname nodename (BSD/Darwin-safe: split off any domain)
    raw = os.uname().nodename.split(".", 1)[0].strip().lower()
    return raw or "unknown"


def _real_home() -> str:
    """The uid's passwd home; falls back to $HOME when pwd is absent.

    Home is derived from the OS uid (os.getuid + pwd.getpwuid), NOT from the
    USER/LOGNAME env vars — those are attacker-influenceable in a worker
    context, whereas the authority must come from the machine's own identity
    (mirrors the bash `id -un` body it replaces).
    """
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        return os.path.expanduser("~")


def is_orchestrator() -> bool:
    """Host-derived orchestrator status. NEVER trusts env. See module docstring."""
    host = _short_hostname()
    if host not in _ORCH_HOSTS:
        return False
    return _real_home() == f"/home/{host}"


def agent_role() -> str:
    """'orchestrator' iff is_orchestrator(), else 'server'.

    The doctor's consumers only ever test `== 'orchestrator'`; 'server' is the
    single non-orchestrator role. There is deliberately no env-driven 'dev'
    downgrade here — AGENT_TYPE must never be able to alter an authority
    verdict, and nothing downstream distinguishes 'server' from 'dev'.
    """
    return "orchestrator" if is_orchestrator() else "server"
