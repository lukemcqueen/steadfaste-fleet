#!/usr/bin/env python3
"""config.yaml `model.default` must be a BARE model id, never a routing label.

The Jev pricing list (`config/jev-router-models.json`) and `~/.hermes/jev/routing.json`
key their entries as `<label>:<provider>/<model>` — e.g.
`openrouter:qwen/qwen3-coder-30b-a3b-instruct`. That LABEL is Jev's internal
routing currency. It is NOT a model id any provider accepts, and passing it
verbatim to the API is answered:

    HTTP 400: openrouter:qwen/qwen3-coder-30b-a3b-instruct is not a valid model ID
    (observed live on this host 2026-11-16, agent.log — `not retryable`)

A labelled `model.default` therefore does NOT hard-fail the host: every new
session's first call 400s and Hermes silently continues on a
`fallback_providers` model instead — so the host runs something other than the
model it was configured for, and nothing reports it. `model.default` must hold
the BARE id (`qwen/qwen3-coder-30b-a3b-instruct`); the provider lives in
`model.provider`. Verified against a working peer (moses, 2026-11-16): its
`hermes config get model` prints `default: qwen/qwen3-coder-30b-a3b-instruct`,
while `openrouter:...` appears ONLY in the derived/combined display form.

The writer that introduced it is the daily `orch-model-market-scan` cron: its
STEP 1.2 re-points `config.yaml model.default` to "the new head", and every head
in the Jev list is a label. Before this check nothing consulted the shape of the
value that reached config.yaml — `fleet_doctor.jev._current_model` merely
TOLERATES a colon in `default` (it skips the `provider:` prefixing and returns
the value as-is), so the cost guard kept passing while the host was broken.

A colon ALONE is not the tell — an Ollama tag is legitimate (`qwen2.5:3b`, and
the ollama-setup skill sets exactly that) — so the tell is a colon followed by a
provider-qualified `org/model` id, or a value carrying this host's OWN provider
as a prefix. Both shapes are pinned below in both directions.

Run:  pytest tests/test_doctor_model_default_label.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(REPO / "ops" / "scripts"))

import fleet_doctor.checks as C  # noqa: E402 — after sys.path setup
from fleet_doctor.results import Results  # noqa: E402

LABELLED = "openrouter:qwen/qwen3-coder-30b-a3b-instruct"
BARE = "qwen/qwen3-coder-30b-a3b-instruct"

CONFIG = """\
model:
  default: {default}
  provider: {provider}
  api_mode: chat_completions
  base_url: https://openrouter.ai/api/v1
providers:
  deepseek:
    request_timeout_seconds: 300
fallback_providers:
  - provider: openrouter
    model: deepseek/deepseek-v4.1-flash
"""


def _verdicts(tmp_path, monkeypatch, default, provider="openrouter"):
    (tmp_path / "config.yaml").write_text(
        CONFIG.format(default=default, provider=provider), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    res = Results()
    C.check_model_default(res)
    return res.checks


# ── the defect: the label must be reported, as a FAIL, naming the bare form ──
def test_labelled_default_fails(tmp_path, monkeypatch):
    checks = _verdicts(tmp_path, monkeypatch, LABELLED)
    assert len(checks) == 1
    assert checks[0]["status"] == "FAIL", checks[0]


def test_labelled_default_names_the_bare_form_to_set(tmp_path, monkeypatch):
    """The remedy must be executable: it carries the value that actually works."""
    checks = _verdicts(tmp_path, monkeypatch, LABELLED)
    fix = checks[0]["fix"] + checks[0]["detail"]
    assert BARE in fix, fix
    assert "hermes config set model.default" in fix, fix


def test_provider_prefixed_id_without_a_slash_is_also_a_label(tmp_path, monkeypatch):
    """`openai:gpt-4o` on an openai host is a label, not a bare id — the
    generic `org/model` shape alone would miss it, so the host's own provider
    prefix is checked too."""
    checks = _verdicts(tmp_path, monkeypatch, "openai:gpt-4o", provider="openai")
    assert len(checks) == 1 and checks[0]["status"] == "FAIL", checks


# ── the healthy cases: provably silent (an empty world must not warn) ────────
def test_bare_openrouter_id_is_silent(tmp_path, monkeypatch):
    assert _verdicts(tmp_path, monkeypatch, BARE) == []


def test_bare_org_slash_model_is_silent(tmp_path, monkeypatch):
    assert _verdicts(tmp_path, monkeypatch, "deepseek/deepseek-v4-flash-0731") == []


def test_ollama_tag_with_a_colon_is_silent(tmp_path, monkeypatch):
    """THE DISCRIMINATING PAIR: a colon alone must NOT be treated as a label.
    An Ollama tag (`qwen2.5:3b`) is a legitimate `model.default` — a rule that
    fired on any colon would FAIL every local-model host permanently."""
    assert _verdicts(tmp_path, monkeypatch, "qwen2.5:3b", provider="ollama-local") == []


def test_missing_config_is_silent(tmp_path, monkeypatch):
    """No config.yaml (non-Hermes host): nothing to govern, nothing to say."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    res = Results()
    C.check_model_default(res)
    assert res.checks == []


def test_present_config_without_a_default_could_not_verify(tmp_path, monkeypatch):
    """A config.yaml that exists but yields no default is anomalous: a check
    that cannot verify must SAY SO, never pass silently."""
    (tmp_path / "config.yaml").write_text("model:\n  provider: openrouter\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    res = Results()
    C.check_model_default(res)
    assert len(res.checks) == 1 and res.checks[0]["status"] == "WARN", res.checks
    assert "COULD NOT VERIFY" in res.checks[0]["detail"]


# ── the "measured but never consulted" trap ─────────────────────────────────
def test_check_is_registered_in_the_doctor_cli():
    """A check nobody calls is a report, not a gate: it must appear BOTH in the
    import block and in the list the doctor actually runs."""
    text = (REPO / "ops" / "scripts" / "manage" / "fleet_doctor" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert text.count("check_model_default") >= 2, "check_model_default is not wired into cli.py"
