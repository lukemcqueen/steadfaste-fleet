#!/usr/bin/env python3
"""Provider/upstream exclusions: ONE policy, projected into each coding agent.

WHY THIS EXISTS (docs/design/agent-interop.md, Luke 2026-10-02: "fix things for
interoperability with different coding agents"): a fix that lives only in one
harness's config file is not a fix, it is that host's private copy. That is
exactly how the Amazon Bedrock exclusion started - hand-written into
~/.hermes/config.yaml, which served ONE harness and taught the fleet nothing, and
which the next agent to hit the same 400 would have had to rediscover.

The three layers, per that doc:

  CONTRACT  config/router-models.json -> provider_exclusions[<provider>]
            {reason, evidence, scope, recorded}. Every entry MUST carry a reason
            and evidence (the same rule router-pools.py --selftest already
            enforces for effort.excluded), so an exclusion can never be an
            unexplained preference.

  ADAPTER   the HARNESSES table below. Per harness it states HOW to read and
            write THAT harness's own knob. Two rules from the interop doc:
            - the projection goes through the harness's OWN api (`hermes config
              set`), never by editing its config file behind its back, so a
              harness that owns a lifecycle keeps owning it;
            - the harness's own API is VERIFIED before its adapter is written
              (rule 7). `hermes config get provider_routing --json` was probed and
              returns JSON, which is why this adapter can read it without a YAML
              dependency (pyyaml is not installed on the host).

  GAP       a harness with no verified knob is DECLARED here with its reason -
            visible, never silently absent. A gap is reported as a finding when
            that harness is actually present on this host (it can still reach
            the excluded upstream), and as information when it is absent.

  provider-policy.py --check           # drift: each present harness vs the artifact
  provider-policy.py --check --json
  provider-policy.py --write           # apply the artifact via each harness's own api
  provider-policy.py --selftest        # the artifact's own invariants, no host state

Exit codes: 0 = every present harness matches, 1 = drift or gap findings,
3 = could not verify (fail closed - never reported as agreement).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

RC_OK = 0
RC_FINDINGS = 1
RC_COULD_NOT_VERIFY = 3

REPO_ROOT = Path(__file__).resolve().parents[3]   # ops/scripts/manage/ -> repo root

# The provider-exclusion surface of each coding agent.
#   mechanism: "config-cli" -> read/write through the harness's own CLI (the
#              interop doc's CLI adapter: "anything that shells out")
#              None -> no verified knob; the entry MUST state why (a gap).
#   presence:  the harness's own artifact on THIS host, the same signal the
#              doctor and ops/install/harnesses/registry.yaml use.
HARNESSES = {
    "hermes": {
        "presence": "~/.hermes/config.yaml",
        "surface": "provider_routing.ignore (config.yaml)",
        "mechanism": "config-cli",
        "get": ["config", "get", "provider_routing", "--json"],
        "set": ["config", "set", "provider_routing.ignore"],
        "why": ("Hermes reads provider_routing.ignore out of config.yaml. `hermes "
                "config set` is the harness's own writer, so the projection goes "
                "through its API instead of a YAML edit; `hermes config get "
                "provider_routing --json` is the verified read half."),
    },
    "claude-code": {
        "presence": "~/.claude.json",
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified. Claude Code's provider/route "
                "surface has NOT been probed on this host, and interop rule 7 "
                "forbids inferring one from a version in a doc. Declared, not "
                "guessed: on a host where Claude Code is present, this is a real "
                "coverage gap for this policy."),
    },
    "codex": {
        "presence": "~/.codex/config.toml",
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified. ~/.codex/config.toml is "
                "documented in the harness registry for MCP servers only; its "
                "routing surface is unprobed. Declared gap."),
    },
    "pi": {
        "presence": "~/.pi/agent",
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified. Pi's model calls go through "
                "its own provider config, which has not been probed here. "
                "Declared gap."),
    },
    "steadfaste-tui": {
        "presence": None,
        "surface": None,
        "mechanism": None,
        "why": ("Harness surface undeclared: ops/install/harnesses/registry.yaml "
                "carries this harness as layer `none` / status `blocked`, so there "
                "is nothing to wire against yet."),
    },
    # Declared with the operator's required agent set (2026-11-23). These four
    # carry NO provider-exclusion mechanism: the registry declares their MCP
    # wiring as `planned`, and no provider-exclusion knob has been probed for
    # any of them. On a host where one IS installed this is a real coverage gap
    # for this policy (reported, never folded into silence) — see
    # test_a_present_harness_with_no_knob_is_a_finding_not_silence.
    "copilot": {
        "presence": "~/.copilot/mcp-config.json",
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified. Copilot CLI's MCP config path "
                "is documented; its provider/routing surface has not been probed on "
                "any host. Declared gap."),
    },
    "cursor": {
        "presence": "~/.cursor/mcp.json",
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified. Cursor reads MCP servers from "
                "mcp.json; nothing about model/provider routing has been probed. "
                "Declared gap."),
    },
    "opencode": {
        "presence": "~/.config/opencode/opencode.json",
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified. OpenCode's own config carries "
                "providers and MCP servers, but the exclusion surface has not been "
                "probed on a host. Declared gap."),
    },
    "docker-agent": {
        "presence": None,
        "surface": None,
        "mechanism": None,
        "why": ("No provider-exclusion knob verified, and no config artifact is "
                "declared either (the registry declares this harness by its binary, "
                "`cagent`). Declared gap."),
    },
}


def default_list() -> Path:
    """The policy artifact, from the repo checkout or the deployed copy."""
    for candidate in (REPO_ROOT / "config" / "router-models.json",
                      Path.home() / ".steadfaste-fleet" / "config" / "router-models.json"):
        if candidate.is_file():
            return candidate
    return REPO_ROOT / "config" / "router-models.json"


def excluded_providers(doc: dict) -> list:
    """The providers the artifact excludes, sorted (the projection's target value)."""
    block = doc.get("provider_exclusions")
    return sorted(block) if isinstance(block, dict) else []


def _run(argv, timeout=30):
    """Run a harness's own CLI. Returns (stdout, error-or-None); never raises."""
    exe = shutil.which(argv[0])
    if exe is None:
        return None, f"{argv[0]} is not on PATH"
    try:
        proc = subprocess.run([exe] + list(argv[1:]), capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{argv[0]} failed to run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, (f"{' '.join(argv)} exited {proc.returncode}"
                      + (f": {detail[-1]}" if detail else ""))
    return proc.stdout, None


def read_live(spec: dict):
    """The harness's live value for this policy, via its own API.

    Returns (value-or-None, error-or-None). An error is NEVER agreement: the
    caller turns it into exit 3.
    """
    argv = ["hermes"] + spec["get"]
    out, err = _run(argv)
    if out is None:
        return None, err
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return None, f"{' '.join(argv)} did not return JSON (got {out.strip()[:120]!r})"
    if not isinstance(parsed, dict):
        return None, f"{' '.join(argv)} returned {type(parsed).__name__}, expected an object"
    ignore = parsed.get("ignore")
    return (sorted(ignore) if isinstance(ignore, list) else []), None


def write_live(spec: dict, providers: list):
    """Apply the artifact through the harness's own API (no file editing)."""
    argv = ["hermes"] + spec["set"] + [json.dumps(providers)]
    out, err = _run(argv)
    if out is None:
        return err
    return None


def check(doc: dict, only_present: bool = True) -> list:
    """Findings where a present harness disagrees with the artifact, or cannot be told."""
    want = excluded_providers(doc)
    findings = []
    for name, spec in HARNESSES.items():
        present = bool(spec["presence"]) and Path(spec["presence"]).expanduser().exists()
        if only_present and not present:
            if spec["mechanism"] is None:
                findings.append({"harness": name, "kind": "gap-absent",
                                 "detail": f"no projection mechanism declared — {spec['why']}"})
            continue
        if spec["mechanism"] is None:
            findings.append({"harness": name, "kind": "gap-present",
                             "detail": ("this harness is installed on this host but has no "
                                        f"verified provider-exclusion knob, so the policy is "
                                        f"NOT enforced for it — {spec['why']}")})
            continue
        got, err = read_live(spec)
        if err is not None:
            findings.append({"harness": name, "kind": "could-not-verify", "detail": err})
            continue
        if got != want:
            findings.append({"harness": name, "kind": "drift",
                             "detail": (f"live {spec['surface']} = {got}, artifact says "
                                        f"{want} (run --write)")})
    return findings


def _selftest(doc: dict) -> int:
    """The artifact's own invariants — no host state read, re-runnable anywhere."""
    failures = []
    block = doc.get("provider_exclusions")
    if block is None:
        print("SELFTEST PASSED — no provider_exclusions block (nothing excluded)")
        return RC_OK
    if not isinstance(block, dict) or not block:
        failures.append(f"provider_exclusions must be a non-empty object: {block!r}")
    for provider, entry in (block or {}).items():
        if not isinstance(entry, dict):
            failures.append(f"provider_exclusions[{provider}] must be an object: {entry!r}")
            continue
        for field in ("reason", "evidence", "scope"):
            if not entry.get(field):
                failures.append(f"provider_exclusions[{provider}] must carry {field}")
    for name, spec in HARNESSES.items():
        if spec["mechanism"] is None and not spec["why"]:
            failures.append(f"HARNESSES[{name}] declares no mechanism and no reason")
    if failures:
        print(f"SELFTEST FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return RC_FINDINGS
    print(f"SELFTEST PASSED — {len(block)} excluded provider(s), each with reason, evidence "
          f"and scope; {sum(1 for s in HARNESSES.values() if s['mechanism'])} projection "
          f"mechanism(s) implemented, "
          f"{sum(1 for s in HARNESSES.values() if s['mechanism'] is None)} harness(es) declared "
          f"as gaps with a reason")
    return RC_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--list", default=str(default_list()), help="the policy artifact")
    ap.add_argument("--check", action="store_true",
                    help="the default mode: report drift between each present harness and "
                         "the artifact (named explicitly so the documented invocation works)")
    ap.add_argument("--write", action="store_true",
                    help="apply the artifact through each present harness's own API")
    ap.add_argument("--selftest", action="store_true",
                    help="assert the artifact's invariants and exit (no host state read)")
    ap.add_argument("--all-harnesses", action="store_true",
                    help="also report declared gaps for harnesses absent from this host")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    list_path = Path(args.list).expanduser()
    try:
        doc = json.loads(list_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"COULD NOT VERIFY: cannot read {list_path}: {exc}", file=sys.stderr)
        return RC_COULD_NOT_VERIFY

    if args.selftest:
        return _selftest(doc)

    want = excluded_providers(doc)
    if args.write:
        applied, failed = [], []
        for name, spec in HARNESSES.items():
            if spec["mechanism"] is None or not spec["presence"]:
                continue
            if not Path(spec["presence"]).expanduser().exists():
                continue
            err = write_live(spec, want)
            (failed if err else applied).append(f"{name}: {err}" if err else name)
        for name in applied:
            print(f"wrote {name} {HARNESSES[name]['surface']} = {want}")
        if failed:
            for line in failed:
                print(f"WARNING: {line}", file=sys.stderr)
            return RC_COULD_NOT_VERIFY
        return RC_OK

    findings = check(doc, only_present=not args.all_harnesses)
    if args.json:
        print(json.dumps({"artifact": str(list_path), "excluded": want,
                          "findings": findings}, indent=2))
    else:
        print(f"provider exclusions : {list_path}")
        print(f"excluded            : {', '.join(want) if want else '(none)'}")
        hard = [f for f in findings if f["kind"] != "gap-absent"]
        if not findings:
            print("\nno findings — every present harness matches the artifact")
        else:
            print(f"\n{len(findings)} finding(s) ({len(hard)} requiring attention):")
            for f in findings:
                print(f"  [{f['kind']}] {f['harness']}: {f['detail']}")
    if any(f["kind"] == "could-not-verify" for f in findings):
        return RC_COULD_NOT_VERIFY
    return RC_FINDINGS if any(f["kind"] != "gap-absent" for f in findings) else RC_OK


if __name__ == "__main__":
    sys.exit(main())
