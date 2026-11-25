---
name: watchdog-flapping-diagnostics
description: "Watchdog flapping: nginx throttle of fleet probes; debounce."
version: 1.0.0
category: devops
author: Steadfaste Fleet
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [watchdog, health, flapping, nginx, failover, diagnostics]
---

# Health / Watchdog Flapping Diagnostics

## When to Use

A health or failover watchdog (e.g. `fleet-bus-failover-watchdog.py` workers, a
fleet-health probe, a push-metrics health check) flips between "unreachable" and
"reachable again" on a repeated, fixed cadence. Examples that fire this skill:
- An alert+recovery pair delivered back-to-back, recurring every :00/:30 of the hour
  or every 5-min tick.
- "Primary bus unreachable" followed minutes later by "routing restored", on a loop.

## First Principle — it is almost never the network

A real outage does NOT self-heal within one watchdog tick. So a periodic
alert-then-recover pattern is NEVER "flaky network" — the probe is seeing a
SERVING-LAYER failure (nginx throttle) that flips between two probes. Investigate
the nginx health endpoint, not the route or the backend service.

## Step 1 — read the serving health-access.log, not the backend

The probe hits the service through nginx. The intermittent status codes are in the
health access log:

```bash
tail -2000 <health-access.log> | awk '{print $9}' | sort | uniq -c | sort -rn
```

Interpret:
- **499** = client closed the connection before nginx replied — the probe's client
  timeout (urllib default 8s) fired while nginx queued the request. With a fast
  backend, 499s are the signature of nginx throttling, not a slow service.
- **503** = nginx rate-limit returned (`limit_req ... nodelay` returns 503 when the
  burst is exceeded; `limit_conn` queues/closes excess).
- Overlay the 499/503 timestamps on the watchdog tick cadence — they line up to the
  second (`10:00:09` nginx 499 ↔ `10:00:12` worker alert).

Confirm it is the fleet's OWN probes being throttled (not an attacker):
```bash
tail -2000 <health-access.log> | grep -E ' (499|503) '          # then group by:
  | awk '{print $NF}'          # User-Agent: Python-urllib/*, hermes-fleet-watchdog, hermes-health-report
  | awk '{print $1}'           # remote addr: the NAT gateway IP dominates
```

Then prove the backend is healthy in parallel: `curl` the upstream directly
(~0.2s = fine). A fast backend + 499s = nginx is the throttle.

## Step 2 — understand the mechanism (why it throttles)

