#!/usr/bin/env python3
"""loop-gov-classify — the FAST access layer over the write-class contract.

One answer to "is this terminal command a write?" for ANY coding agent, over the
ONE implementation (`ops/services/fleet-governance/write_class.py`). The Hermes
enforcer spawns this per terminal tool call, so a rule change in the contract is
picked up on the next call with NO gateway restart (a fresh process always loads
the current file).

    loop-gov-classify 'git status --short'
    → {"read_only": true, "sanctioned": null, "db_mutation": false}     rc 0

CONTRACT
  rc 0  classified — the JSON verdict is on stdout
  rc 3  COULD NOT VERIFY — the contract could not be loaded (fail CLOSED:
        a caller must treat an unknown command as write-class)
  --contract <path>  pin which write_class.py copy to load (test/ops seam;
        default: repo layout, then the deployed copy — the loop-gov.py pattern)

stdlib-only by design: the enforcer hot path pays one python cold start, never
a full governance-server import (see docs/plans/hermes-governance-cr2-wiring.md
§8). This file holds NO classification semantics — it loads the contract and
serialises its verdict.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SERVER_NAME = "write_class.py"


def _contract_path(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "services" / "fleet-governance" / SERVER_NAME,  # repo
        Path.home() / ".steadfaste-fleet" / "services" / "fleet-governance" / SERVER_NAME,  # deployed
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_contract(path: Path):
    if path is None:
        raise ImportError("contract path is None")
    spec = importlib.util.spec_from_file_location("fleet_write_class_cli", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sanctioned_name(mod, command: str) -> str | None:
    if mod.is_sanctioned_fleet_update_command(command):
        return "fleet_update"
    if mod.is_sanctioned_tmp_cleanup_command(command):
        return "tmp_cleanup"
    if mod.is_sanctioned_plan_review_command(command):
        return "plan_review"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(prog="loop-gov-classify")
    ap.add_argument("--contract", default=None,
                    help="explicit write_class.py path (default: repo then deployed)")
    ap.add_argument("command", help="the terminal command to classify")
    a = ap.parse_args()

    path = _contract_path(a.contract)
    if path is None:
        print("COULD NOT VERIFY: the write-class contract was not found "
              "(looked in --contract, the repo, and ~/.steadfaste-fleet/services). "
              "A terminal command whose classification cannot be verified is "
              "treated as write-class. Repair with the fleet venv + "
              "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh",
              file=sys.stderr)
        return 3
    try:
        mod = _load_contract(path)
    except Exception as exc:  # noqa: BLE001 — report, never a forged verdict
        print(f"COULD NOT VERIFY: the write-class contract failed to load from "
              f"{path} ({exc.__class__.__name__}: {exc}). Fail closed: write-class. "
              "Repair with the fleet venv + "
              "bash ~/steadfaste-fleet/ops/scripts/fleet-update.sh",
              file=sys.stderr)
        return 3

    command = a.command
    verdict = {
        "read_only": bool(mod.is_readonly_command(command)),
        "sanctioned": _sanctioned_name(mod, command),
        "db_mutation": bool(mod.is_governance_db_mutation_command(command)),
    }
    print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())