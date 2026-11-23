---
name: linux-server-hardening
version: 1.0.0
category: devops
description: >-
  Systematic Linux server hardening with tiered prioritization. Covers UFW
  firewall, SSH hardening (key-only auth, timeouts, rate limits), unattended-upgrades,
  kernel sysctl tuning, fail2ban jail expansion, and audit tooling. Uses a
  three-tier framework (high impact/low risk → good practice → nice to have)
  so the most important changes happen first.
trigger: >-
  User asks to harden a Linux server, improve security, set up a firewall,
  secure SSH, enable automatic updates, or "harden this box." Also when
  reviewing a server's security posture and suggesting improvements.
---

# Linux Server Hardening

Systematic, tier-prioritized hardening for Linux servers (Debian/Ubuntu/Mint).

## Philosophy

Security hardening is a stack of defenses, ordered by impact vs risk:

- **Tier 1** — High impact, low risk. Do these first. They block the most common attack vectors with minimal chance of breaking things.
- **Tier 2** — Good practice. Broadening the defense surface. Slightly higher risk of impacting legitimate use.
- **Tier 3** — Nice to have. Audit and detection tooling. Valuable for post-incident analysis but lower ROI day-to-day.

**User preference:** Present tiers clearly, then let the user pick ("do 1-3", "just tier 1", etc.). Execute immediately once they decide — don't re-explain what you're about to do.

## Pre-Work Survey

Before suggesting or implementing anything, run these checks:

```bash
# 1. Listening ports — what's exposed to the network
ss -tlnp

# 2. SSH config — auth methods, root login, port, timeouts
sudo sshd -T 2>/dev/null | grep -E "permitrootlogin|passwordauthentication|pubkeyauthentication|port|allowusers|maxauthtries|clientalive"

# 3. Firewall status
sudo ufw status verbose   # or iptables -L -n -v

# 4. Authorized keys — CRITICAL before disabling password auth
ls -la ~/.ssh/authorized_keys 2>/dev/null || echo "NO AUTHORIZED KEYS"
who | grep pts              # check current SSH connections

# 5. Update config
ls /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null || echo "NO AUTO UPGRADES"
dpkg -l unattended-upgrades 2>/dev/null | grep -c "^ii" || echo "NOT INSTALLED"
```

## Tier 1 — High Impact, Low Risk

### 1. UFW Firewall

Set up a host firewall. Default deny incoming, allow only needed ports.

```bash
# Step 1: Allow services BEFORE enabling
# SSH must come first or you lock yourself out
sudo ufw allow ssh                # port 22
sudo ufw allow http               # port 80 (nginx)
sudo ufw allow https              # port 443 (if applicable)
# Add any Hermes/Fleet service ports exposed on all interfaces
for port in 13001 13002 13004 13007; do
  sudo ufw allow "$port"
done

# Step 2: Default deny + enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

**Verification:** Open a second terminal / ask user to reconnect before closing the session. Run `sudo ufw status numbered` to confirm rules.

**Important:** If Docker is used, Docker manipulates iptables directly. UFW's `default deny incoming` may interfere with Docker port mappings. Add `DEFAULT_FORWARD_POLICY=ACCEPT` to `/etc/default/ufw` if Docker containers need inbound access.

### 2. SSH Hardening

**CRITICAL:** Before disabling password auth, verify the user has SSH keys configured in `~/.ssh/authorized_keys`. If the file doesn't exist or is empty, set up key-based auth FIRST — otherwise you will lock the user out.

```bash
# In /etc/ssh/sshd_config (or /etc/ssh/sshd_config.d/hardening.conf):
# (explicit settings override the commented-out defaults)

# Disable password auth (default is 'yes' when commented)
PasswordAuthentication no
ChallengeResponseAuthentication no

# Lock down timeouts — disconnect idle sessions
ClientAliveInterval 300     # seconds between alive checks
ClientAliveCountMax 2       # max missed checks before disconnect

# Rate-limit auth attempts
MaxAuthTries 3

