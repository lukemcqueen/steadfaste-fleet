---
name: macbook-linux-maintenance
version: 1.0.0
category: devops
description: "Legacy MacBook Linux fixes: GPU wedge, reboot hang, thermal."
author: Esther
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [macbook, linux, gpu, thermal, nvidia, reboot, hardware]
    related_skills: [linux-performance-diagnostics]
---

# MacBook on Linux — Maintenance Class

## When to Use

- A legacy Apple laptop on Linux is slow, hot, loud, or hangs on shutdown/reboot;
  load average ~1000; 4K display stuck at 30Hz.
- A GPU-driver install/swap on a MacBook went wrong, or `dkms status` shows a
  driver branch that should not be there.
- You need to identify an Apple laptop's real model/year from DMI data or a serial.
- Desktop automation on a locked-screen Linux session (input goes to the lock grab).
- Changing the display mode or its persistence (any of the `~/.xprofile`, `~/.config/autostart/`, or lightdm conf layers) — load this FIRST for the mode/heat trade-offs and which layer needs the user's sudo.
- A host that is "hot" with no obvious cause — read the thermal section before touching anything.
- The operator reports the machine made a **strange noise**, was loud, or "just
  came back" — run the noise triage (§5b) before touching hardware or blaming a
  crash; the answer is usually an event in the logs, not a fault.

Use when a legacy Apple laptop (2012-era MacBookPro10,1 and siblings) running
Linux reports: load average ~1000, "graphics slow", shutdown/reboot hangs,
4K stuck at 30Hz, or a GPU-driver swap gone wrong. Proven live 2026-08-31 on
the fleet's 2012 15" retina (MacBookPro10,1). Overlaps `linux-performance-diagnostics`
(which covers general perf diagnosis); this skill is the Apple-hardware-specific
playbook.

## 1. Identify the machine — don't trust the seller

```bash
cat /sys/class/dmi/id/product_name      # MacBookPro10,1 = June-2012 15" retina
cat /sys/class/dmi/id/bios_date         # a 2022 date = EFI *update* date, NOT manufacture
lscpu | grep 'Model name'               # i7-3720QM (Ivy Bridge) = 2012 silicon
lsblk -o NAME,SIZE,MODEL                # SATA SSD only → 2014/15 MBPs have NO SATA
```

- 2014/2015 15" models are `MacBookPro11,4/11,5` (Haswell i7-4870HQ + GT 750M/R9,
  PCIe SSD) — a SATA-only machine cannot be one.
- **Apple serial decode** (old 11-12 char format): char 4 = year letter
  (`H/J`=2012, `K/L`=2013, `M/N`=2014, `P/Q`=2015, H1/H2 halves); chars 5-6 = week.
  `C02Jxxxxxx` → Foxconn, 2012H2. Root-gated in sysfs; read from the bottom case
  or `sudo dmidecode -s system-serial`.
- Sellers routinely misread the 2022-era EFI firmware build as the manufacture date.

## 2. GPU wedge / load storm

Signature: `cat /proc/loadavg` ≈ 1000 with ~3 running; `ps -eo stat | sort | uniq -c`
shows 1000+ `D` (all kworkers on CPU0); `dmesg | grep ttm_validate` shows
`nouveau ... validate: -12`; shutdown log shows `drm_fb_helper_damage_work hogged
CPU >10000us`.

Root cause: **nouveau on GK107 with 983MB VRAM** driving a hi-res multi-monitor
desktop + Chromium apps → TTM memory manager wedges → kworker storm. Only a
reboot clears it.

Map outputs to GPUs first (decides driver-swap safety):
`readlink -f /sys/class/drm/card*-*/device` — on this MBP all real outputs are
NVIDIA (01:00.0); Intel (00:02.0) has only a phantom VGA.

## 3. Reboot hang (kernel never powers off)

systemd reaches `reboot.target` / `Sending SIGTERM`, journal stops, machine sits.
Two causes: the GPU wedge at shutdown + **missing the Mac reboot quirk** — Apple
EFI needs the PCI reboot method:

```bash
sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash reboot=pci"/' /etc/default/grub
sudo update-grub
```
Fallback `reboot=kbd` if it still hangs. Verify: `grep reboot /etc/default/grub`.