nginx rate/conn limits are keyed per client IP. Fleet agents reach the host through a
NAT gateway, so MANY agents collapse into ONE source IP (the router/gateway LAN
address, or one external ISP IP). When their watchdogs probe in the same
synchronized tick, the aggregate exceeds the health block's `limit_conn
conn_limit N` + `limit_req ... burst=N` → 503/499. The watchdog treats any
non-200/401/403 as **down**, so one throttled probe = false "unreachable".

## Step 3 — fix at the source

- **PREFERRED — allowlist the fleet so it bypasses the limits entirely**, via the
  empty-key trick, instead of relaxing limits for everyone: nginx applies NO rate
  or conn limit when the limit_zone key variable is empty. Key the zones off a
  `geo` map that yields `""` for trusted fleet IPs and `$binary_remote_addr` for
  everyone else. The fleet gets unlimited probes; the public keeps the tight burst
  / conn ceiling as a real DDoS defence. Add a worker IP to the map when it joins
  off-LAN:
  ```nginx
  geo $limit_bypass {
      default          $binary_remote_addr;
      192.168.0.0/16   "";     # LAN fleet (router NAT aggregate)
      <wan-worker-ip>  "";     # external WAN worker
      127.0.0.0/8      "";     # localhost
  }
  limit_req_zone  $limit_bypass zone=general:10m rate=20r/s;
  limit_conn_zone $limit_bypass zone=conn_limit:10m;
  ```
  Only the zone key changes — no server-block edits. Validate it before shipping:
  parse-check with `nginx -t -c <minimal-http-conf> -p <writable-prefix> \`
  `-g 'pid <prefix>/nginx.pid; ...'` (override the root pid/access_paths via -g or
  the parse fails on permissions, not config), and run that throwaway nginx to
  confirm an allowlisted IP returns 200 instead of 503 under a burst. Raising
  burst/conn for everyone is the fallback when allowlisting isn't feasible, but it
  weakens the public ceiling.
- **The live /etc/nginx is commonly root-owned and stale** — a fix in the repo
  template will NOT reach production without a privileged apply. On a host where
  the agent lacks sudo for /etc/nginx, write the EXACT operator command
  (`sudo cp <repo>/ops/install/deploy/nginx/<conf> /etc/nginx/<conf> && sudo
  nginx -t && sudo systemctl reload nginx`) into the report and flag that the
  change will not take effect until that runs. Shipping the fix and declaring it
  done while /etc/nginx still throttles is exactly how this class recurs.
- **"On disk and included" ≠ "loaded" — verify the running nginx actually has the fix before
  concluding anything.** A fix shipped into `/etc/nginx/<conf>` and included from
  `nginx.conf` can still be inactive in the running process. Decide by the worker, not the
  master: `nginx -s reload` keeps the master PID and spawns NEW workers, so compare the
  WORKER's start time against the config file's mtime —
  `ps -o lstart= -p <worker-pid>` vs the config's mtime (`stat -c '%y'` on GNU, `stat -f '%Sm'` on macOS). Worker predates the
  write ⇒ the fix is not loaded and the reload is still required; worker postdates ⇒ it took.
  When you cannot read `/var/log/nginx/<access>.log` to check for residual 499/503 (root-log,
  sudo prompts for a password, no NOPASSWD), the timestamp comparison is the non-sudo way to
  determine load state. Confirm the include chain in passing (`grep include /etc/nginx/nginx.conf`
  names `<conf>`, `ls sites-enabled/` links the server block, `systemctl is-active nginx` is
  active) — those prove it is IN the config, not that it is LOADED.
- **Compare /etc/nginx against the repo nginx template FIRST**
  (`ops/install/deploy/nginx/` for a fleet install). A drifted/stale deploy can
  carry tighter limits than the source, and a routine `fleet-update --force` will
  NOT refresh it unless the nginx deploy step actually runs. Diff deployed vs repo
  before assuming the running limits are intentional.
- This is a crown-jewel security config: edit the repo template, deploy through the
  real nginx deploy path, run `nginx -t`, reload — never hand-edit /etc/nginx.

## Design rule — debounce alert and recovery

A health/failover watchdog's ALERT must require N consecutive failures and its
RECOVERY M consecutive successes. A path that alerts on the 1st failure and
recovers on the 1st success turns any single transient blip — throttle, timeout, or
retry — into an alert+recovery pair (the flapping noise). A debounce makes the
system robust to ANY future transient probe failure, so you fix the class, not just
today's throttle.

The concrete, tested shape (deployed in `orch-fleet-watchdog.py`): a PURE state
function is the testable seam —
`_debounce_emit(hstate, bad, good, emitted, bad_lim, good_lim)` →
`(action|None, bad, good)`, where `hstate` ∈ {up, unhealthy, unreachable}.
The **caller feeds `emitted` back** each invocation (persisting it per subject), so a
down fires ONCE at the threshold and never re-fires while still down, and a
resolution fires ONCE; a single opposite sample RESETS the streak, so a blip below
the threshold never alerts. ALERT only when `bad >= bad_lim` AND `emitted != "down"`;
RESOLUTION only when `good >= good_lim` AND `emitted == "down"`. Persist
`{bad, good, emitted}` per subject beside the existing change-detection state
(add a sibling key, never reshape the fingerprint map other readers rely on) so
counts survive process restarts. Drive the state-machine tests against the pure
function, and add ONE script-level test that runs `main()` with `_fetch` stubbed and
state redirected to tmp to prove the silent-when-stable contract (empty stdout) is
preserved — the pure tests alone cannot see that.

## Distinguish a self-healing single-bit flap from a real outage

A down that returns to green within one watchdog tick, while the host's vector is
otherwise all-green, is a TRANSIENT health-bit flip, not a service down. Read which
vector index reddened: a `no_stale_crons` (or `no_errored_crons`) redmeans a job's
`last_run_at`/status briefly crossed its threshold — a scheduler stall, not a crash.
Probe the live endpoint (`curl` the external URL directly) and, on green, name the
red as transient and self-healing rather than an outage. Do not chase a network,
service, or disk cause for a bit that already healed.

**A `cron:<name>` suffix on a delivered watchdog alert is the DELIVERING job's
label, not the subject of the finding.** The bridge/runner prefixes each delivery
with the cron that produced it, so `digest down cron:orch-fleet-watchdog` means the
`orch-fleet-watchdog` cron delivered a `digest`-bit red — it is NOT naming the
stale cron. Read the vector index the message names; grep for `cron:` only to learn
the sender, never to identify the offending job.

## Check EVERY per-role path for the debounce, not just the one that flapped last

A watchdog with per-role branches (orchestrator vs worker) can have the debounce
applied to ONE branch and not the other. Measured: commit `84794f85` debounced the
WORKER path of `fleet-bus-failover-watchdog.py` (`WORKER_ALERT_CONSECUTIVE`) and
left the ORCHESTRATOR branch warning on `consecutive_failures == 1`, so a single
blip kept raising `⚠️ <peer> health check FAILED (1/N)` on the orchestrator host
while the peer was up. When you debounce one path, grep the file for the other
branch's alert condition and debounce it too, with a regression test per path.

Prefer a `>= threshold AND not already-alerted` pair (an `orch_alerted`-style flag
reset by the healthy branch) over a bare `== 1` — the flag still fires once when a
carried-over state file already sits at or above the threshold.

## A deploy step that only PRINTS a sudo command leaves the config stale invisibly

`deploy_nginx_configs()` in `fleet-update.sh` compares the repo nginx template
against `/etc/nginx/` and — because applying it needs root — only prints
`sudo cp … && sudo nginx -t && sudo systemctl reload nginx`, then exits 0. Nothing
records that the live config was never updated, so a shipped allowlist fix can sit
undeployed for months while the throttle it was meant to remove keeps producing
false alarms. When a fix depends on a printed manual step, add a guardrail that
READS the live artifact and WARNs (the nginx zone file is world-readable, so no
sudo is needed): make it a pure `path -> (status, detail, remedy)` seam in the
existing check, and pin the repo template in the same test so the guardrail and
the deploy cannot drift apart.

## Pitfalls

- Don't call it network flakiness because the probe source varies — NAT collapses
  many agents to one IP, so "different IPs" still share one nginx rate bucket.
- Don't run `curl http://127.0.0.1:<port>/health` on a TLS-only nginx listener and
  read the `400` as a service fault — plain HTTP to an `ssl` listener returns 400
  (and a closed port returns 000/connection refused). Probe over `https` (or the
  backend directly) for a truthful status.
