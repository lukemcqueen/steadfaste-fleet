"""fleet_lib/envfile.py — canonical .env / key=value resolver (ONE ladder, ONE quoting rule).

The fleet resolves credentials and endpoints from a cascade of env FILES, not
just the process environment — a long-lived gateway / gate / memory service
does not necessarily inherit ~/steadfaste-fleet/.env, so every component reads
it from disk. Historically each component hand-rolled its own "read line,
split on =, first match wins" parser and its own path ladder, and they drifted:

  - judgment.py / loop-gov-mcp.py  : 4-path ladder honouring FLEET_ENV_FILE →
    FLEET_REPO → FLEET_DEPLOY_HOME → ~/.hermes/.env  (env-override capable)
  - mycortex-mem (plugin + store)  : 2-path ladder, fixed (~/steadfaste-fleet
    then ~/.steadfaste-fleet) — no FLEET_ENV_FILE / FLEET_REPO override
  - fleet_bus.py                   : ~/steadfaste-fleet/.env only
  - quoting                        : some strip('"') then strip("'"), some only
    one, some none; some ignore comment lines, some don't

The consequence: the SAME key placed in the deploy-home .env resolves for one
service and not another (e.g. MYFLEET_MEM_PASSWORD visible to the gate but
invisible to the memory provider), and any quoting or ladder fix must be
re-applied across ~18 files.

This module is the single source of truth for BOTH the ladder and the quoting
rule. Use `env_value()` everywhere; port each legacy parser onto it one call
site at a time. `env_file_paths()` is exported for callers that need the ladder
itself (e.g. to report where a value would come from).

Rule set (tested in tests/test_fleet_lib_envfile.py):
  - LADDER (first file that contains the key wins; within a file, first match):
      1. FLEET_ENV_FILE (explicit path, if set)
      2. <FLEET_REPO or ~/steadfaste-fleet>/.env
      3. <FLEET_DEPLOY_HOME or ~/.steadfaste-fleet>/.env
      4. ~/.hermes/.env   (legacy last resort)
  - VALUE: everything right of the FIRST '='; strip surrounding whitespace;
    then strip an optional surrounding '"' or "'" pair. Bare (unquoted) values
    pass through unchanged.
  - PROCESS ENV WINS: env_value returns the process environment (or an
    injected `env=` dict) first when the key is present and non-empty — the
    documented "process env first, then the fleet env, Hermes last" contract
    every credential consumer relied on.
  - FAILURE: a missing or unreadable file is skipped (OSError swallowed per
    file, matching the historic parsers); a key found with an empty value is
    returned as ''.
"""
from __future__ import annotations

import os
from pathlib import Path


def env_file_paths(env: dict | None = None) -> list:
    """The canonical credential-file ladder, first-resolved-wins order.

    Honors FLEET_ENV_FILE / FLEET_REPO / FLEET_DEPLOY_HOME overrides from `env`
    (an injected environment dict) or the real process environment, then the
    legacy ~/.hermes/.env last resort.
    """
    env = env if env is not None else os.environ
    paths = []
    explicit = (env.get("FLEET_ENV_FILE") or "").strip()
    if explicit:
        paths.append(Path(explicit))
    repo = (env.get("FLEET_REPO") or "").strip()
    paths.append((Path(repo) if repo else Path.home() / "steadfaste-fleet") / ".env")
    deploy = (env.get("FLEET_DEPLOY_HOME") or "").strip()
    paths.append((Path(deploy) if deploy else Path.home() / ".steadfaste-fleet") / ".env")
    paths.append(Path.home() / ".hermes" / ".env")   # legacy last resort
    return paths


def _unquote(value: str) -> str:
    """One quoting rule: strip surrounding whitespace, then one optional
    surrounding '"' or "'" pair. Bare values pass through unchanged."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
        return value[1:-1]
    return value


def env_file_value(name: str, env: dict | None = None, paths: list | None = None) -> str:
    """Resolve `name` from the canonical env-file ladder (not the process env).

    Pass `paths` to scan an explicit list instead (used by tests and by callers
    with a custom ladder). Returns '' when absent or empty.
    """
    if not name:
        return ""
    for path in paths if paths is not None else env_file_paths(env):
        try:
            if not path.is_file():
                continue
            for line in path.read_text(errors="ignore").splitlines():
                if line.startswith(f"{name}="):
                    return _unquote(line.split("=", 1)[1])
        except OSError:
            continue
    return ""


def env_value(name: str, env: dict | None = None, paths: list | None = None) -> str:
    """Resolve `name`: process env first (or injected `env=`), then the files.

    This is the canonical entry point every component should use — it encodes
    the documented "process env first, then the fleet env, Hermes last"
    contract plus ONE ladder and ONE quoting rule.
    """
    source = env if env is not None else os.environ
    direct = source.get(name, "").strip()
    if direct:
        return direct
    return env_file_value(name, env=source, paths=paths)