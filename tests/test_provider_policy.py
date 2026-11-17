"""Provider exclusions: one contract, one adapter per harness, no silent gaps.

These are the DRIFT GUARDS for the interop contract in
docs/design/agent-interop.md: a rule with no test is a preference. They pin

  1. the artifact's own schema — every exclusion carries reason + evidence +
     scope, so "we don't like this provider" can never be the whole record;
  2. HARNESS COVERAGE — every harness the fleet registry knows about is either
     wired or DECLARED as a gap with a reason (the interop doc's "visible instead
     of silently absent");
  3. the declared surface really names the key the harness reads, so a rename in
     the harness cannot leave a projection writing into nowhere.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PY = REPO_ROOT / "ops" / "scripts" / "manage" / "provider-policy.py"
ARTIFACT = REPO_ROOT / "config" / "router-models.json"
REGISTRY = REPO_ROOT / "ops" / "install" / "harnesses" / "registry.yaml"


@pytest.fixture(scope="module")
def policy():
    spec = importlib.util.spec_from_file_location("provider_policy", POLICY_PY)
    assert spec is not None and spec.loader is not None, f"cannot load {POLICY_PY}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["provider_policy"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def artifact():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def registry_harnesses():
    """The harness names declared in the registry — THE single source for them."""
    names = []
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        if line.startswith("  - name: "):
            names.append(line.split("name:", 1)[1].strip())
    return names


def test_every_exclusion_carries_reason_evidence_and_scope(artifact):
    block = artifact.get("provider_exclusions") or {}
    assert block, "the Bedrock exclusion must live in the artifact, not only in a host config"
    for provider, entry in block.items():
        for field in ("reason", "evidence", "scope"):
            assert entry.get(field), f"provider_exclusions[{provider}] is missing {field}"


def test_registry_harnesses_are_covered_or_declared(policy):
    """No harness may be absent from the policy surface without a stated reason."""
    known = registry_harnesses()
    assert known, "registry.yaml yielded no harnesses — the parse, not the registry, is suspect"
    missing = [name for name in known if name not in policy.HARNESSES]
    assert not missing, (
        f"harness(es) in registry.yaml with no provider-policy entry: {missing} — "
        "wire an adapter or declare the gap with its reason (interop rule: visible, "
        "never silently absent)")
    for name, spec in policy.HARNESSES.items():
        assert spec.get("why"), f"HARNESSES[{name}] states no why"
        if spec["mechanism"] is None:
            # Structural, not lexical: a declared gap must not CLAIM a surface it
            # cannot write (that is how an invented knob starts), and the gap must
            # be REPORTED by check() rather than folded into silence - pinned
            # separately by test_a_present_harness_with_no_knob_is_a_finding_not_silence.
            assert spec["surface"] is None, (
                f"HARNESSES[{name}] declares no mechanism yet names a surface "
                f"{spec['surface']!r} — state a verified knob or state the gap")


def test_hermes_surface_names_the_key_hermes_actually_reads(policy):
    spec = policy.HARNESSES["hermes"]
    assert "provider_routing.ignore" in spec["surface"]
    assert "provider_routing.ignore" in " ".join(spec["set"]), (
        "the write half must target the same key the read half and the surface claim")


def test_excluded_providers_is_the_projection_target(policy, artifact):
    assert policy.excluded_providers(artifact) == sorted(artifact["provider_exclusions"])


def test_could_not_verify_is_never_reported_as_agreement(policy, artifact, monkeypatch):
    """Fail closed: an unreadable harness must NOT read as 'matches the artifact'."""
    monkeypatch.setattr(policy, "read_live", lambda spec: (None, "hermes is not on PATH"))
    monkeypatch.setattr(policy.Path, "exists", lambda self: True)
    findings = policy.check(artifact)
    assert any(f["kind"] == "could-not-verify" for f in findings), (
        "an unverifiable harness produced no finding — that is the failure this "
        "policy exists to prevent")


def test_a_present_harness_with_no_knob_is_a_finding_not_silence(policy, artifact, monkeypatch):
    """A present harness that cannot be told IS a coverage gap and must be reported."""
    monkeypatch.setattr(policy.Path, "exists", lambda self: True)
    monkeypatch.setattr(policy, "read_live", lambda spec: (["amazon-bedrock"], None))
    findings = policy.check(artifact)
    gaps = [f for f in findings if f["kind"] == "gap-present"]
    assert gaps, "a present, unwired harness must produce a gap-present finding"
    assert all("NOT" in f["detail"] or "not" in f["detail"] for f in gaps)