## 4. Display: 4K stuck at 30Hz

GT 650M HDMI is **1.4** → 4K caps at 30Hz. Monitor EDID offers
`2560x1440 @ 59.95` — switch for 2× smoothness:
`DISPLAY=:0 xrandr --output HDMI-1 --mode 2560x1440 --rate 59.95`
Persist via `~/.config/autostart/<name>.desktop`. 4K@60 needs a DP cable.

If the display is UNUSED (SSH-only host), prefer 1080p to cut GPU heat: set
`1920x1080@60` in **all three layers** — `~/.xprofile` (session),
`~/.config/autostart/*.desktop` (session), and
`/etc/lightdm/lightdm.conf.d/*.conf` with
`display-setup-script=/usr/bin/xrandr --output HDMI-1 --mode 1920x1080 --rate 60`
(greeter). `xrandr` against the live greeter fails from SSH (root X cookie in
`/var/run/lightdm/root/:0`, unreadable to the agent) — user runs the sudo
`install` for the lightdm conf; it applies at next greeter start.

## 5. Thermal: dried-paste signature + bounded load test

Idle package 63-67 °C with the heatsink sensor (TC0F) at 65-74 °C at load <1 is
the dried-paste signature — a healthy chassis reads nearer 50 °C — and the fans
typically will NOT be off their floor. `intel_powerclamp` injecting idle on top
of that means the kernel is throttling before any real work arrives. Confirm it
with a bounded load test; never infer it from a single reading.

**Run `scripts/thermal-load-probe.sh`** (packaged with this skill) rather than
hand-typing the loop: it samples fans / package / heatsink / load / core freq /
CPU PSI across baseline → load → recovery and tees a transcript you can commit
as evidence. Rules that make the run safe and its result mean something:

- **Derive the abort threshold from the hardware first.** Read `temp1_crit`
  (105 °C on MBP10,1) and abort below it — the script defaults to crit − 6 °C.
  An unbounded CPU load on a 2012 laptop can reach thermal shutdown and take the
  host off the fleet mid-test.
- **The harness refuses a foreground `&`** — start the probe with
  `terminal(background=true, notify=true)`, then wait for the `thermal-probe end`
  marker in a second, bounded foreground call and `cat` the artifact. Do not
  hand-poll the sensors and call the result a curve; samples taken seconds apart
  prove nothing.
- **Read the baseline before the peak.** If the "baseline" sample is already hot
  (92 °C with the kernel already injecting idle is a real case), the run measures
  the thermal CEILING, not a cold-start ramp — say which one it is instead of
  presenting the peak as a rise from rest.
- **Killing the workers is part of the procedure** — the recovery curve is the
  evidence that the cooling loop still modulates, so sample it too.
- **Fan health is the MOVEMENT, not the number.** Both fans tracking each other
  and each reaching its own max is a healthy loop; a fan stuck at 0, or one
  pinned at max while the other sits at idle, is the hardware fault.

Measured on MBP10,1: 8× `yes` for 120 s pinned fan1 at 5940/5940 and fan2 at
~5499/5499 within 15 s, package plateaued 87-92 °C, and core frequency held
~3.4 GHz — the throttle is `intel_powerclamp` idle injection, NOT a clock drop,
so do not look for falling MHz as proof of throttling. Fans at 100% to hold
90 °C is also exactly the "crazy noise" an operator reports (§5b). Recovery:
fans 5941 → ~4300 and package 92 → 70 °C in ~45 s, back to fan minimum and
67 °C in ~7 min. Needing full fan for 90 °C at that load, on top of a hot idle,
is the re-paste call (pentalobe bottom case, dust fins, fresh paste on CPU+GPU).

