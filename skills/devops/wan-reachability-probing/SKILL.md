---
name: wan-reachability-probing
description: Test port reachability from internet via external probes.
version: 0.1.0
author: Steadfaste Fleet
license: MIT
platforms: [linux, macos]
---

# WAN Reachability Probing

Verify whether a port/service is actually reachable from the internet when your test host sits inside the same LAN as the target — or when a peer reports you unreachable but local checks look fine.

## When to Use

- "Can't reach the router admin / service from outside the LAN"
- A peer agent reports you unreachable, but the local process and nginx look healthy
- After changing port forwards, DDNS records, or router remote-access settings
- Any "is this port open from the internet?" question where your test vantage is behind the same NAT as the target

## The Hairpin NAT Trap (read first)

Curling your own public IP or DDNS from inside the LAN is **inconclusive**: most routers don't support NAT hairpin, so even a perfectly OPEN port returns HTTP 000/timeout from inside. A 000 from a hairpin test is NOT evidence of a closed port. Verified 2026-08-30: `curl https://<PUBLIC_IP>:8443/` from inside → 000, while external nodes reported 443/14007/11022 OPEN at the same moment.

## Procedure

### 1. LAN liveness (fast sanity)
Ping the target, then curl the LAN endpoint. `401`/`200` means the service is up locally; a LAN-side `000` IS meaningful (service/router down).

### 2. DDNS / public-IP currency
`dig +short <ddns-domain> A` must equal `curl -s https://api.ipify.org`. A stale A record is a top "can't connect" cause even when everything else is healthy.

### 3. External probe — check-host.net free API (no key, scriptable)
yougetsignal-style web port checkers are Cloudflare-blocked (301). Use:

```bash
req=$(curl -s -H "Accept: application/json" "https://check-host.net/check-tcp?host=<PUBLIC_IP>:<PORT>&max_nodes=3" | python3 -c "import sys,json;print(json.load(sys.stdin)['request_id'])")
sleep 6   # nodes report asynchronously
curl -s -H "Accept: application/json" "https://check-host.net/check-result/$req"
```

Result shape per node:
- `[{"address": ..., "time": 0.3}]` = **OPEN**
- `[{"error": "Connection timed out"}]` = **CLOSED**

Poll multiple nodes; majority rules.

### 4. Interpret the matrix

| Probe pattern | Meaning |
|---|---|
| ALL probed ports CLOSED | WAN-level problem: ISP block, router firewall, or remote admin disabled |
| Some OPEN, some CLOSED | CLOSED ones are target-side (unforwarded, service down, host firewall); the router WAN path works |
| One port CLOSED, siblings OPEN | That specific host/service, not the router (2026-08-30: WAN:10022 peer-SSH down while 11022 SSH open) |
| Forwards OPEN but router-admin ports (8443/8080/444/81) CLOSED | FreshTomato remote admin disabled — enable *Administration → Access → Remote Access*, then reach at `https://<ddns>:8443` |

## When a GATE reports your own service unreachable (doctor / push)

A health check that probes your OWN public URL from the host (the fleet doctor's
external-service rows are exactly this) inherits the hairpin trap completely, so its verdict is a
HYPOTHESIS until an external node answers. Two measurements, taken together, settle it:

1. **Local health is not reachability.** `curl -sk https://127.0.0.1:<port>/` returning the status
the check expects (e.g. a `401` from an auth-gated vhost) proves nginx and the upstream are fine and
proves nothing about the public path. A TLS vhost answers plain `http://` on the same port with
`400` — read the scheme, not just the failure.
2. **Probe the suspects AND a known-good sibling in the SAME pass.** Siblings OPEN (443/22) while
every service port is CLOSED is a real closure at the forward/WAN layer, not noise from your test
vantage: the vantage reaches the internet fine, so a dropped port is dropped for everyone. Only when
the siblings ALSO fail is the router/WAN path itself in question.

Rules that follow:

- **Never make such a FAIL green by pointing the check at a local base** (a localhost/`127.0.0.1`
  override of the external base, a dispositions entry, a re-scoped check) — that converts a public
  reachability outage into a passing local-health check, which is the masking this skill exists to
  prevent. Report the outage with the external evidence instead. Dispositions cover warn/info
  findings only — a FAIL has no disposition path, by design.
- **Classify a blocked push by FAIL KIND before touching the change.** A push gate that counts ANY
  doctor FAIL means one unrelated host-service outage blocks every push on that host, including
  pushes whose diff has nothing to do with networking. Separate deploy-drift FAILs (deploy sync,
  checksums — yours, fix them) from unrelated host-service FAILs (not yours) and report the second
  as its own blocker with the external evidence.
- **A service-port migration has THREE layers that must move together**: the host env's public
  URL(s), the server's actual `listen` directives, and the router's forwards. Compare them explicitly
  — a stale public-URL port value in the host env is how a check ends up probing a port the host no
  longer serves, and it silently breaks any documented failover URL that points at the same literal.

## Pitfalls

- Never conclude "not externally accessible" from a hairpin 000 — get an external node's result first.
- Never conclude an outage from a local check's failure either: an unreachable-looking service may
  be a hairpin artifact (open) or a mismatched port prefix (probing a port nobody serves), not a
  closed port. The external node is the only verdict, and it needs a sibling port beside it.
- check-host.net nodes answer asynchronously: `sleep ~6` before polling the result.
- Empty result (no `request_id`) = API rate-limited or transient — retry once.
- Probe the suspect port AND a known-good sibling port in the same pass; the contrast splits "router problem" vs "target problem" in one round.

## Verification

A probe pass is verified when ≥1 external node returns a structured result (OPEN or timed-out) — an empty/no-result JSON proves nothing.

## Related

- `health-external-verification` (user-owned): health-endpoint-specific external testing — overlaps with this skill; curator should consolidate.
- `fresh-tomato-router` (user-owned): router nvram, port-forward rules, remote-admin enable — its Verification section has the same hairpin trap.

Worked example: `references/wan-reachability-probe-example.md`.
