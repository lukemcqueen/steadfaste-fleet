#!/usr/bin/env python3
"""Tests for ops/scripts/manage/router-pools-seed.py — the FALLBACK half of
install-router-layer.sh step 4.

The installer prefers the upstream `jev models suggest --write`; when that command
is absent or fails, the draft is written anyway. That rescue was an inline
heredoc, so no test could exercise it — which is exactly what the review refused
(ADV-14913-1, technique "swallowed-error", install-router-layer.sh step 4: "the
WARN path is untested"). The logic is now this script, and these tests are its
proof.

Run:  python3 tests/test_router_pools_seed.py     (pytest also collects it)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "ops" / "scripts" / "manage" / "router-pools-seed.py"


def _run(pools: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SEED), str(pools)],
                          capture_output=True, text=True, timeout=60)


def test_an_absent_pools_file_still_gets_the_draft(tmp_path):
    """The fallback's most common input: no pools file at all (fresh host)."""
    pools = tmp_path / "sub" / "routing.json"
    r = _run(pools)
    assert r.returncode == 0, r.stderr
    assert pools.is_file(), "the draft must be written even with no input file"
    assert json.loads(pools.read_text())["tiers"] == {}


def test_a_corrupt_pools_file_is_replaced_not_fatal(tmp_path):
    """An unreadable pools file must not stop the rescue."""
    pools = tmp_path / "routing.json"
    pools.write_text("{not json")
    r = _run(pools)
    assert r.returncode == 0, r.stderr
    assert json.loads(pools.read_text())["tiers"] == {}


def test_cheap_fronts_simple_fleet_model_fronts_medium_and_hard_is_untouched(tmp_path):
    pools = tmp_path / "routing.json"
    pools.write_text(json.dumps({"tiers": {
        "simple": {"general": ["openrouter:expensive/thing"]},
        "medium": {"coding": ["openrouter:moonshotai/kimi-k2.7-code", "openrouter:other/x"]},
        "hard": {"writing": ["openrouter:minimax/minimax-m3"]},
    }}))
    r = _run(pools)
    assert r.returncode == 0, r.stderr
    tiers = json.loads(pools.read_text())["tiers"]
    assert tiers["simple"]["general"][0] == "openrouter:qwen/qwen3.7-flash"
    assert tiers["medium"]["coding"][0] == "openrouter:deepseek/deepseek-v4.1-flash"
    assert "moonshotai/kimi" not in " ".join(tiers["medium"]["coding"]), \
        "a kimi model must never be fronted: it costs more than what the fleet runs"
    assert tiers["hard"]["writing"] == ["openrouter:minimax/minimax-m3"], "hard/* is left alone"
    assert "openrouter:expensive/thing" in tiers["simple"]["general"], "the other models are kept"


def test_bad_usage_is_rejected_without_writing_anything(tmp_path):
    r = subprocess.run([sys.executable, str(SEED)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "usage" in r.stderr.lower()
