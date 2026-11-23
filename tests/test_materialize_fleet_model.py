#!/usr/bin/env python3
"""Hermetic write-path test for materialize-fleet-model.sh.

The materializer auto-runs `hermes config set model.default` from fleet-update.sh on
EVERY host's sync — the same write-side as the 2026-09-08 fleet-wide config clobber
(install-model-default.sh / install-fallback-providers.py) that
test_fleet_update_never_overwrites_config.py guards against. That guard only names
the two REMOVED scripts, so this materializer must prove its OWN write discipline:

1. It writes model.default + model.provider ONLY when the fleet-resolved target
   differs from the current value (idempotent no-op otherwise).
2. An operator per-host override (SESSION_MODEL / SESSION_PROVIDER env) is honored
   BEFORE the fleet baseline — so a deliberate operator pin is never clobbered by a
   baseline change (the no-clobber principle, carried forward).
3. A transient `hermes config get` failure does NOT write (distinct exit 4, retry
   next sync) — a read failure must never turn a no-op into a wrong write.
4. Boundary: a routing-LABEL'd target (double label) fails closed (no write).

This drives the REAL deployed script end-to-end with a fake `hermes` binary on PATH
so nothing touches the live config.yaml. Pure shell invocation — the script's own
Python decision is exercised through its real call path.

Run: pytest tests/test_materialize_fleet_model.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "ops" / "scripts" / "manage" / "materialize-fleet-model.sh"


def _run(hermes_bin: Path, *, session_model: str, session_provider: str = "openrouter",
         extra_path: str = "") -> tuple[int, str]:
    """Run the materializer with a fake hermes first on PATH + a forced env target."""
    env = dict(os.environ)
    if session_model is not None:
        env["SESSION_MODEL"] = session_model
    env["SESSION_PROVIDER"] = session_provider
    env["PATH"] = f"{hermes_bin.parent}:{env.get('PATH','')}{(':'+extra_path) if extra_path else ''}"
    env.setdefault("STEADFASTE_REPO", str(_REPO))
    r = subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True, text=True,
                       timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _materializer_target_env() -> dict:
    """The script resolves its venv/REPO via env; keep it deterministic for this host."""
    return {}


# ── 1. write only on change ──
def test_writes_when_target_differs(tmp_path):
    log = tmp_path / "hermes.log"
    hermes = tmp_path / "hermes"
    hermes.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{log}"
        if [ "$1 $2" = "config get" ]; then
          echo "qwen/qwen3-coder-30b-a3b-instruct"
          exit 0
        fi
        exit 0
    """))
    hermes.chmod(0o755)
    rc, out = _run(hermes, session_model="deepseek/deepseek-v4-flash-0731")
    calls = log.read_text().splitlines()
    assert rc == 0, out
    assert any("config set model.default deepseek/deepseek-v4-flash-0731" in c for c in calls)
    assert any("config set model.provider openrouter" in c for c in calls)


# ── 1b. no-op when matching ──
def test_noop_when_matching(tmp_path):
    log = tmp_path / "hermes.log"
    hermes = tmp_path / "hermes"
    hermes.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{log}"
        if [ "$1 $2" = "config get" ]; then
          echo "qwen/qwen3-coder-30b-a3b-instruct"
          exit 0
        fi
        exit 0
    """))
    hermes.chmod(0o755)
    rc, out = _run(hermes, session_model="qwen/qwen3-coder-30b-a3b-instruct")
    calls = log.read_text().splitlines()
    assert rc == 0, out
    assert "no-op" in out
    assert not any("config set" in c for c in calls)


# ── 3. get-failure -> distinct exit 4, no write ──
def test_get_failure_serves_skip_exit4(tmp_path):
    log = tmp_path / "hermes.log"
    hermes = tmp_path / "hermes"
    hermes.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{log}"
        if [ "$1 $2" = "config get" ]; then
          exit 1            # transient read failure
        fi
        exit 0
    """))
    hermes.chmod(0o755)
    rc, out = _run(hermes, session_model="deepseek/deepseek-v4-flash-0731")
    calls = log.read_text().splitlines()
    assert rc == 4, f"expected exit 4, got {rc}: {out}"
    assert "COULD NOT VERIFY" in out
    assert not any("config set" in c for c in calls)


# ── 4. double routing label -> fail closed, no write ──
def test_routing_label_target_fails_closed(tmp_path):
    log = tmp_path / "hermes.log"
    hermes = tmp_path / "hermes"
    hermes.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{log}"
        if [ "$1 $2" = "config get" ]; then
          echo "qwen/qwen3-coder-30b-a3b-instruct"
          exit 0
        fi
        exit 0
    """))
    hermes.chmod(0o755)
    rc, out = _run(hermes, session_model="openrouter:nous:deepseek/deepseek-x")
    calls = log.read_text().splitlines()
    assert rc == 2, f"expected exit 2, got {rc}: {out}"
    assert "routing label" in out
    assert not any("config set" in c for c in calls)


# ── 2. operator override honored (no-clobber: env wins over what baseline would say) ──
def test_operator_override_honored_over_fleet(tmp_path):
    # even if the deployment's baseline resolves elsewhere, an explicit SESSION_MODEL
    # env override is what the materializer targets — the operator's pin is not clobbered
    log = tmp_path / "hermes.log"
    hermes = tmp_path / "hermes"
    hermes.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{log}"
        if [ "$1 $2" = "config get" ]; then
          echo "qwen/qwen3-coder-30b-a3b-instruct"
          exit 0
        fi
        exit 0
    """))
    hermes.chmod(0o755)
    rc, out = _run(hermes, session_model="z-ai/glm-5.3-flash")  # operator pinned model
    calls = log.read_text().splitlines()
    assert rc == 0, out
    assert any("config set model.default z-ai/glm-5.3-flash" in c for c in calls), \
        "operator SESSION_MODEL override must be the write target (not a baseline)"