#!/usr/bin/env python3
"""orch-model-market-scan — daily deterministic re-price + re-order of the fleet router list.

Why this exists: the router spends money on POOL ORDER (first fit wins), and the
shared list config/jev-router-models.json carries per-model $/M captured once
("captured" date). Prices move, so a daily job re-prices the list from the LIVE
OpenRouter catalog and re-orders each pool cheapest-first WITHIN its current,
pre-vetted candidate set — that is the "cheaper, same quality" guarantee: no new
model is ever introduced automatically, only the order of models that were
already approved for that tier is re-arranged by today's price.

WHAT THIS SCRIPT DOES (deterministic, stdlib-only, portable to macOS):
  1. Fetch the live OpenRouter catalog (urllib; no curl/GNU flags). Fall back to
     the jev catalog cache if the network is unavailable; exit 3 (could not
     verify) if neither is readable.
  2. Re-price every model in the list (prices + all pool members) from the catalog.
  3. Re-order each tier/specialty pool cheapest-first by blended(in+out) per M,
     stable (ties keep list order). Reordering only moves cheaper models to the
     head, so it can never raise a head above the baseline or a ceiling.
  4. Invariant check on the reordered list (same rules as router-pools.py
     --selftest): non-hard heads cheaper than baseline, every model within the
     hard $/M cap (a pool member whose price ROSE over the cap is a FAIL to
     report, never auto-applied), hard whitelist respected.
  5. NEW-UNVETTED-CANDIDATE alert: any catalog model cheaper than a pool's current
     head that is not already routed/candidate/rejected in the list is reported
     as a quality-alert candidate (never auto-adopted — it needs an A/B first).
  6. Compare the re-priced/reordered list to the committed list. If they differ,
     "apply pending" is set (the orch cron's governed apply step commits it).
  7. Write ~/.steadfaste-fleet/state/model-market.json with a stable `signature`
     (hash of prices+pools+candidates) so the cron's monitor-gate skips unchanged
     runs cost-free, plus a human-readable summary.

EXIT CODES:
  0 = live prices/order match the committed list (nothing to apply) — silent.
  1 = prices/order changed → an apply is warranted (report the delta).
  2 = quality alert only: new unvetted cheaper candidate(s) found, no apply pending.
  3 = could not verify — no catalog/cache readable, OR the re-priced list breaches
      an invariant (a model over the hard $/M cap, a head not below baseline /
      ceiling, or a listed model ABSENT from the live catalog) so it CANNOT be
      auto-applied. Violations fail closed to this code; they are listed in
      state/model-market.json for the operator, never applied.

This script only WRITES the state file; it does not commit, push, deploy, or edit
config/jev-router-models.json. Applying a pending change is the orchestrator
cron's governed job (begin_change -> apply -> doctor -> commit -> push -> deploy).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

RC_OK = 0
RC_APPLY = 1
RC_ALERT = 2
RC_COULD_NOT_VERIFY = 3

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_URL = "https://openrouter.ai/api/v1/models"

STATE_PATH = os.environ.get("MODEL_MARKET_STATE", "").strip() or (
    str(Path.home() / ".steadfaste-fleet" / "state" / "model-market.json"))


def blended(price: dict) -> float | None:
    """$/M on the same basis as router-pools.py: input + output per million.

    Returns None for an absent/malformed price — NEVER 0.0. 0.0 would price a
    corrupt or unpriceable model as free, sort it to the head of every pool
    (reorder_pool), and make it the cheapest 'head' for the unvetted comparison,
    silently reordering the router list on bad data."""
    if not price:
        return None
    try:
        return float(price.get("in") or 0) + float(price.get("out") or 0)
    except (TypeError, ValueError):
        return None


def per_m(price: dict | None) -> dict | None:
    """Catalog $/token price -> the $/M value the list stores (x1e6, rounded 6dp).

    The live OpenRouter feed prices per TOKEN while config/jev-router-models.json
    (and the policy hard cap) are per MILLION tokens. A direct dict compare (the
    old price_changed) or a blended-vs-cap test on raw catalog values was thus
    comparing different units by a factor of 1e6 — making price_changed always
    True (rc could never read 0 or 2) and the hard-cap check never fire. Converting
    here to the same 6dp the apply round-trips restores both to the list's $/M
    basis, so an unchanged model compares equal and an over-cap model trips."""

    if not price:
        return None
    try:
        return {"in": round(float(price.get("in") or 0) * 1e6, 6),
                "out": round(float(price.get("out") or 0) * 1e6, 6)}
    except (TypeError, ValueError):
        return None


def list_price_match(token: dict | None, listed: dict | None) -> bool:
    """True when a catalog $/token price equals the list's $/M in+out on an
    UNCHANGED model.

    The committed list carries extra provenance keys per entry (e.g.
    cache_read) that the scan does not price; comparing whole dicts would make
    every model read as 'changed' even when its fresh-token price is identical,
    so price_changed cannot ever return 0/2. Compare only the in/out the scan
    prices, each on the $/M basis (see per_m)."""

    pm = per_m(token)
    if pm is None or not isinstance(listed, dict):
        return False
    try:
        return (pm["in"] == round(float(listed.get("in") or 0), 6)
                and pm["out"] == round(float(listed.get("out") or 0), 6))
    except (TypeError, ValueError):
        return False


def default_list() -> Path:
    """The pricing list, repo checkout first, deployed copy second (like the pool tool)."""
    for candidate in (REPO_ROOT / "config" / "jev-router-models.json",
                      Path.home() / ".steadfaste-fleet" / "config" / "jev-router-models.json"):
        if candidate.is_file():
            return candidate
    return REPO_ROOT / "config" / "jev-router-models.json"


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"{path}: {exc}"


def fetch_catalog() -> dict:
    """Live OpenRouter catalog -> {provider: {bare_model: {cost: {input, output}}}}.

    Raises RuntimeError on network failure so the caller can fall back to cache.
    """
    req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "steadfaste-fleet-model-scan"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: dict = {}
    for m in data.get("data", []):
        mid = str(m.get("id", ""))
        provider, _, bare = mid.partition("/")
        if not bare:
            continue
        p = m.get("pricing") or {}
        out.setdefault(provider, {}).setdefault("models", {})[bare] = {
            "cost": {"input": float(p.get("prompt", 0) or 0),
                     "output": float(p.get("completion", 0) or 0)}}
    return out


def catalog_cache_paths() -> list:
    env = os.environ.get("JEV_CATALOG_CACHE", "").strip()
    paths = [Path(env)] if env else []
    paths += [Path.home() / ".hermes" / "models_dev_cache.json",
              Path.home() / ".cache" / "jev" / "models_dev.json"]
    return paths


def _cache_shape_ok(raw) -> bool:
    """True iff raw is {provider: {"models": {bare: {"cost": {...}}}}} — the shape
    both fetch_catalog() (live) and the Jev plugin cache (/v1/models) write. A
    cache of any other shape would make price_for() silently return empty prices
    for every model, so we reject it loudly instead of trusting an unknown shape."""
    if not isinstance(raw, dict):
        return False
    for prov, body in raw.items():
        if not isinstance(body, dict) or not isinstance(body.get("models"), dict):
            return False
        for bare, entry in body["models"].items():
            if not isinstance(entry, dict) or not isinstance(entry.get("cost"), dict):
                return False
    return True


def get_catalog() -> tuple[dict, str]:
    """Live catalog, falling back to a validated plugin cache; raises on total failure.

    Every rejection (live fetch, missing cache path, unparsable cache, wrong cache
    shape) is recorded with its reason and surfaced in the final exception, so a
    corrupt/misshapen cache is distinguishable from no cache at all — never a silent
    'nothing to do'."""
    reasons: list = []
    try:
        return fetch_catalog(), "live"
    except Exception as exc:  # noqa: BLE001 - network can fail many ways; fall back
        reasons.append(f"live HTTP fetch failed: {exc}")
    for path in catalog_cache_paths():
        if not path.is_file():
            reasons.append(f"{path}: cache file not present")
            continue
        raw, err = load_json(path)
        if raw is None:
            reasons.append(f"{path}: unparsable ({err})")
            continue
        if not _cache_shape_ok(raw):
            reasons.append(f"{path}: unexpected shape (expected {{provider: {{models: {{bare: {{cost: ...}}}}}}}})")
            continue
        return raw, f"cache {path}"
    raise RuntimeError("no catalog available; every source rejected:\n  " + "\n  ".join(reasons))


def price_for(catalog: dict, model: str) -> dict:
    """Look up {in, out} prices for a model in the catalog.

    List ids are 'openrouter:<provider>/<model>' (or '<provider>/<model>'): strip
    any 'label:' routing prefix, then split the remainder on the FIRST '/' into
    provider + bare model id, and index the catalog by provider -> models -> id.
    """
    mid = model
    if ":" in mid:
        mid = mid.split(":", 1)[1]
    provider, _, bare = mid.partition("/")
    if not provider or not bare:
        return {}
    prov = catalog.get(provider) or {}
    entry = (prov.get("models") or {}).get(bare)
    if entry and entry.get("cost"):
        c = entry["cost"]
        return {"in": float(c.get("input", 0) or 0), "out": float(c.get("output", 0) or 0)}
    return {}


def reorder_pool(models: list, prices: dict) -> list:
    """Stable sort cheapest-first by blended $/M (in+out). Never drops a model.

    Unpriceable models (blended None) are pushed to the END, stable, so a model
    with no/ corrupt price can never become a pool head (a 0.0 sort would put it
    first and mislead the head comparisons + unvetted scan)."""
    keyed = [(blended(prices.get(m, {})), idx, m) for idx, m in enumerate(models)]
    return [m for _, _, m in sorted(
        keyed,
        key=lambda t: (t[0] is None, t[0] if t[0] is not None else float("inf"), t[1]))]


def signature(doc: dict) -> str:
    """Stable hash of the parts that decide whether an apply is warranted."""
    blob = {
        "captured": str(doc.get("captured", "")),
        "prices": doc.get("prices", {}),
        "tiers": doc.get("tiers", {}),
    }
    raw = json.dumps(blob, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--json" in argv:
        argv.remove("--json")
        want_json = True
    else:
        want_json = False

    list_path = default_list()
    doc, err = load_json(list_path)
    if doc is None:
        print(f"COULD NOT VERIFY: cannot read list: {err}", file=sys.stderr)
        return RC_COULD_NOT_VERIFY

    try:
        catalog, src = get_catalog()
    except RuntimeError as exc:
        print(f"COULD NOT VERIFY: {exc}", file=sys.stderr)
        return RC_COULD_NOT_VERIFY

    prices_old = dict(doc.get("prices") or {})
    tiers_old = doc.get("tiers") or {}
    policy = doc.get("policy") or {}
    baseline = doc.get("baseline")
    hard_cap = (policy.get("hard") or {}).get("max_price_usd_per_m")
    whitelist = set(doc.get("hard_whitelist") or [])

    # 1+2. Re-price every listed model + every pool member from the catalog.
    routed = {m for specs in tiers_old.values() for models in specs.values() for m in models}
    candidates = set(doc.get("candidates") or {})
    rejected = set(doc.get("rejected") or {})
    prices_new = {}
    missing = []
    for model in sorted(set(prices_old) | routed | candidates | rejected):
        price = price_for(catalog, model)
        if not price:
            missing.append(model)
            continue
        prices_new[model] = price

    # 3. Re-order pools cheapest-first within their existing candidate set.
    tiers_new = {}
    violations = []
    base_price = blended(prices_new.get(baseline, {}))
    for tier, specs in tiers_old.items():
        ceiling = (policy.get(tier) or {}).get("max_multiple_of_baseline")
        tiers_new[tier] = {}
        for spec, models in specs.items():
            ordered = reorder_pool(models, prices_new)
            tiers_new[tier][spec] = ordered
            head = ordered[0] if ordered else None
            if not head:
                continue
            head_price = blended(prices_new.get(head, {}))
            if head_price is None:
                continue  # unpriceable head -> already in missing_models; can't validate invariants
            if tier != "hard" and base_price and head_price >= base_price:
                violations.append(f"{tier}/{spec} head {head} (${head_price:.3f}/M) not below baseline ${base_price:.3f}/M")
            if ceiling is not None and base_price and head_price > base_price * float(ceiling):
                violations.append(f"{tier}/{spec} head {head} over {float(ceiling):g}x ceiling")
            if tier == "hard" and (policy.get("hard") or {}).get("whitelist_only") and head not in whitelist:
                violations.append(f"{tier}/{spec} head {head} not on the hard whitelist")

    # 4. Hard $/M cap: any non-rejected listed/routed model over the cap is a FAIL,
    #    never auto-presented (a price that ROSE over the cap must be dropped by hand).
    if hard_cap is not None:
        for model, price in prices_new.items():
            if model in rejected:
                continue
            bm = blended(per_m(price) or {})  # $/M (catalog $/token -> per-M basis)
            if bm is None:
                continue  # unpriceable -> already in missing_models; don't score as $0
            if bm > float(hard_cap):
                violations.append(f"{model} is ${bm:.3f}/M, above the ${float(hard_cap):.2f}/M hard cap")

    # 4b. A model the live catalog no longer lists is NOT a price move: we cannot
    #     verify it is servable, so the re-priced list must never be auto-applied.
    #     Without this, a vanished id was only printed as 'unpriceable : [...]'
    #     while the exit code still reported a routine apply (1 = apply_pending,
    #     because prices_new.get(k) is None != the old price), and reorder_pool()
    #     sank it to the back of its pool — leaving a dead id in the list AND in
    #     the host's model.default, with no gate anywhere in between.
    for model in missing:
        violations.append(
            f"{model} is not in the live catalog (no price) — cannot verify it is servable")

    # 5. NEW unvetted cheaper candidates: catalog models cheaper than a pool head
    #    that are not already routed/candidate/rejected. Quality gated: report only.
    existing = routed | candidates | rejected | set(prices_new)
    alerts = {}
    for tier, specs in tiers_new.items():
        for spec, models in specs.items():
            if not models:
                continue
            head_price = blended(prices_new.get(models[0], {}))
            if not head_price:
                continue
            cheaper = []
            for provider, prov in catalog.items():
                for bare, entry in (prov.get("models") or {}).items():
                    c = entry.get("cost")
                    if not c:
                        continue
                    pb = float(c.get("input", 0) or 0) + float(c.get("output", 0) or 0)
                    # pb > 0: providers price dynamic-pricing metas as -1/-1
                    # sentinels (blended -2.0), which is truthy and '< any head' —
                    # a sentinel is not a lower REAL price and must never be
                    # offered as a candidate for an A/B that cannot run.
                    if pb > 0 and pb < head_price:
                        full = f"{provider}/{bare}"
                        if full in existing:
                            continue
                        cheaper.append((pb, full, c.get("input", 0), c.get("output", 0)))
            if cheaper:
                cheaper.sort(key=lambda t: t[0])
                alerts[f"{tier}/{spec}"] = [
                    {"model": full, "blended": round(pb, 4),
                     "in": round(i, 4), "out": round(o, 4),
                     "vs_head_usd": round(head_price - pb, 4)}
                    for pb, full, i, o in cheaper[:5]]

    # 6. Did prices or order change vs the committed list? Compare on the SAME
    #    $/M basis (catalog $/token converted via per_m) — a raw compare of
    #    $/token against the list's $/M would be unequal for every model, so the
    #    rc ladder could never return 0 (nothing to do) or 2 (alert only).
    order_changed = tiers_new != tiers_old
    price_changed = any(not list_price_match(prices_new.get(k), v)
                        for k, v in prices_old.items())
    apply_pending = bool(order_changed or price_changed)

    # 7. Write state + signature for the cron gate.
    refreshed_doc = dict(doc)
    refreshed_doc["captured"] = "live daily scan"
    refreshed_doc["prices"] = prices_new
    refreshed_doc["tiers"] = tiers_new
    state = {
        "captured": "live daily scan",
        "catalog_source": src,
        "list": str(list_path),
        "prices": prices_new,
        "tiers": tiers_new,
        "order_changed": order_changed,
        "price_changed": price_changed,
        "apply_pending": apply_pending,
        "missing_models": missing,
        "violations": violations,
        "unvetted_cheaper_candidates": alerts,
        "signature": signature(refreshed_doc),
    }
    try:
        Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(STATE_PATH).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"COULD NOT VERIFY: cannot write state {STATE_PATH}: {exc}", file=sys.stderr)
        return RC_COULD_NOT_VERIFY

    if violations:
        # An invariant violation (a model over the hard cap, or a head not below
        # baseline/ceiling) means the re-priced list is NOT safe to auto-apply —
        # fail closed as 'could not verify', never present it as a normal apply
        # or a quality alert. The violations are in the state file for the operator.
        rc = RC_COULD_NOT_VERIFY
    elif apply_pending:
        rc = RC_APPLY
    elif alerts:
        rc = RC_ALERT
    else:
        rc = RC_OK

    summary = {
        "rc": rc,
        "signature": state["signature"],
        "apply_pending": apply_pending,
        "order_changed": order_changed,
        "price_changed": price_changed,
        "catalog_source": src,
        "violations": len(violations),
        "unvetted_candidates": len(alerts),
        "missing_models": missing,
    }
    if want_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"model market scan: captured from {src} ({state['captured']})")
        print(f"  signature      : {state['signature']}")
        print(f"  apply_pending  : {apply_pending} (prices {price_changed}, order {order_changed})")
        print(f"  violations     : {violations if violations else 'none'}")
        if missing:
            print(f"  unpriceable    : {missing}")
        if alerts:
            print(f"  NEW cheaper unvetted candidates (quality-alert, NOT auto-adopted):")
            for spec, cands in alerts.items():
                print(f"    {spec}: " + ", ".join(f"{c['model']} (${c['blended']}/M)" for c in cands))
    return rc


if __name__ == "__main__":
    sys.exit(main())
