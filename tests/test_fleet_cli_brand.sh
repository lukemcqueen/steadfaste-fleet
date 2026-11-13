#!/usr/bin/env bash
# Comprehensive regression suite for the hermes-cortex -> steadfaste-fleet CLI rename
# ("hc" is now "fleet"). Two layers of coverage:
#
#   A. LAUNCHER & CLI EDGE CASES - the fleet launcher must resolve its module in both
#      installed layouts (beside the launcher / one level up), pass argv through, refuse
#      with exit 3 when no module exists anywhere. Every fixture is local - no live bus,
#      no host state, no network. (The transition-compat `hc` shim was REMOVED 2026-11-12
#      — migration complete; no compat alias remains.)
#
#   B. MIGRATION-CONSISTENCY LINT - fail closed if any residual hermes-cortex/hc brand
#      token survives where it must not: the old package dir, the old deployed module
#      names, stale deploy-map registers, or bare-hc CLI invocations in the taught surface.
#      B8 extends this to the RETIRED CORTEX_ env spelling (release 2, 2026-11-13 renamed
#      the whole env family to FLEET_*), and B9 keeps the GENERATED per-harness docs in
#      step with their generator. Historical records (docs/evidence, docs/older, dated
#      session references) are expected to keep `hc` and are excluded — the sweep must
#      never falsify a record.
#
# Run: bash tests/test_fleet_cli_brand.sh   (no venv, no deps beyond bash + python3)
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/fleet-brand.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
pass=0; fails=0
ok()  { pass=$((pass+1)); echo "  ok  $1"; }
bad() { fails=$((fails+1)); echo "  FAIL $1"; }
ck()  { # ck <label> <cmd...>  -- asserts command exit 0
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else bad "$label"; fi
}
# Files that legitimately keep the historical hc brand (dated session records, ledgers,
# an audit, one past-session evidence file) plus this suite's own grep literals. A sweep
# must never falsify a record, so these are excluded from every residual lint below.
RECORD_FILTER='2026-[0-9]{2}-|session|ledger|hc-cli-refactor-session|gbrain-reference-audit|test_fleet_cli_brand\.sh'

echo "== A. LAUNCHER & CLI EDGE CASES =="

# module that identifies itself: which fleet.py ran, and with what args
stub() { printf '#!/usr/bin/env python3\nimport sys\nprint("MODULE=" + __file__)\nprint("ARGS=" + " ".join(sys.argv[1:]))\n' > "$1"; chmod +x "$1"; }

SRC="$REPO/ops/scripts/fleet/bin/fleet"
if [ ! -f "$SRC" ]; then
  bad "fleet launcher exists in repo (src=$SRC)"
else
  ok "fleet launcher exists in repo"
fi

# A1. deployed layout: module BESIDE the launcher
mkdir -p "$TMP/deploy"; cp "$SRC" "$TMP/deploy/fleet"; stub "$TMP/deploy/fleet.py"
out="$(bash "$TMP/deploy/fleet" harness verify pi 2>&1)"; rc=$?
if [ $rc -eq 0 ] && grep -q "MODULE=.*/deploy/fleet\.py$" <<<"$out"; then ok "module beside launcher resolves (rc=0)"; else bad "module beside launcher (rc=$rc out=$out)"; fi

# A2. repo layout: launcher in bin/, module ONE LEVEL UP
mkdir -p "$TMP/repo/bin"; cp "$SRC" "$TMP/repo/bin/fleet"; stub "$TMP/repo/fleet.py"
out="$(bash "$TMP/repo/bin/fleet" y x z 2>&1)"; rc=$?
if [ $rc -eq 0 ] && grep -q "MODULE=.*/repo/fleet\.py$" <<<"$out"; then ok "module one level up resolves"; else bad "module one level up (rc=$rc)"; fi
if grep -q "ARGS=y x z" <<<"$out"; then ok "argv passes through unchanged"; else bad "argv garbled: $out"; fi

