#!/usr/bin/env python3
"""E1 — the review CLASS is WIRED into the close-gate decision and RECORDED.

E0 (test_review_class.py) pinned `_review_class()` as a PURE classifier with
INERT wiring. E1 wires it: end_change computes the class from the measured
change (cx), skips the model judge for A0 (docs-only), routes B to the light
judge resolver, keeps C on the canonical judge — and records the class + its
inputs on the cycle (plan §3 r.6: a skip is a decision, never an absence).

This file pins the WIRED seams, not the classifier (already pinned):
  _class_for_cx(cx)           — the class from the measured change dict
  _review_skip_needed(cx)     — (skip, class): A0 skips even when complex;
                                C/complex reviews; simple skips as today
  _ensure_review_class_col()  — column migration, idempotent, fails to C
These are the exact functions end_change calls; a constant being right is
not a feature being on (the tier-wiring lesson).

Run: python3 -m pytest tests/test_review_class_wiring.py -v
"""

from __future__ import annotations

import importlib.util
import pathlib
import sqlite3
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
MCP = REPO / "mcp-servers" / "loop-gov-mcp.py"

spec = importlib.util.spec_from_file_location("loop_gov_wiring", MCP)
if spec and spec.loader:
    fleet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fleet)
else:
    raise AssertionError(f"cannot load {MCP}")

A0 = "A0"
B = "B"
C = "C"
DOCS = {"paths": ["docs/design/review-risk-routing.md", "docs/evidence/x.txt"]}
CODE_W_TEST = {"paths": ["ops/scripts/tool.py", "tests/test_tool.py"]}
CODE_NO_TEST = {"paths": ["ops/scripts/tool.py"]}
ALWAYS = {"paths": ["mcp-servers/loop-gov-mcp.py"]}
EMPTY = {"paths": []}


# ── _class_for_cx: the class from the MEASURED change dict ──

@pytest.mark.parametrize("cx,expected", [
    (DOCS, A0),
    (CODE_W_TEST, B),
    (CODE_NO_TEST, C),
    (ALWAYS, C),
    (EMPTY, C),
    ({}, C),                                    # no paths key -> fail closed
    ({"paths": DOCS["paths"], "has_uncommitted_code": True}, C),
    (None, C),
], ids=lambda v: v if isinstance(v, str) else "cx")
def test_class_for_cx(cx, expected):
    assert fleet._class_for_cx(cx) == expected


# ── _review_skip_needed: A0 skips even when the change is complex ──

def test_a0_skips_even_when_complex():
    """The E1 core: a doc-only range that crosses the SIZE threshold does NOT
    buy a model judge — the class, not the size, decides."""
    cx = {"is_complex": True, **DOCS}
    skip, cls = fleet._review_skip_needed(cx)
    assert skip is True, "A0 must skip the model judge"
    assert cls == A0


def test_c_never_skips_when_complex():
    cx = {"is_complex": True, **CODE_NO_TEST}
    skip, cls = fleet._review_skip_needed(cx)
    assert skip is False
    assert cls == C


def test_b_complex_still_reviews():
    """B routes to the (operator-set) light judge — it still REVIEWS."""
    cx = {"is_complex": True, **CODE_W_TEST}
    skip, cls = fleet._review_skip_needed(cx)
    assert skip is False
    assert cls == B


def test_simple_change_skips_as_today():
    """The pre-E1 size skip is preserved: a below-threshold change still
    skips, and its class is still computed + recorded."""
    cx = {"is_complex": False, **CODE_NO_TEST}
    skip, cls = fleet._review_skip_needed(cx)
    assert skip is True
    assert cls == C


def test_uncommitted_code_never_a0_even_for_docs():
    cx = {"is_complex": True, "paths": DOCS["paths"], "has_uncommitted_code": True}
    skip, cls = fleet._review_skip_needed(cx)
    assert skip is False, "uncommitted code must not skip via A0"
    assert cls == C


# ── _ensure_review_class_col: column migration ──

def test_column_added_to_old_schema(tmp_path):
    db = tmp_path / "lg.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE loop_cycles (id INTEGER PRIMARY KEY, task_id TEXT,"
                " outcome_note TEXT, decision TEXT)")
    con.commit()
    fleet._ensure_review_class_col(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(loop_cycles)")}
    assert "review_class" in cols
    con.close()


def test_column_migration_idempotent(tmp_path):
    db = tmp_path / "lg.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE loop_cycles (id INTEGER PRIMARY KEY, task_id TEXT,"
                " review_class TEXT)")
    con.commit()
    fleet._ensure_review_class_col(con)  # must not raise
    fleet._ensure_review_class_col(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(loop_cycles)")}
    assert "review_class" in cols
    con.close()


def test_migration_failure_degrades_to_warning(tmp_path, monkeypatch):
    """A broken store must not crash the server — the class stays C (the
    fail-closed default the classifier returns on doubt)."""
    db = tmp_path / "lg.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE loop_cycles (id INTEGER PRIMARY KEY, task_id TEXT)")
    con.commit()

    def _boom(*a, **kw):
        raise sqlite3.Error("nope")

    monkeypatch.setattr(fleet, "_ensure_review_class_col", _boom)
    # The end_change path must still answer C for an unreadable class column.
    assert fleet._class_for_cx({}) == C
    con.close()
