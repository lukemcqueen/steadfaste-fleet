#!/usr/bin/env python3
"""Probe model SERVABILITY (not just reachability) from the free OpenRouter feed.

Usage:
    python3 probe-model-servability.py <provider>/<model> [<provider>/<model> ...]

Prints, per model: in / out / blended $/M, context length, endpoint count,
DISTINCT provider count, the `reasoning` verdict, and the provider names.
Exit 0 when every model is SERVABLE, 1 when any is SUSPECT, 2 on bad usage.

WHY: a model can be in the catalog, priced, and reachable, yet serve ONLY from
an upstream that rejects our message shape (a Bedrock-only model refusing
replayed reasoning content is the canonical case). Reachability is not
servability. Two FREE fields decide it:
  * DISTINCT providers — one upstream is one point of incompatibility;
  * `supported_parameters` — an upstream omitting `reasoning` cannot accept the
    reasoning Hermes replays, so a model whose EVERY upstream omits it is
    unservable at any price.

Unauthenticated: no key, no spend. Per-model latency/throughput are auth-only
and are deliberately NOT read here (a null there is a property of the call, not
evidence the provider publishes nothing).

Portability: stdlib only, no GNU-only flags, runs on macOS as well as Linux.
"""

import json
import sys
import urllib.request

MODELS_URL = "https://openrouter.ai/api/v1/models"


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def load_prices():
    """One catalog fetch for the whole run: {id: (in_per_m, out_per_m)}."""
    prices = {}
    for m in _get(MODELS_URL).get("data") or []:
        pricing = m.get("pricing") or {}
        try:
            prices[m.get("id")] = (
                float(pricing.get("prompt") or 0) * 1e6,
                float(pricing.get("completion") or 0) * 1e6,
            )
        except (TypeError, ValueError):
            continue
    return prices


def probe(model_id):
    data = _get(f"{MODELS_URL}/{model_id}/endpoints").get("data") or {}
    eps = data.get("endpoints") or []
    providers = sorted({e.get("provider_name") for e in eps if e.get("provider_name")})
    reasoning = [
        "reasoning" in (e.get("supported_parameters") or [])
        for e in eps
    ]
    return {
        "ctx": data.get("context_length"),
        "endpoints": len(eps),
        "providers": providers,
        # servable only if the model can be reached through MORE THAN ONE upstream
        # AND every upstream accepts replayed reasoning.
        "servable": bool(eps) and len(providers) > 1 and all(reasoning),
        "reasoning_all": bool(reasoning) and all(reasoning),
    }


def main(argv):
    ids = argv[1:]
    if not ids:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    prices = load_prices()
    suspect = 0
    for model_id in ids:
        try:
            r = probe(model_id)
        except Exception as exc:  # report, never a traceback
            print(f"{model_id:40s} PROBE FAILED: {exc}")
            suspect += 1
            continue

        money = prices.get(model_id)
        cash = (
            f"in=${money[0]:6.3f}/M out=${money[1]:6.3f}/M blend=${money[0] + money[1]:6.3f}/M"
            if money else
            "price=UNKNOWN"
        )
        verdict = "SERVABLE" if r["servable"] else "SUSPECT "
        if not r["servable"]:
            suspect += 1
        print(
            f"{model_id:40s} {cash} ctx={r['ctx']} ep={r['endpoints']} "
            f"providers={len(r['providers'])} reasoning_all={r['reasoning_all']} "
            f"{verdict} {r['providers']}"
        )
    return 1 if suspect else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
