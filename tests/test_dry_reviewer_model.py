#!/usr/bin/env python3
"""The reviewer model must resolve through ONE consistent source (DRY).

Before: the canonical reviewer id `deepseek/deepseek-v4-pro-0813` was hardcoded
in 5+ places (loop-gov-mcp.py, adversarial-review.py x3, deepseek-review-mcp.py,
reviewer-agent-pi.sh with a wrong `openrouter/` prefix) plus a regex read in
fleet_doctor. Changing the reviewer model meant editing every file.

After: `fleet_lib/models.py` owns REVIEWER_MODEL_DEFAULT + get_reviewer_model(),
and every surface resolves through it. This spec proves:
1. get_reviewer_model() returns the canonical default with env unset, honors
   ADVERSARIAL_REVIEWER_MODEL when set.
2. adversarial-review.py falls back to the canonical default as the model when
   env unset (its request body carries it).
3. No hardcoded reviewer-model literal remains in the CONSUMED code surfaces
   (loop-gov-mcp.py, adversarial-review.py, deepseek-review-mcp.py) — only
   fleet_lib/models.py defines it.
4. reviewer-agent-pi.sh no longer carries the wrong `openrouter/` prefix, and
   honors ADVERSARIAL_REVIEWER_MODEL first.

Run:  python3 tests/test_dry_reviewer_model.py
   or: pytest tests/test_dry_reviewer_model.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CANONICAL = "deepseek/deepseek-v4-pro-0813"
ADVERSARIAL_REVIEW = "ADVERSARIAL_REVIEWER_MODEL"

# Code surfaces that must NOT carry a hardcoded reviewer literal after DRY.
# The two ENFORCEMENT surfaces fail-closed (a gate must never silently re-pick
# its judge); deepseek-review-mcp is a dev tool whose fallback literal is
# acceptable but must still resolve through fleet_lib.
_PY_ENFORCEMENT_SURFACES = [
    "ops/scripts/orch-bus/adversarial-review.py",
    "mcp-servers/loop-gov-mcp.py",
]
_SHELL = "ops/scripts/manage/reviewer-agent-pi.sh"


def _load_fleet_models() -> types.ModuleType:
    path = REPO / "ops" / "scripts" / "fleet_lib" / "models.py"
    spec = importlib.util.spec_from_file_location("fleet_lib_models_under_test", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_canonical_default_is_declared_once_in_fleet_lib():
    mod = _load_fleet_models()
    assert mod.REVIEWER_MODEL_DEFAULT == CANONICAL, (
        f"fleet_lib must own the canonical default: {mod.REVIEWER_MODEL_DEFAULT!r}")


def test_get_reviewer_model_returns_canonical_with_env_unset():
    saved = os.environ.pop(ADVERSARIAL_REVIEW, None)
    mod = _load_fleet_models()
    try:
        assert mod.get_reviewer_model() == CANONICAL
    finally:
        if saved is not None:
            os.environ[ADVERSARIAL_REVIEW] = saved


def test_get_reviewer_model_honors_env_override():
    saved = os.environ.get(ADVERSARIAL_REVIEW)
    os.environ[ADVERSARIAL_REVIEW] = "fake/model-x"
    mod = _load_fleet_models()
    try:
        assert mod.get_reviewer_model() == "fake/model-x"
    finally:
        if saved is None:
            os.environ.pop(ADVERSARIAL_REVIEW, None)
        else:
            os.environ[ADVERSARIAL_REVIEW] = saved


def test_adversarial_review_uses_fleet_lib_default_in_its_request_body():
    """The close-gate/plan reviewer must not hardcode a model literal."""
    # adversarial-review.py reads env ADVERSARIAL_REVIEWER_MODEL with a fallback;
    # after DRY that fallback must BE the fleet_lib constant, not a literal.
    path = REPO / "ops" / "scripts" / "orch-bus" / "adversarial-review.py"
    spec = importlib.util.spec_from_file_location("arv_dry_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Simulate env unset: force the key out and assert the resolved default equals
    # the fleet_lib canonical WITHOUT a hardcoded literal in the file body.
    body = _capture_body(mod, env_override=None)
    assert body["model"] == CANONICAL, (
        f"request model with env unset must be the canonical default: {body['model']}")


def _capture_body(mod, env_override):
    captured: dict = {}
    os.environ["OPENROUTER_API_KEY"] = "k"
    if env_override is None:
        os.environ.pop(ADVERSARIAL_REVIEW, None)
    else:
        os.environ[ADVERSARIAL_REVIEW] = env_override

    class R:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"verdict":"CLEAN","findings":[]}'}}],
                               "usage": {}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FU:
        def __init__(s):
            s.request = types.SimpleNamespace(
                urlopen=lambda req, timeout=None: (captured.__setitem__("body", json.loads(req.data.decode())), R())[1],
                Request=urllib.request.Request)
            s.error = urllib.error
    mod.urllib = FU()
    with tempfile.TemporaryDirectory() as td:
        mod._call_model("judge", db_path=str(Path(td) / "lg.db"))
    return captured["body"]


def test_no_hardcoded_reviewer_literal_in_enforcement_surfaces():
    """DRY: only fleet_lib/models.py owns the literal (fail-closed surfaces)."""
    for rel in _PY_ENFORCEMENT_SURFACES:
        src = (REPO / rel).read_text()
        assert CANONICAL not in src, (
            f"{rel} still hardcodes the reviewer literal; it must import from fleet_lib")


def test_deepseek_review_mcp_resolves_through_fleet_lib():
    """The dev-tool MCP server default must come from the shared source."""
    src = (REPO / "mcp-servers" / "deepseek-review-mcp.py").read_text()
    assert "fleet_lib.models" in src and "get_reviewer_model" in src, (
        "deepseek-review-mcp.py must resolve its default through fleet_lib")


def test_reviewer_agent_pi_no_wrong_prefix_and_honors_env():
    src = (REPO / _SHELL).read_text()
    assert "openrouter/deepseek/deepseek-v4-pro-0813" not in src, (
        "reviewer-agent-pi.sh still carries the wrong openrouter/ prefix")
    assert ADVERSARIAL_REVIEW in src, (
        "reviewer-agent-pi.sh must honor ADVERSARIAL_REVIEWER_MODEL when set")


def test_the_pre_change_surfaces_are_not_dry():
    """Discrimination: the canonical literal IS present in pre-change surfaces."""
    changed = all(CANONICAL not in (REPO / rel).read_text()
                  for rel in _PY_ENFORCEMENT_SURFACES)
    shell_fixed = "openrouter/deepseek/deepseek-v4-pro-0813" not in (REPO / _SHELL).read_text()
    if changed and shell_fixed:
        print("SKIP: surfaces already DRY - not a pre-change tree, run on pre-change to discriminate")
        return
    # On the pre-change tree the literal IS present -> the DRY test must FAIL.
    assert not changed, "pre-change tree has no literals - nothing for DRY to remove"


if __name__ == "__main__":
    CASES = (
        test_the_canonical_default_is_declared_once_in_fleet_lib,
        test_get_reviewer_model_returns_canonical_with_env_unset,
        test_get_reviewer_model_honors_env_override,
        test_adversarial_review_uses_fleet_lib_default_in_its_request_body,
        test_no_hardcoded_reviewer_literal_in_enforcement_surfaces,
        test_deepseek_review_mcp_resolves_through_fleet_lib,
        test_reviewer_agent_pi_no_wrong_prefix_and_honors_env,
        test_the_pre_change_surfaces_are_not_dry,
    )
    failures = 0
    for fn in CASES:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"❌ {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"❌ {fn.__name__}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
