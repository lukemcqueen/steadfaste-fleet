# Stale home folders can hold LIVE container data — check bind-mounts before deleting

> **Audience: every agent on every host.** This is the runbook the doctor's
> `Stale pre-migration home folders` check does **not** give you, and its own
> suggestion (`rm -rf`) can destroy a live database. Read this before you delete
> anything under a pre-migration home path.

## The trap, in one line

`fleet-doctor.py` reports a pre-migration home folder as a **STALE path** and
suggests:

```
migrate-home-dirs.sh; then `rm -rf $HOME/.hermes-cortex`
```

A "stale" *path* does not mean dead *data*. A Docker **bind mount keeps working
after a rename**, so a directory the repo stopped referencing in the 2026-11-04
sweep can still be the live data directory of a running container. Deleting it
destroys a production database.

## Measured case (esther, 2026-11-24)

```
$HOME/.hermes-cortex/
└── victoria-data/        <- LIVE root-owned VictoriaMetrics TSDB
```

```
$ docker inspect victoria-metrics --format '{{json .Mounts}}'
[{"Type":"bind","Source":"$HOME/.hermes-cortex/victoria-data",
  "Destination":"/victoria-metrics-data","Mode":"","RW":true}]
```

`RW: true`. Last writes (`flock.lock`, `tmp/`) landed *days after* the path
stopped being referenced by the repo. A `rm -rf $HOME/.hermes-cortex` there
deletes the metrics history.

## The check to run FIRST — every time

Before removing any pre-migration path, ask the container runtime whether anything
still binds into it:

```bash
docker ps --format '{{.Names}}' | while read -r c; do
  docker inspect "$c" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
done | grep -F "$HOME/.hermes-cortex"
```

- **Empty output** → safe to migrate and delete.
- **Any line** → that path is LIVE. Do not delete it. Migrate it (below) or stop
  and escalate.

Do the same for `systemd` units (`grep -r <path> /etc/systemd /usr/lib/systemd`),
`crontab -l`, and `nginx` configs — a bind mount is the commonest case, not the
only one.

## Why `migrate-home-dirs.sh` cannot do this one

`ops/scripts/manage/migrate-home-dirs.sh` renames a whole pre-migration home dir
onto its steadfaste-fleet target and lays a transition symlink. It **refuses**
when the target already exists:

```
[ -e "$tgt" ] && fail "target $tgt already exists as a real dir; refusing to nest"
```

On a host where `$HOME/.steadfaste-fleet` already exists (it is the live state
dir), that guard fires and the script exits 1. Its own output is correct — nesting
one home dir inside another would be worse than not migrating. So this specific
shape (a *subtree* of the stale dir is live, and the target dir already exists)
needs a targeted move, not the whole-dir rename.

## The safe procedure — `migrate-victoria-data.sh`

`ops/scripts/manage/migrate-victoria-data.sh` (dry-run by default; `--apply` to
execute; needs root because the data is root-owned):

1. **Derives every container fact at run time** from `docker inspect` of the live
   container — image, restart policy, port bindings, env, entrypoint, args. Nothing
   is hardcoded, so it survives config drift.
2. **Stops and RENAMES** the old container to `<name>-rollback` instead of removing
   it — a complete rollback artefact.
3. **Moves** the data dir (atomic on one filesystem; never copy-then-delete).
4. **Recreates** the container bound to the new path, reusing the exact same args.
5. **Verifies** the container is running and the data dir is non-empty — *before*
   any cleanup. Any failure triggers `rollback()`: new container removed, old one
   renamed back and started, old data untouched, non-zero exit.
6. Only then removes the old dir and the now-empty stale parent.

Reversible at every step; the destructive step is last and conditional.

## ⚠️ Secret handling — the mistake that is easy to make here

`docker inspect` of a container prints its **full command line**, and container
args routinely carry credentials. On this host `victoria-metrics` runs with
`-httpAuth.password=…`. Writing raw `docker inspect` output into a repo evidence
file **publishes that credential on the next push**.

Rules that follow:

- **Never** redirect `docker inspect` (or `docker compose config`) into a
  repo-tracked path.
- If a container spec must be recorded, **redact it first** and verify the redaction
  landed (`grep -F 'password' <file>` must show only placeholders).
- Prefer deriving config in-process (as the script does) over persisting it.
- `docker inspect` is read-only, so it feels harmless. It is not.

Measured on esther 2026-11-24: a raw inspect dump into `docs/evidence/` contained
the metrics basic-auth password in *two* places (`Args` and `Cmd`). Caught and
redacted before commit. The same credential was also found sitting in `.env` and
**six `.env.*` backup copies** — gitignored, so not a push risk, but each copy is
another leak surface. Keep one `.env`; delete the backups.

## If you cannot get root

Do **not** work around a restricted sudo allowlist by using the docker group to
write root-owned files. `sudo -n -l` shows the real surface; a password-gated
`(ALL) ALL` plus a short NOPASSWD list is a deliberate access control, and
circumventing it is a governance violation, not resourcefulness.

Escalate instead: hand the operator the exact one-liner, or ask for a **scoped**
NOPASSWD rule for the migration script alone. The owner's call.

## Checklist for other agents

1. `docker ps … | grep -F <stale-path>` — is anything bound into it?
2. If yes: migrate with the script above; if no: `migrate-home-dirs.sh` is fine.
3. Redact any container spec before it reaches a repo-tracked file.
4. Never `rm -rf` a pre-migration home path you have not inspected for mounts.
5. If root is unavailable, escalate — never escalate yourself.
