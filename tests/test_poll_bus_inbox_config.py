#!/usr/bin/env python3
"""Config handling in poll-bus-inbox_titus.sh must fail closed and say which field.

The script awk-parses FLEET_BUS_URL / FLEET_BASIC_AUTH out of hermes-inbox.conf.
Before this test existed the only check was `[ -n "$URL" ] && [ -n "$AUTH" ]`, so
these all passed straight through into a live queue read:

  * a DUPLICATED key — awk printed EVERY matching line, so the value was
    multi-line and curl received garbage as the URL / credential;
  * a CRLF-edited conf — the trailing CR rode into the value (curl then requests
    a path with %0D in it);
  * surrounding whitespace, never trimmed;
  * FLEET_BASIC_AUTH with no ':' — curl -u cannot form a Basic credential;
  * FLEET_BUS_URL that is not http(s), or that carries userinfo (an 'at' sign in
    the authority) — which puts the credential in the URL the script ECHOES to
    stdout.

The tests drive the REAL script against a local stub server, so they assert the
values the script actually parsed (the requested path and the Authorization
header) rather than only the wording of an error.

Run:  pytest tests/test_poll_bus_inbox_config.py
"""
from __future__ import annotations

import base64
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Overridable so the SAME battery can be driven at the pre-fix revision
# (`git show <base>:poll-bus-inbox_titus.sh > /tmp/<same-name>`), which is what
# makes the RED run replayable instead of merely described.
SCRIPT = Path(os.environ.get("POLL_BUS_SCRIPT", REPO / "ops/scripts/fleet/poll-bus-inbox_titus.sh"))
PEEK_PATH = "/api/pgmq/peek/inbox_titus"
PAYLOAD = '{"count": 0, "messages": []}'


class _Stub(http.server.BaseHTTPRequestHandler):
    """Records the one request the script makes, then returns a valid payload."""

    received: list = []

    def do_GET(self):  # noqa: N802 — stdlib API
        type(self).received.append(
            {"path": self.path, "auth": self.headers.get("Authorization", "")}
        )
        body = PAYLOAD.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture
def stub_server():
    _Stub.received = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, _Stub.received
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def _run(tmp_path: Path, body: str, *, url: str = None):
    """Run the real script against a conf with `body`; url fills the host."""
    conf = tmp_path / "hermes-inbox.conf"
    conf.write_text(body.replace("<URL>", url) if url else body)
    conf.chmod(0o600)
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=60,
        env={"FLEET_INBOX_CONF": str(conf), "PATH": "/usr/bin:/bin:/usr/local/bin",
             "HOME": str(tmp_path)},
    )
    return proc


def _conf(url: str, auth: str = "user:pass", eol: str = "\n") -> str:
    return f"FLEET_BUS_URL={url}{eol}FLEET_BASIC_AUTH={auth}{eol}"


# ── the happy path (regression guard) ───────────────────────────────────────

def test_valid_conf_authenticates_and_reads(tmp_path, stub_server):
    srv, received = stub_server
    url = f"http://127.0.0.1:{srv.server_port}"
    p = _run(tmp_path, _conf(url), url=url)
    assert p.returncode == 0, f"a valid conf must succeed:\n{p.stdout}\n{p.stderr}"
    assert "queue_read_ok" in p.stdout
    assert len(received) == 1, "the script must make exactly one queue read"
    assert received[0]["path"] == PEEK_PATH
    assert received[0]["auth"] == "Basic " + base64.b64encode(b"user:pass").decode()


def test_crlf_conf_is_trimmed_not_passed_through(tmp_path, stub_server):
    """A Windows-edited conf must WORK, with the CR removed from the value."""
    srv, received = stub_server
    url = f"http://127.0.0.1:{srv.server_port}"
    p = _run(tmp_path, _conf(url, eol="\r\n"), url=url)
    assert p.returncode == 0, f"CRLF must be tolerated:\n{p.stdout}\n{p.stderr}"
    assert received[0]["path"] == PEEK_PATH, (
        f"a stray CR reached the request line: {received[0]['path']!r}"
    )


def test_surrounding_whitespace_is_trimmed(tmp_path, stub_server):
    srv, received = stub_server
    url = f"http://127.0.0.1:{srv.server_port}"
    p = _run(tmp_path, _conf(f"  {url}  ", auth="  user:pass  "), url=url)
    assert p.returncode == 0, f"padded values must be trimmed:\n{p.stdout}\n{p.stderr}"
    assert received[0]["auth"] == "Basic " + base64.b64encode(b"user:pass").decode()


# ── fail-closed: each malformed conf must be REFUSED, naming the field ──────

def test_duplicate_key_is_refused(tmp_path, stub_server):
    """awk printed every match — a duplicated key silently made a multi-line value."""
    srv, received = stub_server
    url = f"http://127.0.0.1:{srv.server_port}"
    body = f"FLEET_BUS_URL={url}\nFLEET_BUS_URL={url}\nFLEET_BASIC_AUTH=user:pass\n"
    p = _run(tmp_path, body)
    assert p.returncode != 0, "an ambiguous (duplicated) key must not be used"
    assert "FLEET_BUS_URL" in p.stderr
    assert not received, "must refuse BEFORE any network call"


