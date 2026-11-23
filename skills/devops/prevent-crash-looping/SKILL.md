---
name: prevent-crash-looping
version: 1.0.0
description: How to prevent systemd service crash-looping from port conflicts, missing directories, and failed dependencies
category: devops
---

# Prevent Crash-Looping

## Problem
A systemd service fails to start → systemd restarts it (`Restart=on-failure`) → fails again → repeats every `RestartSec` seconds indefinitely. This happens from:
- **Port conflict**: Another process (manual or another instance) holds the port
- **Missing directory**: Log directory doesn't exist → `status=209/STDOUT`
- **Missing dependency**: Python packages not installed in the venv
- **Wrong Python**: System python vs venv python

## Three-Layer Defense

### Layer 1: Port Arbitration (in the service's Python/script)
Before binding, check if the port is already in use. If the owner is another instance of this service, **exit 0** (success) instead of failing. This prevents systemd from restarting.

**Pattern:**
```python
import os, socket, sys
from pathlib import Path

PID_FILE = Path.home() / ".service-name" / "service.pid"

def check_port(host: str, port: int) -> bool:
    """Returns True if port is free, False if owned by our process."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.close()
        PID_FILE.write_text(str(os.getpid()))
        return True
    except OSError as e:
        sock.close()
        if e.errno == 98:  # EADDRINUSE
            if PID_FILE.exists():
                pid = int(PID_FILE.read_text())
                cmdline = Path(f"/proc/{pid}/cmdline")
                if cmdline.exists() and "service-name" in cmdline.read_text().replace("\0", " "):
                    return False  # Graceful handoff
            sys.exit(1)  # Unknown owner
        sys.exit(1)
```

### Layer 2: Systemd Service Hardening
```ini
[Service]
# Auto-create log directory before service starts
ExecStartPre=/bin/mkdir -p %h/.service-name/
# Carve out writable paths under ProtectHome=read-only
ProtectHome=read-only
ReadWritePaths=%h/.service-name/
# Clean up PID on exit
ExecStopPost=/bin/rm -f %h/.service-name/service.pid
```

### Layer 3: Graceful Recovery
- Systemd `Restart=on-failure` paired with port arbitration means:
  - Crash → port free → restart succeeds
  - Manual instance conflicts → exit 0 → no restart triggered
- `systemctl reset-failed` is a workaround, not a fix. Fix the root cause.

## Enabled unit whose OWN guard fails closed (not a port, a policy)

A service whose `ExecStartPre` guard deliberately refuses to start — a
"distinct token" check, a missing required env var (`X is not set in the
environment — cannot start`), a fail-closed prerequisite — crash-loops
forever when it is ENABLED but the condition is unmet. The guard is doing
its job; the unit should not be enabled. This is the migration-target shape:
unit A is the live canonical service, unit B (the future replacement) is
enabled on the same host where A owns the resource (bot token, port), and
B's guard correctly fails closed so they never collide — but the 10k
restart cycles are pure waste.

Diagnosis: read the journal line — a guard refusal names the missing
condition, NOT a code bug. `systemctl --user is-enabled <unit>` tells you
it is enabled; check whether the canonical sibling is active
(`systemctl --user is-active <canonical>`). If the canonical is live and
the failing unit is the migration target, the fix is
`systemctl --user disable --now <unit>` — never "fix" the guard to make
it start (that would create the very collision the guard prevents) and
ever leave it enabled to keep burning restarts. The doctor's crash-loop
check flags the unit; masking a failed headless service (e.g. a desktop
portal with "cannot open display" on a headless-boot host) is the
sanctioned disposal when the doctor's own stale-units check skips masked
portal services.

## Testing
1. Start the service: `systemctl --user start service-name`
2. Verify running: `systemctl --user status service-name`
3. Test port arbitration: start a second instance manually — should exit 0
4. Test crash recovery: `kill -9 $(pgrep -f service-name)` — systemd should restart cleanly

## Live Example: health-vector on Moses
- Service: `health-vector.service`
- Script: `~/steadfaste-fleet/ops/scripts/health/health-vector.py` (see `serve_http()` port handling; the retired FastAPI `health-server.py` was removed in commit `42fb8374`)
- Service file: `~/.config/systemd/user/health-vector.service`
