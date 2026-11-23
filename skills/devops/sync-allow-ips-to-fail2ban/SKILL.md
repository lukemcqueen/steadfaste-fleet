---
name: sync-allow-ips-to-fail2ban
version: 1.0.0
description: Sync IPs from allow-ips-manual.conf to fail2ban ignoreip
---

# Sync Allow IPs to fail2ban

## Trigger
User says "add/remove an allow IP" or "sync IPs to fail2ban".

## Workflow — 3 steps (always do ALL):

### ⚠️ Prerequisites — verify BEFORE promising the change (measured on esther + moses, 2026-11-23)

**Adding an allow IP is a ROOT action. An agent normally CANNOT do it — report that plainly instead of implying success.**

- `~/.hermes/scripts/local-sync-allow-ips-to-fail2ban.sh` **does not exist** on esther or moses. The block under Step 1 is what you would CREATE, not something already installed — invoking "the sync script" fails with `No such file or directory`.
- **Neither host has `/etc/fail2ban/jail.local`, and neither carries any `ignoreip` line** (`grep -rn '^ignoreip' /etc/fail2ban/` → empty). Both drive fail2ban from `/etc/fail2ban/jail.d/*.conf`; the effective ignoreip is fail2ban's built-in `127.0.0.1/8 ::1`. So a `sed -i 's/^ignoreip = .*/…/'` **no-ops** — there is no line to replace.
- `/etc/nginx/allow-ips-manual.conf` and `/etc/fail2ban/jail.d/` are `root:root` and **not writable** by the agent, and **both hosts demand a sudo password** (`sudo -n true` → `a password is required`). esther's NOPASSWD list has no `fail2ban-client reload` and no write primitive; moses grants `(ALL) ALL` but not NOPASSWD.
- The repo runbook `docs/runbooks/blocklist-cleanup-ddos-relax.md` treats the allow file as the **manual/human domain** — "NEVER edit nginx templates or allow-ips-manual.conf" by automation. The deploy strips allow-listed IPs automatically but never writes the file.

Confirm your footing first:

```bash
sudo -n true 2>&1 || echo "NO passwordless sudo — this change needs the operator"
test -w /etc/nginx/allow-ips-manual.conf || echo "allow file NOT writable"
ls /etc/fail2ban/jail.local 2>&1; grep -rn '^ignoreip' /etc/fail2ban/ | head
```

### Direct procedure — use this when you hold root

Run on **EACH** host whose fail2ban you want to whitelist — esther and moses run fail2ban independently, so one host does not cover the other:

```bash
# 1. add to the source allow list (manual/root action)
echo 'allow 203.0.113.45;   # <who / reason>' | sudo tee -a /etc/nginx/allow-ips-manual.conf

# 2. create the ignoreip line (jail.local is absent; it is read AFTER jail.d, so it wins)
printf '[DEFAULT]\nignoreip = 127.0.0.1/8 ::1 203.0.113.45\n' | sudo tee /etc/fail2ban/jail.local

# 3. apply — REQUIRED; editing the file alone changes nothing until reload
sudo fail2ban-client reload

# 4. verify
sudo fail2ban-client status sshd      # the banned 'IP list' must not contain the allowed IP
grep -n '^ignoreip' /etc/fail2ban/jail.local
```

fail2ban's ban is only a **rate limiter** (default `maxretry=5` / `findtime=10m` / `bantime=10m`); whitelisting an operator IP is what prevents self-lockout, and it must be repeated on every fail2ban host.

### Step 1 — Create/update the source script (optional automation)
Script lives at `~/.hermes/scripts/local-sync-allow-ips-to-fail2ban.sh`:

```bash
#!/bin/bash
# Reads IPs from /etc/nginx/allow-ips-manual.conf, strips 127.0.0.1/::1,
# updates ignoreip in /etc/fail2ban/jail.local, reloads fail2ban.

ALLOW_FILE="/etc/nginx/allow-ips-manual.conf"
JAIL_LOCAL="/etc/fail2ban/jail.local"

# Extract IPs/CIDRs (skip comments, strip "allow " and ";")
IPS=$(grep -oP 'allow \K[^;]+' "$ALLOW_FILE" | grep -v '^127\.0\.0\.1$' | grep -v '^::1$' | tr '\n' ' ')

# Build new ignoreip line
NEW_IGNORE="ignoreip = 127.0.0.1/8 ::1 $IPS"

# Replace in jail.local
sed -i "s/^ignoreip = .*/$NEW_IGNORE/" "$JAIL_LOCAL"

# Reload fail2ban
fail2ban-client reload
```

Make it executable:

```bash
chmod +x ~/.hermes/scripts/local-sync-allow-ips-to-fail2ban.sh
```

### Step 2 — Add an allow IP
Append the IP or CIDR to `/etc/nginx/allow-ips-manual.conf` (one per line):

```nginx
allow 203.0.113.45;
allow 198.51.100.0/24;
```

Then run the sync script to propagate to fail2ban:

```bash
bash ~/.hermes/scripts/local-sync-allow-ips-to-fail2ban.sh
```

### Step 3 — Verify
Confirm both layers picked up the IP:

```bash
# nginx layer — should show the allow line
grep -c "allow 203.0.113.45" /etc/nginx/allow-ips-manual.conf

# fail2ban layer — ignoreip should contain it
grep "^ignoreip" /etc/fail2ban/jail.local

# fail2ban reload succeeded
fail2ban-client status | grep -i "number of jail"
```

## Pitfalls

- **Confirm you actually hold root before starting** — on esther and moses you do not (`sudo -n true` fails), and the allow file plus `jail.d/` are root-owned. Reporting "added" without the write landing is a false claim; verify with `grep -n '^ignoreip' /etc/fail2ban/jail.local` afterwards.
- **There is no `jail.local` and no existing `ignoreip` line on either fleet host** — create the line; a `sed` against a non-existent line silently does nothing and looks like success.
- **`fail2ban-client reload` is part of the change, not an afterthought** — and it also needs root.
- **Whitelist every host that runs fail2ban** — esther and moses are independent; adding the IP on one leaves the operator bannable on the other.
- **Never add `127.0.0.1` or `::1` to the manual file** — they're always in `ignoreip` by construction; adding them is harmless but noisy.
- **The allow file is nginx `allow` directives, not raw IPs** — the extractor strips the `allow ` prefix and trailing `;`. A malformed line (missing `;`) silently drops the IP.
- **`sed` replaces the whole `ignoreip` line** — if jail.local is absent or lacks an `ignoreip` line, the script no-ops; create the line first with `sed -i "s/^ignoreip = .*/ignoreip = 127.0.0.1\/8 ::1/" "$JAIL_LOCAL"`.
- **fail2ban reload is required** — editing jail.local without `fail2ban-client reload` has no effect until the next service restart.
- **Daily threat-pipeline cron** (5AM) deploys blocked IPs from `deploy/nginx/blocked_ips.add` — this allow-list flow is the complementary whitelist; keep them separate.

## Related
- `threat-defense-pipeline` — the blocking side (fail2ban jails + nginx IP blocking)
- `linux-server-hardening` — broader server security posture
