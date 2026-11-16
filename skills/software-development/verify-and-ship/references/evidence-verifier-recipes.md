# Building a runnable evidence verifier

Depth for Phase 1.5: the capture artifact is worth little on its own. What makes a
load-bearing claim believable is a SMALL SCRIPT a reader can re-run, whose exit code
carries the answer.

## The shape

- One function per claim; print `PASS <claim>` / `FAIL <claim>`, and count failures.
- **`exit "${fails}"` — the exit code is the NUMBER OF FAILED CLAIMS.** Never end on a
  trailing `echo`, which always exits 0 and so says nothing.
- A check that genuinely cannot run prints a LOUD `SKIP` with the override that would
  enable it (`MM_BASE=<sha>`). Silent skipping turns "could not verify" into a pass.
- READ-ONLY: measure, never repair. Wrap the whole thing in
  `make-evidence.sh <out> -- bash <verifier>.sh` so the artifact carries the command, its
  bare rc, and a hash.

## Claim 1 — the regression test exists and is green

```sh
grep -q 'def test_<subject>' "${TEST}" && ok "regression test present"
"${PY}" -m pytest "${TEST}" -q >"${LOG}" 2>&1 \
  && ok "suite green: $(grep -oE '[0-9]+ passed[^,]*' "${LOG}" | head -1)" \
  || bad "suite RED: $(tail -3 "${LOG}" | tr '\n' ' ')"
```

Use the interpreter that HAS the deps (the fleet venv), or the suite silently no-ops.

**Never ANCHOR the passed-count grep.** `pytest -q`'s summary line is
`==== 16 passed in 7.35s ====`, so a claim written as `grep -qE '^[0-9]+ passed'`
never matches: the claim FAILS while the suite is green, and you go hunting a
defect in the artifact that is not there. Match the counts unanchored, and assert
the absence of failures separately (`[0-9]+ (failed|error)`). This is the same
probe-the-probe rule as above, at the level of the regex.

## Claims 2..n — drive the REAL entrypoint, not a reimplementation

A probe that re-implements the logic proves nothing. Import the module and call its own
`main()`, monkeypatching only its INPUTS:

```python
spec = importlib.util.spec_from_file_location("subject", scan_path)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.default_list = lambda: list_path        # module-level names main() actually reads
mod.get_catalog  = lambda: (synthetic_catalog, "synthetic")
mod.STATE_PATH   = str(state_path)
rc = mod.main(["--json"])
```

- **The temp file needs a real `.py` basename.** `spec_from_file_location` against a
  suffix-less `mktemp` path loads nothing and the probe prints an empty result — which
  reads like a code failure. Use `"$(mktemp -d)/<same-basename>.py"`.
- Assert on a value the subject WROTE (its state file, its stdout JSON), not on internals.
- Assert BOTH directions: the bad input produces the new refusal, AND the good input is
  unchanged (rc/stderr as before). A one-directional claim cannot tell a gate from a
  blanket failure.
- **The fixture must satisfy every PRE-EXISTING invariant of the subject.** A synthetic
  input that trips an unrelated rule makes the control case fail and looks like
  over-firing; the fix is the fixture, not the code. Run the clean case first and read
  the subject's own complaint before touching the implementation.

## The BEFORE claim — pin it to the range base

A fix is only a fix against a measured baseline, and a terminal transcript is not
re-runnable. Read the pre-fix revision out of git and reuse the same probe:

```sh
base="${MM_BASE:-$(git -C "${ROOT}" merge-base origin/main HEAD)}"
before="$(mktemp -d)/<same-basename>.py"
git -C "${ROOT}" show "${base}:<path>" >"${before}"
before_out="$(probe yes "${before}" 2>&1)"     # 2>&1: a traceback must not read as 'no output'
case "${before_out}" in
  *"<old symptom>"*) ok "pre-fix ${base} reproduces the defect" ;;
  *) bad "pre-fix ${base} did NOT reproduce it — the after-side proves nothing" ;;
esac
```

Name the sha in the PASS line: the reader needs to know which revision was measured.

## Witnessing the transition

- Commit the verifier (.sh) and its capture (.txt) in the SAME range as the change, so the
  reviewer's window finds the artifact the close note cites.
- One note, one run: every number in the close note must come from the artifact beside it.
- If the capture is normalised after `make-evidence.sh` (a `sed` of the home prefix), the
  recorded hash no longer matches — drop the last line, recompute, re-append, and show
  `--verify` printing `OK`.
