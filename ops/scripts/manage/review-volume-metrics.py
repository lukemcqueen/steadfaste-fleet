#!/usr/bin/env python3
"""review-volume-metrics.py — per-host loops-governance review volume.

Read-only measurement: how many close-gate reviews (blob_reviews) and plan
reviews (adversarial_reviews) a host has recorded in its OWN loop-governance
DB, by judge model and by day. Each host reads only its own store — the
orchestrator cannot read a worker host's DB directly, so this script is what
an EXEC result carries back (the same one-host-own-data pattern as
cron-cost-metrics.py --json-summary).

NO price table lives here. Prices rot on every provider hike; volume times
price is computed by the caller against the live catalogue. This script
reports volume only, and reports an unreadable store as an ERROR (exit 2),
never as $0 — a host with no readable DB is "no data", not "no reviews".

Exit codes:
  0  summary emitted
  2  DB missing/unreadable or no host identity — caller must treat as no data
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".steadfaste-fleet" / "data" / "loop-governance.db"
WINDOW_DAYS = 21

# Host-derived identity, same files the bus command handler uses
IDENTITY_FILES = (
    Path.home() / ".steadfaste-fleet" / "agent.env",
    Path.home() / "steadfaste-fleet" / ".env",
)


def resolve_agent(explicit: str | None) -> str:
    if explicit is not None:
        return explicit.strip()
    env = (os.environ.get("AGENT_NAME") or "").strip()
    if env:
        return env
    for path in IDENTITY_FILES:
        try:
            if not path.is_file():
                continue
            for line in path.read_text().splitlines():
                if line.startswith("AGENT_NAME="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if val:
                        return val
        except OSError:
            continue
    return ""


def _table_volume(conn, table: str, window_days: int) -> dict:
    """Counts by model + per-day window for one review table.

    The table may not exist on an older host — return an absent marker rather
    than fabricating a zero (the caller must not read absent == 0 reviews).
    """
    absent = {"present": False, "by_model": [], "per_day": [], "total": None}
    try:
        by_model = conn.execute(
            "SELECT coalesce(nullif(reviewer_model,''),'(empty)') AS m,"
            " COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts"
            f" FROM {table} GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
        per_day = conn.execute(
            "SELECT substr(ts,1,10) AS d, COUNT(*) AS n"
            f" FROM {table} WHERE ts IS NOT NULL AND ts != ''"
            " GROUP BY 1 ORDER BY d DESC LIMIT ?",
            (window_days,),
        ).fetchall()
    except sqlite3.DatabaseError:
        return absent
    return {
        "present": True,
        "by_model": [
            {"model": m, "n": n, "first_ts": first_ts, "last_ts": last_ts}
            for m, n, first_ts, last_ts in by_model
        ],
        "per_day": [{"date": d, "n": n} for d, n in per_day],
        "total": sum(r[1] for r in by_model),
    }


def _ledger(conn, window_days: int) -> dict:
    """review_calls ledger — MEASURED tokens (2026-11-10).

    blob_reviews has no token columns, so pre-ledger review cost was MODELED.
    This reads the per-call ledger both caller sites now write; from the first
    day it has rows, cost = SUM(prompt_tokens)*live_in + SUM(completion_tokens)
    *live_out — no modeling. A host whose daemon has not respawned yet reports
    present=False (the table materializes at the next loop-gov spawn).
    """
    absent = {"present": False, "by_source": [], "per_day": [], "total": None}
    try:
        by_source = conn.execute(
            "SELECT source, COUNT(*) AS n,"
            " SUM(prompt_tokens) AS p, SUM(completion_tokens) AS c,"
            " SUM(reasoning_tokens) AS r, MIN(ts) AS first_ts, MAX(ts) AS last_ts"
            " FROM review_calls GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
        per_day = conn.execute(
            "SELECT substr(ts,1,10) AS d, COUNT(*) AS n,"
            " SUM(prompt_tokens) AS p, SUM(completion_tokens) AS c"
            " FROM review_calls WHERE ts IS NOT NULL AND ts != ''"
            " GROUP BY 1 ORDER BY d DESC LIMIT ?",
            (window_days,),
        ).fetchall()
    except sqlite3.DatabaseError:
        return absent
    return {
        "present": True,
        "by_source": [
            {"source": s, "n": n, "prompt_tokens": p, "completion_tokens": c,
             "reasoning_tokens": r, "first_ts": first_ts, "last_ts": last_ts}
            for s, n, p, c, r, first_ts, last_ts in by_source
        ],
        "per_day": [
            {"date": d, "n": n, "prompt_tokens": p, "completion_tokens": c}
            for d, n, p, c in per_day
        ],
        "total": sum(r[1] for r in by_source),
    }


def build_summary(agent: str, db_path: Path, window_days: int) -> dict:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as e:
        return {"agent": agent, "error": f"cannot open DB: {type(e).__name__}"}
    try:
        return {
            "agent": agent,
            "db_path": str(db_path),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window_days": window_days,
            "blob_reviews": _table_volume(conn, "blob_reviews", window_days),
            "adversarial_reviews": _table_volume(conn, "adversarial_reviews", window_days),
            "review_calls": _ledger(conn, window_days),
        }
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", default=None,
                   help="agent label (default: $AGENT_NAME, then the host's agent.env)")
    p.add_argument("--db", default=str(DEFAULT_DB), help="loop-governance DB path")
    p.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                   help="per-day window length (default 21)")
    p.add_argument("--json-summary", action="store_true",
                   help="emit one compact JSON object for a bus EXEC_RESULT")
    args = p.parse_args()

    agent = resolve_agent(args.agent)
    if not agent:
        print("[review-volume-metrics] AGENT_NAME not set and no host agent.env — "
              "refusing to label a measurement as unknown", file=sys.stderr)
        return 2
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        print(f"[review-volume-metrics] no loop-governance DB at {db_path}",
              file=sys.stderr)
        return 2
    summary = build_summary(agent, db_path, args.window_days)
    if args.json_summary:
        print(json.dumps(summary))
        return 0 if "error" not in summary else 2
    # human output
    print(f"agent={agent} db={db_path} window={args.window_days}d")
    for table in ("blob_reviews", "adversarial_reviews"):
        block = summary.get(table, {})
        if not block.get("present"):
            print(f"\n== {table}: (table absent on this host)")
            continue
        print(f"\n== {table}: total={block['total']}")
        for row in block["by_model"]:
            print(f"  {row['model']:35s} n={row['n']:6d}  "
                  f"first={row['first_ts']}  last={row['last_ts']}")
        print("  per-day (newest first):")
        for row in block["per_day"]:
            print(f"    {row['date']}  {row['n']}")
    ledger = summary.get("review_calls", {})
    if ledger.get("present"):
        print(f"\n== review_calls (MEASURED tokens): total={ledger['total']}")
        for row in ledger["by_source"]:
            print(f"  {row['source']:6s} n={row['n']:4d}  prompt={row['prompt_tokens']}  "
                  f"completion={row['completion_tokens']}  reasoning={row['reasoning_tokens']}")
        print("  per-day (newest first):")
        for row in ledger["per_day"]:
            print(f"    {row['date']}  n={row['n']}  prompt={row['prompt_tokens']}  "
                  f"completion={row['completion_tokens']}")
    else:
        print("\n== review_calls: (ledger not present yet — table materializes at the "
              "next loop-gov daemon spawn)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
