#!/usr/bin/env python3
"""
agents-md-prune-scan.py — Daily scan for AGENTS.md pruning candidates.

Runs the agents-doc-audit --prune analysis and outputs the JSON report
ONLY when candidates are found. Silent when AGENTS.md is lean.

Designed as a no_agent cron companion: exit 0 + no stdout = nothing to report.
Exit 0 + JSON report = candidates available for LLM review.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "steadfaste-fleet"
AUDIT_SCRIPT = (
    Path.home() / ".steadfaste-fleet" / "scripts" / "agents-doc-audit.py"
)


def main():
    if not AUDIT_SCRIPT.exists():
        # Fallback to repo path
        fallback = REPO / "ops" / "scripts" / "agent" / "agents-doc-audit.py"
        if not fallback.exists():
            print(f"agents-doc-audit.py not found at {AUDIT_SCRIPT} or {fallback}", file=sys.stderr)
            sys.exit(1)
        script = str(fallback)
    else:
        script = str(AUDIT_SCRIPT)

    result = subprocess.run(
        [sys.executable, script, "--repo", str(REPO), "--prune", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Exit-code contract with agents-doc-audit.py --prune --json (dry-run,
    # no --apply): exit 0 = no prune candidates, exit 2 = candidates found —
    # BOTH emit a valid JSON report on stdout and are success cases here. Any
    # other exit is a genuine script failure (e.g. 1 = error, missing --repo).
    # Verified in agents-doc-audit.py (~line 746): `sys.exit(2 if not
    # args.apply else 0)` on the dry-run prune path.
    if result.returncode not in (0, 2):
        # Non-zero in unexpected way — report error
        print(f"ERROR: audit script failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    # Parse the JSON report
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"ERROR: could not parse audit output", file=sys.stderr)
        sys.exit(1)

    # Silent when clean
    if report.get("candidate_count", 0) == 0:
        sys.exit(0)

    # Print the full JSON report for the LLM downstream cron
    print(result.stdout)


if __name__ == "__main__":
    main()
