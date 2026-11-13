# Mechanical-rename sweep regressions (what the per-blob reviewer finds after a blanket replace)

A find-replace sweep (CORTEX_* -> FLEET_*, any legacy->canonical rename) edits code, tests, and
docs in one pass. It leaves a characteristic set of regressions that the close gate will surface
one file at a time. Recognize them by CLASS and fix the class:

## Duplicated fallbacks
A tuple/loop of distinct fallback names collapses to the same name twice:
`for env_name in ("FLEET_DEPLOY_HOME", "FLEET_DEPLOY_HOME"):` — the sweep renamed BOTH spellings
to the canonical one, and the error message duplicates it too. Fix: a single canonical lookup when
the migration made canonical the ONLY spelling; a distinct legacy fallback only if one still
exists.

## Dead placeholder substitutions
`sed "s|OLD_TOKEN|...|g"` on a template whose placeholder the sweep renamed away (or that now uses
`$HOME` self-expansion instead). The sed matches nothing — dead code that also makes the installer
look broken. Fix: remove the sed and copy the self-contained template directly. Check what the
template ACTUALLY contains before assuming the substitution matters.

## Gutted test fixtures
A test that converts legacy -> canonical MUST have the LEGACY spelling in its INPUTS; a
blanket-renamed fixture leaves nothing to convert, so the test passes vacuously, or its assertions
invert (a "legacy dropped" check re-pointed at the canonical name). Fix: restore the legacy
spellings in fixtures AND assertions. Verify the converter's REAL behavior first by extracting the
function and running it on the disputed input (harness pattern:
`sed -n '/^fn()/,/^}/p' src > fn.sh`, source it, call it on a scratch file) — the converter may
not dedupe, may conflict-abort, etc., and the fixture must match real semantics, not the
reviewer's guess. Use an explicit `if grep ...; then chk X false; else chk X true; fi` for
"must NOT be present" assertions so the intent is unmistakeable.

## Reverse-direction tokens
A canonical -> legacy replacement where the sweep over-matched (an accidental reverse pair). Fix:
revert to canonical — but check frozen evidence first (next section).

## The reviewer's revert recommendation can be wrong
A finding may recommend reverting a token "because it contradicts the migration direction" when
the line it flags is actually the CORRECT fix (the base value was itself wrong). Revert
recommendations are testable claims: run the affected test/suite with the suggested value AND with
the current value, and keep whichever passes. A test that asserts against a committed HISTORICAL
transcript (a doctor capture, a landing summary) may REQUIRE the legacy spelling — the evidence
predates the rename and is immutable, and editing the assertion to match the new spelling is
fabrication, not modernization. When you keep the flagged value, add an intent comment ("historical
name recorded in the frozen <date> evidence; the rename came later; old spelling required here")
and cite the test result in the closing note — the comment is the durable defense, because the NEXT
sweep or reviewer will flag the same line again.

## Expect a SECOND wave on the same file
Fixing a file commits a NEW blob, which the next pass re-judges from the diff — and the reviewer
often files new findings on the SAME file (stale header text, tautological descriptions, assertion
labels, fixture shape). Pre-empt with intent comments on anything non-obvious, and expect 2-3
rounds per file. A green local test run is the strongest defense: run it and quote the pass count.