# Disable X11 forwarding unless explicitly needed
X11Forwarding no

# Optional but recommended for headless servers:
PermitRootLogin prohibit-password
AllowAgentForwarding no
AllowTcpForwarding no
```

After changes:

```bash
sudo sshd -t                    # validate config
sudo systemctl reload sshd      # apply without dropping connections
```

**Verification:** In a second terminal (keep the first one open), try `ssh -o PasswordAuthentication=no user@host` — it should connect. Try `ssh -o PreferredAuthentications=password user@host` — it should be rejected.

### 2b. Diagnosing "key rejected under StrictModes"

When someone reports SSH pubkey auth blocked by "file/dir permissions,"
verify before believing or acting — measure, don't relay:

```bash
# 1. Modes + ownership in ONE stat (lock-free on governed hosts):
stat -c '%a %U:%G %n' <home> <home>/.ssh <auth-keys-path>
# StrictModes wants the home dir, its .ssh dir, and the keys file each
# NOT group/other-writable, and owned by the user or root.

# 2. Look for ACLs hiding extra grants (an 'ls -l' '+' suffix means ACLs exist):
ls -la <home>/.ssh

# 3. Config: is StrictModes even on? Any AuthorizedKeysFile override?
#    Default is yes; drop-ins (sshd_config.d/*.conf) can change it.
grep -r -e "StrictModes" -e "AuthorizedKeysFile" /etc/ssh/sshd_config /etc/ssh/sshd_config.d/

# 4. DEFINITIVE evidence — the sshd log names the exact rejection:
journalctl -u sshd --no-pager | grep -i -e "bad ownership" -e "refused" -e "Failed publickey" | tail -20
# "Authentication refused: bad ownership or modes" points at the exact path.
# Zero such lines + correct stat = perms are NOT the problem; check the key
# itself or the client side instead.
```

If a key's fingerprint must be confirmed, `ssh-keygen -lf` prints one line
per key (type, bits, SHA256 fingerprint, comment) — on governed hosts run it
inside a cycle (it is not a lock-free primitive there).

### 2c. Adding a key to the authorized list

- **Check for an existing identical entry FIRST** (same key body, same
  trailing comment) — if it already exists, report "already present" and stop;
  appending again only creates a duplicate line.
- Add the user's line verbatim — same key-type prefix, body, comment. Never
  "repair" or reformat a pasted key; a single changed character breaks it.
- After any edit, preserve strict modes and user ownership on the keys file
  and its directory — a group/other-writable keys file stops authenticating
  under StrictModes.

### 3. Unattended-Upgrades

Automatic security patching prevents known-exploit mass scanning from hitting you.

```bash
# Install
sudo apt update && sudo apt install -y unattended-upgrades

# Configure for security-only updates
sudo dpkg-reconfigure -plow unattended-upgrades
# → Select "Yes" when prompted

# Or configure manually:
sudo tee /etc/apt/apt.conf.d/20auto-upgrades > /dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

# Enable the service
sudo systemctl enable unattended-upgrades
sudo systemctl start unattended-upgrades

# Check it's running
sudo unattended-upgrades --dry-run --debug 2>&1 | head -20
```

## Tier 2 — Good Practice

### 4. Fail2ban Jail Expansion

**Frame it correctly: fail2ban is a rate limiter, not an access control.** It cannot stop a distributed attack (one or two tries per IP across thousands of IPs), it stops nothing when the attacker already holds a valid key, and the shipped default ban is short (`maxretry=5`, `findtime=10m`, `bantime=10m`). What actually resists SSH brute force is key-only auth; fail2ban is the layer that quiets log noise and slows a single-source attacker. Never cite fail2ban as the reason a password-auth host is safe. Verify it is genuinely running and which jails are live — a jail present in config is not a jail that is enabled:

```bash
sudo fail2ban-client status            # jail list — sshd must appear here
sudo fail2ban-client status sshd       # bans, and the IPs currently banned
journalctl -u fail2ban --since '7 days ago' | grep -cE 'Ban |Jail .*sshd.'   # has it actually banned?
```

The sshd filter ignores failed pubkeys for *valid* users by default (`publickey = nofail`) but COUNTS failed pubkeys for *invalid* users, so a login aimed at the wrong host or a non-existent account does accumulate toward a ban.

Add jails for any additional exposed services (Hermes API ports, etc.):

```bash
# In /etc/fail2ban/jail.local or /etc/fail2ban/jail.d/custom.conf:
[hermes-13001]
enabled  = true
port     = 13001
filter   = hermes-auth   # create this filter if there are auth logs
logpath  = /path/to/hermes/service.log
maxretry = 5
bantime  = 86400
findtime = 3600
```

### 5. Kernel Sysctl Hardening

Add to `/etc/sysctl.d/99-hardening.conf`:

```ini
# IP spoofing protection
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.default.rp_filter=1