# A3. no module anywhere -> exit 3 and names what it looked for (never runs a foreign tree's module)
mkdir -p "$TMP/none/bin" "$TMP/none/home"; cp "$SRC" "$TMP/none/bin/fleet"
out="$(HOME="$TMP/none/home" bash "$TMP/none/bin/fleet" 2>&1)"; rc=$?
if [ $rc -eq 3 ] && grep -q "cannot find fleet.py" <<<"$out"; then ok "no module anywhere exits 3 + names it"; else bad "no module anywhere (rc=$rc out=$out)"; fi

echo "== B. MIGRATION-CONSISTENCY LINT (fail closed) =="

# B1. old package dir must be gone from the active tree (repo paths)
left=$(
  grep -RIn --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ \
       --exclude-dir=evidence --exclude-dir=older --exclude-dir=sessions \
       -e 'ops/scripts/hc/' -e 'scripts/hc\.py' -e 'scripts/hc-harness\.py' "$REPO" 2>/dev/null \
    | grep -viE "$RECORD_FILTER"
)
if [ -z "$left" ]; then
  ok "no ops/scripts/hc/, scripts/hc.py, scripts/hc-harness.py in active tree"
else
  bad "residual old path refs:"; echo "$left"; fi

# B2. deploy map: every fleet register source must exist; no old hc register may remain
reg=$(grep -E 'register "ops/scripts/(hc|fleet)' "$REPO/ops/scripts/fleet-update.sh")
# flag ONLY the moved CLI package (ops/scripts/hc/{hc,harness,bin}); the gate tools
# were renamed off the hc- prefix (reflexion-check.py / plan-review-check.py).
if echo "$reg" | grep -qE 'register "ops/scripts/hc/(hc|harness|bin)'; then bad "fleet-update.sh still registers an ops/scripts/hc CLI source"; echo "$reg"; else ok "fleet-update.sh registers no old hc CLI source"; fi
while read -r line; do
  src=$(echo "$line" | sed -E 's/.*register "([^"]+)".*/\1/')
  [ -z "$src" ] && continue
  if [ -f "$REPO/$src" ]; then ok "register source exists: $src"; else bad "register source MISSING: $src"; fi
done <<<"$(echo "$reg" | grep 'ops/scripts/fleet')"

# B3. module internals are fully rebranded (no bare hc CLI, no old paths)
for f in "$REPO/ops/scripts/fleet/fleet.py" "$REPO/ops/scripts/fleet/harness.py"; do
  if grep -qE 'hc [-a-z]|ops/scripts/hc|scripts/hc-harness|hc harness' "$f"; then
    bad "stale hc token in $f:"; grep -nE 'hc [-a-z]|ops/scripts/hc|scripts/hc-harness|hc harness' "$f"
  else ok "module rebranded clean: $(basename "$f")"; fi
done

# B4. doctor resolves the NEW launcher paths, not the old ones
if grep -qE 'scripts" / "fleet"|"scripts" / "fleet"|"ops" / "scripts" / "fleet" / "bin" / "fleet"' "$REPO/ops/scripts/manage/fleet_doctor/checks.py" \
   && ! grep -qE 'FLEET_DEPLOY_HOME / "scripts" / "hc"|"ops" / "scripts" / "hc"' "$REPO/ops/scripts/manage/fleet_doctor/checks.py"; then
  ok "doctor harness-CLI resolver points at fleet paths"
else
  bad "doctor resolver not fully migrated:"; grep -n 'scripts" / "hc"\|scripts / "hc"\|ops / "scripts / "hc' "$REPO/ops/scripts/manage/fleet_doctor/checks.py"
fi

