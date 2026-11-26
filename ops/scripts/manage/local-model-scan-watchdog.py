#!/usr/bin/env python3
"""local-model-scan-watchdog — hourly state-transition watchdog for model availability.

Replaces the LLM-driven cron `local-agent-model-scan-monitor-hourly` (24 LLM
runs/day for a [SILENT] tick). Deterministic, stdlib-only, portable to macOS.

What it does (mirrors the old prompt's mandate):
  Re-read the material model state from three files and report ONLY when it
  changed: a free model became available/unavailable, a candidate's status
  changed, a new unvetted cheaper candidate surfaced, a guardrail/404 appeared,
  the pools were reconfigured, or the session/cron model pins moved.

Material state = a normalized projection of:
  - ~/.hermes/config.yaml         (model.default, model.provider,
                                   fallback_providers[], cron.model)
  - ~/.steadfaste-fleet/state/model-market.json
                                   (prices, tiers, unvetted_cheaper_candidates,
                                    violations, free_tier_junk_dropped)
  - ~/.hermes/cron/jobs.json      (per-job model/provider/model_snapshot/
                                   provider_snapshot pins)

Volatile content is EXCLUDED from the fingerprint (captured timestamps,
catalog_source, list path, order_changed/price_changed/apply_pending flags,
last_run/status fields) so identical state hashes identical and only a real
change fires the report. See local-cron-suppression-design: normalize out of
the HASH, never out of the delivered text.

Exit contract (no_agent watchdog): empty stdout + exit 0 = nothing delivered;
non-zero exit = alert (delivered as an error notice). Report text on change.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("LSM_CONFIG_PATH", "").strip() or (HOME / ".hermes" / "config.yaml"))
MARKET_PATH = Path(os.environ.get("LSM_MARKET_PATH", "").strip() or (HOME / ".steadfaste-fleet" / "state" / "model-market.json"))
JOBS_PATH = Path(os.environ.get("LSM_JOBS_PATH", "").strip() or (HOME / ".hermes" / "cron" / "jobs.json"))
FINGERPRINT_PATH = Path(os.environ.get("LSM_FINGERPRINT_PATH", "").strip() or (HOME / ".steadfaste-fleet" / "state" / "local-model-scan-watchdog.fingerprint"))

# config.yaml keys that count as material model state (line-key based extraction).
_MODEL_KEYS = ("default", "provider", "model")
_FALLBACK_RE = re.compile(r"^\s*-\s*provider:")
_CRON_MODEL_RE = re.compile(r"^cron:\s*$")


def _extract_config_material(cfg_text: str) -> dict:
    """Pull the model-material lines from config.yaml without a YAML dep.

    Captures model.default / model.provider, the fallback_providers[] models,
    and cron.model. Ignores everything else (delegation, stt, tts, ...).
    """
    out: dict = {"model": {}, "fallback_providers": [], "cron": {}}
    in_model = False
    in_fallback = False
    in_cron = False
    for line in cfg_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("model:") and not line.startswith("model_") and not line.startswith("model "):
            in_model, in_fallback, in_cron = True, False, False
            continue
        if _CRON_MODEL_RE.match(line):
            in_model, in_fallback, in_cron = False, False, True
            continue
        if re.match(r"^[a-z_]+:", line) and not line.startswith(" ") and not line.startswith("-"):
            # Any other top-level key ends the current section.
            if line.startswith("fallback_providers:"):
                in_model, in_fallback, in_cron = False, True, False
            else:
                in_model, in_fallback, in_cron = False, False, False
            continue
        if in_cron and re.match(r"^\s+model:", line):
            out["cron"]["model"] = stripped.split(":", 1)[1].strip()
            continue
        if in_fallback:
            if _FALLBACK_RE.match(line):
                out["fallback_providers"].append({})
            elif out["fallback_providers"] and re.match(r"^\s+model:", line):
                out["fallback_providers"][-1]["model"] = stripped.split(":", 1)[1].strip()
            continue
        if in_model:
            m = re.match(r"^\s+(default|provider):\s*(\S+)", line)
            if m:
                out["model"][m.group(1)] = m.group(2)
    return out


def _extract_market_material(market: dict) -> dict:
    return {
        "prices": market.get("prices"),
        "tiers": market.get("tiers"),
        "unvetted_cheaper_candidates": market.get("unvetted_cheaper_candidates"),
        "violations": market.get("violations"),
        "free_tier_junk_dropped": market.get("free_tier_junk_dropped"),
    }


def _extract_jobs_material(jobs: dict) -> dict:
    pins = {}
    for job in jobs.get("jobs", []):
        name = job.get("name")
        if not name:
            continue
        pins[name] = {
            "model": job.get("model"),
            "provider": job.get("provider"),
            "model_snapshot": job.get("model_snapshot"),
            "provider_snapshot": job.get("provider_snapshot"),
            "no_agent": job.get("no_agent"),
        }
    return pins


def _fingerprint(state: dict) -> str:
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path, what: str) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"local-model-scan-watchdog: {what} not found ({path})", file=sys.stderr)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f"local-model-scan-watchdog: cannot read {what}: {exc}", file=sys.stderr)
        return None


def _section_diffs(old: dict, new: dict) -> list[str]:
    """Human-readable summary of what changed between two full nested states.

    old/new are the {config, market, jobs} state dicts written to the
    fingerprint file; each section is diffed by its own key set.
    """
    lines = []

    old_cfg = old.get("config", {})
    new_cfg = new.get("config", {})
    if old_cfg.get("model") != new_cfg.get("model"):
        lines.append(f"config.yaml model: {old_cfg.get('model')} -> {new_cfg.get('model')}")
    if old_cfg.get("cron") != new_cfg.get("cron"):
        lines.append(f"config.yaml cron: {old_cfg.get('cron')} -> {new_cfg.get('cron')}")
    if old_cfg.get("fallback_providers") != new_cfg.get("fallback_providers"):
        lines.append(
            f"fallback_providers: {old_cfg.get('fallback_providers')} -> {new_cfg.get('fallback_providers')}")

    old_mkt = old.get("market", {})
    new_mkt = new.get("market", {})
    if old_mkt.get("prices") != new_mkt.get("prices"):
        lines.append("model-market prices: changed")
    if old_mkt.get("tiers") != new_mkt.get("tiers"):
        lines.append(f"model-market tiers: {old_mkt.get('tiers')} -> {new_mkt.get('tiers')}")
    if old_mkt.get("unvetted_cheaper_candidates") != new_mkt.get("unvetted_cheaper_candidates"):
        old_c = _candidate_ids(old_mkt.get("unvetted_cheaper_candidates"))
        new_c = _candidate_ids(new_mkt.get("unvetted_cheaper_candidates"))
        added = sorted(new_c - old_c)
        removed = sorted(old_c - new_c)
        bits = []
        if added:
            bits.append(f"added {added}")
        if removed:
            bits.append(f"removed {removed}")
        lines.append("unvetted free candidates: " + ("; ".join(bits) if bits else "changed"))
    if old_mkt.get("violations") != new_mkt.get("violations"):
        lines.append(f"violations: {old_mkt.get('violations')} -> {new_mkt.get('violations')}")
    if old_mkt.get("free_tier_junk_dropped") != new_mkt.get("free_tier_junk_dropped"):
        lines.append(f"free-tier junk dropped: {old_mkt.get('free_tier_junk_dropped')} -> {new_mkt.get('free_tier_junk_dropped')}")

    jobs_old = old.get("jobs", {})
    jobs_new = new.get("jobs", {})
    for name in sorted(set(jobs_old) | set(jobs_new)):
        if jobs_old.get(name) != jobs_new.get(name):
            lines.append(f"job pin {name}: {jobs_old.get(name)} -> {jobs_new.get(name)}")
    return lines


def _candidate_ids(cands) -> set[str]:
    out = set()
    for pool in (cands or {}).values():
        for c in pool or []:
            if isinstance(c, dict) and c.get("model"):
                out.add(c["model"])
    return out


def main() -> int:
    cfg_text = None
    try:
        cfg_text = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"local-model-scan-watchdog: config.yaml not found ({CONFIG_PATH})", file=sys.stderr)
        return 3
    market = _read_json(MARKET_PATH, "model-market.json")
    jobs = _read_json(JOBS_PATH, "jobs.json")
    if market is None or jobs is None:
        return 3

    state = {
        "config": _extract_config_material(cfg_text),
        "market": _extract_market_material(market),
        "jobs": _extract_jobs_material(jobs),
    }
    fp = _fingerprint(state)

    prev = None
    prev_state = {}
    try:
        stored = FINGERPRINT_PATH.read_text(encoding="utf-8").strip()
        prev_fp, _, stored_state = stored.partition("|")
        prev = prev_fp
        if stored_state:
            prev_state = json.loads(stored_state)
    except FileNotFoundError:
        prev = None
    except (json.JSONDecodeError, ValueError):
        prev = None

    if prev == fp:
        return 0  # unchanged: silent

    # Changed (or first run). Report the delta, then persist the fingerprint.
    if prev is None:
        print("local-model-scan-watchdog: baseline established (first run) — model state fingerprint written.")
    else:
        lines = _section_diffs(prev_state, state)
        print("🔀 Model state changed:")
        for ln in lines:
            print(f"  - {ln}")
        if not lines:
            print("  (details not attributable — fingerprint changed)")

    FINGERPRINT_PATH.write_text(f"{fp}|{json.dumps(state, sort_keys=True)}", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
