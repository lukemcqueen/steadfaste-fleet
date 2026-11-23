<!-- Backport provenance (deployed -> repo): the DEPLOYED copy of this file carried
     lessons the repo lacked. Every repo-only line it would drop was checked by phrase
     match and is a reflow or a deliberate CORRECTION, so the direction is
     deployed -> repo. Everything below is the deployed lesson copied verbatim, plus this
     note; it was NOT authored in the backport range that landed it. -->
# Close-gate review — the per-blob reviewer that refuses end_change

The close-time reviewer (MCP-owned, runs when you end_change) judges the RANGE'S blobs against a working window. It differs from the plan reviewer in four ways that cost cycles until they are known:

## 1. Its window may not include a test's subject file — pin the subject IN the test

A test that imports module-internal functions from another file is judged against a window that may not contain that file. The reviewer files **fabrication**: "tests functions imported from X, but X is not in the range file list — prove it exists." The fix is to make the reliance asserted, not assumed:

```python
@pytest.fixture()
def subject(monkeypatch):
    assert _SUBJECT_PATH.is_file(), f"subject source missing: {_SUBJECT_PATH}"
    mod = _load(_SUBJECT_PATH, "subject_t")
    for fn in ("_classify_terminal", "_is_write_tool"):
        assert callable(getattr(mod, fn, None)), f"subject lost {fn}()"
```

Asserting the file exists AND the called functions are callable gives the reviewer the in-window proof. Load the subject via importlib from a path the test computes (module name unique to the test), never a bare `import` that assumes the tree layout.

## 2. Hardcoded expectations in tests = unverified-claim — DERIVE from the in-range source

A test asserting `("git status --short", True)` hardcodes the classification truth. When that truth lives in a different file (a contract, a helper), the reviewer files **unverified-claim**. Derive the expectation from the source of truth (which IS in range) instead:

```python
contract = _load(_CONTRACT_PATH, "contract_t")
for cmd in ["git status --short", "rm -rf x", "ls -la"]:
    expect = contract.is_readonly_command(cmd)   # derived, never hand-copied
    assert subject._classify_terminal(cmd) is expect, cmd
```

## 3. end_change refused → the lock is HELD and commits STACK — exit via rereview_change

A refused close stacks whatever you commit next under the old description: "the reviewer will later read a description that no longer matches the diff." The legitimate exits are: (a) fix the findings, commit, then call `mcp__review__rereview_change(task_id=..., note=...)` with a NEW note that states what changed AND carries the command output as evidence — an unchanged note is REFUSED, and the reviewer re-runs against the current diff live (it cannot manufacture a CLEAN); then `end_change` succeeds. (b) If the work is genuinely abandoned, release via `advance_task_state(new_state='cancelled', reason=...)` — never retry `end_change` unchanged. The cycle stays OPEN through the refusal, so `feedback_accept` before it only scores; the close verdict is separate.

## 4. The A4 static gate reads test fixtures too

A test fixture that builds a wrapper script with `exec(open(...).read())` — or any eval/exec on a dynamic string — trips the pre-commit A4 gate as a CRITICAL dangerous-eval, even though it never runs untrusted input. Build the wrapper to `subprocess.call` the real tool with a pinned arg instead:

```python
wrapper.write_text(
    "#!/usr/bin/env python3\n"
    "import subprocess, sys\n"
    f"sys.exit(subprocess.call([sys.executable, {str(_REAL_CLI)!r}, '--contract', {str(contract_copy)!r}, sys.argv[1]]))\n"
)
```

One extra process hop in the test is cheap; a CRITICAL finding on the fixture is not.

## 5. Long reviews and missing rows

The runner prints nothing on its success path, so "no output" is ambiguous, and a
review slow enough to hit a tool's per-call timeout is KILLED before it writes a
row. The cycle is then left with NO review — which looks identical to a review that
ran and returned clean.

- Run the invocation as a BACKGROUND process writing to a log file, then poll. Do
  not hold a foreground tool call open on a model round-trip; a long review exceeds
  it and the abort costs the whole round.
- The DB is the only thing that settles it: read `adversarial_reviews` for the
  cycle and treat **0 rows as "no review happened"** — re-run, never assume clean.
  Read it with python's `sqlite3` in read-only mode
  (`file:$HOME/.steadfaste-fleet/data/loop-governance.db?mode=ro`).
- `findings_json` can be an EMPTY list while `summary` carries the prose findings.
  Parse `summary` for verdict and severities, or a FINDINGS review reads as clean.
- `--reviewer-id` is REQUIRED for `--review`; the call is refused without it.

### Folding a finding costs one test, not one paragraph

A finding can be severity `info` and still name a real gap — and it can also be a
MISREADING of your code. Resolve both the same cheap way: write the test that
drives the exact shape the finding describes. If it PASSES, the hypothesis was
wrong and is now pinned by executable proof; if it FAILS, you found a real bug.
Editing prose explains nothing and leaves the claim unverifiable.

## 6. "Cannot close yet: N blob(s) not covered" with NO findings → RETRY, don't re-review

This refusal is a BUDGET message, not a defect report: one close pass judges a
bounded set of blobs (15 blobs / ~240s) and verdicts are keyed to blob CONTENT, so
**repeated `end_change` calls RESUME** where the last one stopped and the count
falls each pass (5 → 2 → 1 → closed). Retrying is the intended exit; do not edit
code or re-describe the change to "fix" a message that named no finding. Contrast
§3, which applies only when a FINDINGS verdict exists for the blob.

Two shapes that otherwise send you down the wrong path:

- `no blob was SENT this pass: every blob still outstanding already carries a
  not-clean verdict for its exact content` = the reviewer TIMED OUT on that blob,
  so nothing was left to attempt in that pass. Call `end_change` again — a later
  pass can still cover it. Re-rolling is expected here, not a smell.
- `rereview_change` answers `Cycle <id> has no recorded review — nothing to
  re-review` whenever the close has not yet SUCCEEDED. That is not evidence about
  your code and it is not the remedy: the cycle-level row is only written by a
  successful close, and the tool exists to re-judge a recorded FINDINGS verdict.
  Do not loop on it, and do not treat it as a missing review you must go and create.
- A GENERATED file that needs a verdict cannot be hand-edited to force a fresh sha
  — its own banner forbids it and the parity/derived-docs check re-flags the pair —
  so retry the close rather than touching the artifact.