# B5. taught surface: no bare-CLI hc invocations in active skills/docs (records excluded)
bad_cli=$(
  grep -RInE '`?hc (send|exec|inbox|status|depth|bus|watch|kill|doctor|harness|dashboard|env|help)\b' "$REPO" \
      --include=*.md --include=*.sh --include=*.py 2>/dev/null \
      --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ \
      --exclude-dir=evidence --exclude-dir=older --exclude-dir=sessions \
    | grep -viE 'HC_' \
    | grep -viE "$RECORD_FILTER"
)
if [ -z "$bad_cli" ]; then
  ok "no bare-hc CLI invocations in taught (non-record) surface"
else
  bad "bare hc CLI invocations remain:"; echo "$bad_cli"
fi

# B6. no stale hc-branded TEST FILENAMES — the content patterns above cannot see a
#     filename, so this guards the class (tests were renamed test_hc_* -> test_fleet_*
#     on 2026-11-12; a new test_hc_* file is a regression, not a record)
stale_tests=$(
  find "$REPO/tests" -maxdepth 1 -type f \( -name 'test_hc_*' -o -name '*_hc_*' -o -name '*-hc-*' \) 2>/dev/null
)
if [ -z "$stale_tests" ]; then
  ok "no hc-branded test filenames in tests/"
else
  bad "stale hc test filenames:"; echo "$stale_tests"
fi

# B7. no hc session scheme in the gateway / pi harness (hc-<agent>-<chat>,
#     hc-pi-*) and no HC_ env contract in active code — the sweep of 2026-11-12
#     renamed both (fleet-<agent>-<chat> / FLEET_AGENT). Dated records and
#     deliberate-keep literals are exempt: the latter carry a 'STALE' label on
#     their line (the documented contract for the detection literals).
stale_scheme=$(
  grep -RInE 'hc-<agent>|hc-pi-|f"hc-|hc-party|hc-elicit' "$REPO" \
      --include=*.py --include=*.sh --include=*.mjs --include=*.ts --include=*.md 2>/dev/null \
      --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ \
      --exclude-dir=evidence --exclude-dir=older --exclude-dir=sessions \
    | grep -viE 'STALE' \
    | grep -viE "$RECORD_FILTER"
)
if [ -z "$stale_scheme" ]; then
  ok "no hc session-scheme / hc-pi tokens in active surface"
else
  bad "hc session scheme remains:"; echo "$stale_scheme"
fi
stale_env=$(
  grep -RInE '\bHC_[A-Z]+' "$REPO/ops" "$REPO/plugins" "$REPO/mcp-servers" \
      --include=*.py --include=*.sh 2>/dev/null \
      --exclude-dir=.git --exclude-dir=__pycache__ \
      --exclude-dir=evidence --exclude-dir=older \
    | grep -viE 'STALE' \
    | grep -viE "$RECORD_FILTER"
)
if [ -z "$stale_env" ]; then
  ok "no bare HC_ env tokens in production code"
else
  bad "HC_ env tokens remain:"; echo "$stale_env"
fi

# B8. the RETIRED CORTEX_ env spelling must not survive in a live INSTRUCTION
#     surface. Release 2 (2026-11-13) made FLEET_* the only spelling; a doc that
#     still teaches the old one is how a host gets configured half-migrated.
#     Excluded (and NAMED, so a reader can check the coverage):
#       - rationale/plan RECORDS (docs/design, docs/plans) and dated records — they
#         quote the pre-migration state on purpose;
#       - a line carrying the STALE keep label (the repo's documented contract);
#       - the converter ops/scripts/manage/consolidate-env.sh, which must name the
#         spelling it converts;
#       - FLEET_SESSION_* — an EXTERNAL CONTRACT the Hermes harness injects;
#       - this suite's own grep literals.
#     The surfaces are ASSERTED to exist first: a missing directory would make the
#     grep return nothing and the lint would report a clean sweep over nothing.
_b8_missing=""
for _b8d in docs ops skills mcp-servers plugins; do
  [ -d "$REPO/$_b8d" ] || _b8_missing="$_b8_missing $_b8d"
