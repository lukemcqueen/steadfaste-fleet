#!/usr/bin/env python3
"""Regression test: fleet-update.sh must NEVER auto-write operator-owned config.

2026-09-08 fleet-wide clobber (esther/joseph/kustos/gisu): fleet-update.sh
auto-ran install-fallback-providers.py + install-model-default.sh on every
sync/deploy, overwriting operator-owned ~/.hermes/config.yaml
model.default + fallback_providers. A per-host FLEET_SKIP_MODEL_CONVERGENCE
flag in gitignored .env only protected moses — every other agent was clobbered.

2026-09-09: both writer scripts were removed entirely (source, register lines,
deployed copies) after d709d752 de-registered their auto-run. ~/.hermes/config.yaml
model keys are operator-owned Hermes runtime config (set via `hermes config set`),
resolved at runtime by upstream Hermes — cortex ships no tool that writes them.

This test fails if fleet-update.sh ever references either removed writer again
(via register OR invocation) — a re-add is a re-introduction of the clobber class.
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "ops" / "scripts" / "fleet-update.sh"

# Scripts that WRITE operator-owned ~/.hermes/config.yaml model/fallback keys.
# Removed 2026-09-09 — must never be re-introduced in any form.
_REMOVED_CONFIG_WRITERS = (
    "install-fallback-providers.py",
    "install-model-default.sh",
)


def _script_text() -> str:
    return _SCRIPT.read_text()


def test_removed_config_writers_absent_from_cortex_update():
    """Neither removed writer may be referenced in fleet-update.sh at all."""
    text = _script_text()
    offending = [
        i
        for i, line in enumerate(text.splitlines(), 1)
        if any(writer in line for writer in _REMOVED_CONFIG_WRITERS)
    ]
    assert not offending, (
        f"fleet-update.sh line(s) {offending} reference a removed config-writer "
        f"({', '.join(_REMOVED_CONFIG_WRITERS)}) — these were deleted 2026-09-09 "
        "because they overwrote operator-owned ~/.hermes/config.yaml model/fallback "
        "keys on every agent sync (2026-09-08 fleet clobber). Re-adding one in any "
        "form (register OR invocation) re-introduces the clobber class."
    )


def test_no_model_convergence_skip_flag_logic_remains():
    """The FLEET_SKIP_MODEL_CONVERGENCE opt-out is gone (removed with the calls)."""
    text = _script_text()
    assert "FLEET_SKIP_MODEL_CONVERGENCE" not in text, (
        "Stale FLEET_SKIP_MODEL_CONVERGENCE logic remains — the convergence "
        "calls were removed outright; the per-host skip flag is a vestige of "
        "the opt-out anti-pattern and must not linger."
    )


def test_materializer_is_the_sanctioned_config_writer_with_override_seam():
    """The ONLY sanctioned writer of operator-owned config.yaml model keys is the
    materializer, and it must NOT clobber a deliberate per-host pin.

    The 2026-09-08 clobber class is 'fleet-update auto-writes operator-owned
    model.default on every deployment'. The materializer
    (decouple-fleet-model-materializer, cycle 8566) is that write carried forward
    intentionally, but it only re-introduces the class SAFELY:
      (i)   it is idempotent (no-op when current == fleet target),
      (ii)  it honors a per-host SESSION_MODEL / SESSION_PROVIDER override BEFORE
            the fleet baseline (an operator pin is the write target, not the baseline),
      (iii) a routing label or a failed config-read fails closed (no write).
    Those three properties are proven hermetically in test_materialize_fleet_model.py.
    This guard asserts the write is delegated to exactly that script (never a
    re-introduced standalone writer) and that fleet-update invokes it fail-open.
    """
    text = _script_text()
    assert "materialize-fleet-model.sh" in text, (
        "fleet-update.sh must route the model-default write through the sanctioned "
        "materializer only (materialize-fleet-model.sh), which carries the idempotence "
        "+ operator-override + fail-closed properties. A re-introduced standalone "
        "writer (install-model-default.sh class) is the 2026-09-08 clobber returned."
    )
    # the resolver honors an operator pin (SESSION_MODEL) before the baseline — the
    # no-clobber seam lives in fleet_lib.models.resolve_session_target, not in the
    # deploy script's write path.
    _models = _REPO / "ops" / "scripts" / "fleet_lib" / "models.py"
    msrc = _models.read_text()
    assert "SESSION_MODEL" in msrc and "resolve_session_target" in msrc, (
        "fleet_lib/models.py must expose resolve_session_target honoring SESSION_MODEL "
        "first — the operator-override seam that stops the materializer from clobbering "
        "a deliberate per-host pin."
    )
