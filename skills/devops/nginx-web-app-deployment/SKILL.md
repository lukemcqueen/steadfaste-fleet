---
name: nginx-web-app-deployment
description: "Deploy a custom web app (Flask, Python, Node) behind nginx — upstream config, SSL, basic auth, rate limiting, launchd/systemd service, and multi-layer testing pattern."
version: 1.0.0
author: Steadfaste Fleet
platforms: [macos, linux]
---

# Nginx Web App Deployment

Deploy a custom web app behind a Hermes-style nginx stack with SSL, basic auth, rate limiting, and auto-restart.

## Step 0 — verify the HOST before copying any config

The repo holds fleet DEFAULTS; a host may run a local variant. Two reads settle which is
which, and both are cheap:

```bash
ss -ltn                            # what THIS host actually listens on
include_lines=$(grep -n include /etc/nginx/nginx.conf)
ls -la /etc/nginx/sites-enabled/   # the symlinks nginx picks up
```

- **Never infer the host's ports from the repo — run `ss -ltn`.** The fleet runs more than
  one external port namespace: its committed nginx templates are the 13xxx variant
  (`13001`/`13002`/`13003`/`13004`/`13005`/`13007`), while another host listens on
  `14001`/`14002`/`14004`/`14005`/`14007` with NO repo counterpart. Copying the 13xxx
  template onto the 14xxx host moves its dashboard, metrics and health listeners and
  breaks them. The host's live listeners are the arbiter, not the template and not the
  port table in Step 1.
- **Copy only content that is port-free.** A shared include (a `geo` map, `limit_*_zone`
  definitions, `map` blocks) binds no port and is safe across hosts; any file containing
  `server { listen … }` is host-specific — verify its `listen` lines against `ss -ltn`
  before it goes anywhere.
- **Writing to a path nginx does not include is INERT — a silent no-op, not a fix.** A
  deploy step that drops `/etc/nginx/<name>.conf` changes nothing when `nginx.conf` only
  includes `conf.d/*.conf` and `sites-enabled/*`, and it reports success either way. So
  after ANY config write, prove it is loaded: the file is named in an `include` line
  (directly or via a `sites-enabled/` symlink) AND `ss -ltn` shows the port. A config that
  is present but not included is the failure shape — the fix looks applied and nothing
  changes.

## Step 1 — Choose Port

Recommended port convention for Hermes-style setups:

| Service | Port | Purpose |
|---------|------|---------|
| App 1 | 13001 | First service |
| App 2 | 13002 | Second service |
| App 3 | 13003 | Third service |
| _(next)_ | 13004+ | — |

Internal (app) ports are typically in the 5000-9000 range. External (nginx) ports are 13001+.

**This table is a CONVENTION, not a reading of your host.** Confirm the host's real
listeners with `ss -ltn` before choosing or copying anything (Step 0).

## Step 2 — Write the App

Standard Flask app pattern:

```python
app.run(host="127.0.0.1", port=5001)
```

Keep it listening on 127.0.0.1 only — nginx handles external access.

## Step 3 — Create the Service

### macOS (launchd)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hermes.APPNAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/gunicorn</string>
        <string>-w</string><string>2</string>
        <string>-b</string><string>127.0.0.1:INTERNAL_PORT</string>
        <string>app:app</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/app</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/path/to/logs/app.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/logs/app.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/path/to/venv/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.hermes.APPNAME.plist
```

### Linux (systemd user service)

```ini
[Unit]
Description=APPNAME
After=network.target

[Service]
WorkingDirectory=/opt/APPNAME
ExecStart=/opt/APPNAME/venv/bin/gunicorn -w 2 -b 127.0.0.1:INTERNAL_PORT app:app
Restart=always
Environment=PATH=/opt/APPNAME/venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now APPNAME
```

## Step 4 — nginx Config

Add to your nginx sites config:

```nginx
upstream APPNAME_backend {
    server 127.0.0.1:INTERNAL_PORT;
}

