# Cron expr → systemd OnCalendar translation

Converting a 5-field cron expression to a systemd `OnCalendar=` value for a user timer unit. **Always validate the generated value** with `systemd-analyze calendar '<expr>'` (assert rc=0 and `Normalized form:` in stdout, and READ the `Next elapse:` line) BEFORE shipping — a step-field typo passes validation yet fires at the wrong cadence.

## Syntax map (verified on systemd-analyze)

| Cron expr | Meaning | Correct OnCalendar |
|---|---|---|
| `* * * * *` | every minute | `*-*-* *:*:00` |
| `*/N * * * *` | every N minutes | `*-*-* *:0/N:00` |
| `0 */N * * *` / `* */N * * *` | every N hours at :00 | `*-*-* 00/N:00:00` |
| `M * * * *` / `0 * * * *` | every hour at minute M | `*-*-* *:MM:00` |
| `M H * * *` | daily at H:MM | `*-*-* HH:MM:00` |
| `M H1,H2 * * *` | daily at listed hours | `*-*-* HH1,HH2:MM:00` |
| `M H1-H2 * * *` | daily each hour in range | `*-*-* H1..H2:MM:00` |
| `M H * * 1-5` | weekdays | `Mon..Fri *-*-* HH:MM:00` |
| `every 5m`/`10m` | minute interval (60%N==0) | `*-*-* *:0/N:00` |
| `every 2h` / `every 360m` | hour interval | `*-*-* 00/N:00:00` |

## Rules that cost a mistake

- **The `/N` step belongs on the field it steps.** An hour interval puts `/N` on the HOUR field: **`00/N:00:00`** (e.g. `00/6:00:00` = hours 0,6,12,18). A minute interval puts it on the MINUTE field: **`*:0/N:00`** (e.g. `*:0/6:00` = minutes 0,6,12…54).
- **`*:00/N:00` is a MINUTE step, NOT an hour step.** Putting `/N` in the minute position (whatever digit precedes it) fires every N MINUTES on the `:00` phase — so a "every 6 hours" job written as `*:00/6:00` runs every 6 minutes. This is the silent-cadence bug: `systemd-analyze calendar` ACCEPTS the value (rc=0, normalized), and only the `Next elapse:` line (now +N **minutes**, not hours) reveals it. A "nonstop" watchdog / alert that arrives every few minutes when the schedule says every N hours is this bug; check `systemctl --user cat cortex-bridge-<job>.timer` for `*:00/N:00`.
- **Ranges use `..`** (`09..18`), lists use `,` (`11,17`); zero-pad hours (`09` not `9`).
- **Day-of-week**: 0/7=Sun…6=Sat; `1-5` → `Mon..Fri`, hour lists zero-pad.
- **Only map what you're sure of** — non-dividing minute intervals (e.g. `70m`) refuse rather than under-fire.
- **A step-field typo passes the generator silently** — validation must read `Next elapse:` and assert the cadence (hours vs minutes), not just that the value normalizes.

## The validator snippet (cadence-aware)

```python
import subprocess
ok = bad = 0
for v in sorted(values):
    r = subprocess.run(['systemd-analyze','calendar',v],capture_output=True,text=True)
    if r.returncode==0 and 'Normalized form:' in r.stdout: ok += 1
    else: bad += 1
print(f'valid {ok}/{len(values)}, rejected {bad}')
# sanity: every value intended to fire 'every N hours' must show 'Next elapse' ~N hours away.
```

Also run `systemd-analyze verify <job>.service <job>.timer` for unit-level correctness, and confirm `systemctl --user list-timers` shows the unit active with the expected next-elapse (**read the cadence**: minutes-away for an hourly job is the defect).
