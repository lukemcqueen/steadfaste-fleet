# Recurring doctor WARN/FAIL remedies (triage after deploy/update)

Class: triaging `fleet-doctor.py` WARN/INFO/FAIL checks (step 5 of the proper
deploy sequence). For an undisposed item you are NOT fixing now, record the
decision: `fleet-doctor.py --disposition "<exact check name from the · line>"
--as deferred --owner <you> --reason "<why>"`. Copy the check name verbatim — a
paraphrase is refused (`no check named '<X>'`). Dispositions EXPIRE (30 days).

## loop-gov daemon (⚠ "COULD NOT VERIFY by content" / "running stale code")

The check content-verifies every running `loop-gov-mcp.py` MCP daemon against the
file on disk, via a per-pid startup stamp. Two states:

- **"COULD NOT VERIFY by content — pid N … wrote no startup stamp"**: the daemon
  wrote no verifiable stamp. Root cause (fixed durably): `_write_daemon_stamp`
  wrote ONE `{pid, sha256}` object, so the later of two session-spawned daemons
  (a `--role loop-governance` + a `--role review`) overwrote the earlier and the
  sibling could never be content-verified. The durable fix is a per-pid stamp
  MAP — merge on write, migrate a legacy single-pid object on read — so every
  daemon is stamped and verified.
- **"running stale code — the module LOADED differs"**: the daemon genuinely
  loaded a pre-deploy module -> content mismatch, which is correct, not a false
  positive.

Both states clear only when the daemons RESPAWN — a fresh session or
`hermes gateway restart` (host-operator action; agents are blocked by the
lifecycle guard). Not agent-fixable in the current session: record deferred with
that reason, and do NOT chase a restart loop — it self-heals on the next session
or after the operator restarts.

Corollary: deploying `loop-gov-mcp.py` makes the RUNNING daemon stale until it
respawns, and a close can answer a cycle as PENDING right after it was scored for
the same reason — a retry after a `cycle_query` is the workaround, not the fix.

## Jev decision layer (⚠/FAIL — a pool leads dearer than the running model)

The live `$HERMES_HOME/jev/routing.json` is a PER-HOST runtime artifact that
goes stale vs the committed policy (`config/jev-router-models.json`). The doctor
FAILs (WARNs while routing is off) when a simple/medium pool leads with a model
>1.5x dearer than the host's current model — first-fit wins, so it is real money.

Fix: rebuild from the committed policy `python3
ops/scripts/manage/router-pools.py --write`, then verify the bare run (==
`--check`) prints "no findings" and the doctor reads Jev PASS.

A peer re-routing the config on THEIR host does NOT update YOUR routing.json —
re-apply `--write` after any config change a pull brings in, and re-check.
Do not hand-edit routing.json; the tool exists to prevent pool drift.

A FAIL naming a route-unreachable head (a model 404s on this key) is a
REACHABILITY problem the policy owns (promote a head only after re-probing),
not a routing.json hand-edit.

## Skill drift / parity (⚠)

`skills/devops/*` drift, and the `docs/evidence/skill-drift-parity.txt` capture
going stale, are NOT warnings to dispose — the deployed copy carries the newer
lesson. Reconcile the deployed side back to the repo (pure supersets; hand-merge
by PHRASE, never by line), regenerate the parity capture fresh, commit. See
fleet-deployment-sync's "stranded deployed-only skills" section and
`shared-repo-push-gates` for the full class.

## `Service (...)` warns right after a deploy are usually TRANSIENT

A `fleet-update.sh` run's embedded doctor can print `⚠ Service (Dashboard)` /
`Service (Langfuse)` / `Service (Agent Bus)` simply because those services are
momentarily down while the deploy restarts them. Do NOT triage or disposition them
off the deploy snapshot: run a FRESH `fleet-doctor.py` and they normally read PASS
again. Only a Service warn that survives a fresh run is a real item to fix or dispose.

## `fleet-doctor.py --json` is not parseable as-is — strip the DEBUG prefix

The dump leads with non-JSON lines (`[mcp-server] DEBUG: Initializing server '…'`), so
`json.load` dies with `Expecting value: line 1 column 2`. Parse from the first brace:

```python
raw = open(f).read()
d = json.loads(raw[raw.index('{'):])   # first '{' starts the real payload
```

Step 5 of the sequence tells you to grep this dump for WARN/INFO — grep the whole file
(the DEBUG lines do not match) but parse from that offset. Remember also that the JSON
renders only the LIVE check's disposition, so an already-disposed check reads
`disposition: null` here; the TEXT renderer, which honours
`~/.steadfaste-fleet/state/doctor-dispositions.json`, is authoritative for the
"N undisposed" count.