- Throttling that only ever hits your OWN monitoring UAs (Python-urllib, the fleet
  watchdog) is the tell — an attacker would show many unknown/bot UAs.
- **Distinguish buffer-echo from a still-live fix by reading the TIMESTAMPS of the residual
  499/503 rows, never their bare count.** After applying a fix, `tail -N <log> | awk '{print $9}'`
  keeps showing the throttled rows that were already in the buffer before the reload — a large
  499/503 count in the tail window is NOT proof the fix failed. Compare each residual row's
  timestamp against the fix's application time:
  `tail -N <log> | awk '{print $4,$9,$NF}' | grep -E ' (499|503) '` — every row BEFORE the
  fix time is old buffer (fix worked); any row AFTER it is a genuine recurrence (fix is not
  covering that source),which usually means the gate-kept IPs differ from the `geo` allowlist.
- **Quote the `date` format string when slicing a log by clock hour** — `grep -E "$(date +%Y/%m/%d %H)"`
  (unquoted) splits `%H` off as a second `date` operand, `date` errors, and grep then matches
  EVERY line — producing all-time counts that read as a still-failing fix. Use
  `grep -E "$(date '+%Y/%m/%d %H')"` (single-quoted format) so you actually slice the current hour.
- **A limit-zone KEY change cannot be applied by reload — it is a process-start property.
  Zones keyed on `$binary_remote_addr` cannot be re-keyed on `$limit_bypass` via
  `nginx -s reload`: nginx logs an `[emerg]` (`limit_req "general" uses the
  "$limit_bypass" key while previously it used the "$binary_remote_addr" key`) and
  silently keeps serving the stale worker — so a perfectly-correct allowlist config
  applied by reload never actually activates. The empty-key bypass (and any zone KEY
  change) requires a **full `systemctl restart nginx`**; reload re-parses+re-signals
  but refuses the key swap. Verify by a **NEW worker PID** (today-dated) after the
  restart `ps -eo pid,lstart,args | grep 'nginx: worker' — never by `nginx -T`
  (which re-reads the on-disk files and stays unchanged-true while the worker holds the
  pre-fix zones). A `systemctl reload` reporting `status=0` is just the signal
  send, not the application; the worker's start time is the only proof it took.
- **The allowlist-is-in-place fix failing = the reload was silently rejected.** When the
  gate-kept IPs are allowlisted, `nginx -T` shows the geo,and 499/503 persist after a
  "successful reload", suspect the zone-key `[emerg]` — check the worker PID (stale =
  the reload never landed) and restart,and confirm a fresh worker appeared.