done
for _b8f in README.md AGENTS.md CONTRIBUTING.md; do
  [ -f "$REPO/$_b8f" ] || _b8_missing="$_b8_missing $_b8f"
done
if [ -n "$_b8_missing" ]; then
  bad "B8 cannot verify — missing surface(s):$_b8_missing"
else
  _b8_raw="$TMP/b8.raw"; _b8_rc=0
  grep -RInE \
      --include=*.md --include=*.example --include=*.yaml --include=*.yml \
      --include=*.toml --include=*.service --include=*.plist --include=*.conf \
      --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=node_modules \
      --exclude-dir=older --exclude-dir=evidence --exclude-dir=reviews \
      --exclude-dir=design --exclude-dir=plans --exclude-dir=artifacts \
      -e '(^|[^A-Za-z0-9_])CORTEX_[A-Z0-9_]+' \
      "$REPO/docs" "$REPO/ops" "$REPO/skills" "$REPO/mcp-servers" "$REPO/plugins" \
      "$REPO/README.md" "$REPO/AGENTS.md" "$REPO/CONTRIBUTING.md" >"$_b8_raw" || _b8_rc=$?
  if [ "$_b8_rc" -gt 1 ]; then
    bad "B8 grep failed (rc=$_b8_rc) — CANNOT VERIFY the residue surface"
  else
    b8_left="$(grep -v 'STALE' "$_b8_raw" \
      | grep -v 'consolidate-env\.sh' \
      | grep -v 'test_fleet_cli_brand\.sh' \
      | grep -viE "$RECORD_FILTER" || true)"
    if [ -z "$b8_left" ]; then
      ok "B8: no retired CORTEX_ env spelling in a live instruction surface"
    else
      bad "B8: retired CORTEX_ spelling remains (FLEET_* is the only spelling; label a deliberate keep STALE on its line):"
      echo "$b8_left"
    fi
  fi
fi

# B8-negative: prove the lint can SEE a residue. A detector that never fires is
# indistinguishable from a clean tree, so plant the exact shape and require a hit.
mkdir -p "$TMP/neg/docs"
printf 'CORTEX_DEPLOY_HOME=/tmp/x\n' > "$TMP/neg/docs/planted.md"
if grep -RInE --include=*.md -e '(^|[^A-Za-z0-9_])CORTEX_[A-Z0-9_]+' "$TMP/neg/docs" | grep -q 'CORTEX_DEPLOY_HOME'; then
  ok "B8-negative: a planted residue IS detected (the lint is not blind)"
else
  bad "B8-negative: the lint is BLIND — a planted residue was not detected"
fi

# B9. the per-harness docs are GENERATED from registry.yaml. They drifted once
#     (5 READMEs kept the old env names after the registry was already renamed),
#     and nothing caught it — so the generator's own --check runs here.
_b9_py=""
for _cand in "${FLEET_PYTHON:-}" "$HOME/.steadfaste-fleet/venv/bin/python3" python3; do
  [ -n "$_cand" ] || continue
  command -v "$_cand" >/dev/null 2>&1 || continue
  if "$_cand" -c 'import yaml' >/dev/null 2>&1; then _b9_py="$_cand"; break; fi
done
if [ -z "$_b9_py" ]; then
  bad "B9 cannot verify: no interpreter with PyYAML (needed by generate-harnesses.py)"
else
  _b9_out="$(cd "$REPO" && "$_b9_py" ops/install/harnesses/generate-harnesses.py --check 2>&1)"; _b9_rc=$?
  if [ "$_b9_rc" -eq 0 ] && grep -q 'current' <<<"$_b9_out"; then
    ok "B9: per-harness docs match their generator ($_b9_out)"
  else
    bad "B9: generated harness docs are STALE (run ops/install/harnesses/generate-harnesses.py):"
    echo "$_b9_out"
  fi
fi

echo
echo "== RESULT: $pass ok, $fails failed =="
[ $fails -eq 0 ]