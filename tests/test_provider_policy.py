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


# --- EXECUTABLE PROOF (ADV-14939-1): the CLI itself, not just its functions --------

def _run_cli(*args):
    import subprocess
    return subprocess.run([sys.executable, str(POLICY_PY), *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_selftest_passes_on_the_shipped_artifact():
    """The shipped artifact must pass the tool's own --selftest (exit 0)."""
    proc = _run_cli("--selftest")
    assert proc.returncode == 0, f"--selftest failed on the shipped artifact: {proc.stdout}{proc.stderr}"
    assert "SELFTEST PASSED" in proc.stdout


def test_cli_selftest_can_fail(tmp_path):
    """A check that cannot fail is worthless: an exclusion with no reason must exit 1."""
    bad = {"provider_exclusions": {"some-provider": {"scope": "all"}}}
    bad_path = tmp_path / "router-models.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    proc = _run_cli("--selftest", "--list", str(bad_path))
    assert proc.returncode == 1, f"--selftest did not reject a reason-less exclusion: {proc.stdout}"
    assert "SELFTEST FAILED" in proc.stdout


def test_cli_exit_code_contract():
    """0/1/3 as documented: 3 = could not verify (never 'agrees'), 1 = findings."""
    missing = _run_cli("--check", "--list", "/nonexistent/router-models.json")
    assert missing.returncode == 3, (
        f"an unreadable artifact must be COULD NOT VERIFY (3), got {missing.returncode}: {missing.stderr}")
    real = _run_cli("--check", "--json")
    findings = json.loads(real.stdout)["findings"]
    expected = 1 if any(f["kind"] != "gap-absent" for f in findings) else 0
    assert real.returncode == expected, (
        f"--check returned {real.returncode}, expected {expected} for its own findings: {findings}")


def test_cli_read_half_is_the_harness_own_api(policy):
    """Record the probe: the read half goes through the harness CLI and yields JSON.

    Tolerates absence (a host without hermes is not a failure) — but when the
    harness IS present the documented surface must still answer, so a rename in
    the harness breaks this test instead of silently dead-ending the projection.
    """
    import shutil
    import subprocess
    if shutil.which("hermes") is None:
        pytest.skip("hermes is not installed on this host — nothing to probe")
    spec = policy.HARNESSES["hermes"]
    proc = subprocess.run(["hermes"] + spec["get"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"the harness's own read API failed: {proc.stderr}"
    parsed = json.loads(proc.stdout)
    assert isinstance(parsed, dict) and "ignore" in parsed, (
        f"the documented surface no longer returns the ignore list: {proc.stdout[:200]}")
