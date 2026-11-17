#!/usr/bin/env python3
"""The doctor must not prescribe a remedy that cannot run on the host it names.

WHY THIS EXISTS (measured on esther, 2026-11-17):
`fleet_doctor/checks.py` prints the manifest-drift remedy as
`python3 ~/steadfaste-fleet/ops/scripts/manage/cron_manifest.py --repair`.
`cron_manifest.py` PARSES YAML, and the `python3` on an agent's PATH is a
Hermes-managed tools venv that cannot import yaml, so the command the doctor
hands the agent dies with `ManifestLoadError`. A remedy that cannot run is worse
than no remedy: it burns the agent's turn and it reads as the agent failing.

The fleet's canonical answer is the capability resolver
(`~/.steadfaste-fleet/scripts/lib/python-with-module.sh`), which selects an
interpreter that can actually import the module and exits 3 ("COULD NOT VERIFY")
instead of falling back to one that cannot.

This is a TEXT assertion over the doctor's own source: it pins the remedy an
agent will copy, not the code path (the code path is covered by
tests/test_cron_manifest_matches_installer.py and cron_manifest's own suite).
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKS = REPO / "ops" / "scripts" / "manage" / "fleet_doctor" / "checks.py"

# Any remedy line that tells a human/agent to invoke a YAML-parsing fleet tool.
YAML_TOOLS = ("cron_manifest.py",)


def _remedy_lines() -> list[str]:
    """Every line that prescribes running one of the YAML-parsing tools."""
    out = []
    for line in CHECKS.read_text(encoding="utf-8").splitlines():
        if any(t in line for t in YAML_TOOLS):
            out.append(line.strip())
    return out


def test_there_are_remedies_to_check():
    """Non-vacuity: if the parser finds nothing, the assertions below prove nowt."""
    lines = _remedy_lines()
    assert lines, (
        "no line in checks.py mentions cron_manifest.py — either the doctor "
        "stopped prescribing a remedy, or this test stopped looking at it"
    )


def test_no_remedy_invokes_a_yaml_tool_with_a_bare_python3():
    """The exact defect: a bare `python3` that cannot import yaml."""
    offenders = [ln for ln in _remedy_lines() if re.search(r"\bpython3\s+\S*cron_manifest\.py", ln)]
    assert not offenders, (
        "the doctor prescribes a YAML-parsing tool with a bare `python3` — the "
        "PATH python3 is a Hermes tools venv that cannot import yaml, so the "
        "remedy fails with ManifestLoadError:\n  " + "\n  ".join(offenders)
    )


def test_remedies_route_through_the_capability_resolver():
    """The remedy must name how to get a capable interpreter."""
    lines = [ln for ln in _remedy_lines() if "--repair" in ln or "--check" in ln]
    assert lines, "no --repair/--check remedy line found"
    missing = [ln for ln in lines if "python-with-module.sh" not in ln]
    assert not missing, (
        "manifest remedy does not route through python-with-module.sh, so the "
        "agent has no way to reach a yaml-capable interpreter:\n  "
        + "\n  ".join(missing)
    )
