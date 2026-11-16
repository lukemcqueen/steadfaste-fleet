"""Hermetic tests for the model-market scan (orch-model-market-scan.py).

No network, no ambient HOME/repo state: the module's catalog source and list
path are monkeypatched to synthetic inputs, and MODEL_MARKET_STATE is
redirected to a tmp file. Proves the deterministic re-price / re-order /
unvetted-candidate logic the daily cron relies on.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "ops" / "scripts" / "manage" / "orch-model-market-scan.py"


def load_scan():
    spec = importlib.util.spec_from_file_location("orch_model_market_scan", SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SYNTH_CATALOG = {
    "fakeprov": {"models": {
        "alpha": {"cost": {"input": 0.1, "output": 0.2}},   # blended 0.30
        "beta":  {"cost": {"input": 0.05, "output": 0.10}},  # blended 0.15
        "gamma": {"cost": {"input": 0.4, "output": 0.5}},    # blended 0.90
        "zeta":  {"cost": {"input": 0.01, "output": 0.01}},  # blended 0.02 (unvetted cheap)
    }}
}


def _list_doc(beta_first=True):
    head, tail = ("beta", "alpha") if beta_first else ("alpha", "beta")
    return {
        "captured": "test",
        "baseline": "openrouter:fakeprov/gamma",  # baseline is the session default, above pool heads
        "policy": {
            "medium": {"max_multiple_of_baseline": 2.0},
            "hard": {"max_price_usd_per_m": 3.0, "whitelist_only": False},
        },
        "prices": {
            "openrouter:fakeprov/alpha": {"in": 0.1, "out": 0.2},
            "openrouter:fakeprov/beta": {"in": 0.05, "out": 0.10},
            "openrouter:fakeprov/gamma": {"in": 0.4, "out": 0.5},
        },
        "tiers": {"medium": {"coding": [f"openrouter:fakeprov/{head}", f"openrouter:fakeprov/{tail}"]}},
    }


def test_price_for_resolves_prefix_provider_bare():
    mod = load_scan()
    got = mod.price_for(SYNTH_CATALOG, "openrouter:fakeprov/alpha")
    assert got == {"in": 0.1, "out": 0.2}
    # bare form without a routing prefix
    assert mod.price_for(SYNTH_CATALOG, "fakeprov/gamma") == {"in": 0.4, "out": 0.5}
    # unknown model -> empty
    assert mod.price_for(SYNTH_CATALOG, "openrouter:missing/nope") == {}


def test_reorder_pool_cheapest_first_stable():
    mod = load_scan()
    prices = {"openrouter:fakeprov/alpha": {"in": 0.1, "out": 0.2},
              "openrouter:fakeprov/beta": {"in": 0.05, "out": 0.10}}
    order = mod.reorder_pool(["openrouter:fakeprov/alpha", "openrouter:fakeprov/beta"], prices)
    # cheapest (beta, 0.15) first even though alpha was first in the input
    assert order == ["openrouter:fakeprov/beta", "openrouter:fakeprov/alpha"]
    # stable: ties keep input order
    tie_prices = {"openrouter:fakeprov/alpha": {"in": 0.1, "out": 0.2},
                  "openrouter:fakeprov/gamma": {"in": 0.1, "out": 0.2}}  # gamma == alpha price
    tie = mod.reorder_pool(["openrouter:fakeprov/alpha", "openrouter:fakeprov/gamma"], tie_prices)
    assert tie == ["openrouter:fakeprov/alpha", "openrouter:fakeprov/gamma"]


def test_scan_detects_reorder_and_unvetted(tmp_path, monkeypatch):
    mod = load_scan()
    list_path = tmp_path / "jev-router-models.json"
    list_path.write_text(json.dumps(_list_doc(beta_first=False)), encoding="utf-8")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(mod, "default_list", lambda: list_path)
    monkeypatch.setattr(mod, "get_catalog", lambda: (SYNTH_CATALOG, "test"))
    monkeypatch.setattr(mod, "STATE_PATH", str(state_path))

    rc = mod.main(["--json"])
    # alpha is head in input but beta is cheaper -> reorder pending -> REORDER (1)
    assert rc == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["apply_pending"] is True
    assert state["order_changed"] is True
    # beta promoted to head, alpha demoted
    assert state["tiers"]["medium"]["coding"][0] == "openrouter:fakeprov/beta"
    # zeta (0.02) is cheaper than the new head beta (0.15) and not in the list -> alert
    assert state["unvetted_cheaper_candidates"]
    assert any(
        c["model"] == "fakeprov/zeta"
        for cands in state["unvetted_cheaper_candidates"].values() for c in cands
    )


def test_scan_noop_when_unchanged(tmp_path, monkeypatch):
    mod = load_scan()
    list_path = tmp_path / "jev-router-models.json"
    list_path.write_text(json.dumps(_list_doc(beta_first=True)), encoding="utf-8")
    state_path = tmp_path / "state.json"
    # catalog WITHOUT the cheap unvetted 'zeta' -> no alert, prices match the list
    noop_catalog = {"fakeprov": {"models": {
        m: e for m, e in SYNTH_CATALOG["fakeprov"]["models"].items() if m != "zeta"
    }}}
    monkeypatch.setattr(mod, "default_list", lambda: list_path)
    monkeypatch.setattr(mod, "get_catalog", lambda: (noop_catalog, "test"))
    monkeypatch.setattr(mod, "STATE_PATH", str(state_path))

    rc = mod.main(["--json"])
    # list is already beta-first (cheapest head), prices match, no cheaper unvetted -> no-op
    assert rc == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["apply_pending"] is False
