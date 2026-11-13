# Adding a fleet-doctor check

The doctor is the `fleet_doctor/` package under `ops/scripts/manage/`. A new check follows a fixed shape so it is both hermetic-testable and deploys:

## 1. Leaf module, not `checks.py`

Add the check as its own self-contained file `fleet_doctor/<topic>.py` with NO relative imports — mirror `mcp_names.py` / `jev.py`. `checks.py` uses relative imports (`from .helpers import ...`) and therefore cannot be loaded standalone for a hermetic test (`ImportError: attempted relative import with no known parent package`). Tests load the leaf module via `importlib.util.spec_from_file_location(name, BASE/<topic>.py)`.

## 2. Signature and severity

```python
def check_<topic>(res) -> None:
    res.add(name, status, detail, fix)
```

- `PASS` when clean; `WARN` for "not canonical / needs a manual fix" (trackable by `--disposition`); `FAIL` for a hard break.
- The `fix` string is what the operator acts on — point it at the operative runbook / command, never `rm -rf <x>` alone (completing the migration/step is the fix; deleting the artifact is the proof, not the whole instruction).

## 3. Host paths with a test seam

Publish probed paths as a module-level list (e.g. `STALE_X_PATHS = [Path.home() / ...]`) so a test redirects the module attribute (save/restore in a `finally`) instead of patching `Path.home()` globally.

## 4. Register in BOTH places — forgetting either breaks the deployed doctor

- `fleet_doctor/cli.py`: import the check and append it to `all_checks` (the normal pass, not only `--quick`).
- `ops/scripts/fleet-update.sh`: add `register "ops/scripts/manage/fleet_doctor/<name>.py" "${FLEET_DEPLOY_HOME}/scripts/fleet_doctor/<name>.py"` — every `fleet_doctor` file deploys INDIVIDUALLY. The pre-commit docs-audit flags an unregistered new `fleet_doctor` file.

## 5. Test it (TDD)

A minimal `FakeRes` with `.add(name, status, detail="", fix="")` collects the row; assert on `res.checks[-1]`. Drive RED (module absent) then GREEN, and cover the WARN and PASS branches plus a symlink state when the check is about path leftovers.

## 6. Verify against the DEPLOYED tree

`adversarial-verify.py --file <new> --level A4 --gate`, then `bash ops/scripts/fleet-update.sh` and run `~/.steadfaste-fleet/scripts/fleet-doctor.py` (the deployed copy). An unsynced repo-side `cli.py` shows as a mid-cycle `Checksum: cli.py — MD5 mismatch` — that is the "not yet deployed" state, not a defect: deploy, then re-run; the FAIL clears and the new check reports from the real path.
