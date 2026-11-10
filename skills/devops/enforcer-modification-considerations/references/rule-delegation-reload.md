# Proving a rule-change reload hermetically (the delegation acceptance test)

Acceptance for "a rule change needs no restart": edit the contract, observe the
ENFORCER's own next verdict change — with no restart anywhere, and without ever
mutating the real contract or the real CLI.

## Recipe (hermetic, hermetic only)

1. **Copy the contract into a tmp dir** (`shutil.copy` of `write_class.py`).
2. **Write a wrapper CLI in tmp that subprocesses the REAL classify CLI** with its
   own `--contract <copy>` seam and forwards the command as argv[1] — the CLI's
   contract override is the test seam, so the test never needs the real file.
3. **Point the enforcer at the wrapper** — monkeypatch the module's
   `_classify_cli_path` (or whatever resolves the CLI) to the wrapper. The enforcer
   code that runs is the real `_classify_terminal`; only the resolved CLI path is
   substituted, so the acceptance exercises the production delegation path.
4. **Assert the probe command's current verdict**; append ONE rule to the tmp
   copy; **assert the enforcer's next verdict differs**. The fresh subprocess
   loaded the edited contract — that is the reload mechanism, proven on the
   enforcer's own path. No restart, no daemon, no cache flush involved.
5. **Broken-contract direction** (the other half of fail-closed): point
   `_classify_cli_path` at None / a broken interpreter and assert a read-only
   command now FAILS CLOSED (returns False — needs a lock) while the frozen
   in-process recovery predicate (exact `fleet-update.sh` invocation) still
   returns True. Both directions asserted, both in the same test module.

## Close-gate pins for this test class

The close-time reviewer judges a range that may NOT include the enforcer file, and
defaults any unverifiable claim to FABRICATION. So the test must be
self-verifying against in-range sources only:

- **Pin the subject before using it**: `assert _ENFORCER.is_file()` plus
  `callable(getattr(mod, fn))` for each imported private function — the test
  then cannot silently test nothing.
- **Derive expected verdicts from the CONTRACT** (the in-range source of truth),
  never hardcode `("cmd", True/False)` pairs that exist only in the test.
- The close-gate lifecycle (rereview_change with a NEW note + evidence after
  fixing findings; never an unchanged end_change) is in the `verify-and-ship`
  skill — follow that, and update THIS recipe if that skill changes.

## Why the wrapper-CLI indirection at all

Monkeypatching `_classify_cli_path` (a path-resolver) instead of `_classify_terminal`
(a full classifier) keeps the scenario parseable by a reviewer: one injected path,
no stubbed-out production logic. The trade-off to name: the test proves the real
enforcer code path against a substituted CLI resolver, which is exactly the seam
that changes when the delegation ships.