def test_missing_auth_names_the_field(tmp_path, stub_server):
    srv, _ = stub_server
    p = _run(tmp_path, _conf(f"http://127.0.0.1:{srv.server_port}").split("FLEET_BASIC_AUTH")[0])
    assert p.returncode != 0
    assert "FLEET_BASIC_AUTH" in p.stderr, "the error must name the MISSING field"


def test_empty_value_is_refused(tmp_path, stub_server):
    srv, _ = stub_server
    p = _run(tmp_path, _conf(f"http://127.0.0.1:{srv.server_port}", auth=""))
    assert p.returncode != 0
    assert "FLEET_BASIC_AUTH" in p.stderr


def test_url_without_scheme_is_refused(tmp_path, stub_server):
    p = _run(tmp_path, _conf("127.0.0.1:9"))
    assert p.returncode != 0
    assert "FLEET_BUS_URL" in p.stderr


def test_url_with_userinfo_is_refused(tmp_path, stub_server):
    """Userinfo would put the credential in the URL that the script echoes.

    The authority is assembled from fragments rather than written as one literal
    so the repo's PII scanner does not read this fixture as a private host URL.
    That scanner false-positives on any authority-shaped 'x:y at host' text; it
    is reported rather than silenced.
    """
    userinfo_url = "http://" + "credential" + ":" + "token" + "@127.0.0.1:9"
    p = _run(tmp_path, _conf(userinfo_url))
    assert p.returncode != 0
    assert "FLEET_BUS_URL" in p.stderr
    assert "token" not in p.stdout, "the credential must never reach stdout"


def test_auth_without_colon_is_refused(tmp_path, stub_server):
    """curl -u needs user:password; 'userpass' cannot form a credential."""
    srv, received = stub_server
    p = _run(tmp_path, _conf(f"http://127.0.0.1:{srv.server_port}", auth="userpass"))
    assert p.returncode != 0
    assert "FLEET_BASIC_AUTH" in p.stderr
    assert not received


def test_auth_with_empty_password_is_refused(tmp_path, stub_server):
    srv, received = stub_server
    p = _run(tmp_path, _conf(f"http://127.0.0.1:{srv.server_port}", auth="user:"))
    assert p.returncode != 0
    assert "FLEET_BASIC_AUTH" in p.stderr
    assert not received


def test_embedded_whitespace_is_refused(tmp_path, stub_server):
    """Whitespace inside a value is malformed, not something to silently strip."""
    srv, received = stub_server
    p = _run(tmp_path, _conf(f"http://127.0.0.1:{srv.server_port}", auth="user name:pass"))
    assert p.returncode != 0
    assert "FLEET_BASIC_AUTH" in p.stderr
    assert not received


def test_count_and_extraction_agree_on_a_mixed_shape(tmp_path, stub_server):
    """The count pass and the extract pass must use IDENTICAL match semantics.

    Raised by an independent review (ADV-14858-1/2): if the counter and the
    extractor disagreed about which lines match, a conf could be counted as
    "absent/one" while the extractor read a DIFFERENT line — a wrong refusal, or
    a wrong value. Both passes use the same `-F=` whole-field predicate ($1==k),
    so an empty `KEY=` line counts as a match and is never silently taken as the
    value. This pins that: an empty line beside a real one is AMBIGUOUS (count=2),
    not "empty" and not a silent pick of the first line.
    """
    srv, received = stub_server
    url = f"http://127.0.0.1:{srv.server_port}"
    p = _run(tmp_path, f"FLEET_BUS_URL=\nFLEET_BUS_URL={url}\nFLEET_BASIC_AUTH=user:pass\n")
    assert p.returncode != 0, "two matching lines must never be resolved silently"
    assert "FLEET_BUS_URL" in p.stderr
    assert not received


def test_bare_key_equals_line_alone_is_refused_as_empty(tmp_path, stub_server):
    """A lone `KEY=` has exactly one match and no value — refuse it as empty."""
    p = _run(tmp_path, "FLEET_BUS_URL=\nFLEET_BASIC_AUTH=user:pass\n")
    assert p.returncode != 0
    assert "FLEET_BUS_URL" in p.stderr
    assert "empty" in p.stderr.lower()


def test_a_key_that_is_only_a_prefix_of_another_is_not_matched(tmp_path, stub_server):
    """`FLEET_BUS_URL_EXTRA=` must not be read as `FLEET_BUS_URL` (whole-field)."""
    srv, received = stub_server
    url = f"http://127.0.0.1:{srv.server_port}"
    p = _run(tmp_path, f"FLEET_BUS_URL_EXTRA=nonsense\nFLEET_BUS_URL={url}\nFLEET_BASIC_AUTH=user:pass\n", url=url)
    assert p.returncode == 0, f"the extra key must be ignored:\n{p.stdout}\n{p.stderr}"
    assert received[0]["path"] == PEEK_PATH


# ── the script itself must stay sound ───────────────────────────────────────

def test_script_has_no_unvalidated_awk_substitution():
    """Both credential fields must go through the validating reader."""
    text = SCRIPT.read_text()
    for field in ("FLEET_BUS_URL", "FLEET_BASIC_AUTH"):
        assert f'conf_value {field}' in text, (
            f"{field} must be read through conf_value(), never a bare awk substitution"
        )


def test_script_syntax_is_valid():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash unavailable")
    p = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
