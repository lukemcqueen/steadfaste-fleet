"""fleet_doctor.task_integrity — the two ways task work goes invisible.

Both were found in the wild on 2026-10-04, each having hidden real work for weeks
while every board looked healthy:

1. SCHEMA DRIFT. The migration runner only sees migrations that are DEPLOYED, and a
   migration is only deployed if it is in `fleet-update.sh`'s register map (the
   register map IS the deploy). `v009`/`v010` — the entire v3 task model — were never
   registered, so the deployed schema dir stopped at `v008` and no host could ever
   apply them. A repo migration that is not registered is silent, permanent drift.

2. STRANDED SLICES. A slice that is `pending` WITH an assignee is excluded from the
   claim pool (`assignee IS NULL`) and is not `in_progress`, so no worker can take it
   and nobody is working it. Six sat like that for six weeks while the board counted
   them as ordinary pending work (18 shown, 9 actually claimable).

Both checks are read-only and lock-free. Where a check cannot verify, it says so
rather than passing silently — a check that cannot look must not read as "fine".
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure ops/scripts (parent-of-parent) is importable so fleet_lib resolves —
# task_integrity is imported by the doctor CLI's subprocess and by hermetic
# tests that import the package; relying on config.py's side-effect sys.path
# insert (which happens to run first) is fragile.
_scripts_dir = Path(__file__).resolve().parents[2]
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from fleet_lib import identity as _identity

_SCHEMA_GLOB = "v0*.sql"

# A task row starts with the task id: the full uuid (`f8988944-ef87-…`) or the
# 8-char prefix some views echo (`f8988944 `). Advisory lines name the tool that
# produced them instead, so they never match. (Both shapes measured from real
# `task-db list` output — the prefix-only pattern matched NEITHER and turned
# every row-bearing verdict into "COULD NOT VERIFY".)
_ROW_ID = re.compile(r"^[0-9a-f]{8}(?:-|\s)")


def _is_orchestrator() -> bool:
    """Host-derived role, the fleet authority — NEVER trusts env.

    Same rule as fleet_doctor.config.AGENT_ROLE and fleet_lib.identity. The
    probe uses the fleet view (`--repo ALL`) ONLY for an orchestrator so it
    can see stranded slices on every host; a worker keeps its repo-scoped view
    (it has no right to fleet-wide visibility). The old env-first chain here let
    AGENT_TYPE/IS_ORCHESTRATOR widen this probe to fleet scope on a worker host;
    the canonical rule (fleet_lib.identity) refuses env, which is the fix.
    """
    return _identity.is_orchestrator()


def _repo() -> Path:
    return Path(os.environ.get("FLEET_REPO", Path.home() / "steadfaste-fleet"))


def _deploy() -> Path:
    return Path(os.environ.get("FLEET_DEPLOY_HOME", Path.home() / ".steadfaste-fleet"))


def _registered_schema() -> set:
    """Basenames of schema migrations that fleet-update.sh will actually deploy."""
    update = _repo() / "ops" / "scripts" / "fleet-update.sh"
    try:
        text = update.read_text()
    except OSError:
        return set()
    out = set()
    for line in text.splitlines():
        if line.strip().startswith("register") and "tasks/schema/" in line:
            out.add(Path(line.split('"')[1]).name)
    return out


def _run(cmd: list, timeout: int = 25, cwd=None) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def check_task_model_integrity(res: "Results") -> None:            # noqa: F821
    """1d. Task model: migrations actually deploy, and no slice is stranded."""
    schema_dir = _repo() / "ops" / "services" / "tasks" / "schema"
    if not schema_dir.is_dir():
        return

    local = sorted(p.name for p in schema_dir.glob(_SCHEMA_GLOB))
    if not local:
        return

    # ── (1) every migration must be registered, or it never reaches a host ──
    registered = _registered_schema()
    unregistered = [n for n in local if n not in registered]
    if unregistered:
        res.add(
            "Task schema deploy",
            "FAIL",
            f"{len(unregistered)} migration(s) in the repo are NOT in the deploy "
            f"register, so the version-gated runner can never apply them on a host: "
            f"{', '.join(unregistered[:4])}",
            "Add a register() line per migration to ops/scripts/fleet-update.sh "
            "(the register map IS the deploy)",
        )
    else:
        res.add("Task schema deploy", "PASS",
                f"all {len(local)} task migrations are in the deploy register")

    # ── (1b) the DEPLOYED copy must also carry them ──
    deployed_dir = _deploy() / "services" / "tasks" / "schema"
    if deployed_dir.is_dir():
        deployed = {p.name for p in deployed_dir.glob(_SCHEMA_GLOB)}
        missing = [n for n in local if n not in deployed]
        if missing:
            res.add(
                "Task schema deployed", "WARN",
                f"{len(missing)} registered migration(s) are not on the deployed "
                f"tree: {', '.join(missing[:4])}",
                "Run: bash ops/scripts/fleet-update.sh",
            )
        else:
            res.add("Task schema deployed", "PASS",
                    f"all {len(local)} migrations present on the deployed tree")

    # ── (1c) is the DB itself behind the repo? ──
    migrator = _deploy() / "services" / "tasks" / "migrate.py"
    runner = _deploy() / "scripts" / "task-db.py"
    if migrator.is_file() and runner.is_file():
        raw = _run(["python3", str(runner), "--apply-schema", "--dry-run"])
        if not raw.strip():
            res.add("Task schema version", "WARN",
                    "COULD NOT VERIFY: the migration runner produced no output",
                    "Check psql/docker access, then re-run the doctor")
        else:
            pending = len(re.findall(r"pending|would apply|applying", raw, re.I))
            m = re.search(r"current=(\d+)", raw)
            if m and pending:
                res.add("Task schema version", "WARN",
                        f"the DB is at version {m.group(1)} but the repo has "
                        f"unapplied migration(s)",
                        "Run: python3 ~/.steadfaste-fleet/scripts/task-db.py --apply-schema")
            elif m:
                res.add("Task schema version", "PASS",
                        f"DB at version {m.group(1)}, no pending migrations")
            else:
                res.add("Task schema version", "PASS", raw.strip().splitlines()[-1][:70])

    # ── (2) no slice may be pending-with-an-assignee (unclaimable AND unwatched) ──
    # `list --assigned` is repo-scoped and answers "repo unknown" when it cannot
    # resolve one — which is what it did on EVERY doctor run, because the probe
    # left the resolution to the invoker's cwd. Pass the repo root as the
    # subprocess cwd so the check can actually look, and read any advisory as
    # "could not verify" instead of letting its text count as a task row.
    if runner.is_file():
        # An orchestrator sees the whole fleet (--repo ALL); a worker only its
        # own repo (resolved from the repo root the probe passes as cwd — the
        # verdict must never depend on the invoker's cwd).
        cmd = ["python3", str(runner), "list", "--assigned"]
        if _is_orchestrator():
            cmd += ["--repo", "ALL"]
        raw = _run(cmd, cwd=str(_repo()))
        if not raw.strip():
            res.add("Stranded slices", "WARN",
                    "COULD NOT VERIFY: task-db list --assigned returned nothing",
                    "Check psql/docker access, then re-run the doctor")
        elif "repo unknown" in raw:
            res.add("Stranded slices", "WARN",
                    "COULD NOT VERIFY: task-db could not resolve a repo, so the "
                    "assigned-and-unstarted slice list was never checked (the "
                    "verdict would otherwise depend on the invoker's cwd)",
                    "Run the doctor from a repo working copy, or set "
                    "FLEET_SESSION_REPO; then re-run the doctor")
        elif "(no unstarted hand-offs" in raw:
            res.add("Stranded slices", "PASS", "no pending slice is assigned-and-unstarted")
        else:
            rows = [ln for ln in raw.splitlines() if _ROW_ID.match(ln)]
            if not rows:
                res.add("Stranded slices", "WARN",
                        "COULD NOT VERIFY: task-db printed neither rows nor a "
                        "no-hand-offs marker — its output format may have changed",
                        f"Inspect: python3 {runner} list --assigned")
            else:
                res.add(
                    "Stranded slices", "WARN",
                    f"{len(rows)} slice(s) are pending WITH an assignee: unclaimable "
                    f"(the pool needs assignee IS NULL) and not in_progress, so nothing "
                    f"is working them",
                    "Release them to the pool: task-db.py unclaim <id> --reason '<why>' "
                    "(or list them with: task-db.py list --assigned)",
                )


def _probe_default_scope(task_db_py: Path) -> tuple[str, int]:
    """Run the deployed engine's DEFAULT list with every scope input REMOVED.

    The probe's own environment is part of the probe: FLEET_SESSION_REPO is
    stripped and the cwd is a fresh empty directory, so the only legal answer is
    "no tasks" — an engine that answers with rows is serving the fleet view as
    its default. (Input shape checked against task-db's own resolver ladder:
    explicit arg -> injected -> env -> cwd -> UNKNOWN.)
    """
    env = {k: v for k, v in os.environ.items() if k != "FLEET_SESSION_REPO"}
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(["python3", str(task_db_py), "list"],
                               capture_output=True, text=True, timeout=30,
                               cwd=td, env=env)
            return ((p.stdout or "") + (p.stderr or "")).strip(), p.returncode
    except (OSError, subprocess.SubprocessError):
        return "", 3


def check_task_query_scoping(res: "Results") -> None:               # noqa: F821
    """A task query defaults to the CALLER's repo — never the whole host.

    Operator rule (2026-10-14, restated 2026-11-23 after Titus reported that a Pi
    session's task view showed every repo's tasks): "If the repo is unknown …
    NO tasks should be returned", and the fleet-wide view is a SPECIAL REQUEST.
    This drives the deployed engine's own default query and FAILS when it comes
    back with rows — a host whose engine predates repo scoping passes every
    static check while serving another repo's work as the agent's own.
    """
    runner = _deploy() / "scripts" / "task-db.py"
    if not runner.is_file():
        res.add("Task query scoping", "WARN",
                "COULD NOT VERIFY: the deployed task-db.py is not on this host",
                "Run: bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh")
        return
    raw, rc = _probe_default_scope(runner)
    if not raw.strip():
        res.add("Task query scoping", "WARN",
                "COULD NOT VERIFY: the default task query produced no output",
                "Check psql/docker access, then re-run the doctor")
        return
    rows = [ln for ln in raw.splitlines() if _ROW_ID.match(ln)]
    if rows:
        res.add("Task query scoping", "FAIL",
                f"the DEFAULT task query returned {len(rows)} row(s) with no repo "
                f"resolvable — this engine serves every repo's tasks as the caller's "
                f"own (the defect Titus reported)",
                "Update the deployed engine: bash ~/steadfaste-fleet/ops/scripts/"
                "fleet-update.sh (and register the DEPLOYED task-mcp.py, never the "
                "repo working tree — the doctor's MCP wiring row says which)")
    elif "No tasks found" in raw or "repo unknown" in raw:
        res.add("Task query scoping", "PASS",
                "an unresolvable repo returns NO tasks (default = caller's repo; "
                "the fleet-wide view is a special request)")
    else:
        res.add("Task query scoping", "WARN",
                "COULD NOT VERIFY: the default task query printed neither rows nor "
                f"the no-tasks marker (rc={rc}) — its output format may have changed",
                f"Inspect: python3 {runner} list")
