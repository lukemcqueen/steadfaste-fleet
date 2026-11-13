# Optional add-on: a host-local DeepSeek review MCP server (Pi)

**Audience: a Pi agent (or any MCP client) that sees a `deepseek` entry in
`~/.pi/agent/mcp.json` and wonders whether it is a fleet server.** It is not.

Kept in its own file on purpose: `../pi/IMPLEMENT.md` is the fleet wiring guide, and
this is the opposite thing — a server the fleet neither installs nor audits, which an
agent must be able to recognise as *not ours* rather than try to "repair" with
`install-pi-mcp.sh`. Related: [`../../../docs/external/external-services.md`](../../../docs/external/external-services.md).

---

## What it is

| | |
|---|---|
| **Tools** | `review`, `ask_deepseek`, `list_available_models` |
| **What it does** | asks DeepSeek to attack a plan before it is built (adversarial review), over OpenRouter |
| **Where its source lives** | the fleet OWNS a copy: `mcp-servers/deepseek-review-mcp.py` (deployed to `~/.steadfaste-fleet/scripts/deepseek-review-mcp.py`). The same server also lives in a product repo: `~/Developer/AI/steadfaste-s1-model/scripts/util/mcp_deepseek_server.py` — the pi registration may point at either copy. |
| **Credential** | `OPENROUTER_API_KEY` — from the env, or a `./.env` beside the server |
| **Registered in** | `~/.pi/agent/mcp.json`, one entry named `deepseek`, next to the five fleet entries |
| **Default model** | `deepseek/deepseek-v4-pro-0813` (OpenRouter) — the cost-controlled canonical reviewer; never plain `v4-pro` or `chat-v3` |

The review tool is named **`review`**. It was `review_experimental_plan` — narrower than what
it does, since it reviews a memo, a plan, or a results summary. The rename is **automated**, not
a hand procedure: `fleet-doctor.py` reports `DeepSeek MCP tool name`, its auto-fix runs the
manager below, and an agent can run the same two commands directly:

```bash
bash ~/steadfaste-fleet/ops/scripts/install/install-pi-mcp.sh --check-tools   # exit 1 if stale
bash ~/steadfaste-fleet/ops/scripts/install/install-pi-mcp.sh --fix-tools     # rename it
```

That is `install-pi-mcp.sh` — the one file that owns `~/.pi/agent/mcp.json`. It resolves the
server from that config, backs the file up first, and treats an ABSENT server as a PASS: the
entry is host-optional, so a host without it is not a fault. Only the tool NAME is changed —
the foreign entry itself is preserved, exactly as `--remove` preserves it.
See [`../../../docs/external/mcp-deepseek-review.md`](../../../docs/external/mcp-deepseek-review.md).

**NOT part of the fleet suite, and NOT installed by `install-pi-mcp.sh`.** Some hosts
register it by hand; others do not. Neither state is a fault.

## Registration shape

The same file the fleet servers use (Pi reads MCP servers from its own config):

```json
{
  "mcpServers": {
    "deepseek": {
      "command": "<an interpreter that can `import mcp`>",
      "args": ["~/Developer/AI/steadfaste-s1-model/scripts/util/mcp_deepseek_server.py"],
      "timeout": 300,
      "description": "DeepSeek adversarial review via OpenRouter",
      "exposure": "direct"
    }
  }
}
```

**The interpreter is the failure mode, not the entry.** A config naming an interpreter
without the `mcp` package starts the server DEAD while the file still looks complete —
the same trap `install-pi-mcp.sh` guards against. Resolve it by CAPABILITY, never by a
path you happen to have:

```bash
bash ~/steadfaste-fleet/ops/scripts/lib/python-with-module.sh --print mcp
# exit 3 = no interpreter on this host can import it; install it and re-run
```

**Migration signature (hermes-cortex → steadfaste-fleet).** (STALE-path
teaching — the old paths below are the failure signature to recognize.) After the home-dir
migration, an entry that still names the OLD interpreter fails TLS before any tool
runs, because `certifi`'s CA bundle now lives only in the NEW venv, not the
emptied `~/.hermes-cortex/venv` (STALE pre-migration path — kept as the failure
signature this section teaches you to recognize):

> Could not find a suitable TLS CA certificate bundle, invalid path:
> ~/.hermes-cortex/venv/lib/python3.12/site-packages/certifi/cacert.pem
> (STALE pre-migration path — the exact failure line to recognize)

certifi is the failure, not just the `mcp` package, so resolve by capability AND
confirm the bundle — exit 3 from the resolver, or a missing `certifi`, means keep
looking; an interpreter that imports `mcp` alone can still die on TLS:

```bash
PY=$(bash ~/steadfaste-fleet/ops/scripts/lib/python-with-module.sh --print mcp)
"$PY" -c "import os, mcp, certifi; p=certifi.where(); print(p); assert os.path.exists(p), 'cacert.pem missing'"   # must print a real cacert.pem
"$PY" -m pip install certifi || { echo "certifi install failed"; exit 1; }   # fail loud — never 2>/dev/null || true
# then set that "$PY" (absolute) as the deepseek entry's "command" and restart Pi:
pi mcp list | grep deepseek     # connected, with its 3 tools named `review`
```

A stale `command` of `~/.hermes-cortex/venv/bin/python3` (STALE pre-migration
path) is the migration's blast radius, not this addon's fault — see
`docs/runbooks/fleet-local-dir-rename.md`.

## Verify, then leave it alone

```bash
pi mcp list | grep deepseek      # expected: connected, with its 3 tools, named `review`
```

- `install-pi-mcp.sh --remove` strips the **fleet** servers only: run against a
  scratch config holding a `deepseek` entry, the surviving set was
  `['deepseek', 'user-owned']` (measured 2026-10-12, rc=0). A host keeps it across
  re-runs of the installer.
- `audit-pi-integration.sh` requires the five **fleet** servers by name and applies
  its `AGENT_NAME` identity rule **only to those**. A host-local entry like this one is
  reported as *present, NOT audited here*; its command and script are still checked
  (a broken path is a real defect). So a host WITHOUT it is a PASS, and a host WITH it
  is a PASS too.
- **Renaming its tools is the one thing the fleet does touch**, and only through the
  automated script above — a stale tool name is detected by the doctor rather than
  re-derived by each agent.
- **The fleet copy is source-of-record (operator ruling 2026-11-11).** `mcp-servers/deepseek-review-mcp.py`
  is the fleet-owned server, registered in `fleet-update.sh` so every host gets it deployed.
  The product repo may keep its own copy — the pi `deepseek` entry may point at either — but
  this repo no longer depends on a product checkout existing.
- **One model rule for every copy: `deepseek-v4-pro-0813` default.** Plain `v4-pro` and
  `chat-v3` are banned as defaults fleet-wide (cost ruling 2026-11-11).

Evidence for both claims: `docs/evidence/pi-audit-foreign-server-2026-10-12.txt`,
whose commands (`tests/test_pi_integration_foreign_server.py`, the installer scratch
run, the A4 gates) are re-runnable as shown.
