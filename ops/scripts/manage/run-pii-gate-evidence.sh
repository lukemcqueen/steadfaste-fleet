#!/usr/bin/env bash
# Regenerate docs/evidence/pii-gate-redaction-evidence.txt (ADV-10571-1).
#
#   bash ops/scripts/manage/run-pii-gate-evidence.sh
#
# Exit 0 = the redaction claim holds: the guard discriminates (it trips on the
# pre-fix revision) and the redacted test file carries no identifier component
# and passes. Exit 1 = the claim is false; do not ship.
#
# Not a deployed script — a repo-local evidence generator, like
# run-task-queue-evidence.sh. tests/test_pii_gate_evidence.py re-runs it in
# --check mode so the committed artifact cannot be hand-written.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# pii-gate-evidence.py shells out to `sys.executable -m pytest`. Bare `python3`
# (the agent runtime on the cron PATH) has no pytest — measured 2026-11-13,
# where the run printed "(no output)" for the suite row. Resolve a pytest-capable
# interpreter (fleet venv first), fail closed if absent.
PY="${EVIDENCE_PY:-$(bash "${REPO}/ops/scripts/lib/python-with-module.sh" --print pytest)}"
if [ -z "${PY}" ] || [ ! -x "${PY}" ]; then
  echo "COULD NOT VERIFY — no interpreter on this host can import pytest" >&2
  exit 3
fi
exec "${PY}" "${REPO}/ops/scripts/manage/pii-gate-evidence.py" "$@"
