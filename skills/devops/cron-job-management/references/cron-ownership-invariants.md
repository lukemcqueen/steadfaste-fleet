# Cron ownership invariants — what a parity test may assert

A fleet cron has exactly ONE owner, and there are two kinds. A test that assumes
only one will go red the moment crons migrate between them — and the test, not the
manifest, is the wrong side.

| Owner | Hosted by | Live entry |
|-------|-----------|------------|
| Installer `create_cron` block | the Hermes scheduler | present in `jobs.json` |
| Fleet cron bridge (S2a) | user-scope systemd timers | REMOVED from `jobs.json` — one scheduler owns each job |

## Rules

- **Never assert "every manifest cron has an installer `create_cron`".** Bridge-owned
  crons legitimately have none, so that assertion turns red on a correct, fully
  migrated host and reads as drift in the manifest. When it fires, check ownership
  before touching either writer.
- **Derive ownership from the tooling's own sources, never from a hand-kept list.**
  The bridge's eligibility predicate is the generator's `job_is_simple`
  (`no_agent` AND non-empty `script` AND no `context_from` / `continuity` / `model`),
  and a manifest entry can be judged against it directly (it carries `no_agent`,
  `script` and `model`). Runtime ownership is `cron_manifest.py::_bridge_owned()` —
  but that reads the HOST's systemd `timers.target.wants`, so a TEST must use the
  repo-derivable predicate instead: a verdict that depends on the invoking host's
  units or HOME is ambient, and it flaps across machines instead of failing honestly.
- **Keep one STRICT half the exemption can never cover.** The bridge only hosts
  `no_agent` script jobs, so an LLM-driven manifest cron (`no_agent: false`) MUST have
  an installer. Exempt the `no_agent` half — never the whole set — or the test stops
  asserting anything at all.
- **Probe both directions before trusting a rewritten ownership test.** Inject
  (a) a synthetic installer-less LLM cron → must be REPORTED; (b) an installer-less
  `no_agent` cron carrying a `model` pin → must be REPORTED (not bridge-eligible);
  (c) a plain installer-less `no_agent` script cron → must NOT be reported. A green
  rewrite with no probes is indistinguishable from a test that asserts nothing.
- **Agree with the runtime checkers by construction.** The doctor and
  `cron_manifest.py --check` both apply the bridge exemption (a bridged name is not
  reported missing). A parity test that skips it silently contradicts every other
  tool on the host, and the disagreement resurfaces later as a spurious gate FAIL.