server {
    listen EXTERNAL_PORT ssl;
    server_name YOUR_DOMAIN localhost;

    ssl_certificate     /path/to/ssl/YOUR_DOMAIN/fullchain.pem;
    ssl_certificate_key /path/to/ssl/YOUR_DOMAIN/privkey.pem;

    access_log  /var/log/nginx/APPNAME-access.log;
    error_log   /var/log/nginx/APPNAME-error.log;

    auth_basic "APPNAME";
    auth_basic_user_file /path/to/.htpasswd;

    limit_conn conn_limit 10;

    location / {
        limit_req zone=general burst=40 nodelay;
        limit_req zone=auth burst=10 nodelay;

        proxy_pass http://APPNAME_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Trusted sources bypass the limits without loosening the public ceiling

Key the limit zones on a `geo` map that is EMPTY for trusted sources — nginx applies NO
limit when a zone's key variable is empty, so trusted hosts are exempt while everyone else
keeps the tight ceiling keyed by source IP:

```nginx
geo $limit_bypass {
    default          $binary_remote_addr;   # everyone else: keyed by source IP
    192.168.1.0/24   "";                    # trusted network
    127.0.0.0/8      "";                    # localhost
}
limit_req_zone  $limit_bypass zone=general:10m rate=20r/s;
limit_req_zone  $limit_bypass zone=auth:10m   rate=5r/s;
limit_conn_zone $limit_bypass zone=conn_limit:10m;
```

Do NOT reach this outcome by widening `burst`/`limit_conn` for everybody: a ceiling wide
enough for trusted internal probes is no longer a DDoS ceiling. And when traffic from a
trusted host is being throttled, check whether the LIVE include is still the pre-allowlist
revision (`$binary_remote_addr` keys, no `geo`) BEFORE touching any burst value — the limit
that fired may simply not have the allowlist loaded.

### CRITICAL — Single Listener Rule

⚠️ **NEVER add a second `listen` directive on the same port.** Each server block must have exactly ONE `listen` line:

```nginx
# ✅ CORRECT — single SSL listener
listen 13003 ssl;

# ❌ WRONG — this creates a plain HTTP listener that intercepts TLS handshakes → 400 error
listen 13003 ssl;
listen 127.0.0.1:13003;
```

The plain listener catches TLS connections first, reads the encrypted handshake as HTTP, and returns a 400 Bad Request. The `listen PORT ssl;` directive already binds all interfaces.

## Step 5 — Verify Config & Reload

```bash
nginx -t && nginx -s reload
```

## Step 6 — Multi-Layer Testing (MANDATORY)

Test EVERY layer before declaring the deployment done:

```bash
# Layer 1: Internal (app directly)
curl -s -o /dev/null -w "Layer 1: %{http_code}\n" http://127.0.0.1:INTERNAL_PORT/

# Layer 2: nginx proxy
curl -s -o /dev/null -w "Layer 2: %{http_code}\n" http://127.0.0.1:EXTERNAL_PORT/
```

### Write an automated test suite

Create `test_api.py` that covers:

1. ✅ App is reachable (internal port)
2. ✅ nginx proxy returns 401 (basic auth challenge)
3. ✅ No active sessions initially
4. ✅ Start a session → returns 200, active=true
5. ✅ Multi-user isolation
6. ✅ Second start auto-stops first session
7. ✅ Stop → returns duration, active=false
8. ✅ Today endpoint returns structured data
9. ✅ Week endpoint returns expected shape
10. ✅ Error handling (invalid input → 400, stop with no active → 400)

**Key rule:** Test the endpoint the same way the user will access it. Testing only the internal port is not sufficient.

```bash
# Run the test suite
venv/bin/python test_api.py
```

## PITFALLS

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Dual listener on same port | 400 Bad Request, nginx log shows garbled bytes | Remove plain listener, keep only `listen PORT ssl;` |
| nginx listener bound to `127.0.0.1:PORT` only | Domain resolves to a public IP, but external traffic can't connect (works from localhost only) | Change to `listen PORT;` / `listen PORT ssl;` (all interfaces) — a `127.0.0.1:`-scoped listener is unreachable from outside |
| Plain HTTP request to an HTTPS port | Client gets `The plain HTTP request was sent to HTTPS port` (400) | Client must use `https://`; if a plain-HTTP redirect is desired, add a separate server block that listens on the HTTP port and redirects to HTTPS |
| Server name mismatch | SSL cert warnings in browser | Use `YOUR_DOMAIN localhost` in server_name directive |
| nginx not reloaded after config change | Old config still serving | Always run `nginx -t && nginx -s reload` |
| Gunicorn binding to 0.0.0.0 | App accessible without auth | Bind gunicorn to `127.0.0.1` only |
| launchd PATH missing | Gunicorn not found | Set `EnvironmentVariables:PATH` in plist |
| No automated test suite | Regression goes unnoticed | Write test_api.py before declaring done |
| htpasswd written to wrong file | 401 even with correct credentials | Check which file `auth_basic_user_file` in nginx config actually points to vs where you wrote the passwords |
| Repo template ports don't match the host | Copying it binds the wrong ports and takes the host's services down | `ss -ltn` first (Step 0); copy only port-free includes, treat `server { listen … }` files as host-specific |
| Config written to a path nginx never includes | Deploy reports success, behaviour is unchanged | Confirm the file is named in an `include` line (or reachable via `sites-enabled/`) AND the port shows in `ss -ltn` |
| Changed a limit zone's KEY and reloaded | The reload is refused; the old worker keeps serving the stale limits, so the fix looks ineffective | Restart nginx — a limit-zone key change is not accepted by `nginx -s reload` |
