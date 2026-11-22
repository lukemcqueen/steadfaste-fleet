# Tiered schedule pattern — one job, different cadences by time of day

A single cron can only express one cadence. When the work has genuinely different
urgency at different times, splitting into time-bounded jobs beats one compromise
schedule — a job that must be responsive during the workday but is wasted overnight
trades one half's freshness for the other half's cost.

## When to split

- The high-frequency need is bounded to a window (workday, business hours, an
  incident window), not the whole day.
- The low-frequency pass does materially MORE work than the frequent one (a full
  deep pass vs a cheap delta check), so running the heavy pass at the fast cadence
  is pure cost.
- A watchdog must be tight while something is in flight and idle otherwise.

Do NOT split when the two tiers would share a prompt or a script and differ only in
schedule: that is one job with a compromise cadence, and the extra jobs add rename,
installer and manifest surface for no behavioural gain.

## Shape

Name each job for its window so the schedule is readable from the name — a
`-workday` / `-evening` / `-weekend` suffix states the cadence at the call site.
Give each its own schedule entry, installer `create_cron` block and manifest entry,
as with any other cron.

## Pitfalls

- **A cron's NAME and PROMPT encode its intended cadence — changing frequency
  without updating both is a design statement being broken.** A name reading
  `-nightly` on a three-times-a-week schedule, or a prompt saying "if today is
  Monday, run the full deep pass" on a weekly-only schedule, loses the passes the
  original design intended. Re-cadencing means rewriting the name and the prompt to
  describe the NEW schedule, or keeping the original cadence.
- **The per-host minute stagger applies PER JOB, not per family.** Each tier hashes
  its own name, so tiers land on different minutes even when they share an hour —
  that is correct, and it is not drift against a sibling.
- **Check the pause reason before deleting a tier's sibling.** A consolidation that
  pauses the carrier while retiring the other tiers leaves a reason that expires the
  moment those tiers are gone; resume the carrier in the SAME change that removes
  them, or the function silently stops.
- **Tiering multiplies the surfaces that drift independently.** Live job, installer
  `create_cron` block and manifest entry are three writers per tier; a re-cadence of
  a tier means all three move together.
