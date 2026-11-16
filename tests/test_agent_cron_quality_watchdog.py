import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WATCHDOG = _REPO / "ops" / "scripts" / "health" / "agent-cron-quality-watchdog.py"
sys.path.insert(0, str(_REPO / "ops" / "scripts"))  # hermes_tz resolution

_spec = importlib.util.spec_from_file_location("agent_cron_quality_watchdog", _WATCHDOG)
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)


def test_silent_noop_accepts_angle_bracket_variant():
    """A cron agent can wrap the no-op in ASCII angle brackets (<SILENT>) after a provider
    fallback; the matcher must treat it as the healthy no-op, not flag it as token garbage.
    Scope is the cortex watchdog's own token set ([SILENT]/SILENT, uppercase)."""
    for token in ("<SILENT>", "  <SILENT>  ", "< [SILENT] >"):
        assert watchdog._is_silent_noop(token), token


def test_silent_noop_keeps_existing_forms():
    for token in ("[SILENT]", "SILENT", '["SILENT"]', "  [SILENT]  "):
        assert watchdog._is_silent_noop(token), token


def test_silent_noop_still_rejects_prose_and_unclosed_token():
    """Square brackets must stay structural; prose, a stray unclosed token, and marker
    forms outside the cortex watchdog's token set are not silence.

    NOTE 2026-11-16: `<silent>` moved OUT of this rejection list into
    test_silent_noop_accepts_case_drift_of_the_same_token. It is the same
    vocabulary in a different case, which is model-deviation noise (delivers
    nothing), not a foreign marker. Foreign VOCABULARIES below stay rejected.
    """
    for token in ("[SILENT", "SILENT]", "the lane said SILENT mid-sentence", "no changes found",
                  "NO_REPLY", "<NO_REPLY>"):
        assert not watchdog._is_silent_noop(token), token


# ── 2026-11-16: the two REAL payloads from live cron output ─────────────────
_JEV_TRAILER = "[Jev] medium · general → deepseek/deepseek-v4-flash-0731 · confidence 0.00 · effort low"


def test_silent_noop_accepts_the_jev_trailer_real_payloads():
    """Both are healthy no-ops that were being flagged TWICE per run.

    Verbatim from live output: fleet-bus-workday `2026-11-16_20-33-40.md` and
    orch-bus-worker `2026-11-16_20-21-38.md`. The Jev layer appends the routing
    annotation to every Jev-driven reply, so the lane cannot obey the prompt's
    "EXACTLY this single line and nothing else".
    """
    payloads = (
        f"\n\n[SILENT]\n\n{_JEV_TRAILER}\n",
        "\n\n[SILENT]\n\n[Jev] hard · general → minimax/minimax-m3 · confidence 0.69 · effort medium\n",
    )
    for p in payloads:
        assert watchdog._is_silent_noop(p), repr(p)


def test_silent_noop_accepts_case_drift_of_the_same_token():
    """Same vocabulary, different casing = model deviation noise, not garbage.

    Scope change, surfaced deliberately: previously only the exact uppercase
    token counted, so a lane replying `[Silent]` was reported as "possible token
    garbage" (Luke's alert). A differently-cased copy of OUR token delivers
    nothing, which is all the no-op means.
    """
    for token in ("[Silent]", "[silent]", "Silent", "<silent>", " [Silent] "):
        assert watchdog._is_silent_noop(token), token


def test_silent_noop_still_rejects_leaked_prose():
    """The 2026-08-04 regression class must stay caught: a no-op token followed by
    real content is NOT silence — that is the case where a lane delivered prose
    instead of skipping. Widening the no-op must NOT widen it past this."""
    leaked = f"\n\n[SILENT]\n\n{_JEV_TRAILER}\n\nBus SSL cert expired — MCP tools down, fixed locally"
    assert not watchdog._is_silent_noop(leaked), "trailing prose must not read as silence"
    assert not watchdog._is_silent_noop("[SILENT]\nhere is the summary I was told not to deliver")
    assert not watchdog._is_silent_noop("[Silent] and then I did the work anyway")


# ── End-to-end: drive the real main() against a fixture run ─────────────────
def _write_run(tmp_path, job_id, response_body,
               script_out="ACTIVE (1 interactive session(s) active in last 30m)"):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps(
        {"jobs": [{"id": job_id, "name": "fixture-job", "no_agent": False}]}))
    out_dir = tmp_path / "output" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.md").write_text(
        "## Script Output\n```\n" + script_out + "\n```\n\n## Response\n" + response_body)
    return jobs_file


def _point_watchdog_at(tmp_path, monkeypatch, jobs_file):
    monkeypatch.setattr(watchdog, "CRON_JOBS_FILE", jobs_file)
    monkeypatch.setattr(watchdog, "CRON_OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(watchdog, "FALLBACK_CONFIG_FILE", tmp_path / "no-such-config.yaml")


def test_healthy_noop_with_trailer_emits_no_alert_end_to_end(tmp_path, monkeypatch, capsys):
    """THE regression test for the user-visible symptom: a no-op reply under an
    ACTIVE session guard must produce ZERO issues — before the fix this printed
    both "suspiciously short response" and "session guard said ACTIVE but reply
    was ... expected the skip token"."""
    jobs_file = _write_run(tmp_path, "job1", f"\n\n[SILENT]\n\n{_JEV_TRAILER}\n")
    _point_watchdog_at(tmp_path, monkeypatch, jobs_file)
    with pytest.raises(SystemExit) as exc:
        watchdog.main()
    assert exc.value.code == 0, "a healthy no-op must exit clean"
    assert capsys.readouterr().out.strip() == "", "a healthy no-op must print no alert"


def test_leaked_prose_still_alerts_end_to_end(tmp_path, monkeypatch, capsys):
    """The other direction: prose under an ACTIVE guard must STILL alert."""
    stub = types.ModuleType("state_tracker")

    class _ST:
        def __init__(self, *a, **k):
            pass

        def evaluate(self, *a, **k):
            return "alert"

    stub.StateTracker = _ST
    monkeypatch.setitem(sys.modules, "state_tracker", stub)
    jobs_file = _write_run(tmp_path, "job1", "\n\nhere is the summary I was told not to deliver\n")
    _point_watchdog_at(tmp_path, monkeypatch, jobs_file)
    watchdog.main()
    out = capsys.readouterr().out
    assert "session guard said ACTIVE" in out, out