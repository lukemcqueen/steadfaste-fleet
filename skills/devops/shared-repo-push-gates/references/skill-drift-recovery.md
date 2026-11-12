# Skill-drift recovery — the two classes and how to land each

The doctor's `Skill drift` warnings and the parity checker
(`check-skill-drift-parity.py`) measure DIFFERENT surfaces. Identify which
class is firing before touching anything — they need opposite treatment.

## Class 1: stranded SKILL.md supersets (doctor `Skill drift: <cat>/<name>`)

The doctor's `check_skill_drift` scans `~/.hermes/skills/**/SKILL.md`
against the repo — SKILL.md files ONLY, never references/ or templates/.
Direction decides the fix:

- **deployed-newer** — the deployed copy holds lessons never committed:
  `python3 ops/scripts/manage/reconcile-stranded-skills.py --report`
  (classifies SUPERSET vs REVIEW), then `--apply` (applies ONLY pure
  supersets, +N/−0).
- **repo-newer** — a deploy is pending: run `fleet-update.sh`.

## Class 2: parity-absent reference/template files

`check-skill-drift-parity.py` also flags files the repo does not carry at
all while a repo-owned skill dir owns them. TWO sub-classes with OPPOSITE
treatment — read the report line to pick:

- **dropped-from-repo** — the path is in repo history, so the repo DELETED
  it deliberately (a rename sweep, a decommission). The deployed copy is
  the leftover. FIX = delete the deployed file
  (`~/.steadfaste-fleet/skills/<cat>/<name>/...`), never restore it to the
  repo. First grep the repo skills tree for the filename: a live
  SKILL.md reference means it is NOT dropped-from-repo, it is stranded
  content that must be committed.
- **never-committed** — a repo SKILL.md REFERENCES the file but it never
  reached git. FIX = `cp` deployed → repo (creating the target dir if the
  repo never had it) and commit.

Recovery ordering: fix BOTH classes, regenerate the parity table
(`check-skill-drift-parity.py` rewrites docs/evidence/skill-drift-parity.txt),
verify **Verdict PASS** (0 stranded / 0 absent), then commit everything in
ONE governed change (skills/ is an always-review path: adversarial gate +
close + receipt).

## The REVIEW (drop) class

`--apply` refuses anything that would REMOVE lines. Read the diff (repo vs
deployed) before deciding:

- The dropped lines are a **same-paragraph REWRITE** — the deployed side
  supersedes the old text in place (newer, more accurate). Take the
  deployed side wholesale: `cp` + prove with a diff that ONLY that
  paragraph changed, then commit.
- The drop genuinely removes information — merge the additions by hand,
  keeping the removed substance.

## The strand pipeline keeps writing

A background lesson/curation pipeline writes lessons into the DEPLOYED
copies while anyone works: a just-reconciled skill can drift again within
the same session, and fresh strands appear on OTHER skills the moment the
first batch lands (the deploy's `⚠ SKILL DRIFT: <skill>` line + the
parity checker's Stranded/absent counts are the tell). Treat re-appearance
as routine maintenance, not a failed recovery: reconcile, commit the new
supersets, regenerate the table, move on. The parity table regenerated
PASS is the verification, never a single clean reconcile.
