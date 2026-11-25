"""Tests for fleet_doctor.checks.nginx_probe_allowlist_verdict.

Why this exists: the xx007 health server block keeps deliberately tight limits
(`limit_conn conn_limit 10` + `limit_req zone=general burst=10 nodelay`) as the
public DDoS ceiling, and the FLEET is supposed to bypass them via the
`geo $limit_bypass` allowlist in `hermes-zone-defs.conf` (an empty limit-zone
key makes nginx apply no limit). When that allowlist is NOT deployed the zones
stay keyed on `$binary_remote_addr`, so the fleet's own health probes are
rate/conn limited: they queue past the probe's 8 s urlopen timeout and the
watchdog reads Moses as "unreachable" while he is answering — the false
failover-alarm class Luke reported 2026-11-25.

fleet-update's `deploy_nginx_configs()` cannot fix this itself (it needs root
for /etc/nginx), so it only PRINTS the sudo command. Nothing surfaced the stale
state, so it persisted for months. The verdict helper makes it visible.

It is a PURE function of the config path (no globals), so every branch is
driven here against a tmp_path copy — a future refactor cannot quietly drop the
remedy or turn a not-present host into a false alarm.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops" / "scripts" / "manage"))

from fleet_doctor.checks import nginx_probe_allowlist_verdict  # noqa: E402

# The repo template that install/fleet-update is supposed to deploy.
REPO_TEMPLATE = REPO / "ops" / "install" / "deploy" / "nginx" / "hermes-zone-defs.conf"

STALE_TEXT = """\
# older revision — no allowlist
limit_req_zone  $binary_remote_addr zone=general:10m rate=20r/s;
limit_req_zone  $binary_remote_addr zone=auth:10m   rate=5r/s;
limit_conn_zone $binary_remote_addr zone=conn_limit:10m;
"""

ALLOWLISTED_TEXT = """\
geo $limit_bypass {
    default          $binary_remote_addr;
    192.168.1.0/24   "";
    127.0.0.0/8      "";
}
limit_req_zone  $limit_bypass zone=general:10m rate=20r/s;
limit_conn_zone $limit_bypass zone=conn_limit:10m;
"""


def test_stale_config_without_allowlist_warns_with_a_root_remedy(tmp_path):
    """The exact live condition: zones keyed on $binary_remote_addr, no geo map."""
    p = tmp_path / "hermes-zone-defs.conf"
    p.write_text(STALE_TEXT)
    status, detail, fix = nginx_probe_allowlist_verdict(p)
    assert status == "WARN", f"stale config must WARN, got {status}"
    assert "binary_remote_addr" in detail
    assert "$limit_bypass" in fix or "hermes-zone-defs" in fix
    # The remedy must warn that a reload is NOT enough (the zone KEY change
    # requires a full restart) — that is the trap this guardrail exists for.
    assert "restart" in fix
    assert "reload" in fix


def test_allowlisted_config_passes(tmp_path):
    p = tmp_path / "hermes-zone-defs.conf"
    p.write_text(ALLOWLISTED_TEXT)
    status, detail, _fix = nginx_probe_allowlist_verdict(p)
    assert status == "PASS", f"allowlisted config must PASS, got {status} ({detail})"


def test_missing_file_is_info_not_a_false_alarm(tmp_path):
    """A host not using the fleet nginx template must not be warned at."""
    status, _detail, _fix = nginx_probe_allowlist_verdict(tmp_path / "absent.conf")
    assert status == "INFO", f"absent file must be INFO, got {status}"


def test_present_but_unjudgeable_warns_could_not_verify(tmp_path):
    """'Nothing there' (INFO) and 'something there that cannot be judged' (WARN)
    are different facts — collapsing them would report the host clean while the
    guardrail went unverified."""
    # (a) a path that exists but is not a regular file
    status, detail, fix = nginx_probe_allowlist_verdict(tmp_path)  # a directory
    assert status == "WARN", f"non-regular-file path must WARN, got {status}"
    assert "cannot" in detail.lower()
    assert fix, "an unverifiable check must still carry a remedy"

    # (b) a regular file this process cannot read
    import os
    p = tmp_path / "unreadable.conf"
    p.write_text(STALE_TEXT)
    os.chmod(p, 0o000)
    try:
        status_r, detail_r, fix_r = nginx_probe_allowlist_verdict(p)
    finally:
        os.chmod(p, 0o600)
    if os.geteuid() == 0:
        # root reads anything — the case is unreachable, but the check must
        # still have produced SOME verdict rather than raising.
        assert status_r in ("WARN", "PASS", "INFO")
    else:
        assert status_r == "WARN", f"unreadable file must WARN, got {status_r}"
        assert "cannot read" in detail_r.lower()
        assert fix_r


def test_repo_template_itself_passes():
    """Non-vacuous pin: the template the deploy ships must satisfy the check.

    Without this, the check and the template could drift apart silently — the
    check would keep warning on correctly-deployed hosts (or pass on a template
    that lost the allowlist).
    """
    assert REPO_TEMPLATE.is_file(), f"repo nginx template missing: {REPO_TEMPLATE}"
    status, detail, _fix = nginx_probe_allowlist_verdict(REPO_TEMPLATE)
    assert status == "PASS", (
        f"the repo's own hermes-zone-defs.conf must carry the allowlist; "
        f"got {status} ({detail}) — the deploy template and the guardrail have drifted"
    )
