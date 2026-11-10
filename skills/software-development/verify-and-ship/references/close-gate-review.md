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