# Ignore ICMP redirects
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.all.secure_redirects=0
net.ipv6.conf.all.accept_redirects=0

# Log packets with impossible source addresses
net.ipv4.conf.all.log_martians=1

# Enable TCP SYN cookies (protect against SYN flood attacks)
net.ipv4.tcp_syncookies=1

# Protect against time-wait assassination
net.ipv4.tcp_rfc1337=1

# Restrict kernel pointer exposure
kernel.kptr_restrict=2

# Restrict dmesg to root only
kernel.dmesg_restrict=1
```

Apply: `sudo sysctl --system`

### 6. SSHD Banner

Add a legal notice banner to deter casual attackers:

```bash
echo "WARNING: Unauthorized access is prohibited." | sudo tee /etc/ssh/banner
# In sshd_config: Banner /etc/ssh/banner
```

## Tier 3 — Nice to Have

### 7. auditd

```bash
sudo apt install -y auditd
sudo systemctl enable auditd
sudo systemctl start auditd
# Watch key files:
sudo auditctl -w /etc/ssh/sshd_config -p wa -k sshd_config
sudo auditctl -w /etc/passwd -p wa -k user_db
```

### 8. Lynis (Security Audit)

```bash
sudo apt install -y lynis
sudo lynis audit system           # run and review suggestions
# Schedule weekly: 0 6 * * 1 lynis audit system
```

### 9. rkhunter (Rootkit Detection)

```bash
sudo apt install -y rkhunter
sudo rkhunter --propupd          # initialize file property database
sudo rkhunter --check --sk       # run a check
# Schedule weekly: 0 7 * * 1 rkhunter --check --sk --rwo
```

## Pitfalls

1. **CRITICAL — SSH key check before disabling password auth:** If `~/.ssh/authorized_keys` doesn't exist or is empty, disabling `PasswordAuthentication` will *immediately lock out everyone*. Always check this first. If no keys exist, either create one or keep password auth enabled until the user sets up keys.

2. **UFW and Docker:** Docker bypasses UFW because it writes directly to iptables. If you enable UFW after Docker, existing containers' port mappings continue to work (Docker rules sit above UFW in the iptables chain). To force Docker through UFW, set `DEFAULT_FORWARD_POLICY=ACCEPT` in `/etc/default/ufw` and restart. Better: configure Docker's `iptables=false` and manage port mapping via UFW rules.

3. **UFW enable order:** Always `ufw allow ssh` BEFORE `ufw enable`. If you enable first, the default deny blocks your SSH session immediately. If this happens, recover via out-of-band console access (iDRAC, IPMI, physical console).

4. **SSH config format:** Put hardening directives in `/etc/ssh/sshd_config.d/hardening.conf` (cleaner, survives package upgrades) rather than editing the main `sshd_config`.

5. **unattended-upgrades dry-run output:** The `--dry-run` flag produces verbose output about what it *would* do. A clean run means no errors, not that it found packages to upgrade. Verify the service is actually active with `systemctl status unattended-upgrades`.

6. **sysctl ip_forward for Docker:** `net.ipv4.ip_forward=1` is required for Docker networking. Don't set it to 0 in hardening configs.

7. **Reboot for kernel updates:** unattended-upgrades does NOT auto-reboot by default. For kernel security patches to take effect, configure `/etc/apt/apt.conf.d/50unattended-upgrades` with `Unattended-Upgrade::Automatic-Reboot "true";` and `Unattended-Upgrade::Automatic-Reboot-Time "03:00";` if you want automatic reboots for kernel updates.

8. **A peer agent's "perms block auth" claim is a hypothesis, not a finding.**
   Verify with `stat` + the sshd journal before changing anything — the
   measured modes and the log's "bad ownership or modes" lines are the only
   evidence that counts. If the stat is correct and the log is silent, the
   claim does not reproduce on this host; say so and ask which host/path they
   meant rather than "fixing" a non-problem.

9. **HOST KEY CHANGED on a peer is a red flag, not a chore.** When `ssh`
   refuses with a changed host key, never blindly accept the new key
   (no `-o StrictHostKeyChecking=no`, no `ssh-keyscan` append) — confirm the
   rotation with the peer's owner first (reinstall/regeneration is legit, but
   verify). While known_hosts is stale the probe is blocked, so reach the peer
   through the Agent Bus or another channel for the question.
10. **A valid, present key is still refused when its path permissions are wrong.** `StrictModes` (default `yes`, and normally left commented out in `sshd_config`) silently rejects a key when the `.ssh` directory or the authorized-keys file is writable by group or other; the client sees only `Permission denied (publickey)`, and the key itself parses perfectly, so it looks like a key problem. Correct the ownership and modes so the directory is owner-only and the key file is owner read/write (the standard SSH layout), then confirm with `stat -c '%a %U %G'` on both. Check this BEFORE blaming the key, the client, or the file's contents.

11. **`sshd -T` needs root.** Run as a normal user it exits with `no hostkeys available` and prints nothing, which reads like an empty or misconfigured result. Don't retry it — determine the effective settings by reading `sshd_config` and `sshd_config.d/*.conf` directly and knowing the compiled-in defaults (e.g. `StrictModes yes`, `PubkeyAuthentication yes`).

12. **Never infer the effective SSH auth methods from the main `sshd_config` — the DROP-INS decide.** A stock `sshd_config` ships every auth line commented out (`#PasswordAuthentication yes`, `#KbdInteractiveAuthentication yes`), which reads like password auth is ON; near the top, though, is `Include /etc/ssh/sshd_config.d/*.conf`, and any drop-in silently overrides it (e.g. Omarchy's `10-omarchy-hardening.conf` sets `PasswordAuthentication no` + `KbdInteractiveAuthentication no`). Auditing only the main file produced a wrong "this host allows password SSH" finding. Read `ls /etc/ssh/sshd_config.d/` and EVERY file in it, then confirm from a client WITHOUT root:

   ```bash
   ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password \
       -o NumberOfPasswordPrompts=0 -o BatchMode=no -v user@host true 2>&1 \
     | grep "Authentications that can continue"
   ```

   That line is the authoritative list of methods the server will actually accept (`publickey` alone ⇒ password auth is off). `NumberOfPasswordPrompts=0` means no credential is ever sent, so the probe cannot lock an account or trip fail2ban. Trust this live method list over any reading of config files.

## Verification Checklist

After applying any tier, run this:

```bash
# UFW
sudo ufw status numbered

# SSH
sudo sshd -T | grep -E "passwordauthentication|clientalive|maxauthtries|x11forwarding"
ssh -o PreferredAuthentications=password localhost 2>&1 | head -3

# Auto-upgrades
systemctl status unattended-upgrades --no-pager -l | head -5
cat /etc/apt/apt.conf.d/20auto-upgrades

# Sysctl
sysctl kernel.kptr_restrict kernel.dmesg_restrict net.ipv4.conf.all.accept_redirects

# Fail2ban
sudo fail2ban-client status | grep "Jail list"
```