**Fan RPM has two possible sources — check both.** `sensors` lists fans only
while applesmc has a hwmon device registered; on kernels where that
registration is deprecated it prints temps and NO fan lines. The fans are always
readable on the platform device: `cat /sys/devices/platform/applesmc.768/fan1_input`
(plus `fan2_input`, `fan1_min`, `fan1_max` — fan1 2160-5940, fan2 2000-5499 on
this host). Never report "fans healthy" off a `sensors` run that showed no fan lines.
**Temps and thresholds follow the same rule — read the nodes, not `sensors`:**
heatsink / CPU-proximity is `.../temp5_input` (TC0F), the package is
`/sys/class/hwmon/hwmon1/temp1_input` (coretemp) with `temp1_max` and `temp1_crit`
beside it for the limits. `fan*_safe` returns "Invalid argument" and is NOT a
readable limit; `fan*_output` holds the last manually written value, not the live
speed — a probe that reports either invents a number.

## 5b. Strange-noise / loud-fan triage — answer from logs before hardware

When the operator says the machine made a strange noise, the answer is almost
never an alarm: establish WHICH event the noise belongs to, then read the fans.

1. **Same boot?** `uptime` + `journalctl --list-boots --no-pager`. A boot
   boundary inside the reported window means the noise was the BOOT — a cold
   boot drives both fans toward max while journald recovers and the container +
   cron stack starts (Docker, clickhouse, minio, ollama and the cron-bridge
   services pegged this host for ~10 minutes after a cold start).
2. **Read the END of the previous boot:** `journalctl -b -1 -k --no-pager -n 40`.
   BOUND it — a bare `journalctl -b -1` dump exceeded a 180s timeout; use `-k`,
   `-n N`, or `--since/--until` windows instead.
3. **Hang-in-suspend vs clean shutdown** — both look like "it was off":
   - `systemd-logind: Suspend key pressed short` then `PM: suspend entry (deep)`
     as the FINAL journal line = suspended on the sleep key;
   - the next boot reporting `system.journal corrupted or uncleanly shut down,
     renaming and replacing` = the power was CUT, never resumed.
   A journal that stops at suspend entry with no resume line = hung suspend,
   then hard power-off. On a fleet host that is a silent outage: state the
   wall-clock gap and name the crons/backups that fell inside it — a standby
   host that drops out of the fleet unattended is the finding, not the noise.
4. **Sample the fans twice, a minute apart, with `cat /proc/loadavg` beside
   them.** A ramp while load is FLAT is the fan fighting a bad thermal path
   (dried paste, §5), not load. TC0F (`.../temp5_input`) is the CPU/GPU heatsink
   proximity sensor; ~74 °C at load ~1.7 is the dried-paste signature.
5. **Rule out audio before blaming hardware** —
   `cat /proc/asound/card0/pcm0p/sub0/status` (`closed` = nothing playing),
   `pgrep -a orca`, `pgrep -a speech-dispatcher` (a stray screen-reader toggle
   is the "machine started talking" case, §7). Closed ALSA + empty pgrep output
   removes software audio as the source and lets you say so.
6. **Report the mechanism, not just a number:** fans at N of max RPM with load
   flat and heatsink ~T °C, plus the boot/suspend event that started it. Offer
   the next step (re-paste, or disabling suspend so the host cannot drop out)
   instead of stopping at the reading.

## 6. nvidia proprietary driver — the 470/535/580 shim chain (Ubuntu 24.04)

Kepler (GK107) needs the **470** branch; 535/580 modules do not initialize it.
Ubuntu 24.04 gotcha: `nvidia-driver-470` metapackage `Depends: nvidia-driver-535`
→ `Depends: nvidia-driver-580` — it's a **userspace shim**, not the Kepler
module. The real module ships only in **`nvidia-dkms-470`** (which depends on
`nvidia-dkms-535` as a base; both build, 470 wins by install order). Driver-version
libs co-install; an alternatives mechanism selects the active one.

- Before ANY reboot with an nvidia stack installed: **check `dkms status`** —
  expect `nvidia/470.256.02 ... installed`. A 580 module + the nouveau blacklist
  already in initramfs = black-screen risk on next boot.
- Defuse a wrong 580 install: purge 580/535/470 metapackages, `sudo dkms remove
  nvidia/580.173.02 --all`, `update-initramfs -u`, then install
  `nvidia-driver-470 nvidia-dkms-470` + select 470 via alternatives — only while
  physically at the machine (display recovery may be needed).
- Safe fallback that already covers the reboot hang: stay on nouveau + `reboot=pci`.

