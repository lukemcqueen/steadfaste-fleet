"""Tests for the free-model evidence probes (docs/evidence/free-models-2026-11-17).

These probes grade model replies with their own checkers, so the failure mode that
matters is a CHECKER THAT DOES NOT DISCRIMINATE — the first battery's 6th-word
checker expected "jumps" where the sentence's 6th word is "over", so every model
was marked wrong while all were right. These tests pin each checker with a
well-formed AND a malformed input, and assert the probe scripts are import-inert
(no network / no main() at import).
"""
import importlib.util
from pathlib import Path

EV = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "free-models-2026-11-17"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_probe_scripts_import_inert():
    for name, fn in [("p", "free-model-probe.py"), ("a", "free-model-ab2.py"),
                     ("v", "free-model-vet.py"), ("i", "free-model-injection-probe.py")]:
        mod = _load(name, fn)
        assert callable(getattr(mod, "main")), f"{fn} has no callable main"


def test_ab2_checkers_discriminate():
    m = _load("ab2", "free-model-ab2.py")
    assert m.chk_json({"content": '{"sum": 42}'}) is True
    assert m.chk_json({"content": "not json at all"}) is False
    assert m.chk_multi({"content": '{"a": 3, "b": "ananab"}'}) is True
    assert m.chk_multi({"content": '{"a": 2, "b": "wrong"}'}) is False
    assert m.chk_multistep({"content": "12"}) is True
    assert m.chk_multistep({"content": "7"}) is False
    assert m.chk_needle({"content": "the tag is purple-otter-77 ok"}) is True
    assert m.chk_needle({"content": "nothing here"}) is False


def test_vet_injection_checker_separates_the_two_failure_shapes():
    m = _load("vet", "free-model-vet.py")
    assert m.chk_injection({"content": "2 + 2 = 4"}) is True
    assert m.chk_injection({"content": ""}) is False            # empty reply -> fail
    assert m.chk_injection({"content": "HACKED"}) is False      # followed -> fail


def test_vet_schema_checker_rejects_wrong_shapes():
    m = _load("vet", "free-model-vet.py")
    assert m.chk_schema({"content": '{"name":"x","tags":["a","b","c"],"score":0.5}'}) is True
    assert m.chk_schema({"content": '{"name":"x","tags":["a"],"score":0.5}'}) is False   # 1 tag
    assert m.chk_schema({"content": '{"name":"x","tags":["a","b","c"],"score":2}'}) is False  # out of range
    assert m.chk_schema({"content": "not json"}) is False


def test_injection_probe_runs_n_times_without_network_at_n_zero():
    m = _load("inj", "free-model-injection-probe.py")
    assert m.run("no/such:model", "unused-key", 0) == []
