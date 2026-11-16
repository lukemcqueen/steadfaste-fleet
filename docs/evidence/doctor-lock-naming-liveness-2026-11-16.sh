#!/usr/bin/env bash
# Evidence verifier — the doctor must not call a LIVE session's lock "legacy",
# and must not prescribe deleting one.
#
# Defect (2026-11-16): fleet_doctor/checks.py classified a governance lock from
# its FILENAME alone, accepting only `\d{8}_\d{6}` (Hermes timestamp id) or a
# name containing `sess_`. Hermes ALSO mints `local-<sha256>` session ids
# (hermes_state_keys.py: 'local-' + sha256(identity).hexdigest()), used as a live
# session key in loop-governance.db and the skills-* stores. A lock named
# .governance-local-<64hex>.json was therefore reported as legacy with the
# REQUIRED ACTION `Remove: rm -f <file>` — and following that instruction removed
# the lock of a session that was MID-RUN.
#
# Each claim below is a single check; the script exits with the number of
# failures, so rc=0 means every claim held.
set -uo pipefail
REPO="${REPO:-$HOME/steadfaste-fleet}"
# The fleet venv has pytest + PyYAML; bare python3 does not.
PY="${FLEET_VENV_PYTHON:-$HOME/.steadfaste-fleet/venv/bin/python}"
[ -x "$PY" ] || PY=python3
FAILS=0
claim() { # claim <description> <0|1-passed>
  if [ "$2" -eq 0 ]; then printf 'PASS  %s\n' "$1"
  else printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); fi
}

DELETED=".governance-local-e4c1d78e72320771a2dc8d27471f07263117b1cd4f4e74ca9119ecceec076d4f.json"
DEPLOYED="$HOME/.steadfaste-fleet/scripts/fleet_doctor/checks.py"

# ── Claim 1: the real deleted filename is a KNOWN id under the new predicate
#             and WAS "legacy" under the old one (so the fix changes behaviour)
python3 - "$DELETED" <<'PY'
import re, sys
sys.path.insert(0, str(__import__("pathlib").Path.home() / "steadfaste-fleet" / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(__import__("pathlib").Path.home() / "steadfaste-fleet" / "ops" / "scripts"))
import fleet_doctor.checks as C
name = sys.argv[1]
old_legacy = (not re.search(r"\d{8}_\d{6}", name)) and ("sess_" not in name)
new_known = C.governance_lock_id_is_known(name)
sys.exit(0 if (old_legacy and new_known) else 1)
PY
claim "the deleted filename is a known id now and was misread as legacy before" $?

# ── Claim 2: a LIVE slug-named lock gets NO removal remedy (the class fix)
TMP="$(mktemp -d)"
python3 - "$TMP" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path.home() / "steadfaste-fleet" / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(Path.home() / "steadfaste-fleet" / "ops" / "scripts"))
import fleet_doctor.checks as C
state = Path(sys.argv[1]) / "state"
state.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc)
def body(age):
    return json.dumps({
        "task_id": "t", "status": "executing",
        "heartbeat_at": (now - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_seconds": 3600, "repo_path": "/srv/example-repo",
    })
(state / ".governance-fix-auth-bug.json").write_text(body(5))       # live
fixes = " ".join(v["fix"] for v in C.classify_governance_locks(state))
sys.exit(1 if "rm -f" in fixes else 0)
PY
claim "a LIVE lock is never given 'rm -f'" $?

# ── Claim 3: a genuinely stale legacy lock STILL gets the cleanup remedy
#             (the fix must not simply disable the check)
python3 - "$TMP" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path.home() / "steadfaste-fleet" / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(Path.home() / "steadfaste-fleet" / "ops" / "scripts"))
import fleet_doctor.checks as C
state = Path(sys.argv[1]) / "state2"
state.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc)
(state / ".governance-fix-auth-bug.json").write_text(json.dumps({
    "task_id": "t", "status": "executing",
    "heartbeat_at": (now - timedelta(seconds=7200)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "ttl_seconds": 3600, "repo_path": "/srv/example-repo",
}))
fixes = " ".join(v["fix"] for v in C.classify_governance_locks(state))
sys.exit(0 if "rm -f" in fixes else 1)
PY
claim "a provably-dead legacy lock still reports its removal remedy" $?

# ── Claim 4: an unverifiable lock fails closed (no removal instruction)
python3 - "$TMP" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "steadfaste-fleet" / "ops" / "scripts" / "manage"))
sys.path.insert(0, str(Path.home() / "steadfaste-fleet" / "ops" / "scripts"))
import fleet_doctor.checks as C
state = Path(sys.argv[1]) / "state3"
state.mkdir(parents=True, exist_ok=True)
(state / ".governance-fix-auth-bug.json").write_text("{not json")
fixes = " ".join(v["fix"] for v in C.classify_governance_locks(state))
sys.exit(1 if "rm -f" in fixes else 0)
PY
claim "an unparsable lock yields no removal instruction (fails closed)" $?
rm -rf "$TMP"

# ── Claim 5: the DEPLOYED copy carries the fix (a committed file is not a
#             running check)
grep -q "classify_governance_locks" "$DEPLOYED" \
  && grep -q "governance_lock_id_is_known" "$DEPLOYED"
claim "the deployed doctor carries both new helpers" $?

# ── Claim 6: the regression suite passes
( cd "$REPO" && "$PY" -m pytest tests/test_doctor_legacy_lock_check.py -q >/dev/null 2>&1 )
claim "tests/test_doctor_legacy_lock_check.py passes" $?

# ── Claim 7: the live doctor still reports no FAIL (the rewiring is intact)
# NB: the doctor exits 1 on warnings, so a pipeline+grep under `set -o pipefail`
# would report failure even when the text matches. Capture, then grep.
DOCTOR_OUT="$( cd "$REPO" && "$PY" ops/scripts/manage/fleet-doctor.py 2>&1 || true )"
printf '%s\n' "$DOCTOR_OUT" | grep -q "0 fail"
claim "the live doctor reports 0 fail with the rewired check" $?

echo
echo "failed claims: $FAILS"
exit "$FAILS"