## 7. Locked-screen desktop automation (computer-use interplay)

When the desktop session is locked (screensaver): `computer_use` captures still
render (compositor) but injected input goes to the lock screen's grab — clicks
"succeed" with no effect. Detect via `cinnamon-screensaver-command -t` or a
full-screen capture showing the lock UI. Never bypass the lock; instead:
- Use the app's own side-channel — e.g. a login QR the OWNER scans with their
  phone: capture the app window, locate the QR by white-pixel density scan,
  crop, upscale 3×, deliver via `MEDIA:`. (QR codes expire ~60s; re-capture.)
- Or ask the user to unlock.
- A click aimed at the app while locked can land on the lock screen's
  accessibility icon and toggle **Orca screen reader** ON — if the machine
  "starts talking": kill `orca` + `speech-dispatcher` and clear
  `org.gnome.desktop.a11y.applications screen-reader-enabled` + the Cinnamon key.

## 9. Display blanking (login screen): gmux backlight + setpci bridge fix

MacBookPro10,1: the internal LCD backlight is routed through Apple's gmux
controller. On kernel 6.x the `apple_gmux` driver exposes a sysfs interface at
`/sys/class/backlight/gmux_backlight/` but the hardware does NOT respond to
it — writes to `bl_power` and `brightness` return 0/succeed silently but the
backlight stays on 100%. `xset dpms force off` also succeeds but has no effect
on the internal display (external HDMI monitors are unaffected).

### Root cause

The gmux registers are IO-port-mapped (0x700-0x7fe) behind a PCI bridge that
isn't configured to decode them. Writing `BRIDGE_CONTROL` register clears the
bridge's I/O decode restriction:

```bash
sudo setpci -H1 -s 00:01.00 BRIDGE_CONTROL=0
```

After this one command, the gmux responds to both sysfs and X DPMS commands.

### Verify

```bash
# After fix: sysfs actually turns backlight off/on
echo 4 | sudo tee /sys/class/backlight/gmux_backlight/bl_power   # off
echo 0 | sudo tee /sys/class/backlight/gmux_backlight/bl_power   # on
```

**Confirm the off state with `actual_brightness`, not `bl_power` or
`brightness`** — `brightness` keeps the user's set value (e.g. 860/1023) after
power-off and `bl_power=4` alone just echoes the write back;
`cat .../actual_brightness` dropping to 0 is the only sysfs proof the hardware
accepted it (and per the NOT-do list, pair it with the user's visual
confirmation).

**No wattage instrument on this machine** — AC-powered, no `power_now` in
sysfs, battery current reads 0A. Judge power changes by package temp + fan
RPM deltas, taken at matched load (`uptime` before/after), and expect 10-15
min of settling before a before/after comparison means anything; a single
transient spike (e.g. +10°C right after a change with flat CPU load) is not
evidence the change caused it.

