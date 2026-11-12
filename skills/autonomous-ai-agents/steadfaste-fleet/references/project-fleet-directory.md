<!-- renamed from skills/autonomous-ai-agents/steadfaste-fleet/references/project-cortex-directory.md in the cortex-to-fleet sweep; content predates the range (commit 353c3d54) -->
<!-- Renamed from skills/autonomous-ai-agents/hermes-cortex/references/project-fleet-directory.md in the 2026-11-12 hc→fleet brand sweep (commit 2dc01b0e); content predates the range. -->

# Project `.steadfaste-fleet/` Directory Convention

> **Read this first — two directories share the name, and they are NOT the same.**
>
> - `~/.steadfaste-fleet/` is the **runtime infra dir** (deployed by `fleet-update.sh`):
>   `scripts/`, `state/`, `sessions/`, `memory/`, `hooks/`, and the `skills.yaml` the
>   **enforcer** reads. Always write it with the `~/` anchor.
> - `.steadfaste-fleet/` (as below) is a **repo-local overlay inside a CONSUMER project**,
>   created by `seed-project.sh`.
>
> A bare `.steadfaste-fleet/` is never the runtime dir. Agents that conflate them go
> looking for the infra dir inside the project and report the overlay as "the Hermes
> agent infra directory". It is also why the Cortex repo itself must carry **no**
> repo-local `.steadfaste-fleet/` — inside `~/steadfaste-fleet` the name shadows the runtime
> dir (doctor check `Repo-local .steadfaste-fleet`, see `docs/review-receipt-gate.md`).

## Summary

Projects using Steadfaste Fleet can keep agent infrastructure in a hidden **repo-local** `.steadfaste-fleet/` directory at the repo root instead of cluttering the top level with `memory/`, `sessions/`, etc.

## Layout

```
project-root/
├── .gitignore                  # + .steadfaste-fleet/memory/
├── AGENTS.md                   # STAYS at root (tool convention)
├── docs/                       # STAYS at root (team docs)
├── scripts/                    # STAYS at root (project utilities)
│
└── .steadfaste-fleet/
    ├── sessions/
    │   ├── current.md          # Active session (cron updates this)
    │   └── archive/            # Timestamped session snapshots
    ├── memory/                 # Gitignored — per-user agent memory
    │   ├── MEMORY.md
    │   └── USER.md
    ├── skills/                 # Tracked — project-specific skills
    └── .gitkeep
```

## Git Tracking

| Path | Tracked? | Why |
|------|----------|-----|
| `.steadfaste-fleet/sessions/` | Yes | Session history is project narrative |
| `.steadfaste-fleet/skills/` | Yes | Project-specific skills are team assets |
| `.steadfaste-fleet/memory/` | No | Per-user agent memory (MEMORY.md/USER.md) |

**.gitignore:** `.steadfaste-fleet/memory/`

## Agent Discovery

Agents check for `.steadfaste-fleet/` first. If it exists, use it. If not, fall back to `memory/` and `project_current_session.md` at the repo root. Both layouts work.

## When to Use

The convention kicks in once a project has 3+ agent infrastructure files (`AGENTS.md`, `memory/`, `docs/`, `sessions/`, `scripts/`, etc.). For tiny projects (3-file utility scripts), skip it — keep everything at root until the clutter justifies the hidden directory.

## See Also

- steadfaste-fleet `docs/knowledge-isolation-architecture.md`
- Moses's original proposal commit: `5ddcca5`
