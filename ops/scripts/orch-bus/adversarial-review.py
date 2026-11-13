#!/usr/bin/env python3
"""Independent adversarial review — assemble and run the reviewer prompt.

Story M6.3/M6.4/M6.5. This script is WORKER-INVOCABLE (deployed to the shared
path ~/.steadfaste-fleet/scripts/ for all agents, not orchestrator-only) — it is
what the plan-review-before-slice gate uses and any governed agent records a
plan review for their own cycle with it. It:

1. Reads the FIXED reviewer prompt from docs/templates/ (committed,
   orchestrator-only — never from worker-authored input).
2. Appends the reviewed material below the marker (as DATA).
3. --dry-run: emits the assembled prompt without calling a model (this is the
   test surface; the model call itself is exercised by the orchestrator cron
   with the real provider).
4. --sweep (cron mode): lists completed cycles with no review yet from the
   loop-governance DB, reviews each, records verdicts. Silent when the queue
   is empty (watchdog pattern).

Material comes from --material (inline) or --material-file <path> (read the
file; prefer this from a worker — avoids `$(cat ...)` metachar). Single-cycle
`--review` records one row keyed to --cycle-id and is the plan-review gate's
sanctioned writer; the enforcer treats that exact invocation as a sanctioned
lock-free write (same precedent as the brain-write carve-out), so a worker
with a pending cycle of their own can always satisfy the gate without a second
lock.

Independence properties asserted here:
- The prompt source is the committed template, not worker text.
- The reviewed material is appended BELOW the untrusted-data marker.
- Empty reviewed material is refused (nothing to review = no review).
- Independence comes from the fixed prompt + distinct reviewer model
  (deepseek-v4-pro, not the worker's own model) + recorded reviewer_id/model
  for audit — NOT from which actor receives the row.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# This script lives 3 levels below the repo root (ops/scripts/orch-bus/) —
# prefer git toplevel, fall back to the counted walk-up (the classic
# walk-up-depth bug: parents[2] from ops/scripts/orch-bus is ops/, not repo).
_SCRIPT_DIR = Path(__file__).resolve().parent
try:
    REPO = Path(
        subprocess.check_output(
            ["git", "-C", str(_SCRIPT_DIR), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    )
except Exception:  # noqa: BLE001 — git unavailable; fall back to walk-up
    REPO = _SCRIPT_DIR.parents[2]
TEMPLATE = REPO / "docs" / "templates" / "adversarial-reviewer-prompt.md"
# Deployed hosts have no git clone — the template is deployed alongside this
# script by fleet-update.sh. Prefer the deployed copy when present (it is the
# version the deploy put there); fall back to the repo copy when running from
# a source checkout (dev/test).
_DEPLOYED_TEMPLATE = _SCRIPT_DIR.parent / "templates" / "adversarial-reviewer-prompt.md"
if _DEPLOYED_TEMPLATE.is_file():
    TEMPLATE = _DEPLOYED_TEMPLATE
MARKER = "=== REVIEWED MATERIAL ==="
DEFAULT_DB = Path.home() / ".steadfaste-fleet" / "data" / "loop-governance.db"

# DRY reviewer model (2026-11-13): ONE canonical source in fleet_lib.models;
# resolve the fallback there instead of hardcoding the id in this file.
# Fail-closed: if fleet_lib is missing, REFUSE rather than silently revert to a
# different model — a reviewer that silently switches its judge is a gate that
# has quietly changed what it checks.
# Insert the PARENT directory that CONTAINS fleet_lib (import fleet_lib needs
# its parent on sys.path): sibling in deployed layout (scripts/fleet_lib beside
# scripts/adversarial-review.py), one level up in the repo
# (ops/scripts/orch-bus -> ops/scripts/fleet_lib).
for _feel in (str(_SCRIPT_DIR),
              str(_SCRIPT_DIR.parent)):
    if os.path.isdir(os.path.join(_feel, "fleet_lib")):
        sys.path.insert(0, _feel)
        break
from fleet_lib.models import get_reviewer_model  # noqa: E402


def assemble_prompt(material: str) -> str:
    template = TEMPLATE.read_text()
    if MARKER not in template:
        raise SystemExit(
            f"Error: template {TEMPLATE} is missing the {MARKER!r} marker"
        )
    # Material is appended strictly below the marker — it can never displace
    # or rewrite the instructions above it (Seam A guard).
    return f"{template}\n{material}\n"


def _pending_cycles(db_path: Path, limit: int) -> list[dict]:
    """Completed cycles (decision != PENDING) with no adversarial_reviews row.

    Worker-authored material for each cycle = its outcome_note (the cycle's
    own claim about what was done). This is exactly the self-report the
    reviewer is here to challenge.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # First-run graceful: create the sink if absent (idempotent).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adversarial_reviews ("
            " review_id TEXT PRIMARY KEY,"
            " cycle_id INTEGER NOT NULL UNIQUE,"
            " reviewer_id TEXT NOT NULL,"
            " reviewer_model TEXT NOT NULL,"
            " verdict TEXT NOT NULL,"
            " findings_json TEXT NOT NULL,"
            " summary TEXT,"
            " ts TEXT NOT NULL)"
        )
        conn.commit()
        rows = conn.execute(
            "SELECT c.id, c.task_id, c.outcome_note, c.completeness,"
            " c.quality, c.progress"
            " FROM loop_cycles c"
            " LEFT JOIN adversarial_reviews r ON r.cycle_id = c.id"
            " WHERE r.cycle_id IS NULL AND c.decision != 'PENDING'"
            " AND c.id IS NOT NULL"
            " ORDER BY c.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _material_for(row: dict) -> str:
    """Render a cycle as reviewer-visible material (its self-report only)."""
    return (
        f"Cycle #{row['id']} (task: {row['task_id']})\n"
        f"Worker's own scores: completeness={row['completeness']}"
        f" quality={row['quality']} progress={row['progress']}\n"
        f"Worker's note (self-report — the thing being reviewed):\n"
        f"{row['outcome_note'] or '(no note)'}\n"
    )


def _record_review_call(db_path: Path, source: str, model: str,
                        usage: dict | None = None) -> None:
    """Write ONE ledger row per model call (2026-11-10).

    The plan-review sweep is a separate cost surface from the close gate; the
    per-call token ledger (same review_calls table the close gate writes)
    makes the full fleet review bill measured, not modeled. review_id is a
    fresh uuid4 per call — a retry is a new spend and gets a new row.
    TELEMETRY, NOT ENFORCEMENT: a write failure is printed and the review
    proceeds (enforcement-change-safety Rule 1).
    """
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS review_calls ("
            " review_id TEXT PRIMARY KEY,"
            " source TEXT NOT NULL,"
            " reviewer_model TEXT NOT NULL,"
            " reviewer_id TEXT,"
            " prompt_tokens INTEGER,"
            " completion_tokens INTEGER,"
            " reasoning_tokens INTEGER,"
            " ts TEXT NOT NULL)"
        )
        usage = usage or {}
        conn.execute(
            "INSERT OR REPLACE INTO review_calls"
            " (review_id, source, reviewer_model, reviewer_id,"
            "  prompt_tokens, completion_tokens, reasoning_tokens, ts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), source, model, "",
             usage.get("prompt_tokens"), usage.get("completion_tokens"),
             (usage.get("completion_tokens_details") or {})
             .get("reasoning_tokens"),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except sqlite3.Error as e:  # noqa: BLE001 — telemetry must never fail the review
        print(f"  [review-queue-sweep] review_calls write failed ({source}, {model}): {e}",
              file=sys.stderr)
    finally:
        if conn is not None:
            conn.close()


def _call_model(prompt: str, db_path: str | None = None) -> str:
    model = get_reviewer_model()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        # Cron runs may not inherit the shell env — source the Hermes deploy
        # env the same way other fleet scripts do (never print the value).
        hermes_env = Path(os.environ.get("HERMES_HOME",
                                         str(Path.home() / ".hermes"))) / ".env"
        if hermes_env.exists():
            for line in hermes_env.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        raise SystemExit(
            "Error: OPENROUTER_API_KEY not set (env or ~/.hermes/.env) — "
            "cannot run the review"
        )
    # BOUNDED BUDGET (2026-11-13, cycle 8258): the plan review is OUTPUT-heavy
    # (measured 6-15K completion tokens, 74% of the fleet review bill), so the
    # call must carry an explicit output cap AND a reasoning effort, or the
    # model runs its default (effectively max/high) and burns the whole budget.
    # Distinct env names from the close-gate reviewer (REVIEWER_REASONING_EFFORT
    # defaults to 'none' there — right for a JSON verdict, wrong for long
    # findings prose, so sharing the name would gut plan reviews on the
    # close-gate default). Invalid values fall back LOUDLY — a 400 at review
    # time reads exactly like a reviewer outage; only a below-floor cap refuses.
    _PLAN_MAX_TOKENS = 24000          # measured plan output 6-15K + headroom
    _PLAN_MAX_TOKENS_FLOOR = 16000    # below this the review output truncates
    _PLAN_EFFORT_DEFAULT = "low"      # enough thinking for real findings, bounded
    _PLAN_EFFORTS = ("none", "low", "medium", "high")
    max_tokens_raw = os.environ.get("REVIEWER_PLAN_MAX_TOKENS", "")
    try:
        max_tokens = int(max_tokens_raw) if max_tokens_raw.strip() else _PLAN_MAX_TOKENS
    except ValueError:
        raise SystemExit(
            f"Error: REVIEWER_PLAN_MAX_TOKENS={max_tokens_raw!r} is not an "
            f"integer — refusing to review with an ambiguous cap"
        )
    if max_tokens < _PLAN_MAX_TOKENS_FLOOR:
        raise SystemExit(
            f"Error: REVIEWER_PLAN_MAX_TOKENS={max_tokens} is below the "
            f"{_PLAN_MAX_TOKENS_FLOOR} floor — the review output would be "
            "truncated (measured 6-15K tokens); refusing the ambiguous cap"
        )
    effort_raw = os.environ.get("REVIEWER_PLAN_REASONING_EFFORT", "")
    effort = effort_raw.strip().lower() or _PLAN_EFFORT_DEFAULT
    if effort not in _PLAN_EFFORTS:
        print(f"[adversarial-review] invalid REVIEWER_PLAN_REASONING_EFFORT="
              f"{effort_raw!r} — falling back to '{_PLAN_EFFORT_DEFAULT}'",
              file=sys.stderr)
        effort = _PLAN_EFFORT_DEFAULT
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "reasoning": {"effort": effort},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    # Ledger-accurate plan-review cost (2026-11-10): record the provider's OWN
    # usage. Telemetry — a write failure never fails the review.
    _record_review_call(Path(db_path or DEFAULT_DB).expanduser(), "plan", model,
                        data.get("usage"))
    return data["choices"][0]["message"]["content"]


def _extract_reviewer_json(text: str) -> tuple[str, str]:
    """Extract (findings_json, verdict) from the reviewer's reply.

    The reviewer is instructed to emit a JSON object
    {cycle_id, verdict, findings[], summary} — but models wrap JSON in prose
    or fences. Find the outermost {...}, parse it, and pull the fields; if
    parsing fails, record an empty findings array with verdict FINDINGS and
    let the summary carry the raw text (never silently drop a review).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            findings = obj.get("findings", [])
            if not isinstance(findings, list):
                findings = []
            verdict = obj.get("verdict", "FINDINGS" if findings else "CLEAN")
            return json.dumps(findings), str(verdict)
        except json.JSONDecodeError:
            pass
    # Unparseable reviewer output: never dropped, never trusted as CLEAN.
    return "[]", "FINDINGS"


def _record(db_path: Path, cycle_id: int, reviewer_id: str, model: str,
            text: str, re_review: bool = False) -> int:
    """Record the review verdict — CURRENT row per cycle + APPEND-ONLY history.

    Invariants (operator directive 2026-10-30, Luke):
    - ONE current row per cycle (cycle_id UNIQUE): the close-time reviewer's
      `_stored_review()` reads a bare fetchone() and its writer UPSERTs on the
      UNIQUE key — multi-row-per-cycle would break the close gate.
    - NEVER DELETE: a superseded verdict is MOVED to `adversarial_reviews_history`
      (append-only) before the fresh verdict becomes the current row. A FINDINGS
      that was superseded is always recoverable — the audit trail is preserved.

    `re_review=True` (--re-review): move the current row (if any) into HISTORY,
    then UPSERT the fresh verdict as the new current row. Returns 0.
    Plain path (`re_review=False`): INSERT; IntegrityError UNIQUE => benign
    no-op with a hint ("already reviewed — use --re-review to re-judge"), 0.
    """
    findings_json, verdict = _extract_reviewer_json(text)
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adversarial_reviews ("
            " review_id TEXT PRIMARY KEY,"
            " cycle_id INTEGER NOT NULL UNIQUE,"
            " reviewer_id TEXT NOT NULL,"
            " reviewer_model TEXT NOT NULL,"
            " verdict TEXT NOT NULL,"
            " findings_json TEXT NOT NULL,"
            " summary TEXT,"
            " ts TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adversarial_reviews_history ("
            " review_id TEXT PRIMARY KEY,"
            " cycle_id INTEGER NOT NULL,"
            " reviewer_id TEXT NOT NULL,"
            " reviewer_model TEXT NOT NULL,"
            " verdict TEXT NOT NULL,"
            " findings_json TEXT NOT NULL,"
            " summary TEXT,"
            " ts TEXT NOT NULL,"
            " superseded_ts TEXT NOT NULL,"
            " reason TEXT)"
        )
        if re_review:
            # Move the current row (if any) into HISTORY, then UPSERT the fresh
            # verdict — atomic: the audit trail cannot lose the superseded row.
            old = conn.execute(
                "SELECT review_id, cycle_id, reviewer_id, reviewer_model,"
                " verdict, findings_json, summary, ts"
                " FROM adversarial_reviews WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
            if old is not None:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO adversarial_reviews_history (review_id, cycle_id,"
                    " reviewer_id, reviewer_model, verdict, findings_json,"
                    " summary, ts, superseded_ts, reason)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (old[0], old[1], old[2], old[3], old[4], old[5], old[6],
                     old[7], now, "re-review"),
                )
                print(f"  cycle #{cycle_id}: superseded {old[4]} into history "
                      f"({now})", file=sys.stderr)
            conn.execute(
                "INSERT INTO adversarial_reviews"
                " (review_id, cycle_id, reviewer_id, reviewer_model,"
                "  verdict, findings_json, summary, ts)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(cycle_id) DO UPDATE SET"
                "  review_id=excluded.review_id,"
                "  reviewer_id=excluded.reviewer_id,"
                "  reviewer_model=excluded.reviewer_model,"
                "  verdict=excluded.verdict,"
                "  findings_json=excluded.findings_json,"
                "  summary=excluded.summary,"
                "  ts=excluded.ts",
                (
                    str(uuid.uuid4()),
                    cycle_id,
                    reviewer_id,
                    model,
                    verdict,
                    findings_json,
                    text[:2000],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        else:
            conn.execute(
                "INSERT INTO adversarial_reviews"
                " (review_id, cycle_id, reviewer_id, reviewer_model,"
                "  verdict, findings_json, summary, ts)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    cycle_id,
                    reviewer_id,
                    model,
                    verdict,
                    findings_json,
                    text[:2000],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()
        return 0
    except sqlite3.IntegrityError as e:
        if conn is not None:
            conn.rollback()
        if "UNIQUE" in str(e) and not re_review:
            # Already reviewed = the review EXISTS: the sweep's goal is met.
            # Benign no-op — counting it as failure exit-1s the cron and
            # restarts the 3-strike pause (watchdog pattern, fleet dedupe).
            print(f"  cycle #{cycle_id}: already reviewed — use --re-review to "
                  f"re-judge (UNIQUE constraint)", file=sys.stderr)
            return 0
        print(f"  cycle #{cycle_id}: DB error: {e}", file=sys.stderr)
        return 3
    finally:
        # Close on EVERY path — an open transaction on the error path holds
        # the write lock and the next cycle dies with 'database is locked'
        # (observed: cron run 2026-09-24 20:58 KST aborted the whole sweep).
        if conn is not None:
            conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cycle-id", type=int, default=None,
                   help="review one specific cycle (with --material)")
    p.add_argument("--material", default=None,
                   help="the reviewed output (worker-authored, untrusted)")
    p.add_argument("--material-file", default=None,
                   help="path to a file holding the reviewed output; prefer this "
                        "over --material from a worker (avoids `$(cat ...)` "
                        "metachar in the invocation)")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help="loop-governance DB path")
    p.add_argument("--reviewer-id", default=None,
                   help="distinct reviewer session id (default: generated)")
    p.add_argument("--limit", type=int, default=5,
                   help="max cycles per sweep (default 5)")
    p.add_argument("--review", action="store_true",
                   help="call the model and record the verdict")
    p.add_argument("--re-review", action="store_true",
                   help="re-judge an already-reviewed cycle: supersede the current "
                        "verdict into the append-only history and record a fresh one "
                        "(never DELETE — the old verdict stays recoverable)")
    p.add_argument("--sweep", action="store_true",
                   help="review every completed cycle that has no review yet")
    p.add_argument("--dry-run", action="store_true",
                   help="assemble and print the prompt; do not call a model")
    args = p.parse_args()

    if not args.sweep and args.cycle_id is None and args.material is None \
            and args.material_file is None:
        # Cron mode: install-orch-crons.sh create_cron() registers this script
        # with NO argument mechanism, so the orchestrator cron invokes it bare.
        # A bare invocation IS the sweep (docstring: --sweep is "cron mode");
        # erroring here 3-strike-pauses the cron (2026-09-24 incident).
        args.sweep = True

    if args.sweep:
        db_path = Path(args.db).expanduser()
        if not db_path.exists():
            print(f"silent: no loop-governance DB at {db_path}")
            return 0
        pending = _pending_cycles(db_path, args.limit)
        if not pending:
            print("silent: no unreviewed completed cycles")
            return 0
        reviewer_id = args.reviewer_id or f"adv-review-{uuid.uuid4().hex[:8]}"
        model = get_reviewer_model()
        print(f"sweep: {len(pending)} cycle(s) to review (reviewer {reviewer_id})")
        failed = 0
        for row in pending:
            try:
                prompt = assemble_prompt(_material_for(row))
                text = _call_model(prompt)
                rc = _record(db_path, row["id"], reviewer_id, model, text)
                if rc != 0:
                    failed += 1
                print(f"  cycle #{row['id']}: recorded (rc={rc})")
            except SystemExit as e:
                print(f"  cycle #{row['id']}: {e}", file=sys.stderr)
                failed += 1
            except sqlite3.OperationalError as e:
                print(f"  cycle #{row['id']}: DB busy: {e}", file=sys.stderr)
                failed += 1
        return 1 if failed else 0

    # Single-cycle mode
    # --cycle-id requires material, from --material (inline) or --material-file
    # (read the file; the worker-friendly form that avoids $(cat ...)).
    if args.material_file is not None:
        try:
            args.material = Path(args.material_file).expanduser().read_text(
                encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Error: cannot read --material-file {args.material_file}: {exc}",
                  file=sys.stderr)
            return 2
    if args.cycle_id is None or args.material is None:
        p.error("single-cycle mode requires --cycle-id and --material "
                "(or --material-file) — or use --sweep")
    if not args.material.strip():
        print("Error: empty --material — nothing to review", file=sys.stderr)
        return 2
    if (args.review or args.re_review) and not args.reviewer_id:
        print("Error: --review/--re-review requires --reviewer-id", file=sys.stderr)
        return 2

    prompt = assemble_prompt(args.material)

    if args.dry_run:
        print(prompt)
        return 0

    if not args.review and not args.re_review:
        p.error("nothing to do: pass --dry-run, --review, --re-review, or --sweep")

    model = get_reviewer_model()
    text = _call_model(prompt)
    reviewer_id = args.reviewer_id or f"adv-review-{uuid.uuid4().hex[:8]}"
    return _record(Path(args.db).expanduser(), args.cycle_id, reviewer_id,
                   model, text, re_review=args.re_review)


if __name__ == "__main__":
    sys.exit(main())
