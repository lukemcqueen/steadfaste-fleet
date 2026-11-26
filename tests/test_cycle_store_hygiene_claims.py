"""Hermetic tests pinning the STATELESS-decision claims in
skills/devops/loop-governance/references/cycle-store-hygiene.md (COVERED BY
pointer for close-review findings ADV-15212-1/2, 2026-11-26).

The reference claims:
  1. composite_score() receives (completeness, quality, progress) and nothing
     else — no task_id, no history — so the decision is a pure function of the
     composite and re-derives the SAME decision forever.
  2. score_progress() floors any textual diff at 5.0 ("a small commit deserves
     a floor") and no_progress is progress < 2.0, so no_progress is
     structurally unreachable for a commit that changes anything.

These are behavior claims about core/governance/loop_scorer.py. If the scorer
changes, these tests fail loudly instead of the reference silently going
stale.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCORER = _REPO / "core" / "governance" / "loop_scorer.py"

if str(_SCORER.parent) not in sys.path:
    sys.path.insert(0, str(_SCORER.parent))

import loop_scorer  # noqa: E402
import loop_config  # noqa: E402


def _cfg_with(no_progress_score: float) -> dict:
    """Real scorer config (weights/thresholds included) with one threshold
    overridden — composite_score requires the full config shape."""
    cfg = loop_config.get_config()
    cfg["no_progress_score"] = no_progress_score
    return cfg


def test_composite_score_signature_has_no_task_id_or_history():
    """Claim 1: the decision is stateless — no task_id, no history arg."""
    sig = inspect.signature(loop_scorer.composite_score)
    params = list(sig.parameters)
    # (completeness, quality, progress, config=None) — config is the ONLY
    # extra, and it is thresholds/weights, not task identity or history.
    assert "task_id" not in params
    assert "history" not in params
    assert params[:3] == ["completeness", "quality", "progress"]


def test_composite_decision_is_pure_function_of_scores():
    """Claim 1 corollary: identical inputs re-derive the identical decision."""
    d1 = loop_scorer.composite_score(9.0, 9.0, 9.0)
    d2 = loop_scorer.composite_score(9.0, 9.0, 9.0)
    assert d1["decision"] == d2["decision"]


def test_no_progress_unreachable_for_any_diff():
    """Claim 2: score_progress() floors a real diff at 5.0 and no_progress is
    progress < 2.0 — so a commit that changes anything can never be marked
    no_progress."""
    cfg = _cfg_with(no_progress_score=2.0)
    # A real, non-empty change: previous output differs from current.
    progress = loop_scorer.score_progress("def foo():\n    pass\n",
                                          "def foo():\n    return 42\n")
    assert progress >= 5.0
    result = loop_scorer.composite_score(8.0, 8.0, progress, config=cfg)
    assert result["no_progress"] is False


def test_no_progress_only_fires_on_truly_empty_change():
    """Claim 2 boundary: only a genuinely empty diff can reach no_progress."""
    cfg = _cfg_with(no_progress_score=2.0)
    progress = loop_scorer.score_progress("same\n", "same\n")
    assert progress < 2.0
    result = loop_scorer.composite_score(8.0, 8.0, progress, config=cfg)
    assert result["no_progress"] is True
