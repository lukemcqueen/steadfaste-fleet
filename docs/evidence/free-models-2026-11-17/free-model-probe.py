#!/usr/bin/env python3
"""free-model-probe — the documented process for 'getting the free models'.

1. Fetch the LIVE OpenRouter catalog (no key).
2. Enumerate the $0.00/M text-chat models (pricing.prompt==0 and completion==0).
3. Treat a listing as NOT proof of callability: keyed POST /chat/completions
   (max_tokens:1) each free candidate, recording the real HTTP outcome.
4. Compare each reachable free model's capability surface (context, tools,
   vision) against the fleet's currently ROUTED open models.

Read-only against the fleet: no config write. Prints JSON + a human summary.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

ENV = Path.home() / ".hermes" / ".env"
CATALOG = "https://openrouter.ai/api/v1/models"
CHAT = "https://openrouter.ai/api/v1/chat/completions"
REPO = Path.home() / "steadfaste-fleet"
LIST = REPO / "config" / "router-models.json"


def api_key() -> str:
    for line in ENV.read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def fetch_catalog() -> dict:
    req = urllib.request.Request(CATALOG, headers={"User-Agent": "steadfaste-free-probe"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def probe(model: str, key: str) -> dict:
    body = json.dumps({"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}).encode()
    req = urllib.request.Request(CHAT, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "User-Agent": "steadfaste-free-probe"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode("utf-8"))
        u = data.get("usage") or {}
        choice = (data.get("choices") or [{}])[0]
        return {"reachable": True, "http": 200,
                "served_model": data.get("model"),
                "out_tokens": u.get("completion_tokens"),
                "content_present": bool(choice.get("message", {}).get("content"))}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return {"reachable": False, "http": e.code, "error": detail}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "http": None, "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    key = api_key()
    if not key:
        print("COULD NOT VERIFY: no OPENROUTER_API_KEY in ~/.hermes/.env", file=sys.stderr)
        return 3
    cat = fetch_catalog()
    free = []
    for m in cat.get("data", []):
        mid = str(m.get("id", ""))
        p = m.get("pricing") or {}
        try:
            pin = float(p.get("prompt", -1))
            pout = float(p.get("completion", -1))
        except (TypeError, ValueError):
            continue
        if pin != 0.0 or pout != 0.0:
            continue
        arch = m.get("architecture") or {}
        outs = arch.get("output_modalities") or []
        ins = arch.get("input_modalities") or []
        if "text" not in outs:
            free.append({"id": mid, "ctx": m.get("context_length"),
                         "skip": "non-text:" + ",".join(outs or ["?"])})
            continue
        sp = m.get("supported_parameters") or []
        free.append({
            "id": mid,
            "ctx": m.get("context_length"),
            "tools": "tools" in sp,
            "vision": "image" in ins,
            "reasoning": "reasoning" in sp,
            "name": m.get("name"),
        })

    chat_free = [f for f in free if "skip" not in f]
    print(f"# live catalog models: {len(cat.get('data', []))}")
    print(f"# $0.00/M models: {len(free)}  (text-chat: {len(chat_free)})")

    for f in chat_free:
        f["probe"] = probe(f["id"], key)

    routed = {}
    try:
        doc = json.loads(LIST.read_text())
        ids = sorted({m for specs in (doc.get("tiers") or {}).values()
                      for models in specs.values() for m in models})
        routed = {"ids": ids, "baseline": doc.get("baseline"),
                  "baseline_price_usd_per_m": doc.get("baseline_price_usd_per_m")}
    except Exception as e:  # noqa: BLE001
        routed = {"error": str(e)}

    print("\n===== TEXT-CHAT FREE MODELS ($0.00/M) =====")
    for f in sorted(chat_free, key=lambda x: x["id"]):
        pb = f["probe"]
        state = "REACHABLE" if pb.get("reachable") else f"UNREACHABLE(http={pb.get('http')})"
        print(f"  {f['id']:<48} ctx={str(f['ctx']):<9} tools={str(f['tools']):<5} "
              f"vision={str(f['vision']):<5} reasoning={str(f['reasoning']):<5} -> {state}")
        if not pb.get("reachable"):
            print(f"      reason: {pb.get('error','')[:160]}")
        else:
            print(f"      served={pb.get('served_model')} out_tok={pb.get('out_tokens')}")

    print("\n===== NON-TEXT $0.00/M (excluded) =====")
    for f in free:
        if "skip" in f:
            print(f"  {f['id']:<48} {f['skip']}")

    print("\n===== CURRENTLY ROUTED OPEN MODELS (for comparison) =====")
    print(json.dumps(routed, indent=2))

    out = Path.home() / ".hermes/cache/scratch/free-model-probe.json"
    out.write_text(json.dumps({"free": free, "chat_free": chat_free,
                               "routed": routed}, indent=2) + "\n")
    print(f"\nraw -> {str(out).replace(str(Path.home()), '<HOME>')}")
    print("NOTE: '0 endpoints ... guardrail restrictions and data policy' (HTTP 404) = "
          "blocked by this account's OpenRouter data policy, not a transient error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