**Unused display still costs power while 'black'** — an enabled output with
backlight at 0 still keeps the GPU in D0/active (scanout) and out of low-power
states; the greeter (slick-greeter + Xorg) rendering it accounts for ~1% CPU.
Full savings need the output off or the greeter stopped — weigh that against
losing the local login path (SSH stays as recovery; a plugged-in HDMI monitor
works regardless of eDP-1 state because HDMI is the NVIDIA GPU's output and
eDP-1 the Intel's).

### Making it permanent at the login screen

Add to the LightDM `greeter-setup-script` (runs as root before greeter starts):

```bash
# /usr/local/bin/lightdm-screen-blank (called via greeter-setup-script in lightdm.conf.d)
setpci -H1 -s 00:01.00 BRIDGE_CONTROL=0 >/dev/null 2>&1
sleep 2
xset s blank
xset s 30
xset dpms 30 30 30
exit 0
```

Then reference it in `/etc/lightdm/lightdm.conf.d/70-screen-off.conf`:
```
[Seat:*]
greeter-setup-script=/usr/local/bin/lightdm-screen-blank
```

**Critical:** suppress ALL output from `setpci` with `>/dev/null 2>&1` — any
stdout/stderr from the greeter-setup-script causes the greeter to fail.
Also avoid the `-v` (verbose) flag for the same reason.

### Making it permanent system-wide

Add to `/etc/rc.local` or a oneshot systemd service:

```
setpci -H1 -s 00:01.00 BRIDGE_CONTROL=0
```

### Revert

Reboot clears the bridge register — the setting is runtime only. No persistent
side effects.

### What NOT to do

- **Do not** install polling loops (`xprintidle`), background daemons, or
  `brightnessctl`/`vbetool` to work around this. The bridge fix is the correct
  hardware-level solution.
- **Do not** add kernel boot parameters (`acpi_backlight=native/vendor`,
  `apple_gmux.backlight=0`, `modprobe.blacklist=apple_gmux`) — these make the
  backlight interface disappear entirely or break GPU switching without fixing
  anything.
- **Do not** write scripts that `sleep 30; echo 4 > bl_power` — the backlight
  won't respond without the bridge fix regardless of approach.
- **Do not** claim the fix works until the user has visually confirmed the
  backlight turns off. The driver reports success silently even when the
  hardware ignores the command.
- **Do not** use `setpci -v` (verbose flag) in a `greeter-setup-script` --
  any output from the script causes the greeter to fail. Always redirect
  all output: `>/dev/null 2>&1`.
- **Do not** use `disown` to background a process from a `greeter-setup-script` --
  `greeter-setup-script` runs under `/bin/sh` (dash on Debian/Ubuntu), and
  `disown` is a bash builtin. Use `setsid` instead.

### References

- `references/gmux-backlight-macbookpro10-1.md` — full session transcript,
  error messages, and the debugging path that led to this fix.
- Arch Wiki: Mac/Troubleshooting, Backlight
- Launchpad bug #1243897

---

## 8. Trackpad: bcm5974 "urb failed: -19" drop-outs

Signature: `dmesg` shows `bcm5974 ... trackpad urb failed: -19` + `could not
read from device`, trackpad re-enumerates (`input50/event11`), cursor freezes
briefly. Root cause: **USB autosuspend on the PARENT HUBS**, not the trackpad —
the device itself is already `power/control=on`; the Intel `8087:0024` and
Microchip `0424:2512` hubs on `auto` suspend/resume and the driver's wake
URB fails (-19).

Fix (persistent, needs sudo): a udev rule pinning the trackpad AND both parent
hubs to `control=on`, keyed on **vendor:product**, never the sysfs path. After
installing rules for a NEW device class, verify the rule actually fires with
`udevadm test` (or `udevadm info -q property`) before claiming the fix — a
rule that doesn't match silently leaves autosuspend on and the symptom returns.

```
# /etc/udev/rules.d/99-trackpad-no-autosuspend.rules
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", ATTR{idProduct}=="0262", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0424", ATTR{idProduct}=="2512", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="8087", ATTR{idProduct}=="0024", ATTR{power/control}="on"
```

- Verify with `udevadm test /sys/bus/usb/devices/<path>` — it prints the
  applied rules; a 99- rule OVERRIDES the distro's `60-autosuspend.rules`.
- **`udevadm trigger` fires `change` events — `ACTION=="add"` rules do NOT
  re-run.** Apply live via `sudo udevadm trigger --action=add --subsystem-match=usb`,
  or just reboot (rules apply at enumeration).
- **Vendor:product matching survives bus renumbering.** After reboot the
  trackpad moved `4-1.8.2` → `2-1.8.2` (bus number changed); the rule still
  matched. Never hardcode the `4-1.8.2`-style path in the rule.
- Boot-time `-19`/`mode switch failed` bursts at enumeration are benign probe
  noise (device registers right after); the harmful pattern is REPEATED
  drop-outs + re-enumeration cycles during suspend/resume.
- If errors recur even with hubs pinned: worn trackpad ribbon cable — hardware
  reseat/replace, nothing software fixes.

## Verification

After any change: `xrandr --current` (mode/rate), `dkms status` (module version),
`sensors | grep Package` (idle temp — but read fan RPM from
`/sys/devices/platform/applesmc.768/fan*_input`, because `sensors` may list no
fan lines at all, §5b), a clean reboot (with `reboot=pci` present in
`/etc/default/grub`).
