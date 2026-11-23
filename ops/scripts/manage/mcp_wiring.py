#!/usr/bin/env python3
"""Audit MCP server WIRING, not just presence — a registered server can still be dead.

WHY. Kustos reported "No loop-governance MCP tools in catalog" while the doctor said
`MCP server (loop-governance): PASS — configured in config.yaml`: the check tested whether the
server's NAME appeared in the config text, never whether the registered command and arguments
still exist on disk. A registration that points at a missing venv, wrapper or script loads
NOTHING and says nothing — the same false-pass shape as "the venv exists, so it must work".

Pure functions here take the config TEXT and a path-existence callable, so the parsing and the
verdicts are unit-testable without a live host. The CLI reads a real config.

USAGE
  python3 mcp-wiring.py --config ~/.hermes/config.yaml            # report
  python3 mcp-wiring.py --config ~/.hermes/config.yaml --json     # last line is JSON
  python3 mcp-wiring.py --config ... --expected review,tasks

EXIT
  0 every expected server is present AND its paths exist
  1 at least one entry is wired to something that is not there
  2 usage error
  3 COULD NOT VERIFY — no readable config / no expected servers to check
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: name -> server script (relative), and whether only an orchestrator host is expected to
#: carry it. These MUST agree with the doctor's own map (ops/scripts/manage/fleet_doctor/
#: config.py, EXPECTED_MCP_SERVERS / ORCH_ONLY_MCP_SERVERS) — a test asserts the two match BY
#: KEY AND BY VALUE, so there is one source of truth even though the CLI cannot import the
#: doctor's package. The values are BARE filenames (the doctor's convention); the repo-relative
#: path is built by repo_rel_script() wherever one is printed.
DEFAULT_EXPECTED = {
    "agent-bus": "fleet-bus-mcp.py",
    # Two role-distinct registrations of the single loop-gov binary (Slice B):
    # loop-governance (lifecycle) + review (review-only).
    "loop-governance": "loop-gov-mcp.py",
    "review": "loop-gov-mcp.py",
    "tasks": "task-mcp.py",
    "executor": "executor-mcp.py",
    # The memory/session surface itself (mcp-servers/fleet-context-mcp.py). It was
    # declared in ops/scripts/install/mcp-surface.inc.sh — the ONE definition of the
    # fleet MCP surface — but absent from this map, so no host was ever told it was
    # unwired: the audit simply did not cover it. 2026-11-17.
    "fleet-context": "fleet-context-mcp.py",
}
ORCH_ONLY = {"agent-bus", "executor"}

#: The DEPLOYED copy each server must be registered FROM. The registration runs
#: this copy — never the repo working tree: the tree is user-writable and a stale
#: checkout then serves stale behaviour (measured 2026-11-23: a `tasks`
#: registration pointing at <repo>/mcp-servers/task-mcp.py served pre-scoping
#: task queries while the deployed copy was current — an agent saw every repo's
#: tasks as its own). Same paths as `deployed` column of
#: ops/scripts/install/mcp-surface.inc.sh (spelled `~` here rather than ${HOME});
#: pinned to it by tests/test_mcp_wiring.py.
DEFAULT_DEPLOYED = {
    "fleet-context": "~/.steadfaste-fleet/scripts/fleet-context-mcp.py",
    "agent-bus": "~/.steadfaste-fleet/scripts/fleet-bus-mcp.py",
    "tasks": "~/.steadfaste-fleet/scripts/task-mcp.py",
    "executor": "~/.steadfaste-fleet/scripts/executor-mcp.py",
    # Role pairs of the one loop-gov binary (not rows of the surface artifact).
    "loop-governance": "~/.steadfaste-fleet/tools/loop-governance/loop-gov-mcp.py",
    "review": "~/.steadfaste-fleet/tools/loop-governance/loop-gov-mcp.py",
}

#: Path markers: `/.steadfaste-fleet/` is the deploy home (fleet-update owns it);
#: `/steadfaste-fleet/` without the dot is the repo working tree.
DEPLOY_MARK = "/.steadfaste-fleet/"
REPO_TREE_MARK = "/steadfaste-fleet/"


def deployed_for(name, script=None):
    """The path a registration (or a printed fix) should name.

    The deployed copy when this audit knows one; otherwise the repo-relative
    script, so a printed command still resolves on a host running from a clone.
    """
    known = DEFAULT_DEPLOYED.get(name)
    if known:
        return known
    return f"~/{repo_rel_script(script)}"


def _registered_script(block, script):
    """The path the registration actually runs for this server (arg basename match),
    or None when the entry carries no such argument."""
    want = Path(str(script or "")).name
    for p in _paths_of(block):
        if Path(p).name == want:
            return p
    return None

#: Where the server scripts live under the repo root.
MCP_SERVERS_REL = "mcp-servers"


def repo_rel_script(script):
    """The server script's path relative to the repo root.

    Callers pass a BARE filename (the doctor's EXPECTED_MCP_SERVERS does), so a fix string built
    as `~/steadfaste-fleet/{script}` rendered `~/steadfaste-fleet/fleet-bus-mcp.py` — a path that does
    not exist, i.e. a printed command nobody can run. Normalise here so every printed command
    resolves, whichever convention the caller used.
    """
    script = (script or "").strip()
    if not script or "/" in script:
        return script
    return f"{MCP_SERVERS_REL}/{script}"

#: A command inside the HERMES venv works, but it is the tree the fleet is migrating off
#: (Luke 2026-10-13: use the cortex venv). Worth a warning, not a failure.
HERMES_VENV_MARK = "hermes-agent/venv"
FLEET_VENV = "~/.steadfaste-fleet/venv/bin/python3"


def _entry_blocks(config_text):
    """{server: block} for the `mcp_servers:` mapping, by indentation (no PyYAML needed).

    The ENTRY indent is the shallowest key indent inside the block, not a fixed 2: a child key
    like `command:` sits deeper and must not start a new entry.
    """
    lines = config_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"^mcp_servers:\s*$", l))
    except StopIteration:
        return {}
    body = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break                              # next top-level key ends mcp_servers
        body.append(line)

    key_re = re.compile(r"^(\s+)(\S+):")
    indents = []
    for line in body:
        _m = key_re.match(line)
        if _m and not line.strip().startswith("-"):
            indents.append(len(_m.group(1)))
    if not indents:
        return {}
    entry_indent = min(indents)

    blocks, current, name = {}, [], None

    def flush():
        if name:
            blocks[name] = "\n".join(current)

    for line in body:
        m = key_re.match(line)
        if m and len(m.group(1)) == entry_indent and not line.strip().startswith("-"):
            flush()
            name, current = m.group(2), [line]
            continue
        if name is not None:
            current.append(line)
    flush()
    return blocks


def _paths_of(block):
    """Every path a wiring entry needs: the command itself and each argument.

    `[ \\t]` rather than `\\s` around the values: `\\s` matches a NEWLINE, so `args:` with its
    list on the following lines would swallow the next line as the value (caught by a test —
    it produced a path of '- /path/to/server.py').
    """
    out = []
    m = re.search(r"^[ \t]*command:[ \t]*(.+?)[ \t]*$", block, re.MULTILINE)
    if m:
        out.append(m.group(1).strip().strip("'\""))
    m = re.search(r"^[ \t]*args:[ \t]*(.*?)[ \t]*$", block, re.MULTILINE)
    if m and m.group(1).strip() not in ("", "[]"):
        arg = m.group(1).strip()
        if arg.startswith("[") and arg.endswith("]"):
            out.extend(a.strip().strip("'\"") for a in arg[1:-1].split(",") if a.strip())
        else:
            out.append(arg.strip().strip("'\""))
    for line in block.splitlines():                     # YAML list form: "- /path"
        s = line.strip()
        if s.startswith("- ") and "/" in s:
            out.append(s[2:].strip().strip("'\""))
    return [p for p in out if p]


def audit(config_text, expected=None, exists=None, is_orch=True, which=None):
    """[(name, status, detail, fix)] where status is PASS / WARN / FAIL.

    `exists` and `which` are injected (defaults: Path().exists, shutil.which) so the rules are
    testable hermetically.
    """
    import shutil as _shutil
    expected = DEFAULT_EXPECTED if expected is None else expected
    exists = exists or (lambda p: Path(str(p)).expanduser().exists())
    which = which or _shutil.which
    blocks = _entry_blocks(config_text or "")
    results = []

    def _missing_paths(block):
        """Paths that are not there. A BARE command name is resolved on PATH, not as a path —
        treating it as one reported 'python3 does not exist'."""
        gone, bare = [], []
        for p in _paths_of(block):
            if p.startswith("-"):
                continue
            if "/" in p:
                if not exists(p):
                    gone.append(p)
            elif which(p):
                bare.append(p)              # resolvable, but not the fleet venv
            else:
                gone.append(p)              # not resolvable anywhere
        return gone, bare

    def _fix_add(name, script):
        return (f"hermes mcp add {name} --command "
                f"~/.steadfaste-fleet/venv/bin/python3 "
                f"--args {deployed_for(name, script)}")

    for name, script in expected.items():
        if name in ORCH_ONLY and not is_orch:
            if name in blocks:
                results.append((name, "WARN",
                                "configured on a non-orchestrator host",
                                f"Remove the {name} entry from mcp_servers"))
            else:
                results.append((name, "PASS", "not configured (correct here)", ""))
            continue

        block = blocks.get(name)
        if block is None:
            results.append((name, "FAIL", "not configured", _fix_add(name, script)))
            continue

        gone, bare = _missing_paths(block)
        if gone:
            # Present but pointing at nothing: the false-pass the name-only check gave.
            results.append((name, "FAIL",
                            "configured, but these paths do NOT exist: " + ", ".join(gone),
                            f"hermes mcp remove {name} && {_fix_add(name, script)}"
                            "   (or run fleet-update.sh to deploy the missing pieces)"))
            continue
        cmd = re.search(r"^[ \t]*command:[ \t]*(.+?)[ \t]*$", block, re.MULTILINE)
        command = cmd.group(1).strip().strip("'\"") if cmd else ""
        if bare or HERMES_VENV_MARK in command:
            why = ("a bare command, not the fleet venv: " + ", ".join(bare)) if bare else \
                  f"the HERMES venv ({command}) — the tree the fleet is migrating off"
            results.append((name, "WARN", f"registered against {why}",
                            f"hermes mcp remove {name} && {_fix_add(name, script)}"
                            f"   (there is no 'hermes mcp update'; re-add it)"))
            continue
        # The ACTIVE path, not merely an existing one: a registration that runs the
        # repo working tree serves whatever that checkout happens to hold, and a
        # stale checkout keeps serving stale behaviour with every path present.
        reg = _registered_script(block, script)
        deployed = DEFAULT_DEPLOYED.get(name)
        if deployed and reg:
            dep_tail = deployed.split(DEPLOY_MARK, 1)[-1]
            if DEPLOY_MARK in reg:
                if reg.split(DEPLOY_MARK, 1)[-1] != dep_tail:
                    results.append((name, "WARN",
                                    f"registered at {reg} — not the deployed copy "
                                    f"({deployed})",
                                    f"hermes mcp remove {name} && {_fix_add(name, script)}"))
                    continue
            elif REPO_TREE_MARK in reg:
                results.append((name, "WARN",
                                f"registered at {reg} — that is the repo WORKING TREE, "
                                f"not the deployed copy; the server that runs is whatever "
                                f"that checkout holds (a stale one serves stale behaviour)",
                                f"hermes mcp remove {name} && {_fix_add(name, script)}"
                                "   (run fleet-update.sh first if the deployed copy is absent)"))
                continue
        results.append((name, "PASS", "configured and its paths exist", ""))
        continue
    return results


def find_config(path=None):
    """An EXPLICIT path is used as given — never silently swapped for another file, which
    would audit a different host's wiring and report a confident wrong answer."""
    if path:
        cand = Path(path).expanduser()
        return cand if cand.is_file() else None
    for cand in ("~/.hermes/config.yaml", "~/.hermes/config.yml"):
        if Path(cand).expanduser().is_file():
            return Path(cand).expanduser()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit MCP server wiring (paths, not just names).")
    ap.add_argument("--config", default=None, help="config file (default ~/.hermes/config.yaml)")
    ap.add_argument("--expected", default=None, help="comma-separated server names to require")
    ap.add_argument("--orch", action="store_true", help="this host is an orchestrator")
    ap.add_argument("--json", action="store_true", help="last line is machine-readable JSON")
    args = ap.parse_args(argv)

    cfg = find_config(args.config)
    if cfg is None:
        print("COULD NOT VERIFY — no readable MCP config (looked for ~/.hermes/config.yaml)",
              file=sys.stderr)
        return 3
    expected = DEFAULT_EXPECTED
    if args.expected:
        names = [n.strip() for n in args.expected.split(",") if n.strip()]
        expected = {n: DEFAULT_EXPECTED.get(n, "") for n in names}

    results = audit(cfg.read_text(errors="replace"), expected, is_orch=args.orch)
    print(f"config: {cfg}")
    for name, status, detail, fix in results:
        print(f"  {status:4} {name}: {detail}")
        if fix and status != "PASS":
            print(f"       fix: {fix}")
    bad = [r for r in results if r[1] == "FAIL"]
    if args.json:
        print(json.dumps({"config": str(cfg),
                          "entries": [{"name": n, "status": s, "detail": d} for n, s, d, _ in results],
                          "failures": [n for n, s, _, _ in results if s == "FAIL"]}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
