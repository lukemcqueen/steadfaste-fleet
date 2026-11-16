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


# Catalog prices are $/TOKEN (the live OpenRouter feed) while the config list
# stores $/M = round($/token * 1e6, 6). The helpers keep this relationship so the
# unit gap the scanner must bridge (price_changed / hard-cap) is exercised.
SYNTH_CATALOG = {
    "fakeprov": {"models": {
        "alpha": {"cost": {"input": 0.0000001, "output": 0.0000002}},    # $/M 0.30
        "beta":  {"cost": {"input": 0.00000005, "output": 0.0000001}},   # $/M 0.15
        "gamma": {"cost": {"input": 0.0000004, "output": 0.0000005}},    # $/M 0.90
        "zeta":  {"cost": {"input": 0.00000001, "output": 0.00000001}},  # $/M 0.02 (unvetted cheap)
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
            # cache_read is a real provenance field the committed list carries on
            # every entry (cached-read pricing); it is NOT a fresh-token price the
            # scan prices, so it must not make an unchanged model read as 'changed'.
            "openrouter:fakeprov/alpha": {"in": 0.1, "out": 0.2, "cache_read": 0.0},
            "openrouter:fakeprov/beta": {"in": 0.05, "out": 0.10, "cache_read": 0.0},
            "openrouter:fakeprov/gamma": {"in": 0.4, "out": 0.5, "cache_read": 0.0},
        },
        "tiers": {"medium": {"coding": [f"openrouter:fakeprov/{head}", f"openrouter:fakeprov/{tail}"]}},
    }


def test_price_for_resolves_prefix_provider_bare():
    mod = load_scan()
    got = mod.price_for(SYNTH_CATALOG, "openrouter:fakeprov/alpha")
    assert got == {"in": 0.0000001, "out": 0.0000002}  # catalog $/token
    # bare form without a routing prefix
    assert mod.price_for(SYNTH_CATALOG, "fakeprov/gamma") == {"in": 0.0000004, "out": 0.0000005}
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


def _isolate_home(monkeypatch, tmp_path):
    # Point every Path.home() lookup at the sandbox so catalog_cache_paths() never
    # sees the host's real ~/.hermes / ~/.cache Jev caches.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)


def test_get_catalog_falls_back_to_valid_jev_cache(tmp_path, monkeypatch):
    mod = load_scan()
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "fetch_catalog", lambda: (_ for _ in ()).throw(RuntimeError("no network")))
    cache = tmp_path / "env-cache.json"
    # real Jev plugin cache shape: {provider: {models: {bare: {cost: {...}}}}}
    cache.write_text(json.dumps(SYNTH_CATALOG), encoding="utf-8")
    monkeypatch.setenv("JEV_CATALOG_CACHE", str(cache))

    cat, src = mod.get_catalog()
    assert src == f"cache {cache}"
    # price_for resolves from the validated cache — not silently empty/missing
    assert mod.price_for(cat, "openrouter:fakeprov/alpha") == {"in": 0.0000001, "out": 0.0000002}


def test_get_catalog_rejects_corrupt_and_misshapen_cache_with_reasons(tmp_path, monkeypatch):
    mod = load_scan()
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "fetch_catalog", lambda: (_ for _ in ()).throw(RuntimeError("no network")))

    corrupt = tmp_path / "env-cache.json"
    corrupt.write_text("{not json", encoding="utf-8")  # in JEV_CATALOG_CACHE slot
    wrong = tmp_path / ".hermes" / "models_dev_cache.json"
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.write_text(json.dumps({"flat": [1, 2, 3]}), encoding="utf-8")  # misshapen cache slot
    monkeypatch.setenv("JEV_CATALOG_CACHE", str(corrupt))

    try:
        mod.get_catalog()
        assert False, "get_catalog must raise, not silently return a bad/empty catalog"
    except RuntimeError as exc:
        msg = str(exc)
        # each rejection is recorded with its reason — corrupt vs wrong-shape vs missing
        assert "unparsable" in msg and "env-cache.json" in msg
        assert "unexpected shape" in msg and "models_dev_cache" in msg
        assert "not present" in msg  # the missing ~/.cache/jev path is surfaced too


def test_reorder_pool_unpriceable_sinks_to_end():
    mod = load_scan()
    # gamma has no price in the prices dict -> blended None -> must sink to the end,
    # never become pool head (a 0.0 blend would wrongly sort it first as 'free').
    ordered = mod.reorder_pool(
        ["openrouter:fakeprov/alpha", "openrouter:fakeprov/gamma", "openrouter:fakeprov/beta"],
        {"openrouter:fakeprov/alpha": {"in": 0.1, "out": 0.2},
         "openrouter:fakeprov/beta": {"in": 0.05, "out": 0.10}})
    assert ordered == ["openrouter:fakeprov/beta", "openrouter:fakeprov/alpha", "openrouter:fakeprov/gamma"]
    # blended of an absent/malformed price is None, never 0.0
    assert mod.blended({}) is None
    assert mod.blended({"in": "abc", "out": 1}) is None


