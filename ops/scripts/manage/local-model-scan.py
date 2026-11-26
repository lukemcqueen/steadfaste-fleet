#!/usr/bin/env python3
"""local-model-scan — daily deterministic unauthorized-model-usage compliance scan.

Replaces the LLM-driven cron `local-agent-model-scan` (1 LLM run/day that
mostly re-narrates "all checks consistent"). Deterministic, stdlib-only,
portable to macOS. Prints a report ONLY when a check FAILS; silent (empty
stdout, exit 0) when everything is clean — the no_agent watchdog contract.

Checks (mirrors what the LLM cron used to verify):
  1. Session default (config.yaml model.default) matches the router baseline
     (config/router-models.json "baseline", label stripped) — a label-prefixed
     id (e.g. "openrouter:qwen/...") in config would 400 every turn.
  2. No stale known-bad model ids as session default or in any cron override
     (deepseek-chat; ids with zero allowed endpoints on this account).
  3. Cron jobs: no job carries a per-job model pin (scheduler snapshot model
     is normal; an override is an unauthorized route).
  4. Live logs: no recent rejection/fallback markers
     ("is not a valid model ID", "Provider returned error",
     "Fallback activated", "BadRequestError", "API key has expired",
     "Key limit exceeded").
  5. Router consistency: live routing.json medium/simple pool heads are
     members of the vetted list (config/router-models.json tiers).

Exit contract: 0 = clean (silent) or findings reported; 3 = could not verify
(no report, alert delivered as error).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("LSM_CONFIG_PATH", "").strip() or (HOME / ".hermes" / "config.yaml"))
JOBS_PATH = Path(os.environ.get("LSM_JOBS_PATH", "").strip() or (HOME / ".hermes" / "cron" / "jobs.json"))
ROUTER_LIST_PATH = Path(os.environ.get("LSM_ROUTER_LIST_PATH", "").strip() or (HOME / "steadfaste-fleet" / "config" / "router-models.json"))
ROUTING_PATH = Path(os.environ.get("LSM_ROUTING_PATH", "").strip() or (HOME / ".hermes" / "jev" / "routing.json"))
LOG_DIR = Path(os.environ.get("LSM_LOG_DIR", "").strip() or (HOME / ".hermes" / "logs"))

# ids that must NEVER appear as a session default or cron pin.
KNOWN_BAD_IDS = (
    "deepseek-chat",
    "openrouter:deepseek-chat",
)

# live-log markers of a routing/rejection incident (same set the LLM scan greps).
LOG_MARKERS = (
    "is not a valid model ID",
    "Provider returned error",
    "Fallback activated",
    "BadRequestError",
    "API key has expired",
    "Key limit exceeded",
)

LOG_GLOB = ("agent.log*", "errors.log*")


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"local-model-scan: {what} not found ({path})", file=sys.stderr)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f"local-model-scan: cannot read {what}: {exc}", file=sys.stderr)
        return None


def _read_config_models(cfg_text: str) -> dict:
    """Bare-ids from config.yaml: model.default, model.provider, cron.model,
    fallback_providers[].model (line-key extraction, no YAML dep)."""
    out = {"default": None, "provider": None, "cron_model": None, "fallbacks": []}
    in_model, in_fallback, in_cron = False, False, False
    for line in cfg_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("model:") and not line.startswith("model_"):
            in_model, in_fallback, in_cron = True, False, False
            continue
        if re.match(r"^cron:\s*$", line):
            in_model, in_fallback, in_cron = False, False, True
            continue
        if re.match(r"^[a-z_]+:", line) and not line.startswith(" ") and not line.startswith("-"):
            if line.startswith("fallback_providers:"):
                in_model, in_fallback, in_cron = False, True, False
            else:
                in_model, in_fallback, in_cron = False, False, False
            continue
        if in_cron and re.match(r"^\s+model:", line):
            out["cron_model"] = stripped.split(":", 1)[1].strip()
            continue
        if in_fallback:
            if re.match(r"^\s*-\s*provider:", line):
                out["fallbacks"].append({})
            elif out["fallbacks"] and re.match(r"^\s+model:", line):
                out["fallbacks"][-1]["model"] = stripped.split(":", 1)[1].strip()
            continue
        if in_model:
            m = re.match(r"^\s+(default|provider):\s*(\S+)", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def _strip_label(model_id: str | None) -> str | None:
    if not model_id:
        return model_id
    return model_id.split(":", 1)[1] if ":" in model_id and not model_id.startswith("openrouter:") else (
        model_id.split(":", 1)[1] if model_id.startswith("openrouter:") else model_id)


def _pool_members(list_doc: dict) -> set[str]:
    out = set()
    for tier, specs in (list_doc.get("tiers") or {}).items():
        for spec, models in (specs or {}).items():
            for m in models or []:
                out.add(m)
    return out


def _recent_log_markers() -> list[str]:
    hits = []
    files = []
    for pat in LOG_GLOB:
        files.extend(sorted(LOG_DIR.glob(pat)))
    # last 3 rotated files, tail 400 lines each
    for f in files[-3:]:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            continue
        for line in lines:
            low = line.lower()
            for marker in LOG_MARKERS:
                if marker.lower() in low:
                    hits.append(f"{f.name}: {line.strip()[:200]}")
                    break
    return hits[:10]


def main() -> int:
    cfg_text = None
    try:
        cfg_text = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"local-model-scan: config.yaml not found ({CONFIG_PATH})", file=sys.stderr)
        return 3
    cfg = _read_config_models(cfg_text)
    list_doc = _read_json(ROUTER_LIST_PATH, "router-models.json")
    jobs = _read_json(JOBS_PATH, "jobs.json")
    routing = _read_json(ROUTING_PATH, "routing.json")
    if list_doc is None or jobs is None:
        return 3

    findings: list[str] = []
    baseline = (list_doc.get("baseline") or "").strip()
    baseline_bare = _strip_label(baseline)

    # 1. Session default vs router baseline
    default = cfg.get("default")
    if not default:
        findings.append("config.yaml model.default is missing/empty")
    else:
        default_bare = _strip_label(default)
        if default_bare != baseline_bare:
            findings.append(
                f"session default {default!r} != router baseline {baseline!r} (label/bare mismatch or stale re-anchor)")
        if default.startswith("openrouter:") or ":" in default:
            # a label in config is a 400-every-turn regression; flag unless it
            # is the bare id itself (which contains a provider/model slash)
            if not re.match(r"^[a-z0-9-]+/[a-z0-9._-]+$", default):
                findings.append(f"routing LABEL in config model.default: {default!r}")

    # 2. Known-bad ids
    for surface, value in (("model.default", default), ("cron.model", cfg.get("cron_model"))):
        if value and any(bad in value for bad in KNOWN_BAD_IDS):
            findings.append(f"known-bad id on {surface}: {value!r}")
    for fb in cfg.get("fallbacks") or []:
        m = fb.get("model")
        if m and any(bad in m for bad in KNOWN_BAD_IDS):
            findings.append(f"known-bad id in fallback_providers: {m!r}")

    # 3. Cron per-job pins
    for job in jobs.get("jobs", []):
        name = job.get("name", "?")
        if job.get("model"):
            findings.append(f"cron {name}: per-job model pin {job['model']!r} (unauthorized override)")

    # 4. Live log markers
    hits = _recent_log_markers()
    if hits:
        findings.append("live-log rejection/fallback markers:")
        findings.extend(f"  - {h}" for h in hits)

    # 5. Router consistency (routing.json pool heads are vetted list members)
    if routing is not None:
        vetted = _pool_members(list_doc)
        for tier, specs in (routing.get("tiers") or {}).items():
            for spec, models in (specs or {}).items():
                if not models:
                    continue
                head = models[0]
                if head not in vetted:
                    findings.append(f"routing.json {tier}/{spec} head {head!r} not in vetted list")

    if findings:
        print("🔎 Model usage scan — findings:")
        for f in findings:
            print(f"  - {f}")
    # else: silent
    return 0


if __name__ == "__main__":
    sys.exit(main())
