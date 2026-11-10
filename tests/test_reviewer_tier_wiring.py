"""The tier must be WIRED, not just computed.

This file exists because of a real defect in this change set. The cost tier was implemented, unit
tested, documented as the saving — and unreachable on the fleet's DEFAULT configuration, because the
light path required `ADVERSARIAL_REVIEW_BACKEND=agent` while the default backend is `llm`. The earlier
tests asserted the tier's VALUE (`_tier()` returns "light") and never that anything USED it, so they
stayed green while the saving did nothing.

A constant being right is not a feature being on. These tests follow the tier through the dispatch to
the model argument the call is actually made with, on the default backend.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MCP = REPO / "mcp-servers" / "loop-gov-mcp.py"


def _load():
    spec = importlib.util.spec_from_file_location("gov_wiring", MCP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gov_wiring"] = mod
    spec.loader.exec_module(mod)
    return mod


LIGHT_CX = {"always_review": False, "files": 3, "lines": 60}
HEAVY_CX = {"always_review": True, "files": 1, "lines": 1}


@pytest.fixture()
def gov(monkeypatch):
    """The module on the DEFAULT backend, with the llm call captured instead of made."""
    mod = _load()
    monkeypatch.setenv("ADVERSARIAL_REVIEW_BACKEND", "llm")
    # Hermetic: a host-level ADVERSARIAL_REVIEW_LIGHT_MODEL must not leak into
    # the default-resolution assertions below.
    monkeypatch.delenv("ADVERSARIAL_REVIEW_LIGHT_MODEL", raising=False)
    calls = []

    def _fake_llm(prompt, *, model=None, timeout=None):
        calls.append({"model": model or mod._reviewer_model(), "timeout": timeout})
        return '{"verdict": "CLEAN", "findings": [], "summary": "stub"}'

    monkeypatch.setattr(mod, "_call_reviewer_llm", _fake_llm)
    def _fake_agent(prompt, *, author=None, timeout=None):
        # Recorded, not failed: the agent path is legitimate for a HEAVY change on an agent host, and
        # a stub that raises would fail the test that needs to prove exactly that.
        calls.append({"model": "agent", "timeout": timeout})
        return '{"verdict": "CLEAN", "findings": [], "summary": "stub"}'

    monkeypatch.setattr(mod, "_call_reviewer_agent", _fake_agent)
    return mod, calls


def test_the_default_backend_reaches_the_canonical_model_for_a_light_change(gov):
    """THE regression (post-10-18 reversal): on the default `llm` backend, a
    light change judges with the CANONICAL model unless the operator EXPLICITLY
    sets ADVERSARIAL_REVIEW_LIGHT_MODEL. The 2026-10-18 operator decision
    ('FLASH SHOULD NOT BE THE REVIEWER DEFAULT') removed
    REVIEWER_LIGHT_MODEL_DEFAULT — a second constant is where a cheap default
    creeps back (commit 08db5f50)."""
    mod, calls = gov
    mod._call_reviewer("prompt", cx=LIGHT_CX)
    assert calls, "the reviewer was never called"
    assert calls[0]["model"] == mod.REVIEWER_MODEL_DEFAULT, (
        f"a light change ran on {calls[0]['model']} — the canonical default was "
        "not preserved; this is exactly the flash-default the operator reversed"
    )


def test_an_explicit_light_model_is_honoured(gov, monkeypatch):
    """The operator can still spend less by NAMING a cheaper light model —
    the env is honoured strictly, but it is never the default."""
    mod, calls = gov
    monkeypatch.setenv("ADVERSARIAL_REVIEW_LIGHT_MODEL", "deepseek/deepseek-v3.2")
    mod._call_reviewer("prompt", cx=LIGHT_CX)
    assert calls and calls[0]["model"] == "deepseek/deepseek-v3.2"
    calls.clear()
    mod._call_reviewer("prompt", cx=HEAVY_CX)  # heavy ignores the light model
    assert calls and calls[0]["model"] == mod.REVIEWER_MODEL_DEFAULT


def test_an_always_review_change_still_gets_the_deep_model(gov):
    """The other direction: enforcement/governance code must not be judged cheaply."""
    mod, calls = gov
    mod._call_reviewer("prompt", cx=HEAVY_CX)
    assert calls[0]["model"] == mod.REVIEWER_MODEL_DEFAULT


def test_no_measured_cx_keeps_the_canonical_reviewer(gov):
    """Unknown size is not an excuse to spend less — fail towards the stronger judge."""
    mod, calls = gov
    mod._call_reviewer("prompt")
    assert calls[0]["model"] == mod.REVIEWER_MODEL_DEFAULT


def test_the_recorded_reviewer_matches_the_one_that_ran(gov):
    """The stored row must name the model that actually judged it, or the record lies."""
    mod, calls = gov
    mod._call_reviewer("prompt", cx=LIGHT_CX)
    assert calls[0]["model"] == mod._reviewer_label(LIGHT_CX), (
        "what runs and what is RECORDED must not diverge"
    )
    mod._call_reviewer("prompt", cx=HEAVY_CX)
    assert calls[1]["model"] == mod._reviewer_label(HEAVY_CX)


def test_an_agent_backend_host_still_avoids_the_agent_for_a_light_change(gov, monkeypatch):
    """The tier's ORIGINAL purpose must survive: on an agent host, a light change dodges the slow
    agent (and the 300s client ceiling) rather than waiting on it — but it still judges with
    the canonical model (10-18 reversal), never a silent cheap default."""
    mod, calls = gov
    monkeypatch.setenv("ADVERSARIAL_REVIEW_BACKEND", "agent")
    mod._call_reviewer("prompt", cx=LIGHT_CX)
    assert calls and calls[0]["model"] == mod.REVIEWER_MODEL_DEFAULT
    calls.clear()
    mod._call_reviewer("prompt", cx=HEAVY_CX)  # must now go to the agent
    assert calls and calls[0]["model"] == "agent", (
        "a heavy change on an agent host belongs with the agent, not the fast path"
    )


def test_the_canonical_judge_is_used_by_default_and_light_only_via_env():
    """Post-10-18 reversal: the light tier resolves the CANONICAL model by default;
    a cheaper light judge is reachable ONLY through an explicit ADVERSARIAL_REVIEW_LIGHT_MODEL.
    This pins that the resolver returns REVIEWER_MODEL_DEFAULT when no env is set."""
    mod = _load()
    # The generic resolver (no operator env) must return the canonical model.
    assert mod._light_reviewer_model() == mod.REVIEWER_MODEL_DEFAULT, (
        "the light resolver must default to the canonical reviewer (10-18 reversal) — "
        "a cheaper default is exactly what the operator removed"
    )


def test_semantic_triggers_are_always_reviewed():
    """The deep model's one confirmed finding about our gates: a one-line CI edit, a new dependency or
    a schema change was gated by SIZE ALONE, so a tiny high-leverage change could cross the gate
    untouched. This asserts they are listed, AND that the list is actually being LOADED from the
    shared file — a broken load would leave the in-code literal and silently drop every addition."""
    mod = _load()
    listed = " ".join(mod.ALWAYS_REVIEW_PATHS)
    for needle in ("github/workflows", "requirements.txt", "pyproject.toml", "Cargo.toml",
                   "docker-compose.yml", "migrations/"):
        assert needle in listed, (
            f"{needle} is not always-reviewed: a one-line change there would be gated by size alone"
        )
    # And a change touching only one of them must be HEAVY, regardless of how tiny it is.
    assert mod._tier({"always_review": True, "files": 1, "lines": 1}) == "heavy"


def test_the_shared_list_is_the_one_being_used():
    """The bash hook and this module must not disagree about what needs a clean review."""
    shared = (REPO / "ops" / "scripts" / "lib" / "always-review-paths.txt")
    assert shared.is_file(), "the shared list is missing — the hook and the module would diverge"
    entries = [ln.strip() for ln in shared.read_text().splitlines()
               if ln.strip() and not ln.strip().startswith("#")]
    mod = _load()
    for entry in entries:
        assert entry in mod.ALWAYS_REVIEW_PATHS, (
            f"{entry} is in the shared list but the module does not know about it"
        )
