#!/usr/bin/env python3
"""Write the cost-ordered routing-pools draft — the fallback half of
install-router-layer.sh step 4.

The installer prefers the upstream `jev models suggest --write`, which bands by
price. On the first host it was installed on that put a model at the head of
medium/coding costing 6.5x what the fleet already runs — and first fit wins, so
routing as-shipped would have RAISED spend on every medium coding turn. This
script is therefore the writer of record: it fronts every simple/* pool with the
cheapest capable flash and every medium/* pool with the model the fleet already
runs, leaves hard/* alone (escalation there is deliberate spend, not a mistake),
and never fronts a kimi model (dearer than what the fleet runs).

It is a separate script because it was an inline heredoc inside the installer,
which made the fallback branch impossible to exercise: the rescue path that no
test can run is the path that rots (review finding ADV-14913-1). The fallback's
normal inputs — no pools file at all, or unreadable JSON — are its normal path
and yield a minimal draft rather than an error, so the installer can always
rescue.

Usage:
  python3 ops/scripts/manage/router-pools-seed.py <pools.json>

Exit: 0 written · 2 bad usage.
"""

from __future__ import annotations

import json
import pathlib
import sys

CHEAP = "openrouter:qwen/qwen3.7-flash"                 # cheapest capable flash we found
TRUSTED = "openrouter:deepseek/deepseek-v4.1-flash"     # the model the fleet already runs


def front(models: list[str] | None, first: str) -> list[str]:
    out = [first]
    for model in models or []:
        if "moonshotai/kimi" in model:  # dearer than what we run: never route there by accident
            continue
        if model not in out:
            out.append(model)
    return out


def seed(path: pathlib.Path) -> str:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        data = {"mode": "redacted-text", "exclude": ["*:free", "cloudflare-ai-gateway:*"], "tiers": {}}
    tiers = data.setdefault("tiers", {})
    for tier, first in (("simple", CHEAP), ("medium", TRUSTED)):
        for spec, models in (tiers.get(tier) or {}).items():
            tiers[tier][spec] = front(models, first)
    # `hard` is left alone: escalation there is deliberate spend, not a mistake.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return (f"  wrote {path} — simple/* leads with {CHEAP}, medium/* with {TRUSTED}, "
            f"hard/* untouched")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: router-pools-seed.py <pools.json>", file=sys.stderr)
        return 2
    print(seed(pathlib.Path(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
