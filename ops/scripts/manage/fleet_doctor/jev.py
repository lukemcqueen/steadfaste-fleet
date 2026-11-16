"""The Jev decision layer: is it installed, and can it cost more than what we run?

Jev takes the cheap decisions (which model a turn needs, which skill to load, which
search results to open). It is OPTIONAL per host and installed by
`ops/scripts/manage/install-jev-layer.sh`, so this check reports the gap rather
than assuming it: WARN while a host is merely un-migrated, and a FAIL only when
the layer is actually ARMED (routing on) and its pools lead with a model dearer
than the one the host already runs.

That last rule is the whole reason this check exists. `jev models suggest --write`
bands by price, and on the first host it was installed on it put a model 5.4x our
default at the head of medium/coding — and first fit wins, so switching routing on
as-shipped would have raised spend on every medium coding turn. A doc cannot catch
that; a check can.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

CATALOG_CANDIDATES = [
    # Current Jev cache locations (plugin writes here).
    Path.home() / ".hermes" / "models_dev_cache.json",
    # Legacy cache location still used on some hosts.
    Path.home() / ".cache" / "jev" / "models_dev.json",
]
# A pool may lead with a model up to this much dearer than the current one before it
# is worth reporting: prices move, and a hair over is not a finding.
TOLERANCE = 1.5


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _routing() -> dict:
    try:
        data = json.loads((_hermes_home() / "jev" / "routing.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _routing_mode() -> str:
    """The `/jev routing` switch: a profile file wins over the shared one."""
    home = _hermes_home()
    root = home.parent.parent if home.parent.name == "profiles" else home
    state = {}
    for path in (root / "jev" / "state.json", home / "jev" / "state.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state.update(data)
        except (OSError, json.JSONDecodeError):
            continue
    return str(state.get("routing", "off")).lower()


def _catalog_prices() -> dict:
    """{provider:model -> blended $/M} from the local catalog cache (no network)."""
    raw = None
    for path in CATALOG_CANDIDATES:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            continue
    if not isinstance(raw, dict):
        return {}
    prices = {}
    # models.dev is keyed BY PROVIDER at the top level (`{"openrouter": {"models": …}}`).
    # Looking under a "providers" key returned nothing, which silently made the cost
    # guard inert — the check reported "price unknown" on a host with a full catalog.
    providers = raw if isinstance(raw, dict) else {}
    for provider, entry in providers.items():
        models = (entry or {}).get("models") if isinstance(entry, dict) else None
        if not isinstance(models, dict):
            continue
        for model, spec in models.items():
            if not isinstance(spec, dict):
                continue
            cost = spec.get("cost")
            if not isinstance(cost, dict):
                continue
            try:
                prices[f"{provider}:{model}"] = float(cost.get("input", 0)) + float(cost.get("output", 0))
            except (TypeError, ValueError):
                continue
    return prices


def _config_model(home: Path):
    """(provider, default) from config.yaml's `model:` block — NO hermes subprocess.

    Replaces the old `hermes config get model` subprocess (≈8s of Hermes plugin +
    tool startup on first boot). `hermes config get model` echoes config.yaml's
    `model:` section verbatim (default/provider/base_url/api_mode) and does NOT
    resolve fallback_providers, so reading config.yaml is bit-identical in steady
    state — verified against `hermes config get model` output on moses. YAML is
    used when importable; otherwise a deterministic line-scan scoped to the
    `model:` block (the indent check is what stops it from catching the
    TOP-LEVEL fallback_providers' `model:`/`provider:` keys). Both the yaml
    path and the no-yaml line-scan are pinned by tests/test_doctor_model_config.py
    (test_line_scan_fallback_scopes_to_the_model_block forces the ImportError
    path and asserts fallback_providers keys are never read as the running model).
    """
    try:
        text = (home / "config.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    try:
        import yaml as _yml
        cfg = _yml.safe_load(text)
        mdl = cfg.get("model") if isinstance(cfg, dict) else None
        if isinstance(mdl, dict):
            return str(mdl.get("provider") or ""), str(mdl.get("default") or "")
    except Exception:  # noqa: BLE001 - fall back to the text scan below
        pass
    in_block = False
    provider = default = ""
    for line in text.splitlines():
        if line.startswith("model:") and not line[:1].isspace():
            in_block = True
            continue
        if not in_block:
            continue
        if line.strip() and not line[:1].isspace():
            break  # left the model: block
        s = line.strip()
        if s.startswith("default:"):
            default = s.split(":", 1)[1].strip().strip("'\"")
        elif s.startswith("provider:"):
            provider = s.split(":", 1)[1].strip().strip("'\"")
    return provider, default


def _current_model() -> str:
    """The model this host actually runs, as `provider:model` (best effort)."""
    provider, default = _config_model(_hermes_home())
    if default and provider and ":" not in default:
        return f"{provider}:{default}"
    if default:
        return default
    return ""


def _price_for(prices: dict, model: str, prefer: str = "openrouter"):
    """Blended $/M (input+output) for a model id, resolving the provider consistently.

    Two traps, both measured on the first live run of this check:
      * the host's configured model is written BARE (`deepseek/deepseek-v4.1-flash`)
        while the catalog keys it by provider, so an exact-key lookup made the guard
        report "price unknown" and never fire at all;
      * resolving one side through the exact key and the other through a
        cheapest-provider fallback compared the SAME model at two prices ($0.56 vs
        $0.34) and flagged it as dearer than itself.

    So both sides resolve through the same rule: exact key first, then the same id
    under the provider this host actually runs (`openrouter`), then the cheapest
    provider as a last resort.
    """
    if not model:
        return None
    if model in prices:
        return prices[model]
    bare = model.split(":", 1)[1] if ":" in model else model
    if f"{prefer}:{bare}" in prices:
        return prices[f"{prefer}:{bare}"]
    matches = [price for key, price in prices.items() if key.split(":", 1)[-1] == bare]
    return min(matches) if matches else None


def check_jev_layer(res) -> None:
    home = _hermes_home()
    plugin = home / "plugins" / "hermes-jev" / "plugin.yaml"
    installed = plugin.is_file() or shutil.which("jev") is not None
    if not installed:
        res.add(
            "Jev decision layer", "WARN",
            "not installed on this host (optional; takes the cheap decisions off the "
            "expensive model's plate)",
            "bash ops/scripts/manage/install-jev-layer.sh   (or the deployed copy at "
            "~/.steadfaste-fleet/scripts/manage/install-jev-layer.sh)",
        )
        return

    routing = _routing()
    tiers = routing.get("tiers") if isinstance(routing.get("tiers"), dict) else {}
    if not tiers:
        res.add(
            "Jev decision layer", "WARN",
            "installed but no routing pools configured — routing can never pick a model",
            "jev models suggest --write, then reorder each pool cheapest-first "
            "(see docs/design/jev-decision-layer.md)",
        )
        return

    mode = _routing_mode()
    pools = [(tier, spec, models[0]) for tier, specs in tiers.items()
             if isinstance(specs, dict)
             for spec, models in specs.items() if isinstance(models, list) and models]

    prices = _catalog_prices()
    current = _current_model()
    current_price = _price_for(prices, current)
    dearer = []
    if current_price:
        for tier, spec, first in pools:
            # `hard` is SUPPOSED to lead with a dearer model: escalating the genuinely
            # hard work is the point of the tier, not a cost mistake. Only simple and
            # medium are held to "not dearer than what we already run".
            if tier == "hard":
                continue
            price = _price_for(prices, first)
            if price and price > current_price * TOLERANCE:
                dearer.append(f"{tier}/{spec} leads with {first} "
                              f"(${price:.3f}/M vs ${current_price:.3f}/M for {current})")

    detail = (f"installed · routing {mode} · {len(pools)} pool(s) · "
              f"current model {current or 'unknown'}"
              + (f" (${current_price:.3f}/M)" if current_price else " (price unknown)"))
    if dearer:
        # Armed = real money. Otherwise it is a trap waiting for the switch.
        status = "FAIL" if mode == "on" else "WARN"
        res.add("Jev decision layer", status,
                detail + " — " + "; ".join(dearer),
                "reorder each pool cheapest-first in $HERMES_HOME/jev/routing.json "
                "(first fit wins) — see docs/design/jev-decision-layer.md")
        return
    if mode == "on" and not current_price:
        res.add("Jev decision layer", "WARN",
                detail + " — could not verify pool prices against the current model",
                "check `jev models list` and reorder the pools by cost")
        return
    res.add("Jev decision layer", "PASS", detail)
