# gmux Backlight: MacBookPro10,1 Display Blanking Fix

## Hardware

- 2012 MacBook Pro 15" Retina (MacBookPro10,1)
- GPU: Intel HD 4000 (Ivy Bridge i7-3720QM) + NVIDIA GeForce GT 650M (GK107)
- Display: internal Retina LCD routed through Apple gmux (GPU multiplexer)
- gmux version: 3.2.19 [indexed]
- IO ports: 0x700-0x7fe
- Kernel: 7.0.0-31-generic (Ubuntu 24.04-based LM 22.3)

## Symptoms

- `xset dpms force off` succeeds (exit 0) but display stays 100% lit
- `echo 4 > /sys/class/backlight/gmux_backlight/bl_power` succeeds but backlight stays on
- `actual_brightness` reads 16777215 regardless of writes to `brightness`
- `xset q` shows "Monitor is Off" but display is visibly on

## Debugging Path

### Attempted fixes that did NOT work

1. `acpi_backlight=native` — removed gmux_backlight entirely, no alternative backlight appeared
2. `acpi_backlight=vendor` — same result, no backlight interface
3. `apple_gmux.backlight=0` — kernel parameter was silently ignored (no module params)
4. `modprobe.blacklist=apple_gmux` — would break GPU switching, not attempted at boot
5. Writing to gmux sysfs `brightness` and `bl_power` — accepted but hardware ignored
6. Polling `xprintidle` in background loop — crashed greeter (disown not available in /bin/sh)
7. `sudo -u lightdm DISPLAY=:0 xset ...` — X auth failed for lightdm user from root context

### Working fix

`sudo setpci -H1 -s 00:01.00 BRIDGE_CONTROL=0`

After this one command:
- `echo 4 > /sys/class/backlight/gmux_backlight/bl_power` actually turns the backlight off
- `echo 0 > /sys/class/backlight/gmux_backlight/bl_power` turns it back on
- `xset dpms force off` turns the display off properly

### Why it works

The PCI bridge at 00:01.0 connects the CPU to the GPU. Its BRIDGE_CONTROL register
controls I/O decode permissions. MacBooks set a restrictive default that prevents
gmux IO port access from reaching the hardware. Clearing the register to 0 allows
the gmux IO commands to pass through to the display controller.

### Key gotchas

- The `setpci` command must run as root
- Suppress ALL output in greeter-setup-script context — `>/dev/null 2>&1`
- Do NOT use `-v` (verbose flag) — output breaks the greeter
- Do NOT use `disown` in greeter-setup-script — `/bin/sh` (dash) doesn't have
  it; use `setsid` instead
- The fix is runtime only — reboot clears the bridge register
- Screen saver blanking (framebuffer black) and DPMS (backlight off) are separate:
  even if DPMS doesn't work, the screen saver may still blank the framebuffer
  (but on this hardware, without the bridge fix, the backlight stays on regardless)

## Sources

- Arch Wiki Mac/Troubleshooting
- Launchpad bugs #1243897, #1190341
- AskUbuntu: "Macbook Pro backlight control not working"
