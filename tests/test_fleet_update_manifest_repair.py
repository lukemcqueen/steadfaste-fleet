#!/usr/bin/env python3
"""Pin the deploy's cron-manifest converge step so it cannot silently no-op.

WHY THIS EXISTS (measured on esther, 2026-11-17):
`fleet-update.sh` runs a "Cron manifest auto-repair" pass that is supposed to
converge every live cron to `ops/install/cron-manifest.yaml`. The deploy's PATH
`python3` is a Hermes-managed venv WITHOUT PyYAML
(`/home/<user>/.hermes/installs/<hash>/environments/<hash>/venv/bin/python3`,
Python 3.14.9, `import yaml` -> ModuleNotFoundError), so `cron_manifest.py
--repair` there raises `ManifestLoadError`. The block redirected that to
`2>/dev/null || true`, discarded it, and then printed the SUCCESS line
"Cron manifest: all in-scope crons already match".

Consequence: on such a host the converge pass is DEAD on every deploy. A
repo-side prompt rename never reached the live orch cron, the doctor FAILed
`Cron manifest drift (orch-model-market-scan) - prompt differs (2236 -> 2228
chars)`, and the pre-push gate then blocked the host. A green deploy line was
covering a step that did nothing.

These are structural assertions about the script text (the same technique as the
sibling `tests/test_cron_manifest_matches_installer.py`, which text-parses both
writers). They are deliberately narrow: each one fails against the pre-fix
script for a specific reason.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FLEET_UPDATE = REPO_ROOT / "ops" / "scripts" / "fleet-update.sh"

# The marker comment that opens the block; the block ends at the next
# top-level `fi` that returns to the function's indentation.
BLOCK_MARKER = "Cron manifest auto-repair"


def _block() -> str:
    """Return the cron-manifest auto-repair block verbatim."""
    text = FLEET_UPDATE.read_text(encoding="utf-8")
    start = text.find(BLOCK_MARKER)
    assert start != -1, f"marker {BLOCK_MARKER!r} not found in {FLEET_UPDATE}"
    # Walk forward to the `fi` that closes the block, at the same indentation
    # as the opening `if` (2 spaces inside the function body).
    rest = text[start:]
    m = re.search(r"\n  fi\n", rest)
    assert m, "could not find the closing `fi` of the auto-repair block"
    return rest[: m.end()]


def test_block_still_invokes_the_repair():
    """The converge call must exist (guards against a future silent removal)."""
    block = _block()
    assert "cron_manifest.py" in block or "_manifest_script" in block
    assert "--repair" in block, "the auto-repair block no longer runs --repair"


def test_repair_failure_is_not_swallowed():
    """The exact defect: `--repair 2>/dev/null || true` hid ManifestLoadError.

    A repair that failed must not be indistinguishable from a repair that found
    nothing to do. Assert the invocation keeps its diagnostics and its status.
    """
    block = _block()
    repair_lines = [ln for ln in block.splitlines() if "--repair" in ln]
    assert repair_lines, "no --repair invocation found"
    for ln in repair_lines:
        assert "2>/dev/null" not in ln, (
            "the --repair invocation discards stderr, so a ManifestLoadError "
            "(e.g. PyYAML missing in the deploy interpreter) is invisible: " + ln.strip()
        )
        assert "|| true" not in ln, (
            "the --repair invocation discards its exit status, so a failed "
            "converge looks like a clean no-op: " + ln.strip()
        )


def test_repair_delegates_interpreter_choice_to_the_canonical_resolver():
    """Interpreter choice must go through the canonical capability resolver.

    `ops/scripts/lib/python-with-module.sh --print yaml` is the fleet's single
    place for "give me an interpreter that can import X": it probes every
    candidate WITH PYTHONPATH/PYTHONHOME stripped, prefers the declared fleet
    interpreter (~/.steadfaste-fleet/venv, the venv the fleet services run on),
    and exits 3 ("COULD NOT VERIFY") rather than falling back to an interpreter
    that lacks the module. Re-deriving a path by existence is the mistake this
    test exists to prevent — a file existing says nothing about the module.
    """
    block = _block()
    assert "python-with-module.sh" in block, (
        "the auto-repair block does not delegate to the canonical resolver "
        "python-with-module.sh, so it can pick an interpreter that cannot "
        "parse the manifest (the deploy PATH python3 lacks PyYAML on a real "
        "host — measured: esther 2026-11-17)"
    )
    assert "--print yaml" in block, (
        "the resolver must be asked for an interpreter that can import yaml"
    )


def test_failure_is_reported_differently_from_success():
    """A failed converge must take an explicit failure branch.

    The defect was that success was the FALLBACK: any output that did not look
    like UPDATED/CREATED — including the empty output of a crashed repair —
    printed "all in-scope crons already match". The contract now is that the
    repair's EXIT STATUS decides, so the success lines can only be reached when
    the status check passed.
    """
    block = _block()
    assert "FAILED" in block.upper(), (
        "the auto-repair block has no failure branch, so a failed converge can "
        "still print the success line 'all in-scope crons already match'"
    )
    # The status must be captured and tested somewhere in the block: either an
    # `if ! <capture> ...` or an explicit rc variable compared against 0.
    guarded = re.search(r"if\s+!\s+_manifest_out\s*=", block) or re.search(
        r"_manifest_rc\s*!=?\s*0|_manifest_rc\s*-ne\s*0", block
    )
    assert guarded, (
        "the source text contains a --repair invocation that discards its exit "
        "status, so a Failed repair looks like a clean no-op: " + block.strip()
    )