def test_scan_violation_fails_closed(tmp_path, monkeypatch):
    mod = load_scan()
    # catalog has a 'delta' whose $/M (token * 1e6) is over the list's hard cap:
    # token 0.000002 in + 0.000002 out -> $4.0/M > the $3.0/M cap.
    viol_catalog = {"fakeprov": {"models": {
        "alpha": {"cost": {"input": 0.0000001, "output": 0.0000002}},
        "beta":  {"cost": {"input": 0.00000005, "output": 0.0000001}},
        "gamma": {"cost": {"input": 0.0000004, "output": 0.0000005}},
        "delta": {"cost": {"input": 0.000002, "output": 0.000002}},   # $/M 4.0 > 3.0 cap
    }}}
    doc = _list_doc(beta_first=True)
    doc["tiers"]["medium"]["coding"].append("openrouter:fakeprov/delta")
    list_path = tmp_path / "jev-router-models.json"
    list_path.write_text(json.dumps(doc), encoding="utf-8")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(mod, "default_list", lambda: list_path)
    monkeypatch.setattr(mod, "get_catalog", lambda: (viol_catalog, "test"))
    monkeypatch.setattr(mod, "STATE_PATH", str(state_path))

    rc = mod.main(["--json"])
    # violation present AND an apply would be pending -> fail CLOSED (3), never
    # misreported as a normal apply (1) or a quality alert (2)
    assert rc == 3
    st = json.loads(state_path.read_text(encoding="utf-8"))
    assert st["violations"], "expected a violation to be recorded"
    assert any("hard cap" in v for v in st["violations"])


def test_scan_missing_model_fails_closed(tmp_path, monkeypatch):
    """A listed model absent from the live catalog must FAIL CLOSED (3).

    Measured defect this pins: an id that had vanished from the catalog was only
    printed as 'unpriceable : [...]' and left the exit code untouched — the rc
    ladder weighed violations/apply/alerts but never `missing` — so the run
    reported a routine price apply (1). Meanwhile reorder_pool() silently sank
    the dead id to the back of its pool, and nothing removed it from the list or
    from the host's model.default. A model the fleet cannot serve is not a price
    move: it is a could-not-verify.
    """
    mod = load_scan()
    doc = _list_doc(beta_first=True)          # beta head, already cheapest
    doc["tiers"]["medium"]["coding"].append("openrouter:fakeprov/ghost")
    doc["prices"]["openrouter:fakeprov/ghost"] = {"in": 0.02, "out": 0.02}
    list_path = tmp_path / "jev-router-models.json"
    list_path.write_text(json.dumps(doc), encoding="utf-8")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(mod, "default_list", lambda: list_path)
    monkeypatch.setattr(mod, "get_catalog", lambda: (SYNTH_CATALOG, "test"))
    monkeypatch.setattr(mod, "STATE_PATH", str(state_path))

    rc = mod.main(["--json"])
    st = json.loads(state_path.read_text(encoding="utf-8"))
    assert st["missing_models"] == ["openrouter:fakeprov/ghost"]
    assert rc == 3, (
        "a model absent from the live catalog must fail closed, not ride a normal apply"
    )
    assert any("not in the live catalog" in v for v in st["violations"]), (
        "the missing model must be recorded as a violation, not only as 'unpriceable'"
    )


def test_scan_ignores_sentinel_dynamic_pricing(tmp_path, monkeypatch):
    """Providers list dynamic-pricing metas as sentinel -1/-1 (blended -2.0).

    -2.0 is truthy in Python and '<' any real head, so a naive 'if pb and
    pb < head_price' admits them as 'cheaper candidates'. A sentinel is not a
    lower REAL price — it must never appear in the unvetted alert (it would be
    reported to the operator for an A/B that can never run).
    """
    mod = load_scan()
    cat = {"fakeprov": {"models": {
        "alpha": {"cost": {"input": 0.0000001, "output": 0.0000002}},    # $/M 0.30
        "beta":  {"cost": {"input": 0.00000005, "output": 0.0000001}},   # $/M 0.15 head
        "gamma": {"cost": {"input": 0.0000004, "output": 0.0000005}},    # $/M 0.90
    }}}
    # dynamic-pricing sentinels under their own provider: -1/-1 -> blended -2.0
    cat["switchprov"] = {"models": {
        "auto":       {"cost": {"input": -1, "output": -1}},
        "auto-beta":  {"cost": {"input": -1, "output": -1}},
    }}
    list_path = tmp_path / "jev-router-models.json"
    list_path.write_text(json.dumps(_list_doc(beta_first=True)), encoding="utf-8")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(mod, "default_list", lambda: list_path)
    monkeypatch.setattr(mod, "get_catalog", lambda: (cat, "test"))
    monkeypatch.setattr(mod, "STATE_PATH", str(state_path))

    rc = mod.main(["--json"])
    st = json.loads(state_path.read_text(encoding="utf-8"))
    # no violation in play; at most an apply (or nothing) — never a cap breach
    assert rc in (0, 1)
    alerted = [c["model"]
               for cands in st["unvetted_cheaper_candidates"].values() for c in cands]
    assert all("switchprov/" not in m for m in alerted), (
        "sentinel -1/-1 models must not be reported as unvetted cheaper candidates"
    )
